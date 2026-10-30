"""Event bus and the /api/events SSE stream."""
import asyncio
import threading
from unittest.mock import patch

import pytest

from app.services.events import EventBus


def _run(coro):
    return asyncio.run(coro)


def test_publish_from_threads_reaches_every_subscriber():
    bus = EventBus()

    async def main():
        a, _ = bus.subscribe()
        b, _ = bus.subscribe()
        threads = [threading.Thread(target=bus.publish, args=("t", {"n": n})) for n in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        got_a = [(await asyncio.wait_for(a.queue.get(), 1)).data["n"] for _ in range(20)]
        got_b = [(await asyncio.wait_for(b.queue.get(), 1)).data["n"] for _ in range(20)]
        assert sorted(got_a) == sorted(got_b) == list(range(20))
        bus.unsubscribe(a)
        assert bus.subscriber_count == 1

    _run(main())


def test_replay_after_last_event_id_and_resync_when_too_old():
    bus = EventBus(buffer_size=5)
    for n in range(8):
        bus.publish("t", {"n": n})  # ids 1..8, buffer holds 4..8

    async def main():
        _, backlog = bus.subscribe(last_event_id=6)
        assert [e.id for e in backlog] == [7, 8]
        _, backlog = bus.subscribe(last_event_id=3)  # 4 is the oldest kept: no gap
        assert [e.id for e in backlog] == [4, 5, 6, 7, 8]
        _, backlog = bus.subscribe(last_event_id=1)  # 2..3 are gone
        assert [e.type for e in backlog] == ["resync"]

    _run(main())


def test_progress_is_throttled_per_key():
    bus = EventBus()
    assert bus.publish_progress("job:1", {"p": 1}) is not None
    assert bus.publish_progress("job:1", {"p": 2}) is None
    assert bus.publish_progress("job:2", {"p": 1}) is not None
    with patch("app.services.events.time.monotonic", return_value=10**9):
        assert bus.publish_progress("job:1", {"p": 3}) is not None


def test_encode_is_valid_sse():
    e = EventBus().publish("journey.updated", {"id": 1})
    assert e.encode() == 'id: 1\nevent: journey.updated\ndata: {"id": 1}\n\n'


class _FakeRequest:
    async def is_disconnected(self):
        return False


@pytest.fixture
def router_bus(monkeypatch):
    """The real events endpoint wired to a fresh bus and no database.

    The endpoint is driven directly: httpx's ASGITransport buffers whole bodies,
    which never finishes for an endless SSE stream.
    """
    from app.routers import events as events_router

    bus = EventBus()
    monkeypatch.setattr(events_router, "bus", bus)
    monkeypatch.setattr(events_router.journeys, "stats", lambda db: {"active": 0})
    monkeypatch.setattr(events_router, "SessionLocal", lambda: type("S", (), {"close": lambda s: None})())
    return events_router, bus


async def _chunks(resp, n):
    out = []
    it = resp.body_iterator
    for _ in range(n):
        out.append(await asyncio.wait_for(it.__anext__(), 2))
    await it.aclose()
    return out


def test_stream_sends_stats_then_published_events(router_bus):
    router, bus = router_bus

    async def main():
        resp = await router.stream(_FakeRequest(), last_event_id=None)
        assert resp.media_type == "text/event-stream"
        assert resp.headers["cache-control"] == "no-cache"
        it = resp.body_iterator
        assert await it.__anext__() == "retry: 3000\n\n"
        assert (await it.__anext__()).startswith("event: stats\n")
        bus.publish("journey.updated", {"id": 7})
        assert await asyncio.wait_for(it.__anext__(), 2) == \
            'id: 1\nevent: journey.updated\ndata: {"id": 7}\n\n'
        await it.aclose()
        assert bus.subscriber_count == 0  # unsubscribed on disconnect

    _run(main())


def test_stream_replays_from_last_event_id(router_bus):
    router, bus = router_bus
    for n in range(3):
        bus.publish("t", {"n": n})

    async def main():
        resp = await router.stream(_FakeRequest(), last_event_id="1")
        chunks = await _chunks(resp, 3)
        assert chunks[0] == "retry: 3000\n\n"
        assert [c.split("\n")[0] for c in chunks[1:]] == ["id: 2", "id: 3"]

    _run(main())


def test_stream_sends_keepalive_when_idle(router_bus, monkeypatch):
    router, _ = router_bus
    monkeypatch.setattr(router, "KEEPALIVE_SEC", 0.05)

    async def main():
        resp = await router.stream(_FakeRequest(), last_event_id=None)
        assert (await _chunks(resp, 3))[2] == ": keepalive\n\n"

    _run(main())
