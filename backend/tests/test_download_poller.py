"""Live download poller: progress events, instant completion sync, idle when unwatched."""
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.database import Base
from app.models import Download
from app.services import download_poller, events, journeys


@pytest.fixture
def Session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'p.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    journeys.install_download_hooks()
    events.bus._last_progress.clear()
    yield sessionmaker(bind=engine)
    engine.dispose()


def _add(Session, **kw):
    with Session() as db:
        d = Download(title=kw.pop("title", "x"), status=kw.pop("status", "downloading"), **kw)
        db.add(d)
        db.commit()
        return d.id


def test_publishes_progress_for_active_downloads(Session):
    dl = _add(Session, torrent_hash="AAA")
    qbit = {"aaa": {"status": "downloading", "progress": 0.25, "dlspeed": 500, "eta": 60}}
    seen = []
    with patch("app.services.qbittorrent.get_all_torrent_statuses", return_value=qbit), \
         patch.object(events.bus, "publish", side_effect=lambda t, d: seen.append((t, d)) or d), \
         patch("app.services.scheduler._sync_download_statuses") as sync:
        assert download_poller.poll_once(Session) == 1
        assert download_poller.poll_once(Session) == 0  # throttled
    assert seen[-1][0] == "progress"
    assert seen[-1][1]["progress"] == 25.0
    assert seen[-1][1]["download_id"] == dl and seen[-1][1]["journey_id"]
    sync.assert_not_called()
    with Session() as db:
        step = journeys.snapshot(db, journeys.for_download(db, db.get(Download, dl)))["steps"][1]
        assert (step["progress"], step["speed"], step["eta"]) == (25.0, 500, 60)
    journeys.set_live(dl, None)


@pytest.mark.parametrize("qbit", [
    {"aaa": {"status": "uploading", "progress": 1.0}},  # finished, seeding
    {},                                                 # already removed from qBittorrent
])
def test_completion_triggers_immediate_sync(Session, qbit):
    _add(Session, torrent_hash="aaa")
    with patch("app.services.qbittorrent.get_all_torrent_statuses", return_value=qbit), \
         patch("app.services.scheduler._sync_download_statuses") as sync:
        download_poller.poll_once(Session)
    sync.assert_called_once()


def test_error_state_left_to_the_events_sweep(Session):
    _add(Session, torrent_hash="aaa")
    qbit = {"aaa": {"status": "error", "progress": 0.1}}
    with patch("app.services.qbittorrent.get_all_torrent_statuses", return_value=qbit), \
         patch("app.services.scheduler._sync_download_statuses") as sync:
        download_poller.poll_once(Session)
    sync.assert_not_called()


def test_nothing_active_or_qbit_down_is_a_noop(Session):
    with patch("app.services.qbittorrent.get_all_torrent_statuses") as q:
        assert download_poller.poll_once(Session) == 0
        q.assert_not_called()
    _add(Session, torrent_hash="aaa")
    with patch("app.services.qbittorrent.get_all_torrent_statuses", return_value=None), \
         patch("app.services.scheduler._sync_download_statuses") as sync:
        assert download_poller.poll_once(Session) == 0
    sync.assert_not_called()
