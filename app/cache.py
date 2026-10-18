"""Small process-local cache for public TMDB metadata."""

from threading import Lock
from time import monotonic
from collections import OrderedDict


class TTLCache:
    def __init__(self, max_entries=512):
        self._items = OrderedDict()
        self._max_entries = max_entries
        self._lock = Lock()

    def get(self, key):
        item = self.lookup(key)
        return item[0] if item is not None and not item[1] else None

    def lookup(self, key):
        """Return (value, expired); retain expired entries for labelled fallback."""
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires_at, value = item
            self._items.move_to_end(key)
            return value, monotonic() >= expires_at

    def set(self, key, value, ttl_seconds):
        with self._lock:
            self._items[key] = (monotonic() + ttl_seconds, value)
            self._items.move_to_end(key)
            while len(self._items) > self._max_entries:
                self._items.popitem(last=False)
