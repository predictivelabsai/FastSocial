from __future__ import annotations

import base64
import hashlib
import time
from datetime import UTC, date
from typing import Any

import httpx

from fastsocial.config import settings
from fastsocial.models import SocialAccount
from fastsocial.security import decrypt_text, encrypt_text
from fastsocial.social.base import NormalizedMetrics, PublishResult, SocialAPIError
from fastsocial.storage import media_storage


def _raise(response: httpx.Response) -> None:
    if response.is_success:
        return
    retryable = response.status_code == 429 or response.status_code >= 500
    detail = response.text[:500]
    raise SocialAPIError(
        f"Social API returned {response.status_code}: {detail}",
        retryable=retryable,
        status_code=response.status_code,
    )


class MockClient:
    async def publish(
        self, account: SocialAccount, content: dict, media: list[dict]
    ) -> PublishResult:
        seed = f"{account.id}:{content.get('text', '')}:{time.time_ns()}"
        post_id = "mock_" + hashlib.sha256(seed.encode()).hexdigest()[:18]
        return PublishResult(post_id, {"mock": True, "media_count": len(media)})

    async def get_post_metrics(self, account, platform_post_id: str) -> NormalizedMetrics:
        seed = int(hashlib.sha256(platform_post_id.encode()).hexdigest()[:8], 16)
        return NormalizedMetrics(
            impressions=100 + seed % 1000,
            reach=80 + seed % 700,
            likes=5 + seed % 90,
            comments=seed % 20,
            shares=seed % 30,
            clicks=seed % 50,
            raw={"mock": True},
        )

    async def get_account_metrics(self, account, since: date, until: date) -> NormalizedMetrics:
        return NormalizedMetrics(
            followers=1250, impressions=8400, engagement=530, reach=6100, raw={"mock": True}
        )

    async def health(self, account: SocialAccount) -> dict:
        return {"ok": True, "provider": "mock"}

    async def reply_to_conversation(
        self, account: SocialAccount, conversation_id: str, body: str, kind: str
    ) -> str:
        seed = f"{account.id}:{conversation_id}:{body}:{time.time_ns()}"
        return "mock_reply_" + hashlib.sha256(seed.encode()).hexdigest()[:18]


