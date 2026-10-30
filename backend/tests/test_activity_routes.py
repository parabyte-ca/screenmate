"""/api/activity: list/filter, detail, cancel, retry, remove, clear-finished."""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.database import Base, get_db
from app.models import Download, Journey, MediaJob, Setting, WatchlistItem
from app.services import journeys
from app.services.media import store


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    monkeypatch.setattr(store, "engine", engine)
    journeys.install_download_hooks()
    Session = sessionmaker(bind=engine)

    from app.main import app

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    yield TestClient(app), Session
    app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def _dl(Session, **kw):
    with Session() as db:
        d = Download(title=kw.pop("title", "Show S01E01"), torrent_hash=kw.pop("torrent_hash", "h1"),
                     status=kw.pop("status", "downloading"), **kw)
        db.add(d)
        db.commit()
        return d.id, db.query(Journey).filter(Journey.download_id == d.id).one().id


def _media_job(status="error", job_type="encode"):
    fid = store.upsert_file(f"/m/{job_type}-{status}.mkv", "x.mkv", 1, 1.0, "h264", False)
    job = store.create_job(fid, job_type)
    if status != "queued":
        store.dequeue_next_job()
        store.set_job_status(job, status)
        store.set_file_status(fid, "error" if status == "error" else "pending")
    with store.connect() as conn:
        jid = conn.execute("SELECT journey_id FROM media_jobs WHERE id=:i", {"i": job}).scalar()
    return fid, job, jid


def test_list_filters_and_total(ctx):
    client, Session = ctx
    _dl(Session, title="a")
    _dl(Session, title="b", status="failed", torrent_hash="h2")
    _dl(Session, title="c", status="completed", torrent_hash="h3")
    assert client.get("/api/activity").json()["total"] == 3
    assert [i["title"] for i in client.get("/api/activity?filter=active").json()["items"]] == ["a"]
    assert [i["title"] for i in client.get("/api/activity?filter=failed").json()["items"]] == ["b"]
    assert [i["title"] for i in client.get("/api/activity?filter=done_today").json()["items"]] == ["c"]
    page = client.get("/api/activity?limit=1&offset=1").json()
    assert page["total"] == 3 and len(page["items"]) == 1
    assert client.get("/api/activity?filter=bogus").status_code == 400


def test_detail_and_stats(ctx):
    client, Session = ctx
    _, jid = _dl(Session)
    body = client.get(f"/api/activity/{jid}").json()
    assert [s["kind"] for s in body["steps"]] == ["found", "download"]
    assert client.get("/api/activity/999").status_code == 404
    assert client.get("/api/activity/stats").json()["active"] == 1


def test_cancel_download_removes_torrent_and_data(ctx):
    client, Session = ctx
    dl_id, jid = _dl(Session)
    with patch("app.services.qbittorrent.remove_torrent") as rm:
        r = client.post(f"/api/activity/{jid}/cancel")
    assert r.json()["ok"]
    rm.assert_called_once_with("h1", delete_files=True)
    with Session() as db:
        assert db.get(Download, dl_id).status == "cancelled"
        assert db.get(Journey, jid).status == "cancelled"
    assert client.post(f"/api/activity/{jid}/cancel").status_code == 409


def test_retry_manual_download_readds_magnet(ctx):
    client, Session = ctx
    dl_id, jid = _dl(Session, status="failed", magnet_link="magnet:?xt=urn:btih:abc",
                     download_path="/dl")
    with patch("app.services.qbittorrent.add_torrent", return_value="newhash") as add:
        r = client.post(f"/api/activity/{jid}/retry")
    assert r.json() == {"ok": True, "msg": "Download restarted"}
    assert add.call_args.args[:2] == ("magnet:?xt=urn:btih:abc", "/dl")
    with Session() as db:
        dl = db.get(Download, dl_id)
        assert (dl.status, dl.torrent_hash, dl.retry_count) == ("downloading", "newhash", 1)
        assert db.get(Journey, jid).status == "active"


def test_retry_watchlist_download_uses_retry_download(ctx):
    client, Session = ctx
    with Session() as db:
        item = WatchlistItem(title="Show", search_query="show")
        db.add(item)
        db.commit()
        item_id = item.id
    _, jid = _dl(Session, status="failed", watchlist_id=item_id)
    with patch("app.services.download_events.retry_download", return_value="Retrying with: X") as rd:
        r = client.post(f"/api/activity/{jid}/retry")
    assert r.json() == {"ok": True, "msg": "Retrying with: X"}
    assert rd.call_args.args[2]["retry_enabled"] == "true"


def test_retry_refuses_running_step(ctx):
    client, Session = ctx
    _, jid = _dl(Session)
    assert client.post(f"/api/activity/{jid}/retry").status_code == 409


def test_media_step_cancel_and_retry(ctx):
    client, _ = ctx
    fid, job, jid = _media_job(status="queued")
    assert client.post(f"/api/activity/{jid}/cancel").json()["ok"]
    assert store.get_job(job)["status"] == "cancelled"
    r = client.post(f"/api/activity/{jid}/retry").json()
    assert r["ok"] and r["job_id"] != job
    new = store.get_job(r["job_id"])
    assert (new["status"], new["journey_id"], new["job_type"]) == ("queued", jid, "encode")
    assert client.post(f"/api/activity/{jid}/retry").status_code == 409  # now pending


def test_remove_and_clear_finished(ctx):
    client, Session = ctx
    _, active = _dl(Session, title="a")
    dl_done, done = _dl(Session, title="b", status="completed", torrent_hash="h2")
    _, _, media_done = _media_job(status="done")
    assert client.delete(f"/api/activity/{active}").status_code == 409
    assert client.delete(f"/api/activity/{done}").json()["ok"]
    with Session() as db:
        assert db.get(Download, dl_done) is None
    assert client.post("/api/activity/clear-finished").json() == {"ok": True, "cleared": 1}
    with Session() as db:
        assert [j.id for j in db.query(Journey)] == [active]
        assert db.query(MediaJob).count() == 0
        # Nothing for backfill to resurrect.
        assert journeys.backfill(db) == 0
