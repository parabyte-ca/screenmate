"""AuTorrent features that had no test of their own: the indexers that need no key,
signing out every session, and removing finished torrents from qBittorrent.
All offline: indexers get canned responses through httpx.MockTransport."""
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.database import Base, get_db
from app.models import Download, Setting

HASH = "0123456789abcdef0123456789abcdef01234567"
_RealClient = httpx.Client


@pytest.fixture
def fake_http(monkeypatch):
    """Route every httpx.Client request to a per-test handler."""
    routes: dict[str, httpx.Response] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        for prefix, response in routes.items():
            if str(request.url).startswith(prefix):
                return response
        return httpx.Response(404)

    monkeypatch.setattr(httpx, "Client",
                        lambda *a, **kw: _RealClient(transport=httpx.MockTransport(handler),
                                                     **{k: v for k, v in kw.items() if k != "transport"}))
    return routes


# ── Indexers ──────────────────────────────────────────────────────────────────

def test_eztv_keeps_only_titles_with_every_query_word(fake_http):
    from app.services.indexers.eztv import EZTV_API, search_eztv
    fake_http[EZTV_API] = httpx.Response(200, json={"torrents": [
        {"hash": HASH.upper(), "filename": "Severance.S02E01.1080p.WEB.x265", "magnet_url": f"magnet:?xt=urn:btih:{HASH}",
         "seeds": "42", "peers": "3", "size_bytes": "1500000000", "id": 7},
        {"hash": "f" * 40, "filename": "Other.Show.S01E01.720p", "magnet_url": "magnet:?xt=urn:btih:" + "f" * 40},
        {"hash": "", "filename": "Severance no hash", "magnet_url": "x"},
    ]})
    [r] = search_eztv("severance")
    assert (r["info_hash"], r["seeds"], r["leeches"], r["source"], r["quality"]) == (HASH, 42, 3, "eztv", "1080p")


def test_yts_makes_one_result_per_torrent_with_a_magnet(fake_http):
    from app.services.indexers.yts import YTS_API, search_yts
    fake_http[YTS_API] = httpx.Response(200, json={"data": {"movies": [{
        "title_long": "Dune (2021)", "slug": "dune-2021",
        "torrents": [{"hash": HASH, "quality": "2160p", "seeds": 9, "peers": 1, "size_bytes": 5_000_000_000},
                     {"hash": "a" * 40, "quality": "1080p", "seeds": 30, "peers": 2, "size_bytes": 2_000_000_000}],
    }]}})
    results = search_yts("dune")
    assert [r["quality"] for r in results] == ["4K", "1080p"]
    assert results[0]["magnet"].startswith(f"magnet:?xt=urn:btih:{HASH}&dn=") and results[0]["source"] == "yts"


def test_torrentgalaxy_reads_the_ezrss_feed(fake_http):
    from app.services.indexers.torrentgalaxy import TGX_RSS, search_torrentgalaxy
    rss = f"""<?xml version="1.0"?><rss xmlns:torrent="http://xmlns.ezrss.it/0.1/"><channel>
      <item><title>Severance S02E01 2160p</title><comments>https://tgx/t/1</comments>
        <torrent:magnetURI>magnet:?xt=urn:btih:{HASH}</torrent:magnetURI>
        <torrent:seeds>12</torrent:seeds><torrent:peers>4</torrent:peers>
        <torrent:contentLength>3000000000</torrent:contentLength></item>
      <item><title>No magnet</title></item>
    </channel></rss>"""
    fake_http[TGX_RSS] = httpx.Response(200, text=rss)
    [r] = search_torrentgalaxy("severance")
    assert (r["info_hash"], r["seeds"], r["leeches"], r["size_bytes"], r["source"]) == (HASH, 12, 4, 3_000_000_000, "tgx")


