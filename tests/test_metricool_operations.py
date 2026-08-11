from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from sqlalchemy import select
from starlette.testclient import TestClient

from fastsocial.app import app
from fastsocial.db import session_scope
from fastsocial.models import (
    AdCampaignDaily,
    AutolistItem,
    ConnectionProvider,
    ContentAutolist,
    InboxConversation,
    InboxMessage,
    Post,
    ReportRun,
    ReportSchedule,
    SavedReply,
    SocialAccount,
    User,
)
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
            "name": "Operations User",
            "email": email,
            "password": "local-test-password",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_operational_planner_inbox_ads_and_reports():
    email = "operations-parity@example.com"
    with TestClient(app) as client:
        _register(client, email)
        with session_scope() as session:
            user = session.scalar(select(User).where(User.email == email))
            workspace = workspace_for_user(session, user.id)
            account = SocialAccount(
                workspace_id=workspace.id,
                platform="x",
                provider=ConnectionProvider.mock,
                external_account_id="mock-operations",
                username="operations",
                display_name="Operations X",
            )
            session.add(account)
            session.flush()
            user_id, workspace_id, account_id = user.id, workspace.id, account.id

        autolists = client.get("/autolists")
        created = client.post(
            "/autolists",
            data={
                "csrf": _csrf(autolists),
                "name": "Evergreen lessons",
                "cadence": "weekly",
                "publish_time": "09:00",
                "target_ids": str(account_id),
            },
            follow_redirects=False,
        )
        assert created.status_code == 303
        with session_scope() as session:
            autolist = session.scalar(
                select(ContentAutolist).where(ContentAutolist.workspace_id == workspace_id)
            )
            autolist_id = autolist.id
        autolists = client.get("/autolists")
        added = client.post(
            f"/autolists/{autolist_id}/items",
            data={"csrf": _csrf(autolists), "text": "A durable evergreen insight."},
            follow_redirects=False,
        )
        assert added.status_code == 303
        autolists = client.get("/autolists")
        ran = client.post(
            f"/autolists/{autolist_id}/run",
            data={"csrf": _csrf(autolists)},
            follow_redirects=False,
        )
        assert ran.status_code == 303
        with session_scope() as session:
            post = session.scalar(select(Post).where(Post.workspace_id == workspace_id))
            item = session.scalar(
                select(AutolistItem).where(AutolistItem.autolist_id == autolist_id)
            )
            assert post.content["text"] == "A durable evergreen insight."
            assert item.used_count == 1
            post_id = post.id

        planner = client.get("/calendar")
        moved = client.post(
            "/api/planner/reschedule",
            data={
                "csrf": _csrf(planner),
                "post_id": str(post_id),
                "target_date": "2026-08-20",
            },
        )
        assert moved.status_code == 204
        with session_scope() as session:
            post = session.get(Post, post_id)
            value = post.scheduled_at
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            assert value.astimezone(ZoneInfo("Europe/Tallinn")).date().isoformat() == "2026-08-20"

        with session_scope() as session:
            conversation = InboxConversation(
                workspace_id=workspace_id,
                social_account_id=account_id,
                platform="x",
                external_conversation_id="thread-operations",
                participant_name="Customer",
                kind="comment",
                status="unread",
                last_message_preview="Is this available?",
                last_message_at=datetime.now(UTC),
            )
            session.add(conversation)
            session.flush()
            session.add(
                InboxMessage(
                    conversation_id=conversation.id,
                    external_message_id="inbound-1",
                    body="Is this available?",
                    sender_name="Customer",
                )
            )
            conversation_id = conversation.id

        detail = client.get(f"/inbox/{conversation_id}")
        assert "Is this available?" in detail.text
        saved_reply = client.post(
            "/inbox/saved-replies",
            data={
                "csrf": _csrf(detail),
                "title": "Availability",
                "shortcut": "available",
                "body": "Yes, it is available now.",
                "return_to": str(conversation_id),
            },
            follow_redirects=False,
        )
        assert saved_reply.status_code == 303
        detail = client.get(f"/inbox/{conversation_id}")
        sent = client.post(
            f"/inbox/{conversation_id}/reply",
            data={"csrf": _csrf(detail), "body": "Yes, it is available now."},
            follow_redirects=False,
        )
        assert sent.status_code == 303
        detail = client.get(f"/inbox/{conversation_id}")
        triaged = client.post(
            f"/inbox/{conversation_id}/triage",
            data={
                "csrf": _csrf(detail),
                "status": "resolved",
                "priority": "high",
                "assigned_to": str(user_id),
            },
            follow_redirects=False,
        )
        assert triaged.status_code == 303
        with session_scope() as session:
            conversation = session.get(InboxConversation, conversation_id)
            outbound = session.scalar(
                select(InboxMessage).where(
                    InboxMessage.conversation_id == conversation_id,
                    InboxMessage.direction == "outbound",
                )
            )
            assert conversation.status == "resolved"
            assert conversation.priority == "high"
            assert outbound.delivery_status == "sent"
            assert outbound.external_message_id.startswith("mock_reply_")
            assert session.scalar(select(SavedReply).where(SavedReply.workspace_id == workspace_id))

        ads = client.get("/ads")
        imported = client.post(
            "/ads/import",
            data={
                "csrf": _csrf(ads),
                "platform": "meta",
                "campaign_id": "campaign-1",
                "campaign_name": "Launch",
                "metric_date": "2026-08-11",
                "currency": "EUR",
                "spend": "100",
                "impressions": "10000",
                "clicks": "500",
                "conversions": "25",
                "revenue": "400",
            },
            follow_redirects=False,
        )
        assert imported.status_code == 303
        ads = client.get("/ads?days=30")
        assert "4.00×" in ads.text
        assert "Launch" in client.get("/ads/export.csv?days=30").text
        with session_scope() as session:
            assert (
                session.scalar(
                    select(AdCampaignDaily).where(AdCampaignDaily.workspace_id == workspace_id)
                ).clicks
                == 500
            )

        reports = client.get("/reports")
        scheduled = client.post(
            "/reports/schedules",
            data={
                "csrf": _csrf(reports),
                "name": "Weekly operator report",
                "frequency": "weekly",
                "report_days": "7",
                "recipients": "operator@example.com",
                "sections": ["performance", "competitors"],
            },
            follow_redirects=False,
        )
        assert scheduled.status_code == 303
        with session_scope() as session:
            schedule = session.scalar(
                select(ReportSchedule).where(ReportSchedule.workspace_id == workspace_id)
            )
            schedule_id = schedule.id
        reports = client.get("/reports")
        run = client.post(
            f"/reports/schedules/{schedule_id}/run",
            data={"csrf": _csrf(reports)},
            follow_redirects=False,
        )
        assert run.status_code == 303
        with session_scope() as session:
            report_run = session.scalar(
                select(ReportRun).where(ReportRun.schedule_id == schedule_id)
            )
            assert report_run.status == "generated"
            run_id = report_run.id
        artifact = client.get(f"/reports/runs/{run_id}")
        assert artifact.status_code == 200
        assert "Weekly operator report" not in artifact.text
        assert "FastSocial" in artifact.text
        assert client.get("/reports/print?days=7").status_code == 200

        with TestClient(app) as outsider:
            _register(outsider, "operations-outsider@example.com")
            assert outsider.get(f"/inbox/{conversation_id}").status_code == 404
            assert outsider.get(f"/reports/runs/{run_id}").status_code == 404
