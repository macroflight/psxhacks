"""Frankrouter config class."""

import copy
import ipaddress
import logging
import os
import re
import unittest
import tomllib


class _RouterConfigIdentity:  # pylint: disable=missing-class-docstring,too-few-public-methods
    def __init__(self, data):
        self.simulator = data.get('simulator', "Unknown Sim")
        self.router = data.get('router', "Unknown Router")
        self.stop_minded = data.get('stop_minded', False)


class _RouterConfigListen:  # pylint: disable=missing-class-docstring,too-few-public-methods
    def __init__(self, data):
        self.port = data.get('port', 10748)
        if not isinstance(self.port, int):
            raise RouterConfigError("The listen port must be an integer")

        self.rest_api_port = data.get('rest_api_port', 8747)
        if not isinstance(self.port, int):
            raise RouterConfigError("The API port must be an integer")
        self.rest_api_color_scheme = data.get('rest_api_color_scheme', 'dark')


class _RouterConfigUpstream:  # pylint: disable=missing-class-docstring,too-few-public-methods
    def __init__(self, data):
        self.name = data.get('name', None)
        if self.name is None:
            raise RouterConfigError("All upstreams must have a name")
        self.host = data.get('host', '127.0.0.1')
        self.port = data.get('port', 10747)
        if not isinstance(self.port, int):
            raise RouterConfigError("The upstream port must be an integer")
        self.password = data.get('password', None)
        self.default = data.get('default', False)


class _RouterConfigLog:  # pylint: disable=missing-class-docstring,too-few-public-methods
    def __init__(self, data):
        self.traffic = data.get('traffic', False)
        self.traffic_max_size = data.get('traffic_max_size', 0)
        self.traffic_keep_versions = data.get('traffic_keep_versions', 1)

        self.output_max_size = data.get('output_max_size', 0)
        self.output_keep_versions = data.get('output_keep_versions', 1)

        if not isinstance(self.traffic, bool):
            raise RouterConfigError("The traffic setting must be true or false")

        self.directory = data.get('directory', os.getcwd())
        if not os.path.exists(self.directory):
            raise RouterConfigError(f"Log directory {self.directory} does not exist")


class _RouterConfigPsx:  # pylint: disable=missing-class-docstring,too-few-public-methods
    def __init__(self, data):
        self.variables = data.get('variables', 'Variables.txt')
        if not isinstance(self.variables, str):
            raise RouterConfigError("PSX Variables path must be a string")
        self.filter_elevation = data.get('filter_elevation', True)
        self.filter_traffic = data.get('filter_traffic', True)
        self.filter_flight_controls = data.get('filter_flight_controls', True)


class _RouterConfigSharedinfo:  # pylint: disable=missing-class-docstring,too-few-public-methods
    def __init__(self, data):
        self.master = data.get('master', False)


class _RouterConfigFiltering:  # pylint: disable=missing-class-docstring,too-few-public-methods
    def __init__(self, data):
        self.tiller = data.get('tiller', False)
        self.tiller_smallest_movement = data.get('tiller_smallest_movement', 10)
        self.tiller_center = data.get('tiller_center', 25)
        if self.tiller_center < 2 * self.tiller_smallest_movement:
            raise RouterConfigError(
                "tiller_center too small in relation to tiller_smallest_movemeent")


class _RouterConfigPerformance:  # pylint: disable=missing-class-docstring,too-few-public-methods
    def __init__(self, data):
        self.write_buffer_warning = data.get('write_buffer_warning', 100000)
        if not isinstance(self.write_buffer_warning, int):
            raise RouterConfigError("performance write_buffer_warning must be an integer")

        self.queue_time_warning = data.get('queue_time_warning', 0.016)
        if not isinstance(self.queue_time_warning, float):
            raise RouterConfigError("performance queue_time_warning must be an float")

        self.total_delay_warning = data.get('total_delay_warning', 0.024)
        if not isinstance(self.total_delay_warning, float):
            raise RouterConfigError("performance total_delay_warning must be an float")

        self.monitor_delay_warning = data.get('monitor_delay_warning', 0.032)
        if not isinstance(self.monitor_delay_warning, float):
            raise RouterConfigError("performance monitor_delay_warning must be an float")

        self.frdp_rtt_warning = data.get('frdp_rtt_warning', 0.2)
        if not isinstance(self.frdp_rtt_warning, float):
            raise RouterConfigError("performance frdp_rtt_warning must be an float")
        self.inhibit_drain = data.get('inhibit_drain', False)


