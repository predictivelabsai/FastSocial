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
        assert "Your agentic social studio" in landing.text
        assert "BYOK / BYOM" in landing.text
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
        assert "New Post" in dashboard.text
        assert "CHAT HISTORY" in dashboard.text
        assert "app.js" not in dashboard.text
        assert "chart.js" not in dashboard.text.lower()

        new_post = client.get("/new-post")
        assert new_post.status_code == 200
        assert "BYOK required" in new_post.text
        assert "YOLO" in new_post.text

        skills = client.get("/skills")
        assert skills.status_code == 200
        assert "editable marketing skills" in skills.text
        assert len(BeautifulSoup(skills.text, "html.parser").select("a.skill-card")) >= 45

        skill = client.get("/skills/social")
        assert skill.status_code == 200
        assert "skill-markdown" in skill.text
        saved_skill = client.post(
            "/skills/social",
            data={"csrf": _csrf(skill), "content": "# Social\n\nUse evidence and be concise."},
            follow_redirects=False,
        )
        assert saved_skill.status_code == 303

        integrations = client.get("/integrations")
        assert "BYOK / BYOM" in integrations.text
        saved_model = client.post(
            "/integrations/models",
            data={
                "csrf": _csrf(integrations),
                "provider": "xai",
                "api_key": "test-workspace-key",
                "text_model": "grok-test",
                "image_model": "grok-image-test",
                "video_model": "grok-video-test",
                "action": "save",
            },
            follow_redirects=False,
        )
        assert saved_model.status_code == 303
        integrations = client.get("/integrations")
        assert "••••-key" in integrations.text
        assert "test-workspace-key" not in integrations.text

        settings = client.get("/settings")
        changed = client.post(
            "/settings",
            data={
                "csrf": _csrf(settings),
                "name": "Local User",
                "workspace_name": "Local User's Workspace",
                "timezone": "Europe/Tallinn",
                "default_workflow_mode": "yolo",
                "default_model_provider": "xai",
            },
        )
        assert changed.status_code == 200
        assert 'option value="yolo" selected' in changed.text

        connect = client.get("/integrations/connect/x/mock")
        assert connect.status_code == 200
        connected = client.post(
            "/integrations/connect/x/mock",
            data={"csrf": _csrf(connect), "username": "local-demo"},
            follow_redirects=False,
        )
        assert connected.status_code == 303

        agent_composer = client.get("/new-post")
        agent_target = BeautifulSoup(agent_composer.text, "html.parser").select_one(
            'input[name="target_ids"]'
        )
        assert agent_target is not None
        assert agent_target.get("form") == "new-post-form"

        legacy_composer = client.get(
            "/compose",
            follow_redirects=False,
        )
        assert legacy_composer.status_code == 303
        assert legacy_composer.headers["location"] == "/new-post"


def test_health_and_static_css():
    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["service"] == "fastsocial"
        stylesheet = client.get("/static/app.css")
        assert stylesheet.status_code == 200
        assert "--accent" in stylesheet.text


def test_public_discovery_files_use_python_routes():
    with TestClient(app) as client:
        robots = client.get("/robots.txt")
        assert robots.status_code == 200
        assert robots.headers["content-type"].startswith("text/plain")
        assert "Sitemap:" in robots.text

        sitemap = client.get("/sitemap.xml")
        assert sitemap.status_code == 200
        assert sitemap.headers["content-type"].startswith("application/xml")
        assert "<urlset" in sitemap.text
