"""Auto pipeline: rule matching, path mapping, plans, chaining, end-to-end with ffmpeg."""
import json
import os
import shutil
import subprocess
import threading
from collections import namedtuple
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.database import Base, get_db
from app.models import Download, Journey, PathMapping, PipelinePlan, PipelineRule, Setting
from app.services import journeys, pipeline
from app.services.media import config, store, worker

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
ENCODE = {"name": "Shrink", "match": {"codecs": ["h264"]}, "actions": [{"type": "encode"}]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'p.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    monkeypatch.setattr(store, "engine", engine)
    monkeypatch.setattr(journeys, "PIPELINE_SYNC", True)
    for attr, _ in config.SETTINGS_MAP.values():
        monkeypatch.setattr(config, attr, getattr(config, attr))
    monkeypatch.setattr(config, "ENCODER_BACKEND", "cpu")
    monkeypatch.setattr(config, "X265_PRESET", "ultrafast")
    monkeypatch.setattr(config, "TPDB_API_KEY", "")
    journeys.install_download_hooks()
    Session = sessionmaker(bind=engine)
    yield engine, Session, tmp_path


def _rule(Session, name="Shrink", match=None, actions=None, enabled=True, priority=10):
    with Session() as db:
        r = PipelineRule(name=name, match=json.dumps(match or {"codecs": ["h264"]}),
                         actions=json.dumps(actions or [{"type": "encode"}]),
                         enabled=enabled, priority=priority)
        db.add(r)
        db.commit()
        return r.id


def _clip(path, seconds=4):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", f"testsrc=size=320x240:rate=25:duration={seconds}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)], check=True)


def _run_queued_jobs():
    """Drive the worker synchronously until the queue is empty."""
    while (job := store.dequeue_next_job()) is not None:
        worker._run_job(dict(job), threading.Event())


# ── Pure logic ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("actions,err", [
    ([], "at least one"),
    ([{"type": "explode"}], "one of"),
    ([{"type": "encode"}, {"type": "encode"}], "once"),
    ([{"type": "split"}, {"type": "encode"}], "last"),
    ([{"type": "encode", "resolution": "999p"}], "resolution"),
])
def test_validate_rejects_bad_rules(actions, err):
    assert err in pipeline.validate({}, actions)


def test_validate_accepts_chain():
    assert pipeline.validate({"source": "download"},
                             [{"type": "trim"}, {"type": "encode", "resolution": "720p"}, {"type": "split"}]) is None


def test_matches_each_condition():
    info = {"codec": "h264", "height": 2160, "size_bytes": 5_000_000_000, "duration_sec": 7200,
            "filename": "Best Of Vol 3.mkv", "path": "/media/tv/x.mkv", "is_anthology": 1}
    ok = lambda m, src="scan", wl=None: pipeline.matches({"match": m}, info, src, wl)
    assert ok({})
    assert ok({"codecs": ["H264"]}) and not ok({"codecs": ["hevc"]})
    assert ok({"min_height": 1500}) and not ok({"max_height": 1080})
    assert ok({"min_size_mb": 4000}) and not ok({"min_size_mb": 6000})
    assert ok({"min_duration_min": 90, "max_duration_min": 180}) and not ok({"max_duration_min": 60})
    assert ok({"keywords": ["vol"]}) and not ok({"keywords": ["compilation"]})
    assert ok({"anthology": True}) and not ok({"anthology": False})
    assert ok({"path_prefix": "/media/tv"}) and not ok({"path_prefix": "/media/movies"})
    assert ok({"source": "scan"}) and not ok({"source": "download"})
    assert ok({"watchlist_ids": [4]}, "download", 4) and not ok({"watchlist_ids": [4]}, "download", 5)


def test_first_enabled_match_wins():
    info = {"codec": "h264"}
    rules = [{"name": "off", "enabled": False, "match": {}, "actions": []},
             {"name": "hevc", "enabled": True, "match": {"codecs": ["hevc"]}, "actions": []},
             {"name": "a", "enabled": True, "match": {}, "actions": []},
             {"name": "b", "enabled": True, "match": {}, "actions": []}]
    assert pipeline.choose_rule(rules, info, "scan")["name"] == "a"


