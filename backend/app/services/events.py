"""In-process event bus feeding the /api/events SSE stream.

publish() is safe from any thread (media workers, APScheduler jobs, request
handlers). Each SSE client subscribes with its own asyncio.Queue; events cross
from publishing threads into the event loop via call_soon_threadsafe.

A small ring buffer lets a reconnecting client (EventSource sends Last-Event-ID)
replay what it missed; if the gap is older than the buffer it gets a "resync"
event and should refetch its snapshot.
"""
import asyncio
import itertools
import json
import threading
import time
from collections import deque
from dataclasses import dataclass

BUFFER_SIZE = 500
PROGRESS_MIN_INTERVAL = 1.0  # seconds between progress events for the same key
SUBSCRIBER_QUEUE_MAX = 1000


@dataclass(frozen=True)
class Event:
    id: int
    type: str
    data: dict

    def encode(self) -> str:
        return f"id: {self.id}\nevent: {self.type}\ndata: {json.dumps(self.data, default=str)}\n\n"


class _Subscriber:
    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_MAX)

    def offer(self, event: Event) -> None:
        def _put():
            try:
                self.queue.put_nowait(event)
            except asyncio.QueueFull:
                pass  # a stalled client misses events; it resyncs on reconnect
        try:
            self.loop.call_soon_threadsafe(_put)
        except RuntimeError:
            pass  # loop closed — subscriber is gone


class EventBus:
    def __init__(self, buffer_size: int = BUFFER_SIZE):
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._buffer: deque[Event] = deque(maxlen=buffer_size)
        self._subscribers: set[_Subscriber] = set()
        self._last_progress: dict[str, float] = {}

    # ── publishing ────────────────────────────────────────────────────────────
    def publish(self, type_: str, data: dict) -> Event:
        with self._lock:
            event = Event(next(self._ids), type_, data)
            self._buffer.append(event)
            subscribers = list(self._subscribers)
        for sub in subscribers:
            sub.offer(event)
        return event

    def ready(self, key: str) -> bool:
        """Throttle gate for progress events: True at most once per PROGRESS_MIN_INTERVAL per key."""
        now = time.monotonic()
        with self._lock:
            if now - self._last_progress.get(key, float("-inf")) < PROGRESS_MIN_INTERVAL:
                return False
            if len(self._last_progress) > 5000:  # keys are per job/torrent; keep it bounded
                self._last_progress.clear()
            self._last_progress[key] = now
            return True

    def publish_progress(self, key: str, data: dict) -> Event | None:
        """Publish a transient progress event, throttled per key."""
        if not self.ready(key):
            return None
        return self.publish("progress", {"key": key, **data})

    # ── subscribing ───────────────────────────────────────────────────────────
    def subscribe(self, last_event_id: int | None = None) -> tuple[_Subscriber, list[Event]]:
        """Register a subscriber on the running loop; returns it plus any events to replay."""
        sub = _Subscriber(asyncio.get_running_loop())
        with self._lock:
            self._subscribers.add(sub)
            backlog: list[Event] = []
            if last_event_id is not None:
                oldest = self._buffer[0].id if self._buffer else None
                if oldest is not None and last_event_id < oldest - 1:
                    backlog = [Event(0, "resync", {})]
                else:
                    backlog = [e for e in self._buffer if e.id > last_event_id]
        return sub, backlog

    def unsubscribe(self, sub: _Subscriber) -> None:
        with self._lock:
            self._subscribers.discard(sub)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


bus = EventBus()
publish = bus.publish
publish_progress = bus.publish_progress
