"""Live download progress for the Activity page.

While at least one SSE client is connected and something is downloading, poll
qBittorrent every few seconds (one API call for all torrents) and publish
progress events. When a torrent reaches a complete state, run the existing
status sync straight away instead of waiting for the next watchlist scan, so
completion shows up within seconds.
"""
import logging
import threading

from ..database import SessionLocal
from ..models import Download
from . import events, journeys
from .qbit_states import QBITTORRENT_COMPLETE_STATES

logger = logging.getLogger(__name__)

INTERVAL_SEC = 3.0

_stop = threading.Event()
_thread: threading.Thread | None = None


def poll_once(session_factory=SessionLocal) -> int:
    """One polling pass. Returns the number of progress events published."""
    from .qbittorrent import get_all_torrent_statuses
    from .scheduler import _sync_download_statuses

    db = session_factory()
    try:
        active = (db.query(Download)
                  .filter(Download.status.in_(["adding", "downloading"]),
                          Download.torrent_hash.isnot(None))
                  .all())
        if not active:
            return 0
        statuses = get_all_torrent_statuses()
        if statuses is None:
            return 0  # qBittorrent unreachable; the sync jobs report that
        published = 0
        needs_sync = False
        for dl in active:
            qs = statuses.get(dl.torrent_hash.lower())
            if qs is None or qs.get("status") in QBITTORRENT_COMPLETE_STATES:
                # Finished (or already removed from qBittorrent). Error states are left
                # to the 10-minute download-events sweep, which owns retries and alerts.
                needs_sync = needs_sync or dl.status == "downloading"  # what the sync handles
                journeys.set_live(dl.id, None)
                continue
            live = {"progress": qs.get("progress") or 0.0, "dlspeed": qs.get("dlspeed"),
                    "eta": qs.get("eta"), "state": qs.get("status")}
            journeys.set_live(dl.id, live)
            j = journeys.for_download(db, dl)
            db.commit()
            if events.publish_progress(f"download:{dl.id}", {
                "journey_id": j.id, "download_id": dl.id,
                "progress": round(live["progress"] * 100, 1),
                "speed": live["dlspeed"], "eta": live["eta"],
            }):
                published += 1
    finally:
        db.close()
    if needs_sync:
        # Same function the watchlist scan runs; its Download writes refresh the
        # journeys through the session hooks.
        _sync_download_statuses()
    return published


def _loop() -> None:
    while not _stop.wait(INTERVAL_SEC):
        if events.bus.subscriber_count == 0:
            continue
        try:
            poll_once()
        except Exception:
            logger.exception("Download poll failed")


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="download-poller", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
