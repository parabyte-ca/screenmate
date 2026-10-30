"""Fixtures for the media engine tests: an isolated database and media roots."""
import pytest
from sqlalchemy import create_engine

from app import models  # noqa: F401  (registers tables on Base.metadata)
from app.database import Base
from app.services.media import config, store


@pytest.fixture
def media_db(tmp_path, monkeypatch):
    """A fresh SQLite database (file-backed, so worker threads share it) for store calls."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'media.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(store, "engine", engine)
    yield engine
    engine.dispose()


# FileSplitter's tests call this fixture tmp_db; keep the name so they port unchanged.
@pytest.fixture
def tmp_db(media_db):
    return media_db


@pytest.fixture
def tmp_media_roots(tmp_path, monkeypatch):
    """Two fresh, empty media roots for a single test (MEDIA_PATHS supports more than one)."""
    root1 = tmp_path / "media1"
    root2 = tmp_path / "media2"
    root1.mkdir()
    root2.mkdir()
    monkeypatch.setattr(config, "MEDIA_PATHS", [str(root1), str(root2)])
    return [str(root1), str(root2)]


@pytest.fixture
def api(media_db, tmp_media_roots):
    """TestClient for the media router (no lifespan, so no scheduler or worker threads)."""
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)
