from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime

import httpx

from fastsocial.config import settings
from fastsocial.models import ConnectionProvider, SocialAccount
from fastsocial.social.base import (
    AdMetricItem,
    CompetitorMetricItem,
    InboxItem,
    ListeningItem,
    NormalizedMetrics,
    PublishResult,
    SocialAPIError,
    audience_segments_from_payload,
)


class ManagedMCPClient:
    """Calls provider-configured MCP tools without ever handling downstream OAuth tokens."""

    def __init__(self, provider: ConnectionProvider):
        cfg = settings()
        if provider == ConnectionProvider.arcade:
            self.url, self.api_key = cfg.arcade_mcp_url, cfg.arcade_api_key
            self.user_header = "Arcade-User-ID"
        else:
            self.url, self.api_key = cfg.composio_mcp_url, cfg.composio_api_key
            self.user_header = "X-User-ID"
        self.provider = provider.value

    async def call_tool(self, *, workspace_id, metadata: dict, tool: str, arguments: dict) -> dict:
        """Call a managed tool for social or adjacent workspace integrations."""
        if not self.url or not self.api_key:
            raise SocialAPIError(f"{self.provider.title()} MCP is not configured")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            self.user_header: metadata.get("managed_user_id", str(workspace_id)),
        }
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(self.url, headers=headers, json=payload)
        if not response.is_success:
            raise SocialAPIError(
                f"{self.provider.title()} MCP returned {response.status_code}: {response.text[:500]}",
                retryable=response.status_code == 429 or response.status_code >= 500,
                status_code=response.status_code,
            )
        try:
            if "text/event-stream" in response.headers.get("content-type", ""):
                events = [
                    json.loads(line.removeprefix("data:").strip())
                    for line in response.text.splitlines()
                    if line.startswith("data:") and line.removeprefix("data:").strip() != "[DONE]"
                ]
                body = events[-1] if events else {}
            else:
                body = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise SocialAPIError("MCP gateway returned an invalid response") from exc
        if body.get("error"):
            raise SocialAPIError(f"MCP tool error: {body['error']}")
        return body.get("result", body)

    async def _call(self, account: SocialAccount, tool: str, arguments: dict) -> dict:
        return await self.call_tool(
            workspace_id=account.workspace_id,
            metadata=account.account_metadata,
            tool=tool,
            arguments=arguments,
        )

    @staticmethod
    def _records(body: dict, preferred_key: str) -> list[dict]:
        """Accept common MCP structured and text-content result envelopes."""
        candidate = body.get("structuredContent", body)
        if isinstance(candidate, dict):
            for key in (preferred_key, "items", "data", "results"):
                if isinstance(candidate.get(key), list):
                    return [item for item in candidate[key] if isinstance(item, dict)]
        for item in body.get("content", []) if isinstance(body, dict) else []:
            if item.get("type") != "text":
                continue
            try:
                parsed = json.loads(item.get("text", ""))
            except (TypeError, ValueError):
                continue
            if isinstance(parsed, list):
                return [value for value in parsed if isinstance(value, dict)]
            if isinstance(parsed, dict):
                return ManagedMCPClient._records(parsed, preferred_key)
        return []

    @staticmethod
    def object_result(body: dict) -> dict:
        """Normalize one-object MCP results, including JSON text content."""
        candidate = body.get("structuredContent") if isinstance(body, dict) else None
        if isinstance(candidate, dict):
            return candidate
        for item in body.get("content", []) if isinstance(body, dict) else []:
            if item.get("type") != "text":
                continue
            try:
                parsed = json.loads(item.get("text", ""))
            except (TypeError, ValueError):
                continue
            if isinstance(parsed, dict):
                return parsed
        return body if isinstance(body, dict) else {}

    @staticmethod
    def _datetime(value: object) -> datetime:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return datetime.now(UTC)

    async def publish(
        self, account: SocialAccount, content: dict, media: list[dict]
    ) -> PublishResult:
        tool = account.account_metadata.get("publish_tool")
        if not tool:
            raise SocialAPIError("No publish MCP tool is configured for this account")
        body = await self._call(
            account,
            tool,
            {
                "account_id": account.external_account_id,
                "text": content.get("platform_text", {}).get(account.platform)
                or content.get("text", ""),
                "media": [
                    {
                        "url": item.get("url"),
                        "mime_type": item.get("mime_type"),
                        "filename": item.get("filename"),
                        "alt_text": item.get("alt_text", ""),
                    }
                    for item in media
                ],
            },
        )
        post_id = str(body.get("platform_post_id") or body.get("id") or "")
        if not post_id:
            raise SocialAPIError("MCP publish tool did not return a post ID")
        return PublishResult(post_id, body)

    async def get_post_metrics(self, account, platform_post_id: str) -> NormalizedMetrics:
        tool = account.account_metadata.get("metrics_tool")
        if not tool:
            return NormalizedMetrics(raw={"available": False, "reason": "tool_not_configured"})
        body = await self._call(account, tool, {"post_id": platform_post_id})
        return NormalizedMetrics(
            **{
                k: int(body.get(k, 0) or 0)
                for k in ("impressions", "reach", "likes", "comments", "shares", "clicks", "saves")
            },
            raw=body,
        )

    async def get_account_metrics(self, account, since: date, until: date) -> NormalizedMetrics:
        tool = account.account_metadata.get("account_metrics_tool")
        if not tool:
            return NormalizedMetrics(raw={"available": False, "reason": "tool_not_configured"})
        body = await self._call(
            account, tool, {"since": since.isoformat(), "until": until.isoformat()}
        )
        return NormalizedMetrics(
            followers=int(body.get("followers", 0) or 0),
            impressions=int(body.get("impressions", 0) or 0),
            engagement=int(body.get("engagement", 0) or 0),
            reach=int(body.get("reach", 0) or 0),
            audience=audience_segments_from_payload(body),
            raw=body,
        )

    async def health(self, account: SocialAccount) -> dict:
        try:
            tool = account.account_metadata.get("health_tool")
            if not tool:
                return {"ok": bool(self.url and self.api_key), "mode": "configuration_only"}
            body = await self._call(account, tool, {"account_id": account.external_account_id})
            return {"ok": bool(body.get("ok", True)), "raw": body}
        except SocialAPIError as exc:
            return {"ok": False, "error": str(exc)}

    async def reply_to_conversation(
        self, account: SocialAccount, conversation_id: str, body: str, kind: str
    ) -> str:
        tool = account.account_metadata.get("inbox_reply_tool")
        if not tool:
            raise SocialAPIError("No inbox reply MCP tool is configured for this account")
        result = await self._call(
            account,
            tool,
            {
                "account_id": account.external_account_id,
                "conversation_id": conversation_id,
                "body": body,
                "kind": kind,
            },
        )
        message_id = str(result.get("message_id") or result.get("id") or "")
        if not message_id:
            raise SocialAPIError("MCP inbox tool did not return a message ID")
        return message_id

    async def moderate_conversation(
        self, account: SocialAccount, conversation_id: str, action: str, kind: str
    ) -> str:
        tool = account.account_metadata.get("inbox_moderation_tool")
        if not tool:
            raise SocialAPIError("No Inbox moderation MCP tool is configured for this account")
        result = await self._call(
            account,
            tool,
            {
                "account_id": account.external_account_id,
                "conversation_id": conversation_id,
                "action": action,
                "kind": kind,
            },
        )
        return str(result.get("action_id") or result.get("id") or conversation_id)

    async def collect_inbox(self, account: SocialAccount, since: datetime) -> list[InboxItem]:
        tool = account.account_metadata.get("inbox_collect_tool")
        if not tool:
            return []
        result = await self._call(
            account,
            tool,
            {"account_id": account.external_account_id, "since": since.isoformat()},
        )
        return [
            InboxItem(
                conversation_id=str(
                    item.get("conversation_id") or item.get("thread_id") or item.get("id") or ""
                ),
                message_id=str(item.get("message_id") or item.get("id") or ""),
                kind=str(item.get("kind") or item.get("type") or "comment"),
                body=str(item.get("body") or item.get("text") or ""),
                sent_at=self._datetime(item.get("sent_at") or item.get("created_at")),
                participant_name=str(item.get("participant_name") or item.get("author_name") or ""),
                participant_handle=str(
                    item.get("participant_handle") or item.get("author_handle") or ""
                ),
                raw=item,
            )
            for item in self._records(result, "messages")
            if item.get("message_id") or item.get("id")
        ]

    async def collect_ads(
        self, account: SocialAccount, since: date, until: date
    ) -> list[AdMetricItem]:
        tool = account.account_metadata.get("ads_metrics_tool")
        if not tool:
            return []
        result = await self._call(
            account,
            tool,
            {
                "account_id": account.external_account_id,
                "since": since.isoformat(),
                "until": until.isoformat(),
            },
        )
        output = []
        for item in self._records(result, "campaigns"):
            campaign_id = str(item.get("campaign_id") or item.get("id") or "")
            if not campaign_id:
                continue
            try:
                metric_date = date.fromisoformat(str(item.get("date") or until.isoformat())[:10])
            except ValueError:
                metric_date = until
            output.append(
                AdMetricItem(
                    platform=str(item.get("platform") or account.platform),
                    campaign_id=campaign_id,
                    campaign_name=str(item.get("campaign_name") or item.get("name") or campaign_id),
                    metric_date=metric_date,
                    currency=str(item.get("currency") or "EUR")[:3].upper(),
                    status=str(item.get("status") or "active"),
                    spend=float(item.get("spend") or 0),
                    impressions=int(item.get("impressions") or 0),
                    clicks=int(item.get("clicks") or 0),
                    conversions=float(item.get("conversions") or 0),
                    revenue=float(item.get("revenue") or item.get("conversion_value") or 0),
                    raw=item,
                )
            )
        return output

    async def collect_competitors(
        self, account: SocialAccount, handles: list[str], since: date, until: date
    ) -> list[CompetitorMetricItem]:
        tool = account.account_metadata.get("competitor_metrics_tool")
        if not tool or not handles:
            return []
        result = await self._call(
            account,
            tool,
            {
                "account_id": account.external_account_id,
                "handles": handles,
                "since": since.isoformat(),
                "until": until.isoformat(),
            },
        )
        output = []
        for item in self._records(result, "profiles"):
            handle = str(item.get("handle") or item.get("username") or "").lstrip("@").lower()
            if not handle:
                continue
            try:
                metric_date = date.fromisoformat(str(item.get("date") or until.isoformat())[:10])
            except ValueError:
                metric_date = until
            output.append(
                CompetitorMetricItem(
                    handle=handle,
                    metric_date=metric_date,
                    followers=int(item.get("followers") or 0),
                    posts=int(item.get("posts") or item.get("post_count") or 0),
                    engagement=int(item.get("engagement") or item.get("engagements") or 0),
                    reach=int(item.get("reach") or 0),
                    engagement_rate=float(item.get("engagement_rate") or 0),
                    raw=item,
                )
            )
        return output

    async def collect_listening(
        self, account: SocialAccount, queries: list[str], since: datetime
    ) -> list[ListeningItem]:
        tool = account.account_metadata.get("listening_tool")
        if not tool or not queries:
            return []
        result = await self._call(
            account,
            tool,
            {
                "account_id": account.external_account_id,
                "queries": queries,
                "since": since.isoformat(),
            },
        )
        output = []
        for item in self._records(result, "mentions"):
            external_id = str(item.get("mention_id") or item.get("post_id") or item.get("id") or "")
            query = str(item.get("query") or "")
            if not external_id or not query:
                continue
            sentiment = str(item.get("sentiment") or "neutral").lower()
            if sentiment not in {"positive", "neutral", "negative"}:
                sentiment = "neutral"
            output.append(
                ListeningItem(
                    query=query,
                    platform=str(item.get("platform") or account.platform),
                    external_id=external_id,
                    content=str(item.get("content") or item.get("text") or ""),
                    published_at=self._datetime(item.get("published_at") or item.get("created_at")),
                    author_name=str(item.get("author_name") or ""),
                    author_handle=str(item.get("author_handle") or item.get("username") or ""),
                    url=str(item.get("url") or ""),
                    sentiment=sentiment,
                    reach=int(item.get("reach") or 0),
                    engagement=int(item.get("engagement") or item.get("engagements") or 0),
                    raw=item,
                )
            )
        return output
