"""GET /api/events — the live Server-Sent Events stream (see services/events.py)."""
import asyncio
import json

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse

from ..database import SessionLocal
from ..services import journeys
from ..services.events import bus

router = APIRouter(tags=["events"])

KEEPALIVE_SEC = 15  # Cloudflare drops idle connections after ~100 s


@router.get("/events")
async def stream(request: Request, last_event_id: str | None = Header(default=None)):
    try:
        resume_from = int(last_event_id) if last_event_id else None
    except ValueError:
        resume_from = None
    sub, backlog = bus.subscribe(resume_from)

    def _initial_stats() -> dict:
        db = SessionLocal()
        try:
            return journeys.stats(db)
        finally:
            db.close()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            for event in backlog:
                yield event.encode()
            if resume_from is None:
                try:
                    stats = await asyncio.to_thread(_initial_stats)
                    yield f"event: stats\ndata: {json.dumps(stats)}\n\n"
                except Exception:
                    pass
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(sub.queue.get(), timeout=KEEPALIVE_SEC)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield event.encode()
        finally:
            bus.unsubscribe(sub)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",  # disable proxy buffering (nginx etc.)
    })
