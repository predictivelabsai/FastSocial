from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import select

from fastsocial.db import session_scope
from fastsocial.models import (
    ConnectionProvider,
    Post,
    PostStatus,
    SocialAccount,
    TargetStatus,
    utcnow,
)
from fastsocial.security import hash_password
from fastsocial.services import create_post, get_or_create_user, publish_post, workspace_for_user


def test_personal_workspace_and_mock_publish_pipeline():
    with session_scope() as session:
        user = get_or_create_user(session, "pipeline@example.com", name="Pipeline Test")
        user.password_hash = hash_password("correct horse battery staple")
        workspace = workspace_for_user(session, user.id)
        assert workspace is not None
        assert workspace.approval_required is False

        account = SocialAccount(
            workspace_id=workspace.id,
            platform="x",
            provider=ConnectionProvider.mock,
            external_account_id="mock-pipeline",
            username="pipeline",
            display_name="Pipeline",
        )
        session.add(account)
        session.flush()
        post = create_post(
            session,
            workspace=workspace,
            user_id=user.id,
            text="A deterministic local publishing test.",
            target_ids=[account.id],
            scheduled_at=utcnow() - timedelta(seconds=1),
        )
        post_id = post.id

    asyncio.run(publish_post(post_id))

    with session_scope() as session:
        published = session.scalar(select(Post).where(Post.id == post_id))
        assert published is not None
        assert published.status == PostStatus.published
        assert published.published_at is not None
        assert len(published.targets) == 1
        assert published.targets[0].status == TargetStatus.published
        assert published.targets[0].platform_post_id.startswith("mock_")


def test_content_limits_are_checked_for_selected_platform():
    with session_scope() as session:
        user = get_or_create_user(session, "limits@example.com", name="Limits")
        workspace = workspace_for_user(session, user.id)
        account = SocialAccount(
            workspace_id=workspace.id,
            platform="x",
            provider=ConnectionProvider.mock,
            external_account_id="mock-limits",
        )
        session.add(account)
        session.flush()
        try:
            create_post(
                session,
                workspace=workspace,
                user_id=user.id,
                text="x" * 281,
                target_ids=[account.id],
                scheduled_at=utcnow(),
            )
        except ValueError as exc:
            assert "280" in str(exc)
        else:
            raise AssertionError("Expected X content limit validation")
