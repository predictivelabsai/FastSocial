from __future__ import annotations

from bs4 import BeautifulSoup
from starlette.testclient import TestClient

from fastsocial.app import app


def _csrf(response) -> str:
    soup = BeautifulSoup(response.text, "html.parser")
    field = soup.select_one('input[name="csrf"]')
    assert field is not None
    return field["value"]


def test_anonymous_landing_and_password_registration():
    with TestClient(app) as client:
        landing = client.get("/")
        assert landing.status_code == 200
        assert "Plan your voice" in landing.text
        assert 'href="/signin"' in landing.text
        assert "<script" not in landing.text.lower()

        registration = client.get("/register")
        response = client.post(
            "/register",
            data={
                "csrf": _csrf(registration),
                "name": "Local User",
                "email": "local@example.com",
                "password": "local-test-password",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        dashboard = client.get("/")
        assert dashboard.status_code == 200
        assert "Dashboard" in dashboard.text
        assert "Integrations" in dashboard.text
        assert "app.js" not in dashboard.text
        assert "chart.js" not in dashboard.text.lower()

        connect = client.get("/integrations/connect/x/mock")
        assert connect.status_code == 200
        connected = client.post(
            "/integrations/connect/x/mock",
            data={"csrf": _csrf(connect), "username": "local-demo"},
            follow_redirects=False,
        )
        assert connected.status_code == 303

        composer = client.get("/compose")
        soup = BeautifulSoup(composer.text, "html.parser")
        target = soup.select_one('input[name="target_ids"]')
        assert target is not None
        published = client.post(
            "/compose",
            data={
                "csrf": _csrf(composer),
                "text": "Published through the safe local provider.",
                "target_ids": target["value"],
                "publish_mode": "now",
                "action": "schedule",
            },
            follow_redirects=False,
        )
        assert published.status_code == 303
        detail = client.get(published.headers["location"])
        assert "Published" in detail.text


def test_health_and_static_css():
    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["service"] == "fastsocial"
        stylesheet = client.get("/static/app.css")
        assert stylesheet.status_code == 200
        assert "--accent" in stylesheet.text