def test_map_path_uses_longest_prefix(env):
    _, Session, _ = env
    with Session() as db:
        db.add_all([PathMapping(remote_prefix="/downloads", local_prefix="/mnt/a"),
                    PathMapping(remote_prefix="/downloads/tv/", local_prefix="/mnt/tv")])
        db.commit()
        assert pipeline.map_path(db, "/downloads/tv/Show/ep.mkv") == "/mnt/tv/Show/ep.mkv"
        assert pipeline.map_path(db, "/downloads/movie.mkv") == "/mnt/a/movie.mkv"
        assert pipeline.map_path(db, "/downloadsX/movie.mkv") == "/downloadsX/movie.mkv"
        assert pipeline.map_path(db, "/elsewhere/x") == "/elsewhere/x"


def test_find_video_files_skips_samples_and_hidden(tmp_path):
    (tmp_path / "Show").mkdir()
    for name in ["Show/ep1.mkv", "Show/ep1.nfo", "Show/sample.mkv", "Show/ep2.MP4"]:
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "Show/.hidden").mkdir()
    (tmp_path / "Show/.hidden/x.mkv").write_bytes(b"x")
    found = [os.path.basename(f) for f in pipeline.find_video_files(str(tmp_path / "Show"))]
    assert found == ["ep1.mkv", "ep2.MP4"]
    assert pipeline.find_video_files(str(tmp_path / "Show/ep1.nfo")) == []


# ── Download flow ─────────────────────────────────────────────────────────────

def _download(Session, tmp_path, name="Show.S01E01.mkv", **kw):
    dl_dir = tmp_path / "dl"
    dl_dir.mkdir(exist_ok=True)
    with Session() as db:
        d = Download(title=name, torrent_hash=kw.pop("torrent_hash", "abc"), status="downloading",
                     download_path="/remote/dl", **kw)
        db.add(d)
        db.add(PathMapping(remote_prefix="/remote/dl", local_prefix=str(dl_dir)))
        db.commit()
        return d.id, dl_dir


def _complete(Session, dl_id, content="/remote/dl/Show.S01E01.mkv", remove_ok=True):
    with patch("app.services.qbittorrent.get_content_path", return_value=content), \
         patch("app.services.qbittorrent.remove_torrent",
               side_effect=None if remove_ok else RuntimeError("qbit down")) as rm, \
         Session() as db:
        db.get(Download, dl_id).status = "completed"
        db.commit()
    return rm


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_completed_download_runs_rule_end_to_end(env):
    _, Session, tmp = env
    _rule(Session)
    dl_id, dl_dir = _download(Session, tmp)
    _clip(dl_dir / "Show.S01E01.mkv")

    rm = _complete(Session, dl_id)
    rm.assert_called_once_with("abc", delete_files=False)  # torrent out of qBittorrent first, files kept

    with Session() as db:
        plan = db.query(PipelinePlan).one()
        assert (plan.status, plan.next_index, plan.rule_name) == ("running", 1, "Shrink")
        assert db.get(Download, dl_id).qbit_removed
    job = store.get_active_jobs()[0]
    assert (job["job_type"], job["reason"]) == ("encode", "Rule: Shrink")

    _run_queued_jobs()

    with Session() as db:
        plan = db.query(PipelinePlan).one()
        assert (plan.status, plan.library_status, plan.notify_status) == ("done", "skipped", "skipped")
        j = db.query(Journey).one()
        snap = journeys.snapshot(db, j)
        assert [(s["kind"], s["status"]) for s in snap["steps"]] == [
            ("found", "done"), ("download", "done"), ("encode", "done"),
            ("library", "skipped"), ("notify", "skipped")]
        assert snap["steps"][2]["reason"] == "Rule: Shrink"
        assert j.status == "done" and j.media_file_id == plan.file_id


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_steps_run_one_at_a_time_and_finalize_notifies(env):
    _, Session, tmp = env
    _rule(Session, actions=[{"type": "trim"}, {"type": "encode"}])
    dl_id, dl_dir = _download(Session, tmp)
    _clip(dl_dir / "Show.S01E01.mkv", seconds=8)
    with Session() as db:
        db.add_all([Setting(key="apprise_url", value="json://x"),
                    Setting(key="plex_enabled", value="true"),
                    Setting(key="plex_url", value="http://plex"), Setting(key="plex_token", value="t")])
        db.commit()
    _complete(Session, dl_id)

    assert [j["job_type"] for j in store.get_active_jobs()] == ["trim"]  # never two at once
    with Session() as db:
        kinds = [(s["kind"], s["status"]) for s in journeys.snapshot(db, db.query(Journey).one())["steps"]]
        assert kinds[2:] == [("trim", "pending"), ("encode", "pending"), ("library", "pending"), ("notify", "pending")]

    with patch("app.services.media_servers.trigger_media_refresh") as refresh, \
         patch("app.services.apprise_notify.notify") as notify:
        _run_queued_jobs()
    refresh.assert_called_once()
    assert "trim, encode done" in notify.call_args.args[2]
    with Session() as db:
        plan = db.query(PipelinePlan).one()
        assert (plan.status, plan.library_status, plan.notify_status) == ("done", "done", "done")


