from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
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


class SocialAPIError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, status_code: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
