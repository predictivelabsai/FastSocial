from __future__ import annotations

from bs4 import BeautifulSoup
from sqlalchemy import func, select
from starlette.testclient import TestClient

from fastsocial.agentic import create_chat_session
from fastsocial.app import app
from fastsocial.db import session_scope
from fastsocial.models import (
    ArtifactStatus,
    ChatSession,
    ContentArtifact,
    Post,
    User,
    WorkflowMode,
    WorkflowStage,
)
from fastsocial.services import workspace_for_user


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
        logout_form = BeautifulSoup(dashboard.text, "html.parser").select_one(
            'form[action="/auth/logout"]'
        )
        assert logout_form is not None
        assert logout_form.select_one('button[type="submit"]').get_text(strip=True) == "Log out"

        new_post = client.get("/new-post")
        assert new_post.status_code == 200
        assert "BYOK required" in new_post.text
        assert "YOLO" in new_post.text
        new_post_html = BeautifulSoup(new_post.text, "html.parser")
        assert new_post_html.select_one(".creation-chat-pane") is not None
        assert new_post_html.select_one(".creation-artifact-pane") is not None
        assert len(new_post_html.select(".prompt-suggestion-card")) == 4
        assert new_post_html.select_one(".chat-composer-box textarea[name='brief']") is not None

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

        logged_out = client.post(
            "/auth/logout",
            data={"csrf": _csrf(dashboard)},
            follow_redirects=False,
        )
        assert logged_out.status_code == 303
        assert logged_out.headers["location"] == "/signin"
        protected = client.get("/new-post", follow_redirects=False)
        assert protected.status_code == 303
        assert protected.headers["location"] == "/signin"


def test_health_and_static_css():
    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["service"] == "fastsocial"
        assert health.json()["version"] == "0.7.1"
        stylesheet = client.get("/static/app.css")
        assert stylesheet.status_code == 200
        assert "--accent" in stylesheet.text


def test_review_requires_explicit_approval_before_posting():
    email = "review-gate@example.com"
    with TestClient(app) as client:
        registration = client.get("/register")
        response = client.post(
            "/register",
            data={
                "csrf": _csrf(registration),
                "name": "Review Gate",
                "email": email,
                "password": "local-test-password",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303

        with session_scope() as session:
            user = session.scalar(select(User).where(User.email == email))
            workspace = workspace_for_user(session, user.id)
            workspace_id = workspace.id
            chat = create_chat_session(
                session,
                workspace_id=workspace.id,
                user_id=user.id,
                brief="Draft a factual product update.",
                workflow_mode=WorkflowMode.review,
                state={"platforms": ["x"], "target_ids": []},
            )
            chat.stage = WorkflowStage.review
            session.add(
                ContentArtifact(
                    chat_session_id=chat.id,
                    status=ArtifactStatus.review,
                    version=1,
                    content={
                        "text": "A factual product update.",
                        "variants": {"x": "A factual product update."},
                        "review": {"summary": "No unsupported claims.", "risks": []},
                    },
                    provider="xai",
                    model_name="test-model",
                )
            )
            chat_id = chat.id

        review = client.get(f"/chats/{chat_id}")
        assert review.status_code == 200
        assert "Approve for posting" in review.text
        assert "Publish now" not in review.text
        review_html = BeautifulSoup(review.text, "html.parser")
        assert review_html.select_one(".creation-chat-pane") is not None
        assert review_html.select_one(".creation-artifact-pane .artifact-copy") is not None
        assert len(review_html.select(".prompt-suggestion-card")) == 4
        assert review_html.select_one(".creation-artifact-pane textarea") is None
        blocked = client.post(
            f"/chats/{chat_id}/post",
            data={
                "csrf": _csrf(review),
                "text": "A factual product update.",
                "variant_x": "A factual product update.",
                "delivery": "draft",
            },
            follow_redirects=False,
        )
        assert blocked.status_code == 303
        assert "Approve+the+current+artifact" in blocked.headers["location"]
        approved = client.post(
            f"/chats/{chat_id}/approve",
            data={"csrf": _csrf(review)},
            follow_redirects=False,
        )
        assert approved.status_code == 303

        with session_scope() as session:
            artifact = session.scalar(
                select(ContentArtifact)
                .where(ContentArtifact.chat_session_id == chat_id)
                .order_by(ContentArtifact.version.desc())
            )
            chat = session.get(ChatSession, chat_id)
            assert artifact.status == ArtifactStatus.ready
            assert artifact.content["text"] == "A factual product update."
            assert chat.stage == WorkflowStage.post
            assert (
                session.scalar(select(func.count(Post.id)).where(Post.workspace_id == workspace_id))
                == 0
            )

        delivery = client.get(f"/chats/{chat_id}?saved=approved")
        assert "Content approved" in delivery.text
        assert "Publish now" in delivery.text
        saved = client.post(
            f"/chats/{chat_id}/post",
            data={"csrf": _csrf(delivery), "delivery": "draft"},
            follow_redirects=False,
        )
        assert saved.status_code == 303
        with session_scope() as session:
            post = session.scalar(select(Post).where(Post.workspace_id == workspace_id))
            assert post.content["text"] == "A factual product update."


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
