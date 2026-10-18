"""Bounded, process-local rolling rate limits for an optional public demo."""
from collections import OrderedDict, deque
from math import ceil
from threading import Lock
from time import monotonic


class RateLimiter:
    def __init__(self, *, max_clients=4096, clock=monotonic):
        self._clients = OrderedDict()
        self._lock = Lock()
        self._max_clients = max_clients
        self._clock = clock

    def check(self, address, category, limit):
        """Return retry seconds if limited; otherwise record an accepted request."""
        now = self._clock()
        key = (address, category)
        with self._lock:
            # Remove inactive addresses before applying the memory ceiling.
            while self._clients:
                oldest = next(iter(self._clients))
                if self._clients[oldest][-1] > now - 60:
                    break
                self._clients.popitem(last=False)
            events = self._clients.setdefault(key, deque())
            while events and events[0] <= now - 60:
                events.popleft()
            if len(events) >= limit:
                return max(1, ceil(events[0] + 60 - now))
            events.append(now)
            self._clients.move_to_end(key)
            while len(self._clients) > self._max_clients:
                self._clients.popitem(last=False)
            return None
