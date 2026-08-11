from __future__ import annotations

import base64
import importlib
import io

from bs4 import BeautifulSoup
from PIL import Image
from sqlalchemy import func, select
from starlette.testclient import TestClient

from fastsocial.app import app
from fastsocial.db import session_scope
from fastsocial.models import (
    AccountStatus,
    ConnectionProvider,
    ContentTemplate,
    InboxConversation,
    InboxConversationTag,
    InboxMessage,
    InboxModerationAction,
    Media,
    MediaSourceConnection,
    Post,
    PostStatus,
    SocialAccount,
    User,
)
from fastsocial.services import workspace_for_user

routes = importlib.import_module("fastsocial.routes")
services = importlib.import_module("fastsocial.services")


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
            "name": "Content Operator",
            "email": email,
            "password": "local-test-password",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def _workspace(email: str):
    with session_scope() as session:
        user = session.scalar(select(User).where(User.email == email))
        workspace = workspace_for_user(session, user.id)
        return user.id, workspace.id


def test_reusable_library_and_atomic_bulk_scheduler():
    email = "content-library@example.com"
    with TestClient(app) as client:
        _register(client, email)
        user_id, workspace_id = _workspace(email)
        with session_scope() as session:
            account = SocialAccount(
                workspace_id=workspace_id,
                platform="x",
                provider=ConnectionProvider.mock,
                external_account_id="bulk-x",
                username="bulk-x",
                display_name="Bulk X",
                status=AccountStatus.connected,
            )
            session.add(account)
            session.flush()
            account_id = account.id

        library = client.get("/library")
        created = client.post(
            "/library",
            data={
                "csrf": _csrf(library),
                "name": "Launch framework",
                "category": "Campaign",
                "tags": "launch, evergreen",
                "description": "A reusable launch brief.",
                "text": "Announce the product with one sharp benefit and a direct CTA.",
            },
            follow_redirects=False,
        )
        assert created.status_code == 303
        with session_scope() as session:
            template = session.scalar(
                select(ContentTemplate).where(ContentTemplate.workspace_id == workspace_id)
            )
            template_id = template.id

        prefilled = client.get(f"/new-post?template={template_id}")
        assert "Announce the product with one sharp benefit" in prefilled.text
        with session_scope() as session:
            assert session.get(ContentTemplate, template_id).use_count == 1

        csv_body = (
            "text,scheduled_at,x,linkedin\n"
            '"First bulk post","2026-09-01 09:00","Short X version",""\n'
            '"Second bulk post","2026-09-02 10:30","",""\n'
        )
        import_page = client.get("/posts/import")
        imported = client.post(
            "/posts/import",
            data={
                "csrf": _csrf(import_page),
                "mode": "schedule",
                "target_ids": str(account_id),
            },
            files={"file": ("posts.csv", csv_body.encode(), "text/csv")},
            follow_redirects=False,
        )
        assert imported.status_code == 303
        assert imported.headers["location"] == "/posts?imported=2"
        with session_scope() as session:
            posts = list(
                session.scalars(
                    select(Post)
                    .where(Post.workspace_id == workspace_id)
                    .order_by(Post.scheduled_at)
                )
            )
            assert len(posts) == 2
            assert all(item.status == PostStatus.scheduled for item in posts)
            assert posts[0].content["platform_text"]["x"] == "Short X version"
            first_post_id = posts[0].id

        posts_page = client.get("/posts")
        saved_template = client.post(
            f"/posts/{first_post_id}/save-template",
            data={"csrf": _csrf(posts_page), "name": "Winner copy"},
            follow_redirects=False,
        )
        assert saved_template.status_code == 303

        invalid_csv = "text,scheduled_at\nValid row,2026-09-03 09:00\nBroken row,\n"
        import_page = client.get("/posts/import")
        rejected = client.post(
            "/posts/import",
            data={
                "csrf": _csrf(import_page),
                "mode": "schedule",
                "target_ids": str(account_id),
            },
            files={"file": ("invalid.csv", invalid_csv.encode(), "text/csv")},
            follow_redirects=False,
        )
        assert rejected.status_code == 303
        assert "Row+3" in rejected.headers["location"]
        with session_scope() as session:
            assert (
                session.scalar(select(func.count(Post.id)).where(Post.workspace_id == workspace_id))
                == 2
            )
            assert (
                session.scalar(
                    select(func.count(ContentTemplate.id)).where(
                        ContentTemplate.workspace_id == workspace_id
                    )
                )
                == 2
            )


