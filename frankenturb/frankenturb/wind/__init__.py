"""Wind profile fetching and interpolation for frankenturb."""
from .profile import WindProfile, make_fixed_wind_profile
from .fetcher import WindFetcher

__all__ = ["WindProfile", "WindFetcher", "make_fixed_wind_profile"]