def test_nyaa_reads_its_rss_feed(fake_http):
    from app.services.indexers.nyaa import search_nyaa
    fake_http["https://nyaa.si/"] = httpx.Response(200, text=f"""<?xml version="1.0"?>
      <rss xmlns:nyaa="https://nyaa.si/xmlns/nyaa"><channel>
        <item><title>[Group] Frieren - 01 [1080p]</title><guid>https://nyaa.si/view/1234567</guid>
          <nyaa:infoHash>{HASH.upper()}</nyaa:infoHash><nyaa:size>1.4 GiB</nyaa:size>
          <nyaa:seeders>120</nyaa:seeders><nyaa:leechers>3</nyaa:leechers></item>
        <item><title>no hash</title><guid>https://nyaa.si/view/1</guid></item>
      </channel></rss>""")
    [r] = search_nyaa("frieren")
    assert (r["info_hash"], r["seeds"], r["leeches"], r["source"], r["quality"]) == (HASH, 120, 3, "nyaa", "1080p")
    assert r["url"] == "https://nyaa.si/view/1234567" and r["magnet"].startswith(f"magnet:?xt=urn:btih:{HASH}")
    assert r["size_bytes"] > 1_000_000_000


def test_1337x_follows_each_result_to_its_magnet(fake_http):
    from app.services.indexers.leet337x import BASE, search_1337x
    fake_http[f"{BASE}/search/"] = httpx.Response(200, text=(
        '<tr><td><a href="/torrent/123/severance-s02e01/">Severance S02E01 1080p</a></td>'
        '<td class="seeds">55</td>\n<td class="leeches">6</td><td class="date">x</td>'
        '<td class="size">1.4 GB<span>55</span></td></tr>'))
    fake_http[f"{BASE}/torrent/123/"] = httpx.Response(200, text=f'<a href="magnet:?xt=urn:btih:{HASH}&dn=x">Magnet</a>')
    [r] = search_1337x("severance")
    assert (r["info_hash"], r["seeds"], r["leeches"], r["size_bytes"], r["source"]) == (HASH, 55, 6, 1_400_000_000, "1337x")


def test_1337x_unreachable_returns_nothing(fake_http):
    from app.services.indexers.leet337x import search_1337x
    assert search_1337x("anything") == []


# ── Sign out everywhere ───────────────────────────────────────────────────────

@pytest.fixture
def Session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'p.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    engine.dispose()


def test_revoke_invalidates_existing_tokens(Session):
    from app.middleware.auth import AuthMiddleware
    from app.routers import auth

    with Session() as db:
        db.add(Setting(key="ui_password", value="hunter2"))
        db.commit()

    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(auth.router, prefix="/api")

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    cf = {"cf-connecting-ip": "1.2.3.4"}
    with patch("app.middleware.auth.SessionLocal", Session):
        c = TestClient(app)
        token = c.post("/api/auth/login", json={"password": "hunter2"}, headers=cf).json()["token"]
        bearer = {**cf, "Authorization": f"Bearer {token}"}
        assert c.get("/api/ping", headers=bearer).status_code == 200
        assert c.post("/api/auth/revoke", headers=bearer).json() == {"ok": True}
        assert c.get("/api/ping", headers=bearer).status_code == 401
        new = c.post("/api/auth/login", json={"password": "hunter2"}, headers=cf).json()["token"]
        assert new != token and c.get("/api/ping", headers={**cf, "Authorization": f"Bearer {new}"}).status_code == 200


# ── Remove finished torrents ─────────────────────────────────────────────────

@pytest.mark.parametrize("enabled, removed", [("true", True), ("false", False)])
def test_cleanup_removes_finished_torrents_only_when_enabled(Session, enabled, removed):
    from app.services import scheduler

    with Session() as db:
        db.add(Setting(key="remove_on_complete", value=enabled))
        db.add_all([Download(title="done", torrent_hash=HASH, status="completed", qbit_removed=False),
                    Download(title="busy", torrent_hash="b" * 40, status="downloading", qbit_removed=False)])
        db.commit()

    with patch("app.database.SessionLocal", Session), \
         patch("app.services.qbittorrent.remove_torrent") as remove, \
         patch("app.services.media_servers.trigger_media_refresh"):
        scheduler._cleanup_completed()

    with Session() as db:
        state = {d.title: d.qbit_removed for d in db.query(Download)}
    assert state == {"done": removed, "busy": False}
    if removed:
        remove.assert_called_once_with(HASH, delete_files=False)
    else:
        remove.assert_not_called()