def test_unmapped_path_fails_with_hint_and_retry_recovers(env):
    _, Session, tmp = env
    _rule(Session)
    with Session() as db:
        d = Download(title="Show.S01E01.mkv", torrent_hash="abc", status="downloading",
                     download_path="/nowhere")
        db.add(d)
        db.commit()
        dl_id = d.id
    _complete(Session, dl_id, content="/nowhere/Show.S01E01.mkv")
    with Session() as db:
        plan = db.query(PipelinePlan).one()
        assert plan.status == "failed" and "path mapping" in plan.detail
        j = db.query(Journey).one()
        snap = journeys.snapshot(db, j)
        assert snap["steps"][-1]["kind"] == "pipeline" and j.status == "failed"
        assert pipeline.retry_plan(db.get_bind(), plan.id).startswith("Still nothing")


def test_no_matching_rule_leaves_download_alone(env):
    _, Session, tmp = env
    _rule(Session, match={"codecs": ["mpeg2video"]})
    dl_id, dl_dir = _download(Session, tmp)
    (dl_dir / "Show.S01E01.mkv").write_bytes(b"x")
    info = namedtuple("I", "codec width height duration_sec size_bytes")("h264", 320, 240, 4.0, 1)
    with patch("app.services.media.codec_detector.probe_with_error", return_value=(info, None)):
        _complete(Session, dl_id)
    with Session() as db:
        assert db.query(PipelinePlan).count() == 0
        assert db.query(Journey).one().status == "done"


def test_disabled_pipeline_or_no_rules_does_nothing(env):
    _, Session, tmp = env
    dl_id, _ = _download(Session, tmp)
    with patch("app.services.pipeline.find_video_files") as find:
        _complete(Session, dl_id)
        find.assert_not_called()  # no rules at all
    _rule(Session)
    dl2, _ = _download(Session, tmp, name="Other.mkv", torrent_hash="def")
    with Session() as db:
        db.add(Setting(key="pipeline_enabled", value="false"))
        db.commit()
    with patch("app.services.pipeline.find_video_files") as find:
        _complete(Session, dl2)
        find.assert_not_called()


def _planned_file(env, tmp, actions=None, status="running"):
    _, Session, _ = env
    path = tmp / "lib.mkv"
    path.write_bytes(b"x" * 1000)
    fid = store.upsert_file(str(path), "lib.mkv", 1000, 60.0, "h264", False)
    with Session() as db:
        j = journeys.for_media_file(db, fid)
        plan = PipelinePlan(journey_id=j.id, file_id=fid, rule_name="R",
                            actions=json.dumps(actions or [{"type": "encode"}]), status=status)
        db.add(plan)
        db.commit()
        return fid, plan.id, j.id


def test_not_enough_disk_space_fails_the_plan(env):
    _, Session, tmp = env
    fid, plan_id, _ = _planned_file(env, tmp)
    usage = namedtuple("usage", "total used free")(100, 90, 10)
    with patch("app.services.pipeline.shutil.disk_usage", return_value=usage):
        pipeline.advance(store.engine, plan_id)
    with Session() as db:
        plan = db.get(PipelinePlan, plan_id)
        assert plan.status == "failed" and "Not enough free space" in plan.detail
    assert store.get_active_job_for_file(fid) is None
    pipeline.retry_plan(store.engine, plan_id)  # space is fine now
    assert store.get_active_job_for_file(fid)["job_type"] == "encode"


