from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

from bs4 import BeautifulSoup
from sqlalchemy import select
from starlette.testclient import TestClient

import fastsocial.services as services
from fastsocial.app import app
from fastsocial.db import session_scope
from fastsocial.models import (
    AdCampaignDaily,
    CollectionRun,
    CompetitorMetricDaily,
    CompetitorPost,
    CompetitorProfile,
    ConnectionProvider,
    InboxConversation,
    InboxMessage,
    ListeningMention,
    ListeningQuery,
    SocialAccount,
    User,
    WebsiteEvent,
    WebsiteSite,
)
from fastsocial.services import collect_live_data, workspace_for_user
from fastsocial.social.base import AdMetricItem, CompetitorMetricItem, InboxItem, ListeningItem
from fastsocial.social.mcp import ManagedMCPClient


class FakeCollector:
    async def collect_inbox(self, account, since):
        return [
            InboxItem(
                conversation_id="conversation-live-1",
                message_id="message-live-1",
                kind="comment",
                body="A collected customer question",
                sent_at=datetime(2026, 8, 11, 8, 0, tzinfo=UTC),
                participant_name="Live Customer",
                participant_handle="live-customer",
                raw={"source": "fake"},
            )
        ]

    async def collect_ads(self, account, since, until):
        return [
            AdMetricItem(
                platform="meta",
                campaign_id="live-campaign",
                campaign_name="Live Campaign",
                metric_date=date(2026, 8, 11),
                spend=50,
                impressions=5000,
                clicks=250,
                conversions=10,
                revenue=200,
            )
        ]

    async def collect_competitors(self, account, handles, since, until):
        return [
            CompetitorMetricItem(
                handle=handles[0],
                metric_date=date(2026, 8, 11),
                followers=9000,
                posts=20,
                engagement=700,
                reach=15000,
                engagement_rate=4.6,
                raw={
                    "recent_posts": [
                        {
                            "post_id": "rival-post-1",
                            "published_at": "2026-08-10T09:00:00Z",
                            "type": "reel",
                            "caption": "A high-performing competitor reel",
                            "url": "https://example.com/rival-post-1",
                            "reach": 12000,
                            "likes": 450,
                            "comments": 30,
                            "shares": 20,
                        }
                    ]
                },
            )
        ]

    async def collect_listening(self, account, queries, since):
        return [
            ListeningItem(
                query=queries[0],
                platform="instagram",
                external_id="mention-live-1",
                content="FastSocial makes planning easier",
                published_at=datetime(2026, 8, 11, 9, 0, tzinfo=UTC),
                author_name="Industry Voice",
                sentiment="positive",
                reach=2000,
                engagement=80,
            )
        ]


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
            "name": "Collector User",
            "email": email,
            "password": "local-test-password",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_live_collectors_upsert_all_operational_surfaces(monkeypatch):
    email = "live-collector@example.com"
    with TestClient(app) as client:
        _register(client, email)
        with session_scope() as session:
            user = session.scalar(select(User).where(User.email == email))
            workspace = workspace_for_user(session, user.id)
            account = SocialAccount(
                workspace_id=workspace.id,
                platform="instagram",
                provider=ConnectionProvider.composio,
                external_account_id="live-instagram",
                username="live-brand",
                display_name="Live Brand",
                account_metadata={
                    "inbox_collect_tool": "INSTAGRAM_LIST_COMMENTS",
                    "ads_metrics_tool": "META_ADS_INSIGHTS",
                    "competitor_metrics_tool": "INSTAGRAM_PROFILE_INSIGHTS",
                },
            )
            session.add(account)
            session.flush()
            session.add(
                CompetitorProfile(
                    workspace_id=workspace.id,
                    platform="instagram",
                    handle="rival-brand",
                    display_name="Rival Brand",
                )
            )
            session.add(
                ListeningQuery(
                    workspace_id=workspace.id,
                    name="Brand mentions",
                    query="FastSocial",
                    kind="keyword",
                    platforms=["instagram"],
                    created_by=user.id,
                )
            )
            workspace_id = workspace.id

        fake = FakeCollector()
        monkeypatch.setattr(services, "client_for", lambda account: fake)
        result = asyncio.run(collect_live_data(workspace_id))
        assert result == {"inbox": 1, "ads": 1, "competitors": 2, "listening": 1}

        with session_scope() as session:
            conversation = session.scalar(
                select(InboxConversation).where(
                    InboxConversation.workspace_id == workspace_id,
                    InboxConversation.external_conversation_id == "conversation-live-1",
                )
            )
            message = session.scalar(
                select(InboxMessage).where(InboxMessage.conversation_id == conversation.id)
            )
            ad = session.scalar(
                select(AdCampaignDaily).where(AdCampaignDaily.workspace_id == workspace_id)
            )
            competitor = session.scalar(
                select(CompetitorMetricDaily)
                .join(CompetitorProfile)
                .where(CompetitorProfile.workspace_id == workspace_id)
            )
            competitor_post = session.scalar(
                select(CompetitorPost)
                .join(CompetitorProfile)
                .where(CompetitorProfile.workspace_id == workspace_id)
            )
            mention = session.scalar(
                select(ListeningMention)
                .join(ListeningQuery)
                .where(ListeningQuery.workspace_id == workspace_id)
            )
            runs = list(
                session.scalars(
                    select(CollectionRun).where(CollectionRun.workspace_id == workspace_id)
                )
            )
            assert message.body == "A collected customer question"
            assert ad.revenue == 200
            assert competitor.followers == 9000
            assert competitor_post.content_type == "reel"
            assert competitor_post.engagement == 500
            assert mention.sentiment == "positive"
            assert {run.collector_kind for run in runs} == {
                "inbox",
                "ads",
                "competitors",
                "listening",
            }
            assert all(run.status == "success" for run in runs)

        integrations = client.get("/integrations")
        assert "Instagram" in integrations.text
        assert "Collection activity" in integrations.text
        assert "2/2 records" in integrations.text
        assert "Brand mentions" in client.get("/listening").text

        websites = client.get("/websites")
        created_site = client.post(
            "/websites",
            data={"csrf": _csrf(websites), "name": "Product site", "domain": "example.com"},
            follow_redirects=False,
        )
        assert created_site.status_code == 303
        with session_scope() as session:
            website = session.scalar(
                select(WebsiteSite).where(WebsiteSite.workspace_id == workspace_id)
            )
            tracking_key = website.tracking_key
        pixel = client.get(
            f"/track/{tracking_key}.gif",
            params={"p": "/pricing", "r": "https://search.example/result", "v": "browser-1"},
        )
        assert pixel.status_code == 200
        assert pixel.headers["content-type"] == "image/gif"
        website_page = client.get("/websites")
        assert "/pricing" in website_page.text
        with session_scope() as session:
            event = session.scalar(select(WebsiteEvent).where(WebsiteEvent.site_id == website.id))
            assert event.referrer_domain == "search.example"
            assert len(event.visitor_hash) == 64


