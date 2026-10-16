"""The fourteen product modules exposed over HTTP.

The import surface is centralised here so the API layer never reaches into an
individual module implementation, and so the set of available modules has
exactly one declaration (``registry.MODULES``).
"""

from .registry import MODULE_INDEX, MODULES, run_module
from .watchlists import WatchlistStore

__all__ = ["MODULE_INDEX", "MODULES", "WatchlistStore", "run_module"]
