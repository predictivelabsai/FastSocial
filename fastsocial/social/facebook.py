from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from fastsocial.config import settings
from fastsocial.models import SocialAccount
from fastsocial.security import decrypt_json, decrypt_text, encrypt_json
from fastsocial.social.base import NormalizedMetrics, PublishResult, SocialAPIError
from fastsocial.storage import media_storage

log = logging.getLogger(__name__)

FACEBOOK_SCOPES = [
    "public_profile",
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_posts",
]
PAGE_SELECTION_TTL_SECONDS = 15 * 60
_PAGE_PUBLISH_TASKS = {"CREATE_CONTENT", "MANAGE"}


def _published_flag(unpublished: bool) -> str:
    """Graph form-encoded endpoints expect a lowercase string, not a Python bool."""
    return "false" if unpublished else "true"


def facebook_configured() -> bool:
    cfg = settings()
    return bool(cfg.meta_app_id and cfg.meta_app_secret)


def facebook_graph_version() -> str:
    return settings().meta_graph_api_version or "v25.0"


def facebook_graph_url(path: str) -> str:
    return f"https://graph.facebook.com/{facebook_graph_version()}/{path.lstrip('/')}"


def facebook_redirect_uri() -> str:
    return f"{settings().service_url}/oauth/facebook/callback"


def facebook_oauth_url(state: str) -> str:
    cfg = settings()
    params = {
        "client_id": cfg.meta_app_id,
        "redirect_uri": facebook_redirect_uri(),
        "state": state,
        "response_type": "code",
        "scope": ",".join(FACEBOOK_SCOPES),
    }
    return f"https://www.facebook.com/{facebook_graph_version()}/dialog/oauth?{urlencode(params)}"


def stash_facebook_page_selection(sess: dict, payload: dict[str, Any]) -> None:
    body = {**payload, "created_at": datetime.now(UTC).timestamp()}
    sess["facebook_page_selection"] = encrypt_json(body).decode()


def load_facebook_page_selection(sess: dict) -> dict[str, Any] | None:
    raw = sess.get("facebook_page_selection")
    if not raw:
        return None
    try:
        payload = decrypt_json(raw.encode() if isinstance(raw, str) else raw)
    except (RuntimeError, ValueError):
        sess.pop("facebook_page_selection", None)
        return None
    created_at = float(payload.get("created_at") or 0)
    age = datetime.now(UTC).timestamp() - created_at
    if created_at and age > PAGE_SELECTION_TTL_SECONDS:
        sess.pop("facebook_page_selection", None)
        return None
    return payload


def clear_facebook_page_selection(sess: dict) -> None:
    sess.pop("facebook_page_selection", None)