def test_managed_mcp_normalizes_structured_collection_results(monkeypatch):
    account = SocialAccount(
        platform="tiktok",
        provider=ConnectionProvider.arcade,
        external_account_id="managed-tiktok",
        account_metadata={
            "inbox_collect_tool": "TIKTOK_INBOX",
            "ads_metrics_tool": "TIKTOK_ADS_REPORT",
            "competitor_metrics_tool": "TIKTOK_COMPETITORS",
        },
    )
    client = ManagedMCPClient(ConnectionProvider.arcade)
    responses = [
        {
            "structuredContent": {
                "messages": [
                    {
                        "conversation_id": "thread-1",
                        "message_id": "comment-1",
                        "body": "Great video",
                        "created_at": "2026-08-11T08:00:00Z",
                    }
                ]
            }
        },
        {
            "campaigns": [
                {
                    "id": "campaign-1",
                    "name": "Video launch",
                    "date": "2026-08-11",
                    "spend": 25,
                    "impressions": 2500,
                }
            ]
        },
        {"profiles": [{"handle": "rival", "date": "2026-08-11", "followers": 12000}]},
    ]
    monkeypatch.setattr(client, "_call", AsyncMock(side_effect=responses))
    inbox = asyncio.run(client.collect_inbox(account, datetime(2026, 8, 10, tzinfo=UTC)))
    ads = asyncio.run(client.collect_ads(account, date(2026, 8, 10), date(2026, 8, 11)))
    competitors = asyncio.run(
        client.collect_competitors(account, ["rival"], date(2026, 8, 10), date(2026, 8, 11))
    )
    assert inbox[0].message_id == "comment-1"
    assert ads[0].campaign_name == "Video launch"
    assert competitors[0].followers == 12000
