from __future__ import annotations

from bs4 import BeautifulSoup
from sqlalchemy import select
from starlette.testclient import TestClient

from fastsocial.app import app
from fastsocial.db import session_scope
from fastsocial.models import (
    CompetitorMetricDaily,
    CompetitorProfile,
    InboxConversation,
    InboxMessage,
    ReportSchedule,
    SmartLinkItem,
    SmartLinkPage,
    User,
)
from fastsocial.services import workspace_for_user


def _csrf(response) -> str:
    field = BeautifulSoup(response.text, "html.parser").select_one('input[name="csrf"]')
    assert field is not None
    return field["value"]


def _register(client: TestClient, email: str) -> None:
    registration = client.get("/register")
    response = client.post(
        "/register",
        data={
            "csrf": _csrf(registration),
            "name": "Parity User",
            "email": email,
            "password": "local-test-password",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_metricool_parity_routes_are_tenant_safe_and_functional():
    email = "parity@example.com"
    with TestClient(app) as client:
        _register(client, email)

        planner = client.get("/calendar")
        assert planner.status_code == 200
        assert "Best times to publish" in planner.text
        assert "Starter benchmark" in planner.text
        assert client.get("/calendar?view=week").status_code == 200
        assert client.get("/calendar?view=list").status_code == 200

        competitors = client.get("/competitors")
        created = client.post(
            "/competitors",
            data={
                "csrf": _csrf(competitors),
                "platform": "linkedin",
                "handle": "example-rival",
                "display_name": "Example Rival",
                "profile_url": "https://www.linkedin.com/company/example-rival",
            },
            follow_redirects=False,
        )
        assert created.status_code == 303

        with session_scope() as session:
            user = session.scalar(select(User).where(User.email == email))
            workspace = workspace_for_user(session, user.id)
            competitor = session.scalar(
                select(CompetitorProfile).where(
                    CompetitorProfile.workspace_id == workspace.id,
                    CompetitorProfile.handle == "example-rival",
                )
            )
            competitor_id = competitor.id

        competitors = client.get("/competitors")
        snapshot = client.post(
            f"/competitors/{competitor_id}/snapshot",
            data={
                "csrf": _csrf(competitors),
                "metric_date": "2026-08-11",
                "followers": "12500",
                "posts": "42",
                "engagement": "910",
                "reach": "25000",
                "engagement_rate": "3.64",
            },
            follow_redirects=False,
        )
        assert snapshot.status_code == 303
        competitors = client.get("/competitors")
        assert "12,500" in competitors.text
        assert "3.64%" in competitors.text
        export = client.get("/competitors/export.csv")
        assert export.status_code == 200
        assert "example-rival" in export.text

        reports = client.get("/reports")
        scheduled = client.post(
            "/reports/schedules",
            data={
                "csrf": _csrf(reports),
                "name": "Monthly brand report",
                "frequency": "monthly",
                "recipients": "owner@example.com",
                "sections": ["performance", "competitors"],
            },
            follow_redirects=False,
        )
        assert scheduled.status_code == 303
        reports = client.get("/reports")
        assert "Monthly brand report" in reports.text
        report_export = client.get("/reports/export.csv?days=30")
        assert report_export.status_code == 200
        assert "FastSocial brand report" in report_export.text
        assert "Example Rival" in report_export.text

        smartlinks = client.get("/smartlinks")
        created_page = client.post(
            "/smartlinks",
            data={
                "csrf": _csrf(smartlinks),
                "title": "Parity Links",
                "slug": "parity-links",
                "theme": "midnight",
            },
            follow_redirects=False,
        )
        assert created_page.status_code == 303
        detail_path = created_page.headers["location"]
        detail = client.get(detail_path)
        published = client.post(
            detail_path,
            data={
                "csrf": _csrf(detail),
                "title": "Parity Links",
                "bio": "Useful destinations.",
                "theme": "midnight",
                "published": "on",
            },
            follow_redirects=False,
        )
        assert published.status_code == 303
        detail = client.get(detail_path)
        added_link = client.post(
            f"{detail_path}/items",
            data={
                "csrf": _csrf(detail),
                "label": "Visit FastSocial",
                "url": "https://fastsocial.org/",
            },
            follow_redirects=False,
        )
        assert added_link.status_code == 303

        public = client.get("/s/parity-links")
        assert public.status_code == 200
        assert "Useful destinations." in public.text
        public_html = BeautifulSoup(public.text, "html.parser")
        tracked_link = public_html.select_one("a.smartlink-public-link")
        assert tracked_link is not None
        clicked = client.get(tracked_link["href"], follow_redirects=False)
        assert clicked.status_code == 302
        assert clicked.headers["location"] == "https://fastsocial.org/"

        with TestClient(app) as outsider:
            _register(outsider, "parity-outsider@example.com")
            assert outsider.get(detail_path).status_code == 404
            assert outsider.get("/s/parity-links").status_code == 200

        with session_scope() as session:
            page = session.scalar(select(SmartLinkPage).where(SmartLinkPage.slug == "parity-links"))
            item = session.scalar(select(SmartLinkItem).where(SmartLinkItem.page_id == page.id))
            schedule = session.scalar(
                select(ReportSchedule).where(ReportSchedule.workspace_id == workspace.id)
            )
            metric = session.scalar(
                select(CompetitorMetricDaily).where(
                    CompetitorMetricDaily.competitor_id == competitor_id
                )
            )
            assert page.view_count == 2
            assert item.click_count == 1
            assert schedule.recipients == ["owner@example.com"]
            assert metric.followers == 12500


def test_inbox_renders_collected_conversations():
    email = "inbox-parity@example.com"
    with TestClient(app) as client:
        _register(client, email)
        with session_scope() as session:
            user = session.scalar(select(User).where(User.email == email))
            workspace = workspace_for_user(session, user.id)
            conversation = InboxConversation(
                workspace_id=workspace.id,
                platform="instagram",
                external_conversation_id="comment-1",
                participant_name="Thoughtful Customer",
                participant_handle="thoughtful-customer",
                kind="comment",
                status="unread",
                last_message_preview="Can you share the launch date?",
            )
            session.add(conversation)
            session.flush()
            session.add(
                InboxMessage(
                    conversation_id=conversation.id,
                    external_message_id="message-1",
                    body="Can you share the launch date?",
                    sender_name="Thoughtful Customer",
                )
            )

        inbox = client.get("/inbox")
        assert inbox.status_code == 200
        assert "Thoughtful Customer" in inbox.text
        assert "Can you share the launch date?" in inbox.text
        assert "UNREAD" in inbox.text