class _RouterConfigAccess:  # pylint: disable=missing-class-docstring,too-few-public-methods
    def __init__(self, data):
        self.display_name = data.get('display_name', None)
        self.display_name_source = 'access config'
        if self.display_name is None:
            raise RouterConfigError(f"An access rule must have a display_name: {data}")

        self.match_ipv4 = data.get('match_ipv4', None)
        self.is_frankenrouter = data.get('is_frankenrouter', None)
        self.match_password = data.get('match_password', None)
        self.level = data.get('level', None)

        # Sanity checks
        if self.match_ipv4 is None and self.match_password is None:
            raise RouterConfigError("An access rule must use password or match_ipv4")

        if self.match_ipv4 is not None:
            for network in self.match_ipv4:
                if network == 'ANY':
                    continue
                try:
                    ipaddress.ip_network(network)
                except ValueError as exc:
                    raise RouterConfigError(
                        f"Invalid IPv4 network in config file: {network}: {exc}") from exc
        if self.match_password is not None:
            if self.match_password == "":
                raise RouterConfigError(
                    "Empty password in config, remove line for no password access")
        if self.level is None:
            raise RouterConfigError(
                "There must be an access_level in the access config")
        if self.level not in ['full', 'observer', 'blocked']:
            raise RouterConfigError(
                f"Invalid access level {self.level}")


class _RouterConfigCheck:  # pylint: disable=missing-class-docstring,too-few-public-methods
    def __init__(self, data):
        self.checktype = data.get('type', None)
        if self.checktype not in ['is_frankenrouter', 'name_regexp']:
            raise RouterConfigError(f"Invalid checktype: {self.checktype}")
        self.regexp = data.get('regexp', None)
        if self.regexp is not None:
            try:
                re.compile(self.regexp)
            except (TypeError, re.error) as exc:
                raise RouterConfigError(
                    f"Invalid check regexp {self.regexp}: {exc}") from exc
        self.limit_min = data.get('limit_min', None)
        self.limit_max = data.get('limit_min', None)


class RouterConfigError(Exception):
    """Main Exception class."""


class RouterConfig():  # pylint: disable=too-many-instance-attributes,too-few-public-methods,too-many-branches
    """Implements the router configuration.

    - Start with sane defaults
    - Read a config file in TOML format and override the defaults
    - Override certain settings using command line arguments.
    """

    def __init__(self, config_file=None, config_data=None):  # pylint: disable=too-many-statements
        """Initialize the class."""
        self.logger = logging.getLogger(__name__)
        if config_file is not None and config_data is not None:
            raise RouterConfigError("Cannot use both config_file and config_data")
        if config_data is None:
            config_data = ""
        if config_file is not None:
            try:
                with open(config_file, 'r', encoding='utf-8') as tomlfile:
                    config_data = tomlfile.read()
            except FileNotFoundError as exc:
                raise RouterConfigError(f"Config file {config_file} not found") from exc
        try:
            config = tomllib.loads(config_data)
        except tomllib.TOMLDecodeError as exc:
            raise RouterConfigError(f"Invalid config file: {exc}") from exc

        self.identity = _RouterConfigIdentity(config.get('identity', {}))
        self.listen = _RouterConfigListen(config.get('listen', {}))
        self.log = _RouterConfigLog(config.get('log', {}))
        self.psx = _RouterConfigPsx(config.get('psx', {}))
        self.performance = _RouterConfigPerformance(config.get('performance', {}))
        self.sharedinfo = _RouterConfigSharedinfo(config.get('sharedinfo', {}))
        self.filtering = _RouterConfigFiltering(config.get('filtering', {}))

        # To handle the old upstream format, we check if we get a list
        # ([[upstream]]) or a dict ([upstream]).
        if 'upstream' not in config:
            # With no config, use some sensible defaults
            config['upstream'] = [{
                "default": True,
                "name": "Default upstream",
                "host": "127.0.0.1",
                "port": 10748,
            }]
        if isinstance(config['upstream'], dict):
            self.logger.warning("Config file %s using deprecated [upstream] section", config_file)
            data = config.get('upstream', {})
            data['default'] = True
            data['name'] = "Please update your config file"
            self.upstream = _RouterConfigUpstream(data)
            self.upstreams = [self.upstream]
        else:
            # Store all defined upstreams in self.upstreams, but also
            # store the default one in self.upstream.
            default_upstream = None
            self.upstreams = []
            for elem in config['upstream']:
                this_upstream = _RouterConfigUpstream(elem)
                if this_upstream.default:
                    if default_upstream is not None:
                        raise RouterConfigError(
                            f"More than one default upstream in {config_file}") from exc
                    default_upstream = copy.deepcopy(this_upstream)
                self.upstreams.append(this_upstream)
            self.upstream = default_upstream

        self.access = []
        if 'access' in config:
            for elem in config['access']:
                self.access.append(_RouterConfigAccess(elem))
        else:
            # Default to allow all clients to access router
            self.logger.info("No [[access]] section in config, allowing all clients to connect.")
            self.access.append(_RouterConfigAccess({
                'display_name': 'all clients allowed',
                'match_ipv4': ['ANY'],
                'level': 'full'
            }))
        self.check = []
        if 'check' in config:
            for elem in config['check']:
                self.check.append(_RouterConfigCheck(elem))

    def __str__(self):
        """Output human-readable version of the config data."""
        return f"""
identity.simulator = {self.identity.simulator}
identity.router = {self.identity.router}


"""