def parse_facebook_signed_request(value: str, app_secret: str) -> dict[str, Any]:
    encoded_sig, payload = value.split(".", 1)
    padding = "=" * (-len(encoded_sig) % 4)
    signature = base64.urlsafe_b64decode(encoded_sig + padding)
    expected = hmac.new(app_secret.encode(), payload.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise SocialAPIError("Facebook signed request is invalid")
    payload_padding = "=" * (-len(payload) % 4)
    data = json.loads(base64.urlsafe_b64decode(payload + payload_padding))
    if not isinstance(data, dict):
        raise SocialAPIError("Facebook signed request payload is invalid")
    return data


def _raise(response: httpx.Response) -> None:
    if response.is_success:
        return
    retryable = response.status_code == 429 or response.status_code >= 500
    detail = response.text[:500]
    raise SocialAPIError(
        f"Facebook Graph API returned {response.status_code}: {detail}",
        retryable=retryable,
        status_code=response.status_code,
    )


def _page_picture(item: dict[str, Any]) -> str:
    picture = item.get("picture") or ""
    if isinstance(picture, str):
        return picture
    if not isinstance(picture, dict):
        return ""
    data = picture.get("data") if isinstance(picture.get("data"), dict) else picture
    return str(data.get("url") or "")


def normalize_facebook_pages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    pages = []
    for item in payload.get("data") or []:
        if not isinstance(item, dict):
            continue
        page_id = str(item.get("id") or "").strip()
        token = str(item.get("access_token") or "").strip()
        if not page_id or not token:
            continue
        tasks = [str(task) for task in (item.get("tasks") or []) if task]
        pages.append(
            {
                "id": page_id,
                "name": str(item.get("name") or f"Facebook Page {page_id}"),
                "access_token": token,
                "tasks": tasks,
                "picture": _page_picture(item),
                "can_publish": not tasks or bool(_PAGE_PUBLISH_TASKS.intersection(tasks)),
            }
        )
    return pages


async def exchange_facebook_code(code: str) -> dict[str, Any]:
    cfg = settings()
    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.get(
            facebook_graph_url("oauth/access_token"),
            params={
                "client_id": cfg.meta_app_id,
                "client_secret": cfg.meta_app_secret,
                "redirect_uri": facebook_redirect_uri(),
                "code": code,
            },
        )
    _raise(response)
    body = response.json()
    if not body.get("access_token"):
        raise SocialAPIError("Facebook token exchange did not return an access token")
    return body


async def exchange_long_lived_token(short_token: str) -> dict[str, Any]:
    cfg = settings()
    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.get(
            facebook_graph_url("oauth/access_token"),
            params={
                "grant_type": "fb_exchange_token",
                "client_id": cfg.meta_app_id,
                "client_secret": cfg.meta_app_secret,
                "fb_exchange_token": short_token,
            },
        )
    if not response.is_success:
        log.warning(
            "Facebook long-lived token exchange failed (%s); falling back to the short-lived token",
            response.status_code,
        )
        return {"access_token": short_token, "expires_in": 3600, "long_lived": False}
    body = response.json()
    if not body.get("access_token"):
        log.warning(
            "Facebook long-lived token exchange returned no token; using the short-lived one"
        )
        return {"access_token": short_token, "expires_in": 3600, "long_lived": False}
    return {**body, "long_lived": True}


async def facebook_profile(user_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.get(
            facebook_graph_url("me"),
            params={"access_token": user_token, "fields": "id,name"},
        )
    _raise(response)
    return response.json()


async def list_facebook_pages(user_token: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.get(
            facebook_graph_url("me/accounts"),
            params={
                "access_token": user_token,
                "fields": "id,name,access_token,tasks,picture{url}",
                "limit": 100,
            },
        )
    _raise(response)
    return normalize_facebook_pages(response.json())


class FacebookClient:
    def _token(self, account: SocialAccount) -> str:
        token = decrypt_text(account.access_token_encrypted)
        if not token:
            raise SocialAPIError("Facebook Page token is missing")
        return token

    def _page_id(self, account: SocialAccount) -> str:
        return str(account.account_metadata.get("page_id") or account.external_account_id)

    async def publish(
        self, account: SocialAccount, content: dict, media: list[dict]
    ) -> PublishResult:
        text = content.get("platform_text", {}).get("facebook") or content.get("text", "")
        unpublished = bool(content.get("unpublished"))
        token = self._token(account)
        page_id = self._page_id(account)
        if media:
            return await self._publish_with_media(page_id, token, text, media, unpublished)
        payload = {
            "message": text,
            "access_token": token,
            "published": _published_flag(unpublished),
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(facebook_graph_url(f"{page_id}/feed"), data=payload)
        _raise(response)
        body = response.json()
        post_id = str(body.get("id") or "")
        if not post_id:
            raise SocialAPIError("Facebook did not return a post ID")
        return PublishResult(post_id, body)

    async def _publish_with_media(
        self,
        page_id: str,
        token: str,
        text: str,
        media: list[dict],
        unpublished: bool,
    ) -> PublishResult:
        if len(media) > 10:
            raise SocialAPIError("Facebook Pages support at most ten images in one post")
        for item in media:
            if not str(item.get("mime_type") or "").startswith("image/"):
                raise SocialAPIError(
                    "Direct Facebook video upload is not enabled; use a managed connector"
                )
        async with httpx.AsyncClient(timeout=60) as client:
            if len(media) == 1:
                item = media[0]
                response = await client.post(
                    facebook_graph_url(f"{page_id}/photos"),
                    data={
                        "caption": text,
                        "published": _published_flag(unpublished),
                        "access_token": token,
                    },
                    files={
                        "source": (
                            item.get("filename") or "image",
                            media_storage().get(item["storage_key"]),
                            item.get("mime_type") or "image/jpeg",
                        )
                    },
                )
                _raise(response)
                body = response.json()
                post_id = str(body.get("post_id") or body.get("id") or "")
                if not post_id:
                    raise SocialAPIError("Facebook photo upload did not return a post ID")
                return PublishResult(post_id, body)

            attached: list[dict] = []
            try:
                for item in media:
                    uploaded = await client.post(
                        facebook_graph_url(f"{page_id}/photos"),
                        data={"published": "false", "access_token": token},
                        files={
                            "source": (
                                item.get("filename") or "image",
                                media_storage().get(item["storage_key"]),
                                item.get("mime_type") or "image/jpeg",
                            )
                        },
                    )
                    _raise(uploaded)
                    media_id = str(uploaded.json().get("id") or "")
                    if not media_id:
                        raise SocialAPIError("Facebook photo upload did not return a media ID")
                    attached.append({"media_fbid": media_id})
                response = await client.post(
                    facebook_graph_url(f"{page_id}/feed"),
                    params={"access_token": token},
                    # This request sends a JSON body, so `published` is a real bool here,
                    # unlike the form-encoded single-photo/text paths that use a string flag.
                    json={
                        "message": text,
                        "attached_media": attached,
                        "published": not unpublished,
                    },
                )
                _raise(response)
            except SocialAPIError:
                # A later upload or the feed post failed; the already-uploaded photos are
                # unpublished and orphaned on the Page. Best-effort clean them up.
                await self._delete_media(client, token, attached)
                raise
        body = response.json()
        post_id = str(body.get("id") or "")
        if not post_id:
            raise SocialAPIError("Facebook did not return a post ID")
        return PublishResult(post_id, body)

    @staticmethod
    async def _delete_media(client: httpx.AsyncClient, token: str, attached: list[dict]) -> None:
        for entry in attached:
            media_id = entry.get("media_fbid")
            if not media_id:
                continue
            try:
                await client.delete(
                    facebook_graph_url(str(media_id)), params={"access_token": token}
                )
            except httpx.HTTPError:
                log.warning("Failed to clean up orphaned Facebook photo %s", media_id)

    async def get_post_metrics(self, account, platform_post_id: str) -> NormalizedMetrics:
        token = self._token(account)
        params = {
            "access_token": token,
            "fields": "shares,comments.summary(true),reactions.summary(true)",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(facebook_graph_url(platform_post_id), params=params)
        if not response.is_success:
            return NormalizedMetrics(raw={"available": False, "status_code": response.status_code})
        body = response.json()
        comments = (body.get("comments") or {}).get("summary") or {}
        reactions = (body.get("reactions") or {}).get("summary") or {}
        shares = body.get("shares") or {}
        return NormalizedMetrics(
            likes=int(reactions.get("total_count") or 0),
            comments=int(comments.get("total_count") or 0),
            shares=int(shares.get("count") or 0),
            raw=body,
        )

    async def get_account_metrics(self, account, since: date, until: date) -> NormalizedMetrics:
        token = self._token(account)
        page_id = self._page_id(account)
        params = {
            "access_token": token,
            "metric": "page_impressions,page_fans,page_post_engagements",
            "period": "day",
            "since": since.isoformat(),
            "until": until.isoformat(),
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(facebook_graph_url(f"{page_id}/insights"), params=params)
        if not response.is_success:
            return NormalizedMetrics(raw={"available": False, "status_code": response.status_code})
        body = response.json()
        totals = {"impressions": 0, "followers": 0, "engagement": 0}
        for item in body.get("data") or []:
            name = str(item.get("name") or "")
            values = item.get("values") or []
            latest = values[-1] if values else {}
            value = int(latest.get("value") or 0)
            if name == "page_impressions":
                totals["impressions"] = sum(int(entry.get("value") or 0) for entry in values)
            elif name == "page_fans":
                totals["followers"] = value
            elif name == "page_post_engagements":
                totals["engagement"] = sum(int(entry.get("value") or 0) for entry in values)
        return NormalizedMetrics(
            impressions=totals["impressions"],
            followers=totals["followers"],
            engagement=totals["engagement"],
            raw=body,
        )

    async def health(self, account: SocialAccount) -> dict:
        try:
            token = self._token(account)
            page_id = self._page_id(account)
        except SocialAPIError as exc:
            return {"ok": False, "error": str(exc)}
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                facebook_graph_url(page_id),
                params={"access_token": token, "fields": "id,name"},
            )
        if response.is_success:
            return {"ok": True, "status_code": response.status_code, "page": response.json()}
        error = ""
        try:
            error = str((response.json().get("error") or {}).get("message") or "")
        except ValueError:
            error = response.text[:300]
        return {
            "ok": False,
            "status_code": response.status_code,
            "error": error or "Facebook Page check failed",
        }

    async def collect_inbox(self, account: SocialAccount, since: datetime) -> list:
        return []

    async def collect_ads(self, account: SocialAccount, since: date, until: date) -> list:
        return []

    async def collect_competitors(
        self, account: SocialAccount, handles: list[str], since: date, until: date
    ) -> list:
        return []

    async def collect_listening(
        self, account: SocialAccount, queries: list[str], since: datetime
    ) -> list:
        return []
