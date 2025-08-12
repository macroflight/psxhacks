"""Frankrouter config class."""

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


class _RouterConfigListen:  # pylint: disable=missing-class-docstring,too-few-public-methods
    def __init__(self, data):
        self.port = data.get('port', 10748)
        if not isinstance(self.port, int):
            raise RouterConfigError("The listen port must be an integer")

        self.rest_api_port = data.get('rest_api_port', None)
        if not isinstance(self.port, int):
            raise RouterConfigError("The API port must be an integer")


class _RouterConfigUpstream:  # pylint: disable=missing-class-docstring,too-few-public-methods
    def __init__(self, data):
        self.interactive = data.get('interactive', False)

        self.host = data.get('host', '127.0.0.1')

        self.port = data.get('port', 10747)
        if not isinstance(self.port, int):
            raise RouterConfigError("The upstream port must be an integer")

        self.password = data.get('password', None)


class _RouterConfigLog:  # pylint: disable=missing-class-docstring,too-few-public-methods
    def __init__(self, data):
        self.traffic = data.get('traffic', False)
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

        self.frdp_rtt_warning = data.get('frdp_rtt_warning', 0.1)
        if not isinstance(self.frdp_rtt_warning, float):
            raise RouterConfigError("performance frdp_rtt_warning must be an float")


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
                "Invalid access level {self.level}")


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


class RouterConfig():  # pylint: disable=too-many-instance-attributes,too-few-public-methods
    """Implements the router configuration.

    - Start with sane defaults
    - Read a config file in TOML format and override the defaults
    - Override certain settings using command line arguments.
    """

    def __init__(self, config_file=None, config_data=None):
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
        self.upstream = _RouterConfigUpstream(config.get('upstream', {}))
        self.log = _RouterConfigLog(config.get('log', {}))
        self.psx = _RouterConfigPsx(config.get('psx', {}))
        self.performance = _RouterConfigPerformance(config.get('performance', {}))
        self.access = []
        if 'access' in config:
            for elem in config['access']:
                self.access.append(_RouterConfigAccess(elem))
        else:
            # Default to allow all clients to access router
            self.logger.info("No [[access]] section in config, allowing all clients to connect.")
            self.access.append(_RouterConfigAccess({
                'display_name': 'all clients allowed',
                'match_ipv4': [ 'ANY' ],
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

[upstream]
host = '127.0.0.1'
port = 20747

[log]
traffic = true

[psx]
variables = 'C:\PSX\Variables.txt'

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
        self.assertEqual(conf.upstream.host, '127.0.0.1')
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
        self.assertEqual(conf.upstream.host, '127.0.0.1')
        self.assertEqual(conf.psx.variables, r'C:\fs\PSX\Variables.txt')
        self.assertEqual(conf.access[1].display_name, 'Ventus')

    def test_bad_input(self):
        """A few tests with valid input data."""
        with self.assertRaises(RouterConfigError):
            RouterConfig(config_data=self.bad_data_1)


if __name__ == '__main__':
    unittest.main()