def test_managed_media_bank_browse_and_import(monkeypatch):
    async def fake_tool(_self, *, workspace_id, metadata, tool, arguments):
        assert workspace_id
        assert metadata["managed_user_id"]
        assert arguments["account_id"] == "drive-account"
        if tool == "GOOGLEDRIVE_SEARCH_FILES":
            return {
                "structuredContent": {
                    "files": [
                        {
                            "file_id": "file-1",
                            "name": "campaign-hero.png",
                            "mime_type": "image/png",
                        }
                    ]
                }
            }
        image = Image.new("RGB", (4, 3), color=(65, 130, 92))
        output = io.BytesIO()
        image.save(output, format="PNG")
        return {
            "structuredContent": {
                "filename": "campaign-hero.png",
                "mime_type": "image/png",
                "content_base64": base64.b64encode(output.getvalue()).decode(),
            }
        }

    monkeypatch.setattr(routes.ManagedMCPClient, "call_tool", fake_tool)
    email = "media-bank@example.com"
    with TestClient(app) as client:
        _register(client, email)
        _user_id, workspace_id = _workspace(email)
        integrations = client.get("/integrations")
        connected = client.post(
            "/integrations/media-sources",
            data={
                "csrf": _csrf(integrations),
                "source_provider": "google_drive",
                "connector_provider": "composio",
                "name": "Campaign Drive",
                "external_account_id": "drive-account",
                "managed_user_id": "managed-user",
                "list_tool": "GOOGLEDRIVE_SEARCH_FILES",
                "download_tool": "GOOGLEDRIVE_DOWNLOAD_FILE",
            },
            follow_redirects=False,
        )
        assert connected.status_code == 303
        with session_scope() as session:
            source = session.scalar(
                select(MediaSourceConnection).where(
                    MediaSourceConnection.workspace_id == workspace_id
                )
            )
            source_id = source.id

        browser = client.get(f"/media?source={source_id}&q=campaign")
        assert browser.status_code == 200
        assert "campaign-hero.png" in browser.text
        imported = client.post(
            f"/media/import/{source_id}",
            data={"csrf": _csrf(browser), "file_id": "file-1"},
            follow_redirects=False,
        )
        assert imported.status_code == 303
        with session_scope() as session:
            item = session.scalar(select(Media).where(Media.workspace_id == workspace_id))
            source = session.get(MediaSourceConnection, source_id)
            assert item.filename == "campaign-hero.png"
            assert (item.width, item.height) == (4, 3)
            assert source.status == "connected"


def test_inbox_bulk_notes_labels_and_provider_moderation(monkeypatch):
    class FakeModerationClient:
        async def moderate_conversation(self, account, conversation_id, action, kind):
            assert account.platform == "instagram"
            assert conversation_id == "comment-1"
            assert action == "hide"
            assert kind == "comment"
            return "moderation-1"

    monkeypatch.setattr(services, "client_for", lambda _account: FakeModerationClient())
    email = "inbox-moderation@example.com"
    with TestClient(app) as client:
        _register(client, email)
        user_id, workspace_id = _workspace(email)
        with session_scope() as session:
            account = SocialAccount(
                workspace_id=workspace_id,
                platform="instagram",
                provider=ConnectionProvider.composio,
                external_account_id="instagram-account",
                username="brand",
                display_name="Brand",
                status=AccountStatus.connected,
                account_metadata={"inbox_moderation_tool": "INSTAGRAM_MODERATE_COMMENT"},
            )
            session.add(account)
            session.flush()
            first = InboxConversation(
                workspace_id=workspace_id,
                social_account_id=account.id,
                platform="instagram",
                external_conversation_id="comment-1",
                participant_name="Customer One",
                kind="comment",
                status="unread",
            )
            second = InboxConversation(
                workspace_id=workspace_id,
                social_account_id=account.id,
                platform="instagram",
                external_conversation_id="comment-2",
                participant_name="Customer Two",
                kind="comment",
                status="unread",
            )
            session.add_all([first, second])
            session.flush()
            first_id, second_id = first.id, second.id

        inbox = client.get("/inbox")
        bulk = client.post(
            "/inbox/bulk",
            data={
                "csrf": _csrf(inbox),
                "action": "resolved",
                "conversation_ids": [str(first_id), str(second_id)],
            },
            follow_redirects=False,
        )
        assert bulk.status_code == 303

        detail = client.get(f"/inbox/{first_id}")
        note = client.post(
            f"/inbox/{first_id}/notes",
            data={"csrf": _csrf(detail), "body": "Follow up after the launch."},
            follow_redirects=False,
        )
        assert note.status_code == 303
        detail = client.get(f"/inbox/{first_id}")
        tag = client.post(
            f"/inbox/{first_id}/tags",
            data={"csrf": _csrf(detail), "name": "VIP Customer"},
            follow_redirects=False,
        )
        assert tag.status_code == 303
        detail = client.get(f"/inbox/{first_id}")
        moderated = client.post(
            f"/inbox/{first_id}/moderate",
            data={"csrf": _csrf(detail), "action": "hide"},
            follow_redirects=False,
        )
        assert moderated.status_code == 303

    with session_scope() as session:
        assert session.get(InboxConversation, first_id).status == "resolved"
        assert session.get(InboxConversation, second_id).status == "resolved"
        internal = session.scalar(
            select(InboxMessage).where(
                InboxMessage.conversation_id == first_id,
                InboxMessage.direction == "internal",
            )
        )
        assert internal.body == "Follow up after the launch."
        tag = session.scalar(
            select(InboxConversationTag).where(InboxConversationTag.conversation_id == first_id)
        )
        assert tag.name == "vip-customer"
        action = session.scalar(
            select(InboxModerationAction).where(InboxModerationAction.conversation_id == first_id)
        )
        assert action.status == "completed"
        assert action.external_action_id == "moderation-1"
        assert action.requested_by == user_id
