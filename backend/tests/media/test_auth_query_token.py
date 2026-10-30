"""?token= lets <video src> and EventSource authenticate over Cloudflare Tunnel."""
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.middleware.auth import AuthMiddleware
from app.models import Setting
from app.services.token import make_token

CF = {"cf-connecting-ip": "1.2.3.4"}


@pytest.fixture
def client(media_db):
    session = sessionmaker(bind=media_db)()
    session.add_all([Setting(key="ui_password", value="hunter2"),
                     Setting(key="session_secret", value="s3cret")])
    session.commit()

    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    @app.post("/api/ping")
    def ping_post():
        return {"ok": True}

    with patch("app.middleware.auth.SessionLocal", return_value=session), \
         patch.object(session, "close"):
        yield TestClient(app), make_token("s3cret", "hunter2")
    session.close()


def test_get_with_valid_query_token_is_allowed(client):
    c, token = client
    assert c.get(f"/api/ping?token={token}", headers=CF).status_code == 200


def test_get_with_bad_query_token_is_rejected(client):
    c, _ = client
    assert c.get("/api/ping?token=garbage", headers=CF).status_code == 401


def test_query_token_not_accepted_for_writes(client):
    c, token = client
    assert c.post(f"/api/ping?token={token}", headers=CF).status_code == 401


def test_bearer_header_still_works(client):
    c, token = client
    assert c.get("/api/ping", headers={**CF, "Authorization": f"Bearer {token}"}).status_code == 200
