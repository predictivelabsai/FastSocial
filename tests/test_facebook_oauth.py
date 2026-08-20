from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from datetime import date
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from sqlalchemy import select
from starlette.testclient import TestClient

from fastsocial.app import app
from fastsocial.config import settings
from fastsocial.db import session_scope
from fastsocial.models import AccountStatus, ConnectionProvider, SocialAccount, User
from fastsocial.security import encrypt_text
from fastsocial.services import workspace_for_user
from fastsocial.social import facebook as facebook_mod
from fastsocial.social.facebook import (
    FacebookClient,
    normalize_facebook_pages,
    parse_facebook_signed_request,
)


def _routes():
    from fastsocial import routes

    return routes


def _csrf(response) -> str:
    soup = BeautifulSoup(response.text, "html.parser")
    field = soup.select_one('input[name="csrf"]')
    assert field is not None
    return field["value"]


def _register(client: TestClient, email: str) -> None:
    registration = client.get("/register")
    response = client.post(
        "/register",
        data={
            "csrf": _csrf(registration),
            "name": "Facebook Tester",
            "email": email,
            "password": "local-test-password",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def _enable_facebook(monkeypatch) -> None:
    cfg = settings()
    monkeypatch.setattr(cfg, "meta_app_id", "meta-app")
    monkeypatch.setattr(cfg, "meta_app_secret", "meta-secret")
    monkeypatch.setattr(cfg, "meta_graph_api_version", "v25.0")
    monkeypatch.setattr(cfg, "service_url", "http://testserver")


def _signed_request(payload: dict, secret: str) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    encoded_sig = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{encoded_sig}.{body}"


class FakeResponse:
    def __init__(self, payload, status_code=200, text=None):
        self._payload = payload
        self.status_code = status_code
        self.is_success = 200 <= status_code < 300
        self.text = text if text is not None else json.dumps(payload)
        self.headers = {}

    def json(self):
        return self._payload


class FakeAsyncClient:
    def __init__(self, handler):
        self.handler = handler

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        return self.handler("GET", url, kwargs)

    async def post(self, url, **kwargs):
        return self.handler("POST", url, kwargs)


def test_normalize_facebook_pages_requires_token_and_marks_publish_tasks():
    pages = normalize_facebook_pages(
        {
            "data": [
                {
                    "id": "111",
                    "name": "Publishable",
                    "access_token": "page-token",
                    "tasks": ["ANALYZE", "CREATE_CONTENT"],
                    "picture": {"data": {"url": "https://img.example/page.png"}},
                },
                {"id": "222", "name": "No token", "tasks": ["MANAGE"]},
                {
                    "id": "333",
                    "name": "Analyze only",
                    "access_token": "limited-token",
                    "tasks": ["ANALYZE"],
                },
            ]
        }
    )
    assert [page["id"] for page in pages] == ["111", "333"]
    assert pages[0]["can_publish"] is True
    assert pages[0]["picture"] == "https://img.example/page.png"
    assert pages[1]["can_publish"] is False


def test_parse_facebook_signed_request_round_trip():
    payload = {"user_id": "user-9", "algorithm": "HMAC-SHA256"}
    parsed = parse_facebook_signed_request(_signed_request(payload, "meta-secret"), "meta-secret")
    assert parsed["user_id"] == "user-9"


def test_facebook_oauth_start_requires_configuration():
    with TestClient(app) as client:
        _register(client, "facebook-unconfigured@example.com")
        response = client.get("/oauth/facebook/start", follow_redirects=False)
        assert response.status_code == 303
        assert "Facebook+OAuth+is+not+configured" in response.headers["location"]


def test_facebook_connect_and_callback_auto_selects_single_page(monkeypatch):
    _enable_facebook(monkeypatch)

    async def fake_code(_code: str):
        return {"access_token": "short-token", "expires_in": 3600}

    async def fake_long_lived(token: str):
        assert token == "short-token"
        return {"access_token": "long-token", "expires_in": 5184000}

    async def fake_profile(token: str):
        assert token == "long-token"
        return {"id": "fb-user-1", "name": "Owner"}

    async def fake_pages(token: str):
        assert token == "long-token"
        return [
            {
                "id": "page-1",
                "name": "FastSocial",
                "access_token": "page-token",
                "tasks": ["CREATE_CONTENT", "ANALYZE"],
                "picture": "https://img.example/page.png",
                "can_publish": True,
            }
        ]

    routes = _routes()
    monkeypatch.setattr(routes, "exchange_facebook_code", fake_code)
    monkeypatch.setattr(routes, "exchange_long_lived_token", fake_long_lived)
    monkeypatch.setattr(routes, "facebook_profile", fake_profile)
    monkeypatch.setattr(routes, "list_facebook_pages", fake_pages)

    with TestClient(app) as client:
        _register(client, "facebook-single@example.com")
        start = client.get("/oauth/facebook/start", follow_redirects=False)
        assert start.status_code == 302
        location = start.headers["location"]
        assert "facebook.com" in location
        assert "pages_manage_posts" in location
        state = parse_qs(urlparse(location).query)["state"][0]
        callback = client.get(
            "/oauth/facebook/callback",
            params={"code": "ok-code", "state": state},
            follow_redirects=False,
        )
        assert callback.status_code == 303
        assert callback.headers["location"] == "/integrations?saved=1#facebook"

        with session_scope() as session:
            account = session.scalar(
                select(SocialAccount).where(SocialAccount.platform == "facebook")
            )
            assert account is not None
            assert account.external_account_id == "page-1"
            assert account.display_name == "FastSocial"
            assert account.provider == ConnectionProvider.direct
            assert account.account_metadata["facebook_user_id"] == "fb-user-1"
            assert "pages_manage_posts" in account.scopes


def test_facebook_callback_shows_page_picker_for_multiple_pages(monkeypatch):
    _enable_facebook(monkeypatch)
    pages = [
        {
            "id": "page-a",
            "name": "Page A",
            "access_token": "token-a",
            "tasks": ["CREATE_CONTENT"],
            "picture": "",
            "can_publish": True,
        },
        {
            "id": "page-b",
            "name": "Page B",
            "access_token": "token-b",
            "tasks": ["MANAGE"],
            "picture": "",
            "can_publish": True,
        },
    ]

    async def fake_code(_code: str):
        return {"access_token": "short-token"}

    async def fake_long_lived(token: str):
        return {"access_token": token}

    async def fake_profile(_token: str):
        return {"id": "fb-user-2", "name": "Owner"}

    async def fake_pages(_token: str):
        return pages

    routes = _routes()
    monkeypatch.setattr(routes, "exchange_facebook_code", fake_code)
    monkeypatch.setattr(routes, "exchange_long_lived_token", fake_long_lived)
    monkeypatch.setattr(routes, "facebook_profile", fake_profile)
    monkeypatch.setattr(routes, "list_facebook_pages", fake_pages)

    with TestClient(app) as client:
        _register(client, "facebook-multi@example.com")
        start = client.get("/oauth/facebook/start", follow_redirects=False)
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        callback = client.get(
            "/oauth/facebook/callback",
            params={"code": "ok-code", "state": state},
            follow_redirects=False,
        )
        assert callback.status_code == 303
        assert callback.headers["location"] == "/oauth/facebook/pages"
        picker = client.get("/oauth/facebook/pages")
        assert picker.status_code == 200
        assert "Page A" in picker.text
        assert "Page B" in picker.text
        selected = client.post(
            "/oauth/facebook/pages",
            data={"csrf": _csrf(picker), "page_id": "page-b"},
            follow_redirects=False,
        )
        assert selected.status_code == 303
        assert selected.headers["location"] == "/integrations?saved=1#facebook"

        with session_scope() as session:
            account = session.scalar(
                select(SocialAccount).where(SocialAccount.external_account_id == "page-b")
            )
            assert account is not None
            assert account.display_name == "Page B"
            assert (
                session.scalar(
                    select(SocialAccount).where(SocialAccount.external_account_id == "page-a")
                )
                is None
            )


def test_facebook_callback_rejects_invalid_state(monkeypatch):
    _enable_facebook(monkeypatch)
    with TestClient(app) as client:
        _register(client, "facebook-state@example.com")
        client.get("/oauth/facebook/start", follow_redirects=False)
        response = client.get(
            "/oauth/facebook/callback",
            params={"code": "ok-code", "state": "tampered"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "Facebook+authorization+failed" in response.headers["location"]


def test_facebook_connect_path_redirects_to_oauth(monkeypatch):
    _enable_facebook(monkeypatch)
    with TestClient(app) as client:
        _register(client, "facebook-connect@example.com")
        response = client.get("/integrations/connect/facebook/direct", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/oauth/facebook/start"


def test_facebook_client_publishes_text_and_reports_health(monkeypatch):
    calls = []

    def handler(method, url, kwargs):
        calls.append((method, url, kwargs))
        if method == "POST" and url.endswith("/page-9/feed"):
            return FakeResponse({"id": "page-9_55"})
        if method == "GET" and url.endswith("/page-9"):
            return FakeResponse({"id": "page-9", "name": "FastSocial"})
        return FakeResponse({"error": {"message": "unexpected"}}, status_code=400)

    monkeypatch.setattr(facebook_mod.httpx, "AsyncClient", lambda *a, **k: FakeAsyncClient(handler))
    account = SocialAccount(
        platform="facebook",
        provider=ConnectionProvider.direct,
        external_account_id="page-9",
        access_token_encrypted=encrypt_text("page-token"),
        account_metadata={"page_id": "page-9"},
    )
    client = FacebookClient()
    published = asyncio.run(client.publish(account, {"text": "Hello Page"}, []))
    health = asyncio.run(client.health(account))
    metrics = asyncio.run(client.get_post_metrics(account, "page-9_55"))
    assert published.platform_post_id == "page-9_55"
    assert health["ok"] is True
    assert metrics.raw.get("available") is False
    assert any(method == "POST" and "/page-9/feed" in url for method, url, _ in calls)


def test_facebook_account_metrics_sum_daily_insights(monkeypatch):
    def handler(method, url, kwargs):
        assert method == "GET"
        assert url.endswith("/page-9/insights")
        return FakeResponse(
            {
                "data": [
                    {
                        "name": "page_impressions",
                        "values": [{"value": 10}, {"value": 15}],
                    },
                    {"name": "page_fans", "values": [{"value": 80}]},
                    {
                        "name": "page_post_engagements",
                        "values": [{"value": 3}, {"value": 4}],
                    },
                ]
            }
        )

    monkeypatch.setattr(facebook_mod.httpx, "AsyncClient", lambda *a, **k: FakeAsyncClient(handler))
    account = SocialAccount(
        platform="facebook",
        provider=ConnectionProvider.direct,
        external_account_id="page-9",
        access_token_encrypted=encrypt_text("page-token"),
        account_metadata={"page_id": "page-9"},
    )
    metrics = asyncio.run(
        FacebookClient().get_account_metrics(account, date(2026, 8, 1), date(2026, 8, 7))
    )
    assert metrics.impressions == 25
    assert metrics.followers == 80
    assert metrics.engagement == 7


def test_facebook_data_deletion_disables_matching_pages(monkeypatch):
    _enable_facebook(monkeypatch)
    with TestClient(app) as client:
        _register(client, "facebook-delete@example.com")
        with session_scope() as session:
            user = session.scalar(select(User).where(User.email == "facebook-delete@example.com"))
            workspace = workspace_for_user(session, user.id)
            session.add(
                SocialAccount(
                    workspace_id=workspace.id,
                    platform="facebook",
                    provider=ConnectionProvider.direct,
                    external_account_id="page-del",
                    username="To delete",
                    display_name="To delete",
                    access_token_encrypted=encrypt_text("page-token"),
                    account_metadata={"facebook_user_id": "user-9", "page_id": "page-del"},
                )
            )
        signed = _signed_request({"user_id": "user-9"}, "meta-secret")
        response = client.post(
            "/oauth/facebook/data-deletion",
            data={"signed_request": signed},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["confirmation_code"]
        assert body["url"].endswith("/data-deletion")
        with session_scope() as session:
            account = session.scalar(
                select(SocialAccount).where(SocialAccount.external_account_id == "page-del")
            )
            assert account.status == AccountStatus.disabled
            assert account.access_token_encrypted is None
