"""Insights figures from media jobs, the library and download history."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.database import Base, get_db
from app.services import insights
from app.services.media import config


@pytest.fixture
def Session(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'i.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    monkeypatch.setattr(config, "ENCODER_BACKEND", "cpu")
    yield sessionmaker(bind=engine)
    engine.dispose()


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def seed(db):
    now = datetime.now().astimezone()
    files = [  # path, codec, height, size, duration, status
        ("/m/a.mkv", "hevc", 1080, 400, 3600, "done"),
        ("/m/b.mkv", "h264", 2160, 3000, 7200, "pending"),
        ("/m/c.mkv", "h264", 720, 1000, 1800, "pending"),
        ("/m/d.mkv", "mpeg4", 480, 200, 1200, "skipped"),
    ]
    for path, codec, h, size, dur, status in files:
        db.execute(text("INSERT INTO media_files (path, filename, codec, height, size_bytes, duration_sec, status)"
                        " VALUES (:p, :f, :c, :h, :s, :d, :st)"),
                   {"p": path, "f": path[3:], "c": codec, "h": h, "s": size, "d": dur, "st": status})
    jobs = [  # file_id, saved, finished, encoder, media seconds, wall seconds
        (1, 600, now, "cpu", 3600, 1800),           # 2× realtime
        (1, 100, now - timedelta(days=3), "nvenc", 3600, 360),  # 10× realtime
        (1, 999, now - timedelta(days=40), None, None, None),   # outside the window, no speed data
    ]
    for fid, saved, fin, enc, media, wall in jobs:
        start = fin - timedelta(seconds=wall or 60)
        db.execute(text("INSERT INTO media_jobs (file_id, job_type, status, saved_bytes, started_at, finished_at,"
                        " encoder, media_seconds) VALUES (:f, 'encode', 'done', :s, :st, :fi, :e, :m)"),
                   {"f": fid, "s": saved, "st": iso(start), "fi": iso(fin), "e": enc, "m": media})
    db.execute(text("INSERT INTO media_jobs (file_id, job_type, status, saved_bytes, finished_at)"
                    " VALUES (2, 'encode', 'error', 0, :f)"), {"f": iso(now)})
    for added, size in [(now, 5000), (now - timedelta(days=1), 7000), (now - timedelta(days=60), 1)]:
        db.execute(text("INSERT INTO download_history (name, source, status, size_bytes, added_at)"
                        " VALUES ('x', 'manual', 'completed', :s, :a)"),
                   {"s": size, "a": added.astimezone(timezone.utc).replace(tzinfo=None)})
    db.execute(text("INSERT INTO settings (key, value) VALUES ('total_saved_bytes', '1699')"))
    db.commit()


def test_figures(Session):
    with Session() as db:
        seed(db)
        r = insights.compute(db, days=7)
    t = r["totals"]
    assert (t["saved_bytes"], t["saved_bytes_period"], t["encodes_period"]) == (1699, 700, 2)
    assert (t["downloads_period"], t["downloaded_bytes_period"]) == (2, 12000)
    assert (t["library_files"], t["library_bytes"]) == (4, 4600)
    assert t["hevc_share"] == round(400 / 4600, 3)

    days = r["savings_by_day"]
    assert len(days) == 7 and days[-1]["date"] == datetime.now().astimezone().date().isoformat()
    assert days[-1]["saved_bytes"] == 600 and days[-4]["saved_bytes"] == 100
    assert sum(d["count"] for d in r["downloads_by_day"]) == 2

    assert [c["label"] for c in r["codec_mix"]] == ["H.264", "HEVC (x265)", "MPEG-4"]  # by size
    assert [x["label"] for x in r["resolution_mix"]] == ["4K", "1080p", "720p", "480p"]

    speed = {s["encoder"]: s for s in r["speed"]}
    assert speed["cpu"]["realtime_x"] == 2.0 and speed["nvenc"]["realtime_x"] == 10.0

    f = r["forecast"]  # b and c: h264, not done/skipped
    assert (f["files"], f["bytes"]) == (2, 4000)
    assert f["encoder"] == "cpu" and f["est_hours"] == round((7200 + 1800) / 2.0 / 3600, 1)
    assert 0 < f["est_saved_bytes"] < 4000

    assert [x["saved_bytes"] for x in r["top_savings"]] == [999, 600, 100]


def test_empty_install(Session):
    with Session() as db:
        r = insights.compute(db)
    assert r["totals"]["library_bytes"] == 0 and r["totals"]["hevc_share"] == 0
    assert r["speed"] == [] and r["forecast"]["est_hours"] is None and len(r["savings_by_day"]) == 30


def test_more_than_five_codecs_fold_into_other(Session):
    with Session() as db:
        for n, codec in enumerate(["hevc", "h264", "av1", "vp9", "mpeg4", "mpeg2video", "vc1"]):
            db.execute(text("INSERT INTO media_files (path, filename, codec, size_bytes) VALUES (:p, :p, :c, :s)"),
                       {"p": f"/m/{n}", "c": codec, "s": 100 - n})
        db.commit()
        mix = insights.compute(db)["codec_mix"]
    assert len(mix) == 6 and mix[-1] == {"codec": "other", "label": "Other", "files": 2, "bytes": 95 + 94}


def test_api(Session):
    from app.main import app

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    try:
        client = TestClient(app)
        assert client.get("/api/insights?days=14").json()["days"] == 14
        assert client.get("/api/insights?days=0").status_code == 422
    finally:
        app.dependency_overrides.pop(get_db, None)
