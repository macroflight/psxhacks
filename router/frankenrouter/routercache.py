"""A variable cache for the router."""
import json
import logging
import time
import unittest


class RouterCacheException(Exception):
    """A custom exception."""


class RouterCache():  # pylint: disable=too-few-public-methods
    """A cache for PSX network variables."""

    def __init__(self, cache_file):
        """Initialize the instance."""
        self.logger = logging.getLogger(__name__)
        self.cache = {}
        self.cache_file = cache_file

    def read_from_file(self):
        """Read cached data from file, for use e.g before PSX main server started."""
        try:
            with open(self.cache_file, 'r', encoding='utf-8') as statefile:
                self.cache = json.load(statefile)
                if 'version' not in self.cache:
                    # Bad data, reject cache
                    self.logger.warning("Bad data in %s, starting with empty cache",
                                        self.cache_file)
                    self.cache = {}
                elif isinstance(self.cache['version'], str):
                    self.logger.warning("Cache file is old format, starting with empty cache")
                    self.cache = {}
        except (FileNotFoundError, json.decoder.JSONDecodeError):
            self.logger.warning(
                "Failed to load data from %s found, you might need to reconnect some clients",
                self.cache_file)
            self.cache = {}

    def write_to_file(self):
        """Write state cache from file."""
        if self.get_size() > 0:
            with open(self.cache_file, 'w', encoding='utf-8') as statefile:
                statefile.write(json.dumps(self.cache))
        else:
            self.logger.info(
                "Not writing empty cache to disk"
            )

    def get_size(self):
        """Return the number of keywords in the cache."""
        return len(self.cache)

    def has_keyword(self, keyword):
        """Return True if keyword in cache."""
        if keyword in self.cache:
            return True
        return False

    def get_value(self, keyword):
        """Return the value of the cached variable, or None if not in cache."""
        if keyword in self.cache:
            return self.cache[keyword]['value']
        raise RouterCacheException(
            f"get_cached_variable got request for uncached keyword {keyword}")

    def get_age(self, keyword):
        """Return the time in seconds since the keyword value was updated."""
        if keyword in self.cache:
            return time.perf_counter() - self.cache[keyword]['updated']
        raise RouterCacheException(
            f"get_cached_variable_age got request for uncached keyword {keyword}")

    def get_keywords(self):
        """Return a list of all keywords in the cache."""
        return self.cache.keys()

    def update(self, keyword, value, updated=None):
        """Update a variable in the cache.

        If updated is provided, use that timestamp, otherwise use the
        current time.

        Also does checking and conversion to make sure the cache
        contains the expected type for the variable.
        """
        if keyword[:2] in ['Qi', 'Qh']:
            try:
                value = int(value)
            except TypeError as exc:
                raise RouterCacheException("Wrong data type for {value}") from exc
        else:
            try:
                value = str(value)
            except TypeError as exc:
                raise RouterCacheException("Wrong data type for {value}") from exc

        if updated is None:
            updated = time.perf_counter()
        if keyword not in self.cache:
            self.cache[keyword] = {}
        self.cache[keyword]['value'] = value
        self.cache[keyword]['updated'] = float(updated)


class TestVariablesParser(unittest.TestCase):
    """Basic test cases for the module."""

    def test_basic_cache(self):
        """A few tests of the cache."""
        me = RouterCache("dummy.json")
        self.assertEqual(me.get_size(), 0)
        me.update("Qs123", 456)
        me.update("Qs128", "somestring")
        self.assertEqual(me.get_size(), 2)
        self.assertEqual(me.get_value("Qs128"), "somestring")
        with self.assertRaises(RouterCacheException):
            me.get_value("Qs999")


if __name__ == '__main__':
    unittest.main()