def test_waits_for_seeding_when_auto_remove_is_off(env):
    _, Session, tmp = env
    fid, plan_id, jid = _planned_file(env, tmp, status="waiting")
    with Session() as db:
        d = Download(title="x", torrent_hash="abc", status="completed")
        db.add(d)
        db.add(Setting(key="pipeline_remove_torrent", value="false"))
        db.commit()
        db.get(PipelinePlan, plan_id).download_id = d.id
        db.commit()
        dl_id = d.id
    pipeline.tick(store.engine)
    with Session() as db:
        assert db.get(PipelinePlan, plan_id).status == "waiting"
        steps = journeys.snapshot(db, db.get(Journey, jid))["steps"]
        assert any(s["kind"] == "waiting" for s in steps)
        db.get(Download, dl_id).qbit_removed = True  # the cleanup job removed the torrent
        db.commit()
    pipeline.tick(store.engine)
    assert store.get_active_job_for_file(fid)["job_type"] == "encode"


def test_failed_job_stops_the_plan_and_job_retry_resumes_it(env):
    _, Session, tmp = env
    fid, plan_id, jid = _planned_file(env, tmp, actions=[{"type": "trim"}, {"type": "encode"}])
    pipeline.advance(store.engine, plan_id)
    job = store.get_active_job_for_file(fid)["id"]
    store.dequeue_next_job()
    store.set_job_status(job, "error")
    with Session() as db:
        assert db.get(PipelinePlan, plan_id).status == "failed"
        snap = journeys.snapshot(db, db.get(Journey, jid))
        assert [(s["kind"], s["status"]) for s in snap["steps"]] == [
            ("trim", "failed"), ("encode", "skipped"), ("library", "skipped"), ("notify", "skipped")]
        assert db.get(Journey, jid).status == "failed"
    new = store.create_job(fid, "trim", journey_id=jid)  # what Activity → Retry does
    store.dequeue_next_job()
    store.set_job_status(new, "done")
    assert store.get_active_job_for_file(fid)["job_type"] == "encode"


def test_cancel_plan_skips_remaining_steps(env):
    _, Session, tmp = env
    fid, plan_id, jid = _planned_file(env, tmp, actions=[{"type": "trim"}, {"type": "encode"}])
    pipeline.cancel_plan(store.engine, plan_id)
    pipeline.tick(store.engine)
    assert store.get_active_job_for_file(fid) is None
    with Session() as db:
        assert db.get(Journey, jid).status == "cancelled" or db.get(PipelinePlan, plan_id).status == "cancelled"


def test_scan_discoveries_only_when_enabled(env):
    _, Session, tmp = env
    _rule(Session)
    path = tmp / "new.mkv"
    path.write_bytes(b"x")
    fid = store.upsert_file(str(path), "new.mkv", 1000, 60.0, "h264", False)
    assert pipeline.on_scanned(store.engine, [fid]) == 0  # off by default
    with Session() as db:
        db.add(Setting(key="pipeline_on_scan", value="true"))
        db.commit()
    assert pipeline.on_scanned(store.engine, [fid]) == 1
    assert pipeline.on_scanned(store.engine, [fid]) == 0  # already planned


# ── API ───────────────────────────────────────────────────────────────────────

@pytest.fixture
def api(env):
    engine, Session, tmp = env
    from app.main import app

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    yield TestClient(app), Session, tmp
    app.dependency_overrides.pop(get_db, None)


def test_rules_crud_and_reorder(api):
    client, _, _ = api
    assert client.post("/api/pipeline/rules", json={"name": "", "actions": []}).status_code == 400
    assert client.post("/api/pipeline/rules", json={"name": "x", "actions": [{"type": "nope"}]}).status_code == 400
    a = client.post("/api/pipeline/rules", json=ENCODE).json()
    b = client.post("/api/pipeline/rules", json={**ENCODE, "name": "Second"}).json()
    assert (a["priority"], b["priority"]) == (10, 20)
    assert [r["name"] for r in client.post("/api/pipeline/rules/reorder",
                                           json={"ids": [b["id"], a["id"]]}).json()] == ["Second", "Shrink"]
    assert client.post("/api/pipeline/rules/reorder", json={"ids": [a["id"]]}).status_code == 400
    upd = client.put(f"/api/pipeline/rules/{a['id']}", json={**ENCODE, "enabled": False}).json()
    assert upd["enabled"] is False
    assert client.delete(f"/api/pipeline/rules/{a['id']}").json() == {"ok": True}
    assert len(client.get("/api/pipeline/rules").json()) == 1
    assert len(client.get("/api/pipeline/templates").json()) >= 3


