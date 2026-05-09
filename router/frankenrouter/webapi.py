"""Web API (REST + HTML UI) for frankenrouter."""
# pylint: disable=fixme,invalid-name
import asyncio
import math
import re
import signal
import statistics
import textwrap
import time

from aiohttp import web  # pylint: disable=import-error


_INDEX_PAGE = '''
<html>
<head>
<meta name="color-scheme" content="{rest_api_color_scheme}" />
</head>
<body>
<h1>Frankenrouter control</h1>
<ul>
<li><a href="/filter">Filter control</a>
<li><a href="/upstream">Upstream control</a>
<li><a href="/shutdown">Router shutdown</a>
</ul>
</body>
</html>
'''

_SHUTDOWN_PAGE = '''
<html>
<head>
<meta name="color-scheme" content="{rest_api_color_scheme}" />
</head>
<body>
<h1>Frankenrouter shutdown</h1>
<hr>
<p><b>Warning: this will stop the router. All connected clients will be disconnected.</b>
<p>
<form action="/api/shutdown/yes" method="post">
<input type="submit" value="Shut down router now">
</form>
<hr>
<p><a href="/">Cancel</a>
</body>
</html>
'''

_SHUTDOWN_CONFIRM_PAGE = '''
<html>
<head>
<meta name="color-scheme" content="{rest_api_color_scheme}" />
</head>
<body>
<h1>Router is shutting down</h1>
<p>The router is shutting down. You can close this window.
</body>
</html>
'''

_FILTER_PAGE = '''
<html>
<head>
<meta name="color-scheme" content="{rest_api_color_scheme}" />
</head>
<body>
<h1>Frankenrouter filter control</h1>
<hr>
<p>Note: Filter control is now automatic. If you are connected to a master sim you will have options below to start or stop sending elevation or traffic data from your sim. These control the filters in all connected routers.
{network_source_section}
<hr>
</body>
</html>
'''

_FILTER_PAGE_NETWORK_SOURCE_SECTION = '''
<hr>
<h2>Network filter source control</h2>
<p>Note: you need to reload this page to see the current settings.
<p>This sim: <b>{this_sim}</b>
<p>Elevation data source: <b>{elevation_source}</b>
<p><a href="/api/filter/elevation/start_sending">START SENDING ELEVATION DATA</a>
<p><a href="/api/filter/elevation/stop_sending">STOP SENDING ELEVATION DATA</a>
<p>Traffic data source: <b>{traffic_source}</b>
<p><a href="/api/filter/traffic/start_sending">START SENDING TRAFFIC DATA</a>
<p><a href="/api/filter/traffic/stop_sending">STOP SENDING TRAFFIC DATA</a>
'''

_UPSTREAM_PAGE = '''
<html>
<head>
<meta name="color-scheme" content="{rest_api_color_scheme}" />
</head>
<body>
<h1>Frankenrouter connection control</h1>
<hr>
<p>Current upstream status: {status}

<hr>
<form action="/api/upstream" method="post">
<label for="host">IP address or hostname of master sim:</label><br>
<input type="text" id="host" value="{host}" name="host"><br>
<label for="port">Port number of master sim:</label><br>
<input type="text" id="port" value="{port}" name="port"><br>
<label for="password">Your password for the master sim:</label><br>
<input type="text" id="password" value="{password}" name="password"><br>
<p><input type="submit" value="Reconnect using data entered above">
</form>
{presets}
</body>
</html>
'''

_UPSTREAM_PAGE_PRESET_SECTION = '''
<hr>
<form action="/api/upstream" method="post">
<input type="hidden" id="host" value="{host}" name="host">
<input type="hidden" id="port" value="{port}" name="port">
<input type="hidden" id="password" value="{password}" name="password">
<input type="submit" value="Switch to upstream {preset_name}: {host} port {port}">
</form>
'''