class XClient:
    base_url = "https://api.x.com/2"

    def _headers(self, account: SocialAccount) -> dict[str, str]:
        return {"Authorization": f"Bearer {decrypt_text(account.access_token_encrypted)}"}

    async def _ensure_token(self, account: SocialAccount) -> None:
        from datetime import UTC, datetime, timedelta

        if not account.token_expires_at:
            return
        expires = account.token_expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires > datetime.now(UTC) + timedelta(minutes=2):
            return
        refresh_token = decrypt_text(account.refresh_token_encrypted)
        if not refresh_token:
            raise SocialAPIError("X access token expired and no refresh token is available")
        cfg = settings()
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.x.com/2/oauth2/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": cfg.x_client_id,
                },
                auth=(cfg.x_client_id, cfg.x_client_secret) if cfg.x_client_secret else None,
            )
        _raise(response)
        body = response.json()
        account.access_token_encrypted = encrypt_text(body["access_token"])
        if body.get("refresh_token"):
            account.refresh_token_encrypted = encrypt_text(body["refresh_token"])
        account.token_expires_at = datetime.now(UTC) + timedelta(
            seconds=int(body.get("expires_in", 7200))
        )

    async def _upload_images(self, account: SocialAccount, media: list[dict]) -> list[str]:
        if len(media) > 4:
            raise SocialAPIError("X supports at most four images in one post")
        media_ids: list[str] = []
        for item in media:
            mime_type = item.get("mime_type", "")
            if not mime_type.startswith("image/"):
                raise SocialAPIError(
                    "Direct X video upload requires a chunked upload; use a managed connector"
                )
            raw = media_storage().get(item["storage_key"])
            payload = {
                "media": base64.b64encode(raw).decode(),
                "media_category": "tweet_image",
                "media_type": mime_type,
                "shared": False,
            }
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    f"{self.base_url}/media/upload",
                    headers={**self._headers(account), "Content-Type": "application/json"},
                    json=payload,
                )
            _raise(response)
            media_id = str(response.json().get("data", {}).get("id") or "")
            if not media_id:
                raise SocialAPIError("X media upload did not return a media ID")
            media_ids.append(media_id)
        return media_ids

    async def publish(
        self, account: SocialAccount, content: dict, media: list[dict]
    ) -> PublishResult:
        await self._ensure_token(account)
        payload: dict[str, Any] = {
            "text": content.get("platform_text", {}).get("x") or content.get("text", "")
        }
        media_ids = await self._upload_images(account, media) if media else []
        if media_ids:
            payload["media"] = {"media_ids": media_ids}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.base_url}/tweets", headers=self._headers(account), json=payload
            )
        _raise(response)
        body = response.json()
        return PublishResult(body["data"]["id"], body)

    async def get_post_metrics(self, account, platform_post_id: str) -> NormalizedMetrics:
        await self._ensure_token(account)
        params = {"tweet.fields": "public_metrics,non_public_metrics,organic_metrics"}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{self.base_url}/tweets/{platform_post_id}",
                headers=self._headers(account),
                params=params,
            )
        _raise(response)
        body = response.json()
        values = body.get("data", {}).get("public_metrics", {})
        return NormalizedMetrics(
            impressions=values.get("impression_count", 0),
            likes=values.get("like_count", 0),
            comments=values.get("reply_count", 0),
            shares=values.get("retweet_count", 0) + values.get("quote_count", 0),
            raw=body,
        )

    async def get_account_metrics(self, account, since: date, until: date) -> NormalizedMetrics:
        await self._ensure_token(account)
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{self.base_url}/users/me",
                headers=self._headers(account),
                params={"user.fields": "public_metrics"},
            )
        _raise(response)
        body = response.json()
        metrics = body.get("data", {}).get("public_metrics", {})
        return NormalizedMetrics(followers=metrics.get("followers_count", 0), raw=body)

    async def health(self, account: SocialAccount) -> dict:
        await self._ensure_token(account)
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"{self.base_url}/users/me", headers=self._headers(account))
        return {"ok": response.is_success, "status_code": response.status_code}


class LinkedInClient:
    base_url = "https://api.linkedin.com/rest"

    def _headers(self, account: SocialAccount) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {decrypt_text(account.access_token_encrypted)}",
            "Linkedin-Version": settings().linkedin_api_version,
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        }

    async def publish(
        self, account: SocialAccount, content: dict, media: list[dict]
    ) -> PublishResult:
        text = content.get("platform_text", {}).get("linkedin") or content.get("text", "")
        payload: dict[str, Any] = {
            "author": account.external_account_id,
            "commentary": text,
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        media_urns = await self._upload_images(account, media) if media else []
        if media_urns:
            payload["content"] = {
                "media": {
                    "id": media_urns[0],
                    "altText": media[0].get("alt_text", "")[:4086],
                }
            }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.base_url}/posts", headers=self._headers(account), json=payload
            )
        _raise(response)
        post_id = response.headers.get("x-restli-id", "")
        if not post_id:
            raise SocialAPIError("LinkedIn did not return a post ID")
        return PublishResult(post_id, {"headers": dict(response.headers)})

    async def _upload_images(self, account: SocialAccount, media: list[dict]) -> list[str]:
        if len(media) > 1:
            raise SocialAPIError(
                "Direct LinkedIn supports one image here; use a managed connector for multi-image posts"
            )
        urns: list[str] = []
        for item in media:
            if not item.get("mime_type", "").startswith("image/"):
                raise SocialAPIError(
                    "Direct LinkedIn video upload is not enabled; use a managed connector"
                )
            async with httpx.AsyncClient(timeout=60) as client:
                initialize = await client.post(
                    f"{self.base_url}/images?action=initializeUpload",
                    headers=self._headers(account),
                    json={"initializeUploadRequest": {"owner": account.external_account_id}},
                )
                _raise(initialize)
                details = initialize.json().get("value", {})
                upload_url = details.get("uploadUrl")
                image_urn = details.get("image")
                if not upload_url or not image_urn:
                    raise SocialAPIError("LinkedIn image initialization returned incomplete data")
                uploaded = await client.put(
                    upload_url,
                    headers={
                        "Authorization": self._headers(account)["Authorization"],
                        "Content-Type": item["mime_type"],
                    },
                    content=media_storage().get(item["storage_key"]),
                )
                _raise(uploaded)
                urns.append(image_urn)
        return urns

    async def get_post_metrics(self, account, platform_post_id: str) -> NormalizedMetrics:
        # LinkedIn post analytics require approved community-management scopes.
        return NormalizedMetrics(raw={"available": False, "reason": "restricted_scope"})

    async def get_account_metrics(self, account, since: date, until: date) -> NormalizedMetrics:
        return NormalizedMetrics(raw={"available": False, "reason": "restricted_scope"})

    async def health(self, account: SocialAccount) -> dict:
        token = decrypt_text(account.access_token_encrypted)
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                "https://api.linkedin.com/v2/userinfo", headers={"Authorization": f"Bearer {token}"}
            )
        return {"ok": response.is_success, "status_code": response.status_code}


