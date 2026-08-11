from __future__ import annotations

import asyncio
import calendar as calendar_module
import hashlib
import io
import logging
import uuid
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from fastsocial.config import settings
from fastsocial.db import session_scope
from fastsocial.models import (
    AccountMetricDaily,
    AccountStatus,
    AuditLog,
    ContentAutolist,
    InboxConversation,
    InboxMessage,
    Media,
    Post,
    PostMedia,
    PostMetric,
    PostStatus,
    PostTarget,
    SocialAccount,
    TargetStatus,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
    utcnow,
)
from fastsocial.social import client_for
from fastsocial.social.base import SocialAPIError
from fastsocial.storage import media_storage, object_key

log = logging.getLogger(__name__)

PLATFORM_LIMITS = {"x": 280, "linkedin": 3000, "bluesky": 300}
ALLOWED_MEDIA_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "video/mp4",
    "video/quicktime",
}
MAX_MEDIA_BYTES = 512 * 1024 * 1024


def slugify(value: str) -> str:
    clean = "-".join("".join(c.lower() if c.isalnum() else " " for c in value).split())
    return clean[:100] or "workspace"


def create_personal_workspace(session, user: User) -> Workspace:
    base = slugify(user.name or user.email.split("@", 1)[0])
    slug = base
    counter = 2
    while session.scalar(select(Workspace.id).where(Workspace.slug == slug)):
        slug = f"{base}-{counter}"
        counter += 1
    workspace = Workspace(
        name=f"{user.name or user.email.split('@', 1)[0]}'s workspace",
        slug=slug,
        owner_id=user.id,
        approval_required=False,
        default_model_provider=(
            settings().model_provider if settings().model_provider in {"xai", "openai"} else "xai"
        ),
    )
    session.add(workspace)
    session.flush()
    session.add(
        WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=WorkspaceRole.owner)
    )
    return workspace


def get_or_create_user(
    session, email: str, *, name: str = "", google_subject: str | None = None
) -> User:
    user = session.scalar(select(User).where(func.lower(User.email) == email.lower()))
    if user:
        if google_subject and not user.google_subject:
            user.google_subject = google_subject
        if name and not user.name:
            user.name = name
        return user
    user = User(
        email=email.lower(),
        name=name,
        google_subject=google_subject,
        email_verified=bool(google_subject),
    )
    session.add(user)
    session.flush()
    create_personal_workspace(session, user)
    return user


def workspace_for_user(
    session, user_id: uuid.UUID, preferred: str | None = None
) -> Workspace | None:
    query = (
        select(Workspace)
        .join(WorkspaceMember)
        .where(WorkspaceMember.user_id == user_id)
        .order_by(Workspace.created_at)
    )
    workspaces = list(session.scalars(query))
    if not workspaces:
        return None
    if preferred:
        return next((item for item in workspaces if str(item.id) == preferred), workspaces[0])
    return workspaces[0]


def membership_for(session, workspace_id: uuid.UUID, user_id: uuid.UUID) -> WorkspaceMember | None:
    return session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )


def audit(
    session, workspace_id, actor_id, action: str, entity, details: dict | None = None
) -> None:
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action=action,
            entity_type=entity.__class__.__name__.lower(),
            entity_id=str(getattr(entity, "id", "")),
            details=details or {},
        )
    )


def validate_content(text: str, platforms: Iterable[str]) -> dict[str, str]:
    errors = {}
    if not text.strip():
        errors["text"] = "Post text is required"
    for platform in platforms:
        limit = PLATFORM_LIMITS.get(platform)
        if limit and len(text) > limit:
            errors[platform] = (
                f"{platform.title()} allows {limit} characters; this post has {len(text)}"
            )
    return errors


def create_post(
    session,
    *,
    workspace: Workspace,
    user_id: uuid.UUID,
    text: str,
    target_ids: list[uuid.UUID],
    media_ids: list[uuid.UUID] | None = None,
    scheduled_at: datetime | None = None,
    save_draft: bool = False,
    platform_text: dict[str, str] | None = None,
    recurrence_rule: str = "",
) -> Post:
    accounts = list(
        session.scalars(
            select(SocialAccount).where(
                SocialAccount.workspace_id == workspace.id,
                SocialAccount.id.in_(target_ids),
                SocialAccount.status == AccountStatus.connected,
            )
        )
    )
    if len(accounts) != len(set(target_ids)):
        raise ValueError("One or more selected accounts are unavailable")
    errors = validate_content(text, [item.platform for item in accounts])
    if errors and not save_draft:
        raise ValueError("; ".join(errors.values()))

    if save_draft or not accounts:
        status = PostStatus.draft
    elif workspace.approval_required:
        status = PostStatus.pending_approval
    else:
        status = PostStatus.scheduled
        scheduled_at = scheduled_at or utcnow()

    post = Post(
        workspace_id=workspace.id,
        created_by=user_id,
        status=status,
        scheduled_at=scheduled_at,
        content={"text": text.strip(), "platform_text": platform_text or {}},
        recurrence_rule=recurrence_rule
        if recurrence_rule in {"daily", "weekly", "monthly"}
        else "",
    )
    session.add(post)
    session.flush()
    for account in accounts:
        session.add(PostTarget(post_id=post.id, social_account_id=account.id))
    if media_ids:
        valid_media = list(
            session.scalars(
                select(Media).where(Media.workspace_id == workspace.id, Media.id.in_(media_ids))
            )
        )
        if len(valid_media) != len(set(media_ids)):
            raise ValueError("One or more media items are unavailable")
        for position, item in enumerate(valid_media):
            session.add(PostMedia(post_id=post.id, media_id=item.id, position=position))
    audit(session, workspace.id, user_id, "post.created", post, {"status": status.value})
    return post