class TestToml(unittest.TestCase):
    """Basic test cases for the module."""

    good_data_1 = r"""
# Sample config data

[identity]
simulator = 'SampleSim'
router = 'somerouter1'

[listen]
host = false
port = 10747

[log]
traffic = true

[psx]
variables = 'C:\PSX\Variables.txt'

[[upstream]]
host = '127.0.0.1'
name = 'PSX main server'
default = true
port = 20747

[[upstream]]
host = '123.123.123.123'
name = 'test upstream 1'
port = 10748

[[access]]
display_name = 'CDUPAD'
match_ipv4 = [ '192.168.42.8/32' ]
level = 'full'

[[access]]
display_name = 'Any local client'
match_ipv4 = [ '127.0.0.1/32', '192.168.42.0/24' ]
level = 'full'

[[access]]
display_name = 'RemoteSim'
match_ipv4 = [ '123.123.123.123/32' ]
level = 'observer'

[[check]]
type = 'name_regexp'
regexp = '.*PSX .*'
limit_min = 5
limit_max = 5
comment = 'There should be exactly 5 PSX main clients connected'

[[check]]
type = 'name_regexp'
regexp = '.*BACARS.*'
limit_min = 1
limit_max = 1
comment = 'There should be exactly one BACARS'
"""

    bad_data_1 = r"""
I'm not TOML
"""

    def test_good_input(self):
        """A few tests with valid input data."""
        conf = RouterConfig(config_data=self.good_data_1)
        self.assertEqual(conf.identity.simulator, 'SampleSim')
        self.assertEqual(conf.identity.router, 'somerouter1')
        self.assertEqual(conf.listen.port, 10747)
        # self.assertEqual(conf.upstream.host, '127.0.0.1')
        self.assertEqual(conf.psx.variables, r'C:\PSX\Variables.txt')
        self.assertEqual(conf.performance.write_buffer_warning, 100000)
        self.assertEqual(conf.access[0].level, 'full')

    def test_file_input(self):
        """Test reading from one of the example files."""
        config_file = os.path.join(
            os.path.dirname(os.path.realpath(__file__)),
            '../config_examples/frankenrouter-frankensim.toml')
        conf = RouterConfig(config_file=config_file)
        self.assertEqual(conf.identity.simulator, 'FrankenSim')
        self.assertEqual(conf.identity.router, 'router1')
        # self.assertEqual(conf.upstream.host, '127.0.0.1')
        self.assertEqual(conf.psx.variables, r'C:\fs\PSX\Variables.txt')
        self.assertEqual(conf.access[1].display_name, 'Ventus')

    def test_bad_input(self):
        """A few tests with valid input data."""
        with self.assertRaises(RouterConfigError):
            RouterConfig(config_data=self.bad_data_1)


if __name__ == '__main__':
    unittest.main()