class BlueskyClient:
    base_url = "https://bsky.social/xrpc"

    async def _session(self, account: SocialAccount) -> tuple[str, str]:
        password = decrypt_text(account.access_token_encrypted)
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{self.base_url}/com.atproto.server.createSession",
                json={"identifier": account.username, "password": password},
            )
        _raise(response)
        body = response.json()
        return body["accessJwt"], body["did"]

    async def publish(
        self, account: SocialAccount, content: dict, media: list[dict]
    ) -> PublishResult:
        from datetime import datetime

        token, did = await self._session(account)
        text = content.get("platform_text", {}).get("bluesky") or content.get("text", "")
        record = {
            "$type": "app.bsky.feed.post",
            "text": text,
            "createdAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        async with httpx.AsyncClient(timeout=30) as client:
            if media:
                if len(media) > 4:
                    raise SocialAPIError("Bluesky supports at most four images in one post")
                images = []
                for item in media:
                    mime_type = item.get("mime_type", "")
                    if not mime_type.startswith("image/"):
                        raise SocialAPIError(
                            "Direct Bluesky video upload is not enabled; use a managed connector"
                        )
                    upload = await client.post(
                        f"{self.base_url}/com.atproto.repo.uploadBlob",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": mime_type,
                        },
                        content=media_storage().get(item["storage_key"]),
                    )
                    _raise(upload)
                    images.append({"alt": item.get("alt_text", ""), "image": upload.json()["blob"]})
                record["embed"] = {"$type": "app.bsky.embed.images", "images": images}
            response = await client.post(
                f"{self.base_url}/com.atproto.repo.createRecord",
                headers={"Authorization": f"Bearer {token}"},
                json={"repo": did, "collection": "app.bsky.feed.post", "record": record},
            )
        _raise(response)
        body = response.json()
        return PublishResult(body["uri"], body)

    async def get_post_metrics(self, account, platform_post_id: str) -> NormalizedMetrics:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{self.base_url}/app.bsky.feed.getPosts", params={"uris": platform_post_id}
            )
        _raise(response)
        body = response.json()
        post = (body.get("posts") or [{}])[0]
        return NormalizedMetrics(
            likes=post.get("likeCount", 0),
            comments=post.get("replyCount", 0),
            shares=post.get("repostCount", 0),
            raw=body,
        )

    async def get_account_metrics(self, account, since: date, until: date) -> NormalizedMetrics:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{self.base_url}/app.bsky.actor.getProfile", params={"actor": account.username}
            )
        _raise(response)
        body = response.json()
        return NormalizedMetrics(followers=body.get("followersCount", 0), raw=body)

    async def health(self, account: SocialAccount) -> dict:
        try:
            await self._session(account)
            return {"ok": True}
        except SocialAPIError as exc:
            return {"ok": False, "error": str(exc)}