def store_media(
    session, *, workspace_id, user_id, filename: str, mime_type: str, body: bytes
) -> Media:
    if mime_type not in ALLOWED_MEDIA_TYPES:
        raise ValueError(f"Unsupported media type: {mime_type}")
    if len(body) > MAX_MEDIA_BYTES:
        raise ValueError("Media exceeds the 512 MB limit")
    width = height = None
    if mime_type.startswith("image/"):
        try:
            with Image.open(io.BytesIO(body)) as image:
                image.verify()
            with Image.open(io.BytesIO(body)) as image:
                width, height = image.size
        except UnidentifiedImageError as exc:
            raise ValueError("Uploaded file is not a valid image") from exc
    media = Media(
        workspace_id=workspace_id,
        uploaded_by=user_id,
        filename=filename,
        storage_key="pending",
        mime_type=mime_type,
        width=width,
        height=height,
        size_bytes=len(body),
        checksum_sha256=hashlib.sha256(body).hexdigest(),
    )
    session.add(media)
    session.flush()
    media.storage_key = object_key(str(workspace_id), str(media.id), filename)
    media_storage().put(media.storage_key, body, mime_type)
    audit(session, workspace_id, user_id, "media.uploaded", media, {"size": len(body)})
    return media


def _media_payload(post: Post) -> list[dict]:
    return [
        {
            "id": str(link.media.id),
            "filename": link.media.filename,
            "mime_type": link.media.mime_type,
            "storage_key": link.media.storage_key,
            "alt_text": link.media.alt_text,
            "url": media_storage().url(link.media.storage_key),
        }
        for link in sorted(post.media_links, key=lambda item: item.position)
    ]


async def publish_post(post_id: uuid.UUID) -> None:
    with session_scope() as session:
        post = session.scalar(
            select(Post)
            .where(Post.id == post_id)
            .options(
                selectinload(Post.targets).selectinload(PostTarget.social_account),
                selectinload(Post.media_links).selectinload(PostMedia.media),
            )
        )
        if not post or post.status not in {PostStatus.scheduled, PostStatus.publishing}:
            return
        post.status = PostStatus.publishing
        post.error_message = ""
        session.flush()
        media = _media_payload(post)
        targets = list(post.targets)

        for target in targets:
            if target.status == TargetStatus.published:
                continue
            if target.next_retry_at and target.next_retry_at > utcnow():
                continue
            target.status = TargetStatus.publishing
            target.attempt_count += 1
            session.flush()
            try:
                result = await client_for(target.social_account).publish(
                    target.social_account, post.content, media
                )
                target.platform_post_id = result.platform_post_id
                target.status = TargetStatus.published
                target.published_at = utcnow()
                target.error_message = ""
            except SocialAPIError as exc:
                target.error_message = str(exc)
                if exc.retryable and target.attempt_count < 5:
                    target.status = TargetStatus.pending
                    delay = min(3600, 30 * (2 ** (target.attempt_count - 1)))
                    target.next_retry_at = utcnow() + timedelta(seconds=delay)
                else:
                    target.status = TargetStatus.failed
                log.warning("Publish failed for target %s: %s", target.id, exc)
            except Exception as exc:  # noqa: BLE001
                target.status = TargetStatus.failed
                target.error_message = str(exc)
                log.exception("Unexpected publishing failure for target %s", target.id)

        published = sum(item.status == TargetStatus.published for item in targets)
        failed = sum(item.status == TargetStatus.failed for item in targets)
        pending = sum(
            item.status in {TargetStatus.pending, TargetStatus.publishing} for item in targets
        )
        if published == len(targets):
            post.status = PostStatus.published
            post.published_at = utcnow()
            _spawn_recurring_post(session, post)
        elif pending:
            post.status = PostStatus.scheduled
            post.scheduled_at = min(
                (item.next_retry_at for item in targets if item.next_retry_at), default=utcnow()
            )
        elif published and failed:
            post.status = PostStatus.partially_failed
        else:
            post.status = PostStatus.failed
        post.error_message = "; ".join(item.error_message for item in targets if item.error_message)


