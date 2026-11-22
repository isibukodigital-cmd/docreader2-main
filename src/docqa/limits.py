"""Small framework-free helpers: TTL cache, sliding-window rate limiter, zip-bomb check."""
from __future__ import annotations

import io
import threading
import time
import zipfile
from collections import defaultdict, deque

MAX_DOCX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_DOCX_ENTRIES = 5000


class TTLSet:
    """Remembers keys for ttl seconds. Used to skip repeat membership registration."""

    def __init__(self, ttl: float = 60.0, max_size: int = 10_000, clock=time.monotonic):
        self.ttl, self.max_size, self.clock = ttl, max_size, clock
        self._items: dict = {}
        self._lock = threading.Lock()

    def __contains__(self, key) -> bool:
        with self._lock:
            expires = self._items.get(key)
            if expires is None:
                return False
            if expires <= self.clock():
                del self._items[key]
                return False
            return True

    def add(self, key) -> None:
        with self._lock:
            now = self.clock()
            if len(self._items) >= self.max_size:
                self._items = {k: v for k, v in self._items.items() if v > now}
                if len(self._items) >= self.max_size:
                    self._items.clear()
            self._items[key] = now + self.ttl


class RateLimiter:
    """Allow at most `limit` events per `window` seconds per key (in-memory, per process)."""

    def __init__(
        self,
        limit: int,
        window: float = 60.0,
        clock=time.monotonic,
        max_keys: int = 50_000,
    ):
        self.limit, self.window, self.clock = limit, window, clock
        if max_keys < 1:
            raise ValueError("max_keys must be at least 1")
        self.max_keys = max_keys
        self._events: dict = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key) -> bool:
        if self.limit <= 0:
            return True
        with self._lock:
            now = self.clock()
            q = self._events[key]
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self._events) > self.max_keys:
                active = {}
                for existing_key, events in self._events.items():
                    while events and events[0] <= now - self.window:
                        events.popleft()
                    if events:
                        active[existing_key] = events
                if len(active) > self.max_keys:
                    for oldest_key in list(active)[: len(active) - self.max_keys]:
                        del active[oldest_key]
                self._events = defaultdict(deque, active)
            return True


def check_docx_safe(content: bytes) -> None:
    """Reject DOCX files whose declared uncompressed size is absurd (zip bombs). ValueError."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            infos = zf.infolist()
    except zipfile.BadZipFile as exc:
        raise ValueError("not a valid DOCX archive") from exc
    if len(infos) > MAX_DOCX_ENTRIES:
        raise ValueError("DOCX contains too many parts")
    if sum(i.file_size for i in infos) > MAX_DOCX_UNCOMPRESSED_BYTES:
        raise ValueError("DOCX expands to an unreasonable size")
