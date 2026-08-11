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
class AudienceSegment:
    dimension: str
    segment: str
    value: int = 0
    percentage: float = 0
    raw: dict[str, Any] = field(default_factory=dict)


def audience_segments_from_payload(payload: dict[str, Any]) -> list[AudienceSegment]:
    """Normalize common provider demographic envelopes without discarding their raw shape."""
    source = payload.get("audience") or payload.get("demographics") or []
    records: list[dict[str, Any]] = []
    if isinstance(source, list):
        records = [item for item in source if isinstance(item, dict)]
    elif isinstance(source, dict):
        for dimension, values in source.items():
            if isinstance(values, dict):
                records.extend(
                    {
                        "dimension": dimension,
                        "segment": segment,
                        "value": value,
                    }
                    for segment, value in values.items()
                )
            elif isinstance(values, list):
                records.extend(
                    {**item, "dimension": item.get("dimension") or dimension}
                    for item in values
                    if isinstance(item, dict)
                )

    normalized: list[AudienceSegment] = []
    for item in records:
        dimension = str(item.get("dimension") or item.get("type") or "").strip().lower()
        segment = str(
            item.get("segment") or item.get("label") or item.get("name") or item.get("key") or ""
        ).strip()
        if not dimension or not segment:
            continue
        raw_value = item.get("value", item.get("count", 0))
        raw_percentage = item.get("percentage", item.get("percent", item.get("share", 0)))
        try:
            value = max(0, int(float(raw_value or 0)))
        except (TypeError, ValueError):
            value = 0
        try:
            percentage = max(0, float(raw_percentage or 0))
        except (TypeError, ValueError):
            percentage = 0
        normalized.append(
            AudienceSegment(
                dimension=dimension[:60],
                segment=segment[:255],
                value=value,
                percentage=percentage,
                raw=item,
            )
        )
    return normalized


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
    audience: list[AudienceSegment] = field(default_factory=list)
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

    async def moderate_conversation(
        self, account: SocialAccount, conversation_id: str, action: str, kind: str
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