def _spawn_recurring_post(session, post: Post) -> Post | None:
    if not post.recurrence_rule or post.content.get("_next_post_id") or not post.scheduled_at:
        return None
    scheduled_at = post.scheduled_at
    if post.recurrence_rule == "daily":
        next_at = scheduled_at + timedelta(days=1)
    elif post.recurrence_rule == "weekly":
        next_at = scheduled_at + timedelta(days=7)
    elif post.recurrence_rule == "monthly":
        import calendar

        year = scheduled_at.year + (1 if scheduled_at.month == 12 else 0)
        month = 1 if scheduled_at.month == 12 else scheduled_at.month + 1
        day = min(scheduled_at.day, calendar.monthrange(year, month)[1])
        next_at = scheduled_at.replace(year=year, month=month, day=day)
    else:
        return None
    content = {key: value for key, value in post.content.items() if key != "_next_post_id"}
    next_post = Post(
        workspace_id=post.workspace_id,
        created_by=post.created_by,
        status=PostStatus.scheduled,
        scheduled_at=next_at,
        content=content,
        recurrence_rule=post.recurrence_rule,
    )
    session.add(next_post)
    session.flush()
    for target in post.targets:
        session.add(PostTarget(post_id=next_post.id, social_account_id=target.social_account_id))
    for link in post.media_links:
        session.add(PostMedia(post_id=next_post.id, media_id=link.media_id, position=link.position))
    post.content = {**post.content, "_next_post_id": str(next_post.id)}
    return next_post


async def publish_due_posts(limit: int = 25) -> int:
    now = utcnow()
    with session_scope() as session:
        query = (
            select(Post)
            .where(Post.status == PostStatus.scheduled, Post.scheduled_at <= now)
            .order_by(Post.scheduled_at)
            .limit(limit)
        )
        if session.bind and session.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        posts = list(session.scalars(query))
        ids = [item.id for item in posts]
        for item in posts:
            item.status = PostStatus.publishing
    for post_id in ids:
        await publish_post(post_id)
    return len(ids)


def next_autolist_run(
    *, cadence: str, publish_time: str, timezone: str, after: datetime | None = None
) -> datetime:
    """Return the next cadence boundary in UTC, preserving the workspace wall time."""
    now = after or utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo("UTC"))
    zone = ZoneInfo(timezone)
    local = now.astimezone(zone)
    hour, minute = (int(part) for part in publish_time.split(":", 1))
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local:
        if cadence == "monthly":
            year = candidate.year + (1 if candidate.month == 12 else 0)
            month = 1 if candidate.month == 12 else candidate.month + 1
            day = min(candidate.day, calendar_module.monthrange(year, month)[1])
            candidate = candidate.replace(year=year, month=month, day=day)
        else:
            candidate += timedelta(days=1 if cadence == "daily" else 7)
    return candidate.astimezone(ZoneInfo("UTC"))


async def process_due_autolists(limit: int = 25) -> int:
    """Create the next scheduled post from each due evergreen content list."""
    now = utcnow()
    created = 0
    with session_scope() as session:
        query = (
            select(ContentAutolist)
            .where(
                ContentAutolist.active.is_(True),
                ContentAutolist.next_run_at.is_not(None),
                ContentAutolist.next_run_at <= now,
            )
            .options(selectinload(ContentAutolist.items))
            .order_by(ContentAutolist.next_run_at)
            .limit(limit)
        )
        if session.bind and session.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        lists = list(session.scalars(query))
        for autolist in lists:
            items = [item for item in autolist.items if item.active]
            if not items:
                autolist.active = False
                continue
            item = items[autolist.current_index % len(items)]
            try:
                workspace = session.get(Workspace, autolist.workspace_id)
                target_ids = [uuid.UUID(value) for value in autolist.target_ids]
                media_ids = [uuid.UUID(value) for value in item.media_ids]
                post = create_post(
                    session,
                    workspace=workspace,
                    user_id=autolist.created_by,
                    text=item.text,
                    target_ids=target_ids,
                    media_ids=media_ids,
                    scheduled_at=now,
                )
                item.used_count += 1
                item.last_used_at = now
                autolist.current_index = (autolist.current_index + 1) % len(items)
                autolist.next_run_at = next_autolist_run(
                    cadence=autolist.cadence,
                    publish_time=autolist.publish_time,
                    timezone=autolist.timezone,
                    after=now,
                )
                audit(
                    session,
                    autolist.workspace_id,
                    autolist.created_by,
                    "autolist.post.created",
                    post,
                    {"autolist_id": str(autolist.id), "item_id": str(item.id)},
                )
                created += 1
            except Exception as exc:  # noqa: BLE001
                autolist.next_run_at = now + timedelta(hours=1)
                log.exception("Autolist processing failed for %s: %s", autolist.id, exc)
    return created


