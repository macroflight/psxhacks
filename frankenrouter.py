import asyncio

clients = {}
psx_server = None

variables = [
    'Qh73', # CMD L
    'Qi182', # Nose cargo - sent during start
]

current_conn = {
}

lexicon = {}

async def handle_new_psx_client(reader, writer):
    addr = writer.get_extra_info('peername')
    clients[addr] = {
        'peername': addr,
        'reader': reader,
        'writer': writer,
    }
    print(f"New client connected: {addr}, clients={clients.keys()}")

    # Faking a new client connection to the real PSX
    # id
    # version
    # layout - not implemented yet
    # Lexicon entries
    # Qi138
    # Qs440
    # Qs439
    # Qs450
    # load1
    # Qi0, Qi1, .. Qi31
    # load2 (race puts it here?)
    # Qi32 ...
    # Qh3, Qh4, ...
    # Qs0, Qs1, ...
    # load3
    # metar
    # <done>

    # Problem with FrankenUSB: psx.get(FltControls) returns None until
    # someone else has moved the flight controls. Works with a direct
    # connection though.
    
    # Send the id and version to the client
    for variable in ['id', 'version']:
        if variable in current_conn:
            line = f"{variable}={current_conn[variable]}\n"
            writer.write(line.encode())
        else:
            # fake it
            line = f"{variable}=42\n"
            writer.write(line.encode())

    line = f"layout=1\n" # fake
    writer.write(line.encode())
            
    # Send the lexicon to the client (at least FrankenUSB needs this)
    # FIXME: handle this for a client that connects before the main
    # server, or reconnect.
    for key, value in lexicon.items():
        line = f"{key}={value}\n"
        writer.write(line.encode())
    # At least FrankenUSB needs a load1 after the lexicon
    line = f"load1\n"
    writer.write(line.encode())
    # FIXME: do we need to cache and send Qi variables here?
    line = f"load2\n"
    # FIXME: do we need to cache and send Qi, Qh and Qs variables here?
    writer.write(line.encode())
    line = f"load3\n"
    writer.write(line.encode())
        
    # Send a "start" to the server to force it to resend the startup
    # info
    await write_to_psx_server("start\n".encode(), f"forced startup due to {addr}")
    
    # Send a "bang" to force resending of all non-Delta variables
    await write_to_psx_server("bang\n".encode(), f"forced bang due to {addr}")
    
    # Wait for client input
    while True:
        # We know the protocol is text-based, so we can use readline()0
        data = await reader.readline()
        message = data.decode()
        if message == "":
            writer.close()
            del clients[addr]
            print(f"Received EOF from client {addr} - closing connection. clients={clients.keys()}")
            return
        print(f"Received {len(message)} bytes from {addr}, sending to PSX: {data}")
        print(f"DEBUG: type of data is {type(data)}")
        await write_to_psx_server(data, addr)
        await writer.drain()

async def write_to_connected_clients(data):
    # Filter data
    line = data.decode().rstrip()
    # print(f"line: {line}")
    try:
        (key, value) = line.split('=',1)
    except ValueError:
        # Print all non-key-value data
        print(f"PSX(non-kv) => {data.decode().rstrip()}")
    else:
        # print(f"key: {key}")
        if key in variables:
            print(f"PSX => {data.decode().rstrip()}")
    if len(clients) < 1:
        # print("No connected clients, cannot write data")
        return
    for addr, client in clients.items():       
        # print(f"Writing {len(data)} bytes to client {addr}")
        # print(f"PSX => {addr}: {data.decode().rstrip()}")
        await client['writer'].drain()
        client['writer'].write(data)
        
async def write_to_psx_server(data, client_addr):
    global psx_server
    if psx_server is None:
        print(f"No PSX server connection, cannot send client data: {data}")
        return
    print(f"DEBUG: data from client: =>>{data}<==")
    print(f"PSX <= {client_addr}: {data.decode().rstrip()}")
    writer = psx_server[1]
    writer.write(data)
    await writer.drain()
        
async def psx_server_connection():
    global psx_server, current_conn
    while True:
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', 10747)
        except ConnectionRefusedError:
            print(f"No PSX server, sleeping 1s ({len(clients)} clients connected)")
            await asyncio.sleep(1)
            continue
        psx_server = (reader, writer)
        print(f"psx_server=={psx_server}")
        print("Waiting for PSX data")
        while True:
            data = await reader.readline()
            line = data.decode()
            if line == '':
                print("PSX disconnected")
                psx_server = None
                current_conn = {}
                print(f"psx_server=={psx_server}")
                # wait 1s before reconnect
                await asyncio.sleep(1)
                break

            print(f'Received from PSX: {data.decode().rstrip()}')

            # Store various things that we get e.g on initial
            # connection and that we might need later.

            line = data.decode().strip()
            key, sep, value = line.partition("=")

            # Some keys are relevant at the connection level.
            if key == "load1":
                print(f"Loaded {len(lexicon)} lexicon entries")
            elif key[0]=="L":
                print(f"Adding {key} to lexicon")
                lexicon[key] = value
            elif key == 'id':
                current_conn['id'] = value
            elif key == 'version':
                current_conn['version'] = value
                
            await write_to_connected_clients(data)
    
async def main():
    print("Setting up proxy server")
    proxy_server = await asyncio.start_server(handle_new_psx_client, '127.0.0.1', 10748)
    addr = proxy_server.sockets[0].getsockname()
    print(f'Serving on {addr}')

    print("Setting up connection to PSX server")
    await psx_server_connection()

    async with proxy_server:
        await proxy_server.serve_forever()

asyncio.run(main())
