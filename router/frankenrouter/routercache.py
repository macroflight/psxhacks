"""A variable cache for the router."""
import time
import unittest


class RouterCacheException(Exception):
    """A custom exception."""


class RouterCacheTypeError(Exception):
    """A custom exception."""


class RouterCache():  # pylint: disable=too-few-public-methods
    """An in-memory cache for PSX network variables.

    Lives only for the lifetime of the router process; never persisted
    to or loaded from disk.
    """

    def __init__(self):
        """Initialize the instance."""
        self.cache = {}

    def get_size(self):
        """Return the number of keywords in the cache."""
        return len(self.cache)

    def has_keyword(self, keyword):
        """Return True if keyword in cache."""
        if keyword in self.cache:
            return True
        return False

    def get_value(self, keyword):
        """Return the value of the cached variable, or raise exception if not in cache."""
        if keyword in self.cache:
            return self.cache[keyword]['value']
        raise RouterCacheException(
            f"get_cached_variable got request for uncached keyword {keyword}")

    def get_age(self, keyword):
        """Return the time in seconds since the keyword value was updated.

        If variable not in cache return a very old age.
        """
        if keyword in self.cache:
            return time.perf_counter() - self.cache[keyword]['updated']
        return float(365 * 24 * 3600)

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
            except (TypeError, ValueError) as exc:
                raise RouterCacheTypeError(f"Wrong data type for {keyword}={value}") from exc
        else:
            try:
                value = str(value)
            except (TypeError, ValueError) as exc:
                raise RouterCacheTypeError(f"Wrong data type for  {keyword}={value}") from exc

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
        me = RouterCache()
        self.assertEqual(me.get_size(), 0)
        me.update("Qs123", 456)
        me.update("Qs128", "somestring")
        self.assertEqual(me.get_size(), 2)
        self.assertEqual(me.get_value("Qs128"), "somestring")
        with self.assertRaises(RouterCacheException):
            me.get_value("Qs999")


if __name__ == '__main__':
    unittest.main()