async def send_inbox_reply(
    conversation_id: uuid.UUID, user_id: uuid.UUID, body: str
) -> InboxMessage:
    """Dispatch a reply through the configured provider and persist its exact delivery state."""
    failure: Exception | None = None
    with session_scope() as session:
        conversation = session.scalar(
            select(InboxConversation)
            .where(InboxConversation.id == conversation_id)
            .options(selectinload(InboxConversation.messages))
        )
        if not conversation or not conversation.social_account_id:
            raise ValueError("This conversation has no connected reply account")
        account = session.get(SocialAccount, conversation.social_account_id)
        if not account or account.workspace_id != conversation.workspace_id:
            raise ValueError("The reply account is unavailable")
        message = InboxMessage(
            conversation_id=conversation.id,
            direction="outbound",
            sender_name="FastSocial",
            body=body.strip(),
            delivery_status="sending",
            sent_at=utcnow(),
        )
        session.add(message)
        session.flush()
        try:
            sender = getattr(client_for(account), "reply_to_conversation", None)
            if not sender:
                raise SocialAPIError(
                    "Direct replies are not available for this network; connect Arcade or Composio"
                )
            message.external_message_id = await sender(
                account, conversation.external_conversation_id, body.strip(), conversation.kind
            )
            message.delivery_status = "sent"
            conversation.status = "open"
            conversation.last_message_preview = body.strip()[:500]
            conversation.last_message_at = message.sent_at
            audit(session, conversation.workspace_id, user_id, "inbox.reply.sent", message)
        except Exception as exc:
            message.delivery_status = "failed"
            message.error_message = str(exc)
            failure = exc
            audit(
                session,
                conversation.workspace_id,
                user_id,
                "inbox.reply.failed",
                message,
                {"error": str(exc)},
            )
        result = message
    if failure:
        raise SocialAPIError(str(failure)) from failure
    return result


async def collect_metrics() -> int:
    today = date.today()
    since = today - timedelta(days=1)
    count = 0
    with session_scope() as session:
        accounts = list(
            session.scalars(
                select(SocialAccount).where(SocialAccount.status == AccountStatus.connected)
            )
        )
        for account in accounts:
            client = client_for(account)
            try:
                values = await client.get_account_metrics(account, since, today)
                row = session.scalar(
                    select(AccountMetricDaily).where(
                        AccountMetricDaily.social_account_id == account.id,
                        AccountMetricDaily.metric_date == today,
                    )
                )
                if not row:
                    row = AccountMetricDaily(social_account_id=account.id, metric_date=today)
                    session.add(row)
                row.followers = values.followers
                row.impressions = values.impressions
                row.engagement = values.engagement
                row.reach = values.reach
                row.raw = values.raw
                count += 1
            except Exception:  # noqa: BLE001
                log.exception("Account metrics failed for %s", account.id)

        targets = list(
            session.scalars(
                select(PostTarget)
                .where(PostTarget.status == TargetStatus.published)
                .options(selectinload(PostTarget.social_account))
            )
        )
        for target in targets:
            try:
                values = await client_for(target.social_account).get_post_metrics(
                    target.social_account, target.platform_post_id
                )
                session.add(
                    PostMetric(
                        post_target_id=target.id,
                        impressions=values.impressions,
                        reach=values.reach,
                        likes=values.likes,
                        comments=values.comments,
                        shares=values.shares,
                        clicks=values.clicks,
                        saves=values.saves,
                        raw=values.raw,
                    )
                )
                count += 1
            except Exception:  # noqa: BLE001
                log.exception("Post metrics failed for %s", target.id)
    return count


async def check_account_health(workspace_id: uuid.UUID | None = None) -> int:
    checked = 0
    with session_scope() as session:
        query = select(SocialAccount)
        if workspace_id:
            query = query.where(SocialAccount.workspace_id == workspace_id)
        accounts = list(session.scalars(query))
        for account in accounts:
            result = await client_for(account).health(account)
            account.last_health_check_at = utcnow()
            account.status = (
                AccountStatus.connected if result.get("ok") else AccountStatus.needs_reauth
            )
            account.last_error = (
                "" if result.get("ok") else result.get("error", "Connection check failed")
            )
            checked += 1
    return checked


def run_async(coro) -> None:
    asyncio.run(coro)
