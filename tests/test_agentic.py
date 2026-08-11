from __future__ import annotations

import asyncio
import base64

import pytest
from sqlalchemy import select

from fastsocial import agentic
from fastsocial.db import session_scope
from fastsocial.model_provider import ModelAccessError, resolve_model
from fastsocial.models import (
    AgentEvent,
    AIProviderCredential,
    ChatSession,
    ContentArtifact,
    Media,
    WorkflowMode,
    WorkflowStage,
)
from fastsocial.security import encrypt_text
from fastsocial.services import get_or_create_user, workspace_for_user


def _workspace(email: str):
    with session_scope() as session:
        user = get_or_create_user(session, email, name="Agent Test")
        workspace = workspace_for_user(session, user.id)
        session.flush()
        return user.id, workspace.id


def test_non_allowlisted_user_requires_byok():
    _user_id, workspace_id = _workspace("model-gate@example.com")
    with session_scope() as session:
        with pytest.raises(ModelAccessError, match="Add your own"):
            resolve_model(
                session,
                workspace_id=workspace_id,
                user_email="model-gate@example.com",
                provider="xai",
                purpose="text",
            )


def test_agent_chat_generates_versioned_artifact_and_media(monkeypatch):
    user_id, workspace_id = _workspace("agent-flow@example.com")
    with session_scope() as session:
        session.add(
            AIProviderCredential(
                workspace_id=workspace_id,
                provider="xai",
                api_key_encrypted=encrypt_text("workspace-test-key"),
                masked_hint="••••-key",
                created_by=user_id,
            )
        )
        chat = agentic.create_chat_session(
            session,
            workspace_id=workspace_id,
            user_id=user_id,
            brief="Launch an analytics feature with an image and no invented claims.",
            workflow_mode=WorkflowMode.review,
            state={"provider": "xai", "platforms": ["x", "linkedin"], "media_kinds": ["image"]},
        )
        chat_id = chat.id

    async def fake_json(*_args, **_kwargs):
        return {
            "text": "Analytics should clarify decisions, not decorate dashboards.",
            "variants": {
                "x": "Analytics should clarify decisions, not decorate dashboards.",
                "linkedin": "A useful analytics workflow should clarify the next decision.",
            },
            "hashtags": ["analytics"],
            "image_prompt": "A calm editorial analytics dashboard, no text",
            "video_prompt": "A slow reveal of a clean analytics dashboard",
            "review": {"summary": "Claims checked.", "risks": []},
        }

    monkeypatch.setattr(agentic, "invoke_json", fake_json)
    artifact_id = asyncio.run(
        agentic.generate_chat_artifact(chat_id, user_email="agent-flow@example.com")
    )
    with session_scope() as session:
        chat = session.get(ChatSession, chat_id)
        artifact = session.get(ContentArtifact, artifact_id)
        events = list(
            session.scalars(select(AgentEvent).where(AgentEvent.chat_session_id == chat_id))
        )
        assert chat.stage == WorkflowStage.review
        assert "social" in chat.selected_skills
        assert "image" in chat.selected_skills
        assert artifact.content["variants"]["x"].startswith("Analytics")
        assert {event.stage for event in events} >= {"create", "generate", "review"}

    one_pixel_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )

    async def fake_image(_resolved, _prompt):
        return one_pixel_png, "image/png"

    monkeypatch.setattr(agentic, "generate_image_bytes", fake_image)
    media_id = asyncio.run(
        agentic.generate_media_for_chat(
            chat_id,
            user_id=user_id,
            user_email="agent-flow@example.com",
            kind="image",
            prompt="A calm dashboard",
        )
    )
    with session_scope() as session:
        assert session.get(Media, media_id).mime_type == "image/png"