def test_templates_are_valid():
    from app.routers.pipeline import TEMPLATES
    for t in TEMPLATES:
        assert pipeline.validate(t["match"], t["actions"]) is None, t["name"]


def test_settings_and_mappings(api):
    client, _, _ = api
    assert client.get("/api/pipeline/settings").json() == {
        "pipeline_enabled": True, "pipeline_on_scan": False, "pipeline_remove_torrent": True}
    assert client.put("/api/pipeline/settings", json={"pipeline_on_scan": True, "junk": 1}).json()["pipeline_on_scan"]
    assert client.put("/api/pipeline/mappings", json=[{"remote_prefix": "rel", "local_prefix": "/x"}]).status_code == 400
    m = client.put("/api/pipeline/mappings", json=[{"remote_prefix": "/downloads", "local_prefix": "/mnt"}]).json()
    assert [(x["remote_prefix"], x["local_prefix"]) for x in m] == [("/downloads", "/mnt")]


def test_test_path(api):
    client, Session, tmp = api
    (tmp / "show").mkdir()
    (tmp / "show/ep.mkv").write_bytes(b"x")
    client.put("/api/pipeline/mappings", json=[{"remote_prefix": "/downloads", "local_prefix": str(tmp)}])
    client.post("/api/pipeline/rules", json=ENCODE)
    info = namedtuple("I", "codec width height duration_sec size_bytes")("h264", 1920, 1080, 2700.0, 10**9)
    with patch("app.services.media.codec_detector.probe_with_error", return_value=(info, None)):
        r = client.post("/api/pipeline/test-path", json={"path": "/downloads/show"}).json()
    assert r["mapped"] == f"{tmp}/show" and r["exists"] and r["video_files"] == 1
    assert r["files"][0]["rule_name"] == "Shrink" and r["files"][0]["actions"] == ["encode"]


def test_preview_and_apply(api):
    client, Session, tmp = api
    for name, codec in [("a.mkv", "h264"), ("b.mkv", "hevc"), ("c.mkv", "h264")]:
        (tmp / name).write_bytes(b"x")
        store.upsert_file(str(tmp / name), name, 1_000_000_000, 60.0, codec, False)
    done_id = store.get_file_by_path(str(tmp / "c.mkv"))["id"]
    store.set_file_status(done_id, "done")
    r = client.post("/api/pipeline/preview", json={"rule": ENCODE}).json()
    assert [i["filename"] for i in r["items"]] == ["a.mkv"]  # hevc doesn't match, done is skipped
    assert r["est_saved_bytes"] == int(1_000_000_000 * pipeline.FALLBACK_ENCODE_RATIO)
    assert client.post("/api/pipeline/preview", json={"rule": {"actions": []}}).status_code == 400

    rule = client.post("/api/pipeline/rules", json=ENCODE).json()
    out = client.post("/api/pipeline/apply", json={"file_ids": [r["items"][0]["file_id"]], "rule_id": rule["id"]})
    assert out.json() == {"ok": True, "queued": 1}
    assert client.post("/api/pipeline/preview", json={}).json()["count"] == 0  # now planned
    assert client.post("/api/pipeline/apply", json={"file_ids": []}).status_code == 400


def test_activity_cancel_and_retry_for_plan_steps(api):
    client, Session, tmp = api
    fid, plan_id, jid = _planned_file((None, Session, tmp), tmp, status="waiting")
    with Session() as db:
        db.get(PipelinePlan, plan_id).detail = "Waiting for the torrent"
        db.commit()
    assert client.post(f"/api/activity/{jid}/cancel").json()["msg"] == "Remaining steps cancelled"
    with Session() as db:
        assert db.get(PipelinePlan, plan_id).status == "cancelled"