class RouterWebAPI:  # pylint: disable=too-few-public-methods
    """Owns the aiohttp application and all REST/HTML route handlers."""

    def __init__(self, router):
        """Store reference to the router instance."""
        self.router = router

    async def run(self, name):  # pylint: disable=too-many-statements,too-many-locals
        """Start the HTTP server and serve until cancelled."""
        from frankenrouter import routercache  # pylint: disable=import-outside-toplevel
        router = self.router

        try:
            routes = web.RouteTableDef()

            @routes.get('/')
            async def handle_web(_):
                data = {'rest_api_color_scheme': router.config.listen.rest_api_color_scheme}
                return web.json_response(
                    text=_INDEX_PAGE.format(**data), content_type='text/html')

            @routes.get('/api/flightinfo')
            async def handle_clightinfo_get(_):
                router.logger.info("GOT /flightinfo API call")
                try:
                    acft_state = router.cache.get_value('Qs121')
                    route = router.cache.get_value('Qs376')
                    fltno = router.cache.get_value('Qs401')
                except routercache.RouterCacheException:
                    return web.json_response({'ok_data': False})
                if acft_state is None:
                    return web.json_response({'ok_data': False})

                PiBaHeAlTas = acft_state.split(';')
                pitch = math.degrees(float(PiBaHeAlTas[0]) / 1000000)
                bank = math.degrees(float(PiBaHeAlTas[1]) / 1000000)
                heading_true = math.degrees(float(PiBaHeAlTas[2]))
                alt_true_ft = float(PiBaHeAlTas[3]) / 1000
                tas = float(PiBaHeAlTas[4]) / 1000
                lat = math.degrees(float(PiBaHeAlTas[5]))
                lon = math.degrees(float(PiBaHeAlTas[6]))
                router.logger.info("Returned OK info for /flightinfo API call")
                return web.json_response({
                    'ok_data': True,
                    'latitude_degrees': lat,
                    'longitude_degrees': lon,
                    'altitude_feet': alt_true_ft,
                    'heading_degrees': heading_true,
                    'speed_knots': tas,
                    'pitch_degrees': pitch,
                    'bank_degrees': bank,
                    'route': route,
                    'flight': fltno,
                })

            @routes.get('/api/stats')
            async def handle_stats_get(request):
                params = request.rel_url.query
                history = 0
                try:
                    history = int(params['history'])
                except (KeyError, ValueError):
                    pass
                response = {
                    'upstream_queue': router.messagequeue_from_upstream.qsize(),
                    'client_queue': router.messagequeue_from_clients.qsize(),
                }
                if len(router.message_write_times) > 1:
                    response['write_times_ms'] = {
                        'max': 1000 * max(router.message_write_times),
                        'median': 1000 * statistics.median(router.message_write_times),
                        'mean': 1000 * statistics.mean(router.message_write_times),
                        'stdev': 1000 * statistics.stdev(router.message_write_times),
                    }
                if len(router.log_times) > 1:
                    response['log_times_ms'] = {
                        'max': 1000 * max(router.log_times),
                        'median': 1000 * statistics.median(router.log_times),
                        'mean': 1000 * statistics.mean(router.log_times),
                        'stdev': 1000 * statistics.stdev(router.log_times),
                    }
                if len(router.writes_counter) > 1:
                    response['writes_per_second'] = {
                        'last': router.writes_counter[1]['count'],
                    }
                    if history > 0:
                        response['writes_per_second']['history'] = list(
                            router.writes_counter)[1:history]
                if len(router.message_counter) > 1:
                    response['messages_per_second'] = {
                        'last': router.message_counter[1]['count'],
                    }
                    if history > 0:
                        response['messages_per_second']['history'] = list(
                            router.message_counter)[1:history]
                return web.json_response(response)

            @routes.get('/api/clients')
            async def handle_clients_get(_):
                clients = []
                for client in router.clients.values():
                    thisclient = {
                        'ip': client.ip,
                        'id': client.client_id,
                        'port': client.port,
                        'display_name': client.display_name,
                        'messages_sent': client.messages_sent,
                        'messages_received': client.messages_received,
                        'client_provided_id': client.client_provided_id,
                        'client_provided_display_name': client.client_provided_display_name,
                        'write_buffer_size': client.writer.transport.get_write_buffer_size(),
                    }
                    if len(client.message_write_times) > 1:
                        thisclient['write_times_ms'] = {
                            'max': 1000 * max(client.message_write_times),
                            'median': 1000 * statistics.median(client.message_write_times),
                            'mean': 1000 * statistics.mean(client.message_write_times),
                            'stdev': 1000 * statistics.stdev(client.message_write_times),
                        }
                    bucket = int(time.time() - 1.0)
                    if bucket in client.received_stats:
                        thisclient['received_messages_per_second'] = client.received_stats[bucket]['received_messages']  # pylint: disable=line-too-long
                        thisclient['received_bytes_per_second'] = client.received_stats[bucket]['received_bytes']  # pylint: disable=line-too-long
                    if bucket in client.sent_stats:
                        thisclient['sent_messages_per_second'] = client.sent_stats[bucket]['sent_messages']  # pylint: disable=line-too-long
                        thisclient['sent_bytes_per_second'] = client.sent_stats[bucket]['sent_bytes']  # pylint: disable=line-too-long
                    clients.append(thisclient)
                return web.json_response(clients)

            @routes.post('/api/disconnect')
            async def handle_client_disconnect(request):
                data = await request.post()
                client_id = int(data.get('client_id'))
                for client in router.clients.values():
                    if client.client_id == client_id:
                        await router.close_client_connection(client)
                        return web.Response(text=f"Client connection {client_id} closed")
                return web.Response(text=f"Client connection {client_id} not found")

            @routes.get('/api/routerinfo')
            async def handle_routerinfo_get(_):
                return web.json_response(router.routerinfo)

            @routes.get('/filter')
            async def handle_web_filter_get(_):
                data = {'rest_api_color_scheme': router.config.listen.rest_api_color_scheme}
                if router.filter_elevation:
                    data["filter_status_elevation"] = "enabled"
                    data["filter_status_description_elevation"] = "your sim is NOT sending elevation data"  # pylint: disable=line-too-long
                    data["next_state_elevation"] = "disable"
                else:
                    data["filter_status_elevation"] = "disabled"
                    data["filter_status_description_elevation"] = "your sim IS sending elevation data"  # pylint: disable=line-too-long
                    data["next_state_elevation"] = "enable"
                if router.filter_traffic:
                    data["filter_status_traffic"] = "enabled"
                    data["filter_status_description_traffic"] = "your sim is NOT sending traffic data"  # pylint: disable=line-too-long
                    data["next_state_traffic"] = "disable"
                else:
                    data["filter_status_traffic"] = "disabled"
                    data["filter_status_description_traffic"] = "your sim IS sending traffic data"  # pylint: disable=line-too-long
                    data["next_state_traffic"] = "enable"

                if router.get_router_type() == 'slave':
                    data['network_source_section'] = _FILTER_PAGE_NETWORK_SOURCE_SECTION.format(
                        this_sim=router.config.identity.simulator,
                        elevation_source=router.sharedinfo.get(
                            'elevation_source_simulator', 'unknown'),
                        traffic_source=router.sharedinfo.get(
                            'traffic_source_simulator', 'unknown'),
                    )
                else:
                    data['network_source_section'] = ''
                return web.json_response(
                    text=_FILTER_PAGE.format(**data), content_type='text/html')

            @routes.get('/api/filter/elevation/start_sending')
            async def handle_filter_elevation_start_sending(_):
                sim = router.config.identity.simulator
                router.logger.info("API: sending ELEVATION_SOURCE:%s upstream", sim)
                await router.send_to_upstream(
                    f"addon=FRANKENROUTER:{router.frdp_version}:ELEVATION_SOURCE:{sim}")
                raise web.HTTPFound('/filter')

            @routes.get('/api/filter/elevation/stop_sending')
            async def handle_filter_elevation_stop_sending(_):
                router.logger.info("API: sending ELEVATION_SOURCE:NOSIM upstream")
                await router.send_to_upstream(
                    f"addon=FRANKENROUTER:{router.frdp_version}:ELEVATION_SOURCE:NOSIM")
                raise web.HTTPFound('/filter')

            @routes.get('/api/filter/traffic/start_sending')
            async def handle_filter_traffic_start_sending(_):
                sim = router.config.identity.simulator
                router.logger.info("API: sending TRAFFIC_SOURCE:%s upstream", sim)
                await router.send_to_upstream(
                    f"addon=FRANKENROUTER:{router.frdp_version}:TRAFFIC_SOURCE:{sim}")
                raise web.HTTPFound('/filter')

            @routes.get('/api/filter/traffic/stop_sending')
            async def handle_filter_traffic_stop_sending(_):
                router.logger.info("API: sending TRAFFIC_SOURCE:NOSIM upstream")
                await router.send_to_upstream(
                    f"addon=FRANKENROUTER:{router.frdp_version}:TRAFFIC_SOURCE:NOSIM")
                raise web.HTTPFound('/filter')

            @routes.get('/upstream')
            async def handle_web_upstream_get(_):
                data = {
                    'rest_api_color_scheme': router.config.listen.rest_api_color_scheme,
                    'host': router.config.upstream.host,
                    'port': router.config.upstream.port,
                }
                if router.upstream:
                    data["status"] = (
                        f"connected to {router.config.upstream.host}"
                        f":{router.config.upstream.port}"
                    )
                else:
                    data["status"] = "NOT CONNECTED"
                data["password"] = router.config.upstream.password or ""
                data["presets"] = ""
                for upstream in router.config.upstreams:
                    formatdata = {
                        "preset_name": upstream.name,
                        "host": upstream.host,
                        "port": upstream.port,
                        "password": upstream.password if upstream.password is not None else "",
                    }
                    data['presets'] += _UPSTREAM_PAGE_PRESET_SECTION.format(**formatdata)
                return web.json_response(
                    text=_UPSTREAM_PAGE.format(**data), content_type='text/html')

            @routes.post('/api/upstream')
            async def handle_upstream_set(request):
                data = await request.post()
                new_host = data.get('host')
                new_password = data.get('password')
                new_port = int(data.get('port'))
                router.logger.info(
                    "Got request to change upstream to %s:%s:%s",
                    new_host, new_port, new_password)
                router.logger.info(
                    "Current upstream is %s:%s (connected=%s)",
                    router.config.upstream.host,
                    router.config.upstream.port,
                    router.is_upstream_connected(),
                )
                reconnect = False
                if new_host != router.config.upstream.host:
                    router.config.upstream.host = new_host
                    reconnect = True
                if new_port != router.config.upstream.port:
                    router.config.upstream.port = new_port
                    reconnect = True
                if new_password != router.config.upstream.password:
                    router.config.upstream.password = new_password
                    reconnect = True
                if not reconnect:
                    return web.Response(text="Already connected to that host/port/password")
                router.logger.info(
                    "Will change upstream to %s:%s:%s",
                    router.config.upstream.host,
                    router.config.upstream.port,
                    router.config.upstream.password,
                )
                router.upstream_reconnect_requested = True
                await asyncio.sleep(5)  # typical reconnect time
                raise web.HTTPFound('/upstream')

            @routes.get('/api/upstream')
            async def handle_upstream_get(_):
                if router.is_upstream_connected():
                    res = {
                        'connected': True,
                        'host': router.upstream.ip,
                        'port': router.upstream.port,
                        'display_name': router.upstream.display_name,
                        'messages_sent': router.upstream.messages_sent,
                        'messages_received': router.upstream.messages_received,
                    }
                else:
                    res = {'connected': False}
                return web.json_response(res)

            @routes.get('/api/sharedinfo')
            async def handle_sharedinfo(_):
                res = router.sharedinfo
                res['master_uuid'] = router.sharedinfo['master_uuid']
                return web.json_response(res)

            @routes.post('/api/sharedinfo')
            async def handle_sharedinfo_post(request):
                # FIXME: refuse unless we are the sharedinfo master
                data = await request.post()
                new_simulator = data.get('pilot_flying_simulator')
                changes = 0
                if (
                        new_simulator is not None and
                        new_simulator != router.sharedinfo["pilot_flying_simulator"]
                ):
                    router.logger.info(
                        "REST API changed pilot flying simulator to %s", new_simulator)
                    changes += 1
                    router.sharedinfo["pilot_flying_simulator"] = new_simulator
                if changes == 0:
                    return web.Response(text="Nothing was changed")
                router.logger.info("API: sharedinfo changed to %s", router.sharedinfo)
                router.connection_state_changed()
                return web.Response(text=f"{changes} SHAREDINFO variables changed")

            @routes.get('/api/blocklist')
            async def handle_blocklist_get(_):
                return web.json_response(list(router.blocklist))

            @routes.get('/api/blocklist/reset')
            async def handle_blocklist_reset(_):
                router.blocklist = set()
                router.logger.info("API: blocklist was reset")
                return web.Response(text="Block list reset")

            @routes.post('/api/blocklist/add')
            async def handle_blocklist_post_add(request):
                data = await request.post()
                address = str(data.get('address'))
                router.logger.info("API: %s added to blocklist", address)
                router.blocklist.add(address)
                return web.json_response(list(router.blocklist))

            @routes.post('/api/blocklist/remove')
            async def handle_blocklist_post_remove(request):
                data = await request.post()
                address = str(data.get('address'))
                router.blocklist.discard(address)
                router.logger.info("API: %s removed from blocklist", address)
                return web.json_response(list(router.blocklist))

            @routes.post('/api/vpilotprint/message')
            async def handle_print(request):
                data = await request.post()
                token = str(data.get('token'))
                title = str(data.get('title'))
                message = str(data.get('message'))
                priority = str(data.get('priority'))

                if re.match(
                        r".*(Connected. Running version|Disconnected from network)",
                        message
                ):
                    router.logger.info(
                        "vPilot title=%s message not printed: %s", title, message)
                    return web.Response(text="OK")

                router.logger.info(
                    "vPilot message: token=%s, title=%s, message=%s, priority=%s",
                    token, title, message, priority)

                # FIXME: filter invalid characters

                text = textwrap.wrap(message, width=40)
                text = '^'.join(text)
                text = f"From {title} via {router.config.identity.simulator}:^{text}"
                text = text.upper()

                router.cache.update("Qs119", text)
                await router.send_to_upstream(f"Qs119={text}")
                await router.client_broadcast(f"Qs119={text}")
                return web.Response(text="OK")

            @routes.get('/shutdown')
            async def handle_shutdown_get(_):
                data = {'rest_api_color_scheme': router.config.listen.rest_api_color_scheme}
                return web.json_response(
                    text=_SHUTDOWN_PAGE.format(**data), content_type='text/html')

            @routes.post('/api/shutdown/yes')
            async def handle_shutdown_yes(_):
                router.logger.info("API: shutdown requested via web interface")
                loop = asyncio.get_running_loop()

                def _do_shutdown():
                    signal.signal(signal.SIGINT, signal.SIG_DFL)
                    signal.raise_signal(signal.SIGINT)

                loop.call_later(0.5, _do_shutdown)
                data = {'rest_api_color_scheme': router.config.listen.rest_api_color_scheme}
                return web.json_response(
                    text=_SHUTDOWN_CONFIRM_PAGE.format(**data), content_type='text/html')

            @web.middleware
            async def cors_middleware(request, handler):
                response = await handler(request)
                response.headers['Access-Control-Allow-Origin'] = '*'
                return response

            app = web.Application(middlewares=[cors_middleware])
            app.add_routes(routes)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, '0.0.0.0', router.config.listen.rest_api_port)
            await site.start()
            while True:
                await asyncio.sleep(3600.0)

        except asyncio.exceptions.CancelledError:
            router.logger.info("Task %s was cancelled, cleanup and exit", name)
            await runner.cleanup()
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            router.logger.critical(
                "Unhandled exception %s in %s, shutting down", exc, name)
            router.logger.critical(__import__('traceback').format_exc())
