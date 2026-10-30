"""Importing AuTorrent and FileSplitter databases."""
import io
import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.database import Base
from app.models import Journey
from app.services import importer
from app.services.media import config, store

AUTORRENT_TABLES = ["download_paths", "quality_profiles", "watchlist", "watchlist_episodes",
                    "downloads", "download_history", "trakt_seen", "settings"]


@pytest.fixture
def target(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'screenmate.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    monkeypatch.setattr(store, "engine", engine)
    monkeypatch.setattr(importer, "UPLOAD_DIR", str(tmp_path / "uploads"))
    for attr, _ in config.SETTINGS_MAP.values():
        monkeypatch.setattr(config, attr, getattr(config, attr))
    yield engine, tmp_path
    engine.dispose()


def make_autorrent(path):
    """An AuTorrent database: ScreenMate's original tables, minus a later column."""
    eng = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(eng, tables=[Base.metadata.tables[t] for t in AUTORRENT_TABLES])
    with eng.begin() as c:
        c.execute(text("INSERT INTO download_paths (id, name, path, is_default) VALUES (7, 'TV', '/tv', 1)"))
        c.execute(text("INSERT INTO quality_profiles (id, name, quality, codec) VALUES (3, 'HD', '1080p', 'x265')"))
        c.execute(text("INSERT INTO watchlist (id, title, search_query, quality, codec, season, episode, "
                       "download_path_id, profile_id, enabled) VALUES (5, 'Severance', 'severance', '1080p', 'x265', 2, 4, 7, 3, 1)"))
        c.execute(text("INSERT INTO watchlist_episodes (watchlist_id, season, episode) VALUES (5, 2, 3)"))
        c.execute(text("INSERT INTO downloads (id, title, torrent_hash, status, watchlist_id, created_at) "
                       "VALUES (9, 'Severance S02E03', 'abc', 'completed', 5, '2026-01-02 10:00:00')"))
        c.execute(text("INSERT INTO download_history (name, source, torrent_hash, status, watchlist_id, added_at) "
                       "VALUES ('Severance S02E03', 'watchlist', 'abc', 'completed', 5, '2026-01-02 10:00:00')"))
        c.execute(text("INSERT INTO trakt_seen (trakt_id, title, status) VALUES (42, 'Severance', 'added')"))
        for k, v in [("qbit_host", "10.0.0.5"), ("session_secret", "theirs"), ("scan_interval", "30")]:
            c.execute(text("INSERT INTO settings (key, value) VALUES (:k, :v)"), {"k": k, "v": v})
        c.execute(text("ALTER TABLE watchlist DROP COLUMN poster_url"))  # an older schema
    eng.dispose()


def make_filesplitter(path, media_root="/media/old"):
    con = sqlite3.connect(path)
    con.executescript(f"""
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, filename TEXT NOT NULL,
            size_bytes INTEGER, duration_sec REAL, codec TEXT, is_anthology INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending', error_msg TEXT, discovered_at TEXT, updated_at TEXT,
            width INTEGER DEFAULT 0, height INTEGER DEFAULT 0, anthology_override INTEGER);
        CREATE TABLE jobs (id INTEGER PRIMARY KEY, file_id INTEGER, job_type TEXT NOT NULL,
            status TEXT DEFAULT 'queued', progress_pct REAL DEFAULT 0, target_resolution TEXT DEFAULT 'original',
            log_tail TEXT, started_at TEXT, finished_at TEXT, saved_bytes INTEGER DEFAULT 0, episode_count INTEGER DEFAULT 0);
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO files (id, path, filename, codec, status, height) VALUES
            (11, '{media_root}/a.mkv', 'a.mkv', 'hevc', 'done', 1080),
            (12, '{media_root}/b.mkv', 'b.mkv', 'h264', 'queued', 720);
        INSERT INTO jobs (file_id, job_type, status, saved_bytes, finished_at) VALUES
            (11, 'encode', 'done', 500, '2026-01-01T00:00:00+00:00'), (12, 'encode', 'queued', 0, NULL);
        INSERT INTO settings VALUES ('x265_crf', '22'), ('total_saved_bytes', '1000'),
            ('secret_key', 'theirs'), ('queue_paused', '1');
    """)
    con.commit()
    con.close()


def _upload(path):
    with open(path, "rb") as f:
        return importer.save_upload(f)


def test_rejects_non_sqlite_and_screenmate_files(target, tmp_path):
    with pytest.raises(importer.ImportFailed, match="isn't a SQLite"):
        importer.save_upload(io.BytesIO(b"hello"))
    token = _upload(tmp_path / "screenmate.db")
    with pytest.raises(importer.ImportFailed, match="ScreenMate database"):
        importer.inspect_upload(token)
    with pytest.raises(importer.ImportFailed, match="Unknown upload"):
        importer.upload_path("../etc/passwd")


def test_inspect_reports_contents(target, tmp_path):
    make_autorrent(tmp_path / "autorrent.db")
    make_filesplitter(tmp_path / "filesplitter.db")
    a = importer.inspect_upload(_upload(tmp_path / "autorrent.db"))
    f = importer.inspect_upload(_upload(tmp_path / "filesplitter.db"))
    assert a["kind"] == "autorrent" and a["counts"]["watchlist"] == 1 and a["counts"]["downloads"] == 1
    assert f["kind"] == "filesplitter" and f["counts"]["files"] == 2 and f["counts"]["saved_bytes"] == 1000
    assert f["counts"]["files_found_here"] == 0 and f["sample_paths"][0] == "/media/old/a.mkv"


def test_full_import_merges_both(target, tmp_path):
    engine, _ = target
    make_autorrent(tmp_path / "autorrent.db")
    make_filesplitter(tmp_path / "filesplitter.db")
    with engine.begin() as c:  # an existing install with its own data
        c.execute(text("INSERT INTO settings (key, value) VALUES ('session_secret', 'mine'), ('qbit_host', 'keep-me')"))
        c.execute(text("INSERT INTO download_paths (name, path, is_default) VALUES ('Mine', '/mine', 1)"))
    tokens = [_upload(tmp_path / "autorrent.db"), _upload(tmp_path / "filesplitter.db")]

    out = importer.run(engine, tokens, rewrite=("/media/old", "/mnt/media"),
                       db_path=str(tmp_path / "screenmate.db"))

    assert out["backup"] and out["backup"].endswith(".db")
    by_kind = {r["kind"]: r["imported"] for r in out["results"]}
    assert by_kind["autorrent"] == {"folders": 1, "profiles": 1, "watchlist": 1, "episodes": 1,
                                    "downloads": 1, "history": 1, "trakt_seen": 1, "settings": 1}
    assert by_kind["filesplitter"]["files"] == 2 and by_kind["filesplitter"]["jobs"] == 2
    with engine.connect() as c:
        s = dict(c.execute(text("SELECT key, value FROM settings")).all())
        assert s["session_secret"] == "mine"          # never imported
        assert s["qbit_host"] == "keep-me"            # existing values kept by default
        assert s["scan_interval"] == "30"             # new ones added
        assert s["x265_crf"] == "22" and s["total_saved_bytes"] == "1000"
        assert "secret_key" not in s and "queue_paused" not in s
        wl = c.execute(text("SELECT id, download_path_id, profile_id FROM watchlist")).one()
        path_id = c.execute(text("SELECT id FROM download_paths WHERE path='/tv'")).scalar()
        prof_id = c.execute(text("SELECT id FROM quality_profiles WHERE name='HD'")).scalar()
        assert (wl.download_path_id, wl.profile_id) == (path_id, prof_id)  # ids remapped
        assert c.execute(text("SELECT is_default FROM download_paths WHERE path='/tv'")).scalar() == 0
        assert c.execute(text("SELECT watchlist_id FROM downloads")).scalar() == wl.id
        files = dict(c.execute(text("SELECT path, status FROM media_files")).all())
        assert files == {"/mnt/media/a.mkv": "done", "/mnt/media/b.mkv": "pending"}
        jobs = c.execute(text("SELECT status, reason, journey_id FROM media_jobs ORDER BY id")).all()
        assert [j.status for j in jobs] == ["done", "cancelled"]
        assert all(j.reason == importer.IMPORTED_JOB_REASON and j.journey_id is None for j in jobs)
    assert config.X265_CRF == 22  # settings applied live
    with sessionmaker(bind=engine)() as db:
        journeys = db.query(Journey).all()  # the download, but not FileSplitter's history
        assert [(j.source, j.status) for j in journeys] == [("download", "done")]
        assert journeys[0].updated_at.year == 2026 and journeys[0].updated_at.month == 1  # dated, not "today"


def test_import_is_idempotent_and_overwrite_flag(target, tmp_path):
    engine, _ = target
    make_autorrent(tmp_path / "autorrent.db")
    importer.run(engine, [_upload(tmp_path / "autorrent.db")])
    with engine.begin() as c:
        c.execute(text("UPDATE settings SET value='changed' WHERE key='qbit_host'"))
    out = importer.run(engine, [_upload(tmp_path / "autorrent.db")], overwrite_settings=True)
    rep = out["results"][0]["imported"]
    assert (rep["watchlist"], rep["downloads"], rep["history"], rep["episodes"]) == (0, 0, 0, 0)
    with engine.connect() as c:
        assert c.execute(text("SELECT value FROM settings WHERE key='qbit_host'")).scalar() == "10.0.0.5"
        assert c.execute(text("SELECT COUNT(*) FROM watchlist")).scalar() == 1


def test_two_of_the_same_kind_rejected(target, tmp_path):
    engine, _ = target
    make_filesplitter(tmp_path / "f1.db")
    make_filesplitter(tmp_path / "f2.db")
    with pytest.raises(importer.ImportFailed, match="at most one"):
        importer.run(engine, [_upload(tmp_path / "f1.db"), _upload(tmp_path / "f2.db")])


def test_api_inspect_and_run(target, tmp_path, monkeypatch):
    engine, _ = target
    from app.main import app
    from app.routers import importer as router_mod

    monkeypatch.setattr(router_mod, "engine", engine)
    monkeypatch.setattr(router_mod, "DATABASE_URL", f"sqlite:///{tmp_path / 'screenmate.db'}")
    make_filesplitter(tmp_path / "filesplitter.db")
    client = TestClient(app)
    with open(tmp_path / "filesplitter.db", "rb") as f:
        r = client.post("/api/import/inspect", files=[("files", ("filesplitter.db", f.read())),
                                                      ("files", ("notes.txt", b"nope"))]).json()
    assert r[0]["kind"] == "filesplitter" and "isn't a SQLite" in r[1]["error"]
    assert client.post("/api/import/run", json={"tokens": []}).status_code == 400
    assert client.post("/api/import/run", json={"tokens": [r[0]["token"]],
                                                "path_rewrite": {"from": "rel", "to": "/x"}}).status_code == 400
    out = client.post("/api/import/run", json={"tokens": [r[0]["token"]]}).json()
    assert out["ok"] and out["results"][0]["imported"]["files"] == 2
    assert client.post("/api/import/run", json={"tokens": [r[0]["token"]]}).status_code == 400  # consumed
