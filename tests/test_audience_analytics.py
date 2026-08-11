from __future__ import annotations

from datetime import date

from bs4 import BeautifulSoup
from sqlalchemy import select
from starlette.testclient import TestClient

from fastsocial.app import app
from fastsocial.db import session_scope
from fastsocial.models import (
    AccountMetricDaily,
    AudienceMetricDaily,
    ConnectionProvider,
    Post,
    PostMetric,
    PostStatus,
    PostTarget,
    SocialAccount,
    TargetStatus,
    User,
    utcnow,
)
from fastsocial.services import workspace_for_user
from fastsocial.social.base import audience_segments_from_payload


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
            "name": "Analytics User",
            "email": email,
            "password": "local-test-password",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_provider_audience_envelopes_are_normalized():
    segments = audience_segments_from_payload(
        {
            "demographics": {
                "country": {"Estonia": 62, "Finland": 38},
                "age": [
                    {"label": "25-34", "count": 425, "percentage": 42.5},
                    {"label": "35-44", "count": 310, "percentage": 31},
                ],
            }
        }
    )
    assert [(item.dimension, item.segment) for item in segments] == [
        ("country", "Estonia"),
        ("country", "Finland"),
        ("age", "25-34"),
        ("age", "35-44"),
    ]
    assert segments[-2].value == 425
    assert segments[-2].percentage == 42.5


def test_analytics_compares_content_types_and_audience_without_chart_javascript():
    email = "audience-analytics@example.com"
    with TestClient(app) as client:
        _register(client, email)
        with session_scope() as session:
            user = session.scalar(select(User).where(User.email == email))
            workspace = workspace_for_user(session, user.id)
            account = SocialAccount(
                workspace_id=workspace.id,
                platform="instagram",
                provider=ConnectionProvider.mock,
                external_account_id="analytics-instagram",
                username="analytics-brand",
                display_name="Analytics Brand",
            )
            session.add(account)
            session.flush()
            post = Post(
                workspace_id=workspace.id,
                created_by=user.id,
                status=PostStatus.published,
                published_at=utcnow(),
                content={"text": "A measured short-form video", "post_type": "reel"},
            )
            session.add(post)
            session.flush()
            target = PostTarget(
                post_id=post.id,
                social_account_id=account.id,
                platform_post_id="reel-1",
                status=TargetStatus.published,
                published_at=utcnow(),
            )
            session.add(target)
            session.flush()
            session.add_all(
                [
                    PostMetric(
                        post_target_id=target.id,
                        impressions=10000,
                        reach=8000,
                        likes=500,
                        comments=50,
                        shares=80,
                        saves=120,
                        clicks=200,
                        raw={"content_type": "reel"},
                    ),
                    AccountMetricDaily(
                        social_account_id=account.id,
                        metric_date=date.today(),
                        followers=15000,
                        impressions=10000,
                        reach=8000,
                        engagement=750,
                    ),
                    AudienceMetricDaily(
                        social_account_id=account.id,
                        metric_date=date.today(),
                        dimension="age",
                        segment="25-34",
                        value=4250,
                        percentage=42.5,
                    ),
                    AudienceMetricDaily(
                        social_account_id=account.id,
                        metric_date=date.today(),
                        dimension="country",
                        segment="Estonia",
                        value=6200,
                        percentage=62,
                    ),
                ]
            )

        page = client.get("/analytics?days=30&platform=instagram&content_type=reel")
        assert page.status_code == 200
        assert "Performance by content type" in page.text
        assert "25-34" in page.text
        assert "42.5%" in page.text
        assert "Estonia" in page.text
        assert "10,000" in page.text
        assert "750" in page.text
        markup = BeautifulSoup(page.text, "html.parser")
        assert markup.select_one('select[name="platform"] option[selected][value="instagram"]')
        assert markup.select_one('select[name="content_type"] option[selected][value="reel"]')
        assert not markup.select("script")
        export = client.get("/analytics/audience.csv?days=30&platform=instagram")
        assert export.status_code == 200
        assert "age,25-34,4250,42.5" in export.text
        assert "country,Estonia,6200,62.0" in export.text
