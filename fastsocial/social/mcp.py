from __future__ import annotations

import json
import uuid
from datetime import date

import httpx

from fastsocial.config import settings
from fastsocial.models import ConnectionProvider, SocialAccount
from fastsocial.social.base import NormalizedMetrics, PublishResult, SocialAPIError


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

    async def _call(self, account: SocialAccount, tool: str, arguments: dict) -> dict:
        if not self.url or not self.api_key:
            raise SocialAPIError(f"{self.provider.title()} MCP is not configured")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            self.user_header: account.account_metadata.get(
                "managed_user_id", str(account.workspace_id)
            ),
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
