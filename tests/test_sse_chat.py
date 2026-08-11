from __future__ import annotations

import uuid

from bs4 import BeautifulSoup
from sqlalchemy import select
from starlette.testclient import TestClient

from fastsocial import routes
from fastsocial.agentic import create_chat_session, record_event
from fastsocial.app import app
from fastsocial.db import session_scope
from fastsocial.models import (
    AIProviderCredential,
    ArtifactStatus,
    ChatSession,
    ContentArtifact,
    User,
    WorkflowMode,
    WorkflowStage,
)
from fastsocial.security import encrypt_text
from fastsocial.services import workspace_for_user


def _csrf(response) -> str:
    field = BeautifulSoup(response.text, "html.parser").select_one('input[name="csrf"]')
    assert field is not None
    return field["value"]


def _register(client: TestClient, email: str) -> None:
    page = client.get("/register")
    response = client.post(
        "/register",
        data={
            "csrf": _csrf(page),
            "name": "SSE Test",
            "email": email,
            "password": "local-test-password",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_new_post_runs_in_background_and_exposes_htmx_sse_trace(monkeypatch):
    email = f"sse-{uuid.uuid4()}@example.com"
    with TestClient(app) as client:
        _register(client, email)
        with session_scope() as session:
            user = session.scalar(select(User).where(User.email == email))
            workspace = workspace_for_user(session, user.id)
            session.add(
                AIProviderCredential(
                    workspace_id=workspace.id,
                    provider="xai",
                    api_key_encrypted=encrypt_text("workspace-test-key"),
                    masked_hint="••••-key",
                    created_by=user.id,
                )
            )
            user_id = user.id
            workspace_id = workspace.id

        async def fake_generate(chat_id, *, user_email):
            assert user_email == email
            with session_scope() as session:
                chat = session.get(ChatSession, chat_id)
                artifact = ContentArtifact(
                    chat_session_id=chat.id,
                    status=ArtifactStatus.review,
                    content={
                        "text": "A factual streamed draft.",
                        "variants": {},
                        "review": {"summary": "Claims checked.", "risks": []},
                    },
                    provider="xai",
                    model_name="test-model",
                )
                session.add(artifact)
                session.flush()
                chat.stage = WorkflowStage.review
                record_event(
                    session,
                    chat.id,
                    "review",
                    "completed",
                    "Editorial review completed",
                )
                return artifact.id

        monkeypatch.setattr(routes, "generate_chat_artifact", fake_generate)
        page = client.get("/new-post")
        response = client.post(
            "/new-post",
            data={
                "csrf": _csrf(page),
                "brief": "Create a concise product update.",
                "workflow_mode": "review",
                "delivery": "draft",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        chat_id = uuid.UUID(response.headers["location"].rsplit("/", 1)[-1])
        with session_scope() as session:
            chat = session.get(ChatSession, chat_id)
            assert chat.workspace_id == workspace_id
            assert chat.created_by == user_id
            assert chat.status == "awaiting_review"
            assert chat.stage == WorkflowStage.review

        # A queued run renders one declarative HTMX SSE connection and no custom chat JS.
        with session_scope() as session:
            queued = create_chat_session(
                session,
                workspace_id=workspace_id,
                user_id=user_id,
                brief="Queued SSE rendering check.",
                workflow_mode=WorkflowMode.review,
                state={"provider": "xai", "execution_action": "generate"},
            )
            queued.status = "queued"
            queued_id = queued.id
        queued_page = client.get(f"/chats/{queued_id}")
        markup = BeautifulSoup(queued_page.text, "html.parser")
        stream_root = markup.select_one('[hx-ext="sse"]')
        assert stream_root is not None
        assert stream_root["sse-connect"] == f"/chats/{queued_id}/events"
        assert markup.select_one('[hx-trigger="sse:update"]') is not None
        script_sources = [script.get("src", "") for script in markup.select("script[src]")]
        assert script_sources == [
            "https://cdn.jsdelivr.net/npm/htmx.org@2.0.10/dist/htmx.min.js",
            "https://cdn.jsdelivr.net/npm/htmx-ext-sse@2.2.4",
        ]
        assert "EventSource(" not in queued_page.text

        # Terminal streams refresh once and close, and failure details are redacted in the UI.
        with session_scope() as session:
            queued = session.get(ChatSession, queued_id)
            queued.status = "failed"
            queued.stage = WorkflowStage.failed
            record_event(
                session,
                queued.id,
                "generate",
                "failed",
                "provider leaked secret workspace-test-key",
            )
        failed_page = client.get(f"/chats/{queued_id}")
        assert "workspace-test-key" not in failed_page.text
        assert "Check the model integration" in failed_page.text
        stream = client.get(f"/chats/{queued_id}/events")
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        assert "event: update" in stream.text
        assert "event: done" in stream.text


def test_chat_stream_is_workspace_scoped():
    with TestClient(app) as owner_client, TestClient(app) as other_client:
        owner_email = f"sse-owner-{uuid.uuid4()}@example.com"
        other_email = f"sse-other-{uuid.uuid4()}@example.com"
        _register(owner_client, owner_email)
        _register(other_client, other_email)
        with session_scope() as session:
            owner = session.scalar(select(User).where(User.email == owner_email))
            workspace = workspace_for_user(session, owner.id)
            chat = create_chat_session(
                session,
                workspace_id=workspace.id,
                user_id=owner.id,
                brief="Private workspace trace.",
                workflow_mode=WorkflowMode.review,
                state={},
            )
            chat_id = chat.id

        assert other_client.get(f"/chats/{chat_id}/live").status_code == 404
        assert other_client.get(f"/chats/{chat_id}/events").status_code == 404
