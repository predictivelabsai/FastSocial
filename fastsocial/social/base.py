from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol

from fastsocial.models import SocialAccount


@dataclass
class PublishResult:
    platform_post_id: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedMetrics:
    impressions: int = 0
    reach: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    clicks: int = 0
    saves: int = 0
    followers: int = 0
    engagement: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class InboxItem:
    conversation_id: str
    message_id: str
    kind: str
    body: str
    sent_at: datetime
    participant_name: str = ""
    participant_handle: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class AdMetricItem:
    platform: str
    campaign_id: str
    campaign_name: str
    metric_date: date
    currency: str = "EUR"
    status: str = "active"
    spend: float = 0
    impressions: int = 0
    clicks: int = 0
    conversions: float = 0
    revenue: float = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompetitorMetricItem:
    handle: str
    metric_date: date
    followers: int = 0
    posts: int = 0
    engagement: int = 0
    reach: int = 0
    engagement_rate: float = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ListeningItem:
    query: str
    platform: str
    external_id: str
    content: str
    published_at: datetime
    author_name: str = ""
    author_handle: str = ""
    url: str = ""
    sentiment: str = "neutral"
    reach: int = 0
    engagement: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


class SocialClient(Protocol):
    async def publish(
        self, account: SocialAccount, content: dict, media: list[dict]
    ) -> PublishResult: ...

    async def get_post_metrics(
        self, account: SocialAccount, platform_post_id: str
    ) -> NormalizedMetrics: ...

    async def get_account_metrics(
        self, account: SocialAccount, since: date, until: date
    ) -> NormalizedMetrics: ...

    async def health(self, account: SocialAccount) -> dict: ...

    async def reply_to_conversation(
        self, account: SocialAccount, conversation_id: str, body: str, kind: str
    ) -> str: ...

    async def collect_inbox(self, account: SocialAccount, since: datetime) -> list[InboxItem]: ...

    async def collect_ads(
        self, account: SocialAccount, since: date, until: date
    ) -> list[AdMetricItem]: ...

    async def collect_competitors(
        self, account: SocialAccount, handles: list[str], since: date, until: date
    ) -> list[CompetitorMetricItem]: ...

    async def collect_listening(
        self, account: SocialAccount, queries: list[str], since: datetime
    ) -> list[ListeningItem]: ...


class SocialAPIError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, status_code: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
