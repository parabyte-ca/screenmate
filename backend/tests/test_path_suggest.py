"""Folder changes and path mappings worked out from the imported databases."""
import sqlite3

import pytest
from sqlalchemy import create_engine, text

from app import models  # noqa: F401
from app.database import Base
from app.services import importer, path_suggest


def fs_db(path, files):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT)")
    con.executemany("INSERT INTO files (path) VALUES (?)", [(f,) for f in files])
    con.commit()
    con.close()
    return str(path)


def at_db(path, folders, downloads=()):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE download_paths (id INTEGER PRIMARY KEY, path TEXT)")
    con.execute("CREATE TABLE downloads (id INTEGER PRIMARY KEY, download_path TEXT)")
    con.executemany("INSERT INTO download_paths (path) VALUES (?)", [(f,) for f in folders])
    con.executemany("INSERT INTO downloads (download_path) VALUES (?)", [(d,) for d in downloads])
    con.commit()
    con.close()
    return str(path)


@pytest.fixture
def nas(tmp_path):
    """This server's media folder: /…/nas with tv/Show and movies inside."""
    root = tmp_path / "nas"
    (root / "tv" / "Severance").mkdir(parents=True)
    (root / "movies").mkdir()
    (root / "anime").mkdir()
    for f in ("tv/Severance/S01E01.mkv", "tv/Severance/S01E02.mkv", "anime/ep1.mkv"):
        (root / f).write_bytes(b"x")
    return root


def test_filesplitter_files_found_under_a_new_prefix(tmp_path, nas):
    db = fs_db(tmp_path / "fs.db", ["/media/old/tv/Severance/S01E01.mkv", "/media/old/tv/Severance/S01E02.mkv",
                                    "/media/old/tv/Gone/Gone.mkv"])
    out = path_suggest.suggest(None, db, [str(nas)])
    assert out["rewrite"] == {"from": "/media/old", "to": str(nas), "matched": 2, "checked": 3}


def test_files_already_in_place_need_no_rewrite(tmp_path, nas):
    db = fs_db(tmp_path / "fs.db", [str(nas / "tv/Severance/S01E01.mkv")])
    assert path_suggest.suggest(None, db, [str(nas)])["rewrite"] is None


def test_qbittorrent_folders_become_path_mappings(tmp_path, nas):
    db = at_db(tmp_path / "at.db", ["/downloads/tv", "/downloads/movies"], downloads=["/downloads/tv"])
    out = path_suggest.suggest(db, None, [str(nas)])
    assert out["mappings"] == [{"from": "/downloads", "to": str(nas), "matched": 2, "checked": 2}]
    assert (out["folders_checked"], out["folders_missing"]) == (2, 2)


def test_folder_that_is_a_media_root_under_another_name(tmp_path, nas):
    db = at_db(tmp_path / "at.db", ["/data/nas"])
    assert path_suggest.suggest(db, None, [str(nas)])["mappings"][0] == {
        "from": "/data/nas", "to": str(nas), "matched": 1, "checked": 1}


def test_inferred_through_filesplitter_when_the_folder_isnt_mounted_here(tmp_path, nas):
    # qBittorrent's /srv/torrents/tvshows has no folder of that name here, but
    # FileSplitter knew files under …/tvshows, and those are found once rewritten.
    at = at_db(tmp_path / "at.db", ["/srv/torrents/tvshows"])
    fs = fs_db(tmp_path / "fs.db", ["/media/old/tv/Severance/S01E01.mkv", "/media/old/tvshows/Pilot.mkv"])
    out = path_suggest.suggest(at, fs, [str(nas)])
    assert out["rewrite"]["to"] == str(nas)
    [m] = out["mappings"]
    assert (m["from"], m["to"], m.get("via")) == ("/srv/torrents/tvshows", f"{nas}/tvshows", "filesplitter")


def test_existing_mappings_and_present_folders_are_left_alone(tmp_path, nas):
    db = at_db(tmp_path / "at.db", ["/downloads/tv", str(nas / "movies")])
    out = path_suggest.suggest(db, None, [str(nas)], existing_mappings=[("/downloads", "/somewhere")])
    assert out["mappings"] == [] and out["folders_missing"] == 0


def test_run_adds_chosen_mappings_without_replacing_yours(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'sm.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as c:
        c.execute(text("INSERT INTO path_mappings (remote_prefix, local_prefix) VALUES ('/downloads', '/mine')"))
    from sqlalchemy.orm import Session
    with Session(bind=engine) as db:
        added = importer._add_mappings(db, [("/downloads/", "/theirs"), ("/srv/torrents", "/mnt/nas")])
        db.commit()
    assert added == 1
    with engine.connect() as c:
        rows = dict(c.execute(text("SELECT remote_prefix, local_prefix FROM path_mappings")).all())
    assert rows == {"/downloads": "/mine", "/srv/torrents": "/mnt/nas"}
    engine.dispose()
