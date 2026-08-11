from __future__ import annotations

import base64
import calendar as calendar_module
import csv
import hashlib
import io
import secrets
import uuid
from datetime import UTC, date, datetime
from urllib.parse import quote_plus, urlencode
from zoneinfo import ZoneInfo

import httpx
from fasthtml.common import (
    H1,
    H2,
    H3,
    A,
    Body,
    Br,
    Button,
    Details,
    Div,
    Form,
    Html,
    Input,
    Label,
    Li,
    Link,
    NotStr,
    Option,
    P,
    Script,
    Select,
    Small,
    Span,
    Strong,
    Summary,
    Table,
    Tbody,
    Td,
    Textarea,
    Th,
    Thead,
    Tr,
    Ul,
)
from sqlalchemy import desc, func, select
from sqlalchemy.orm import selectinload
from starlette.responses import JSONResponse, RedirectResponse, Response

from fastsocial.agentic import (
    create_chat_session,
    generate_chat_artifact,
    generate_media_for_chat,
    latest_artifact,
    record_event,
)
from fastsocial.app import rt
from fastsocial.components import (
    PLATFORM_MARKS,
    PLATFORM_NAMES,
    app_page,
    auth_page,
    csrf_input,
    empty_state,
    flash,
    google_button,
    head,
    page_intro,
    platform_pill,
    stat_card,
    status_badge,
)
from fastsocial.config import settings
from fastsocial.db import session_scope
from fastsocial.model_provider import (
    default_model,
    invoke_json,
    resolve_model,
    test_model_connection,
)
from fastsocial.models import (
    AccountMetricDaily,
    AccountStatus,
    AgentEvent,
    AIProviderCredential,
    ApprovalStatus,
    ArtifactStatus,
    AuditLog,
    ChatMessage,
    ChatRole,
    ChatSession,
    ConnectionProvider,
    ContentArtifact,
    Media,
    ModelProfile,
    Post,
    PostApproval,
    PostMetric,
    PostStatus,
    PostTarget,
    SkillDefinition,
    SkillVersionStatus,
    SocialAccount,
    TargetStatus,
    User,
    WorkflowMode,
    WorkflowStage,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
    WorkspaceSkillVersion,
    utcnow,
)
from fastsocial.security import (
    csrf_token,
    encrypt_text,
    hash_password,
    verify_csrf,
    verify_password,
)
from fastsocial.services import (
    audit,
    check_account_health,
    create_post,
    get_or_create_user,
    membership_for,
    publish_post,
    store_media,
    validate_content,
    workspace_for_user,
)
from fastsocial.skills_service import publish_skill_version, skill_content
from fastsocial.storage import LocalStorage, media_storage


class PageContext:
    def __init__(self, user, workspace, membership, accounts, pending_approvals, chat_sessions):
        self.user = user
        self.workspace = workspace
        self.membership = membership
        self.accounts = accounts
        self.pending_approvals = pending_approvals
        self.chat_sessions = chat_sessions


def _context(sess: dict) -> PageContext | None:
    user_id = sess.get("user_id")
    if not user_id:
        return None
    try:
        user_uuid = uuid.UUID(str(user_id))
    except ValueError:
        sess.clear()
        return None
    with session_scope() as session:
        user = session.get(User, user_uuid)
        if not user or not user.is_active:
            sess.clear()
            return None
        workspace = workspace_for_user(session, user.id, sess.get("workspace_id"))
        if not workspace:
            return None
        sess["workspace_id"] = str(workspace.id)
        membership = membership_for(session, workspace.id, user.id)
        accounts = list(
            session.scalars(
                select(SocialAccount)
                .where(SocialAccount.workspace_id == workspace.id)
                .order_by(SocialAccount.platform, SocialAccount.display_name)
            )
        )
        pending = (
            session.scalar(
                select(func.count(Post.id)).where(
                    Post.workspace_id == workspace.id, Post.status == PostStatus.pending_approval
                )
            )
            or 0
        )
        chat_sessions = list(
            session.scalars(
                select(ChatSession)
                .where(ChatSession.workspace_id == workspace.id)
                .order_by(desc(ChatSession.updated_at))
                .limit(12)
            )
        )
        return PageContext(user, workspace, membership, accounts, pending, chat_sessions)


def _signin_redirect():
    return RedirectResponse("/signin", status_code=303)


def _login(sess: dict, user: User, workspace: Workspace | None = None) -> None:
    sess["user_id"] = str(user.id)
    if workspace:
        sess["workspace_id"] = str(workspace.id)
    csrf_token(sess)


def _app_page(ctx: PageContext, title: str, path: str, *children, action=None):
    return app_page(
        title,
        path,
        ctx.user,
        ctx.workspace,
        ctx.accounts,
        *children,
        pending_approvals=ctx.pending_approvals,
        chat_sessions=ctx.chat_sessions,
        action=action,
    )


def _parse_datetime(value: str, timezone_name: str) -> datetime | None:
    if not value:
        return None
    local = datetime.fromisoformat(value)
    if local.tzinfo is None:
        local = local.replace(tzinfo=ZoneInfo(timezone_name))
    return local.astimezone(UTC)


def _format_datetime(value: datetime | None, timezone_name: str) -> str:
    if not value:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(ZoneInfo(timezone_name)).strftime("%d %b %Y · %H:%M")


def _landing():
    features = [
        (
            "Create with agents",
            "Turn a brief into network-native copy through editable marketing skills.",
        ),
        (
            "Bring any model",
            "Use your own xAI or OpenAI key and choose separate text, image, and video models.",
        ),
        (
            "Review or YOLO",
            "Pause for human review by default, or let the autonomous workflow deliver end to end.",
        ),
    ]
    return Html(
        head("Social publishing under your control"),
        Body(
            Div(
                Div(
                    A(
                        Span("F", cls="brand-glyph"),
                        Span("FastSocial"),
                        href="/",
                        cls="brand public-brand",
                    ),
                    Div(
                        A("Sign in", href="/signin", cls="btn"),
                        A("Start free", href="/register", cls="btn primary"),
                        cls="public-actions",
                    ),
                    cls="public-nav",
                ),
                Div(
                    Span("PERSONAL-FIRST SOCIAL MANAGEMENT", cls="eyebrow accent"),
                    H1("Your agentic social studio. Your models. Your voice."),
                    P(
                        "Create, review, publish, and measure across X, LinkedIn, and Bluesky with editable skills and BYOK / BYOM freedom. Personal today, company-ready tomorrow."
                    ),
                    Div(
                        A("Create your workspace", href="/register", cls="btn primary"),
                        A("Sign in", href="/signin", cls="btn"),
                        cls="public-hero-actions",
                    ),
                    Small("FastHTML · HTMX · PostgreSQL · Cloudflare R2 · xAI + OpenAI"),
                    cls="public-hero",
                ),
                Div(
                    *[
                        Div(
                            Span(str(index + 1).zfill(2), cls="feature-number"),
                            H2(title),
                            P(copy),
                            cls="public-feature",
                        )
                        for index, (title, copy) in enumerate(features)
                    ],
                    cls="public-features",
                ),
                Div(
                    Div(
                        H2("Bring your own key. Bring your own model. Keep control."),
                        P(
                            "All model features require sign-in. Workspace API keys are encrypted, shared server models are gated, and publishing remains deterministic and auditable."
                        ),
                    ),
                    A("Build your queue", href="/register", cls="btn primary"),
                    cls="public-cta",
                ),
                Div(
                    Span("FastSocial"),
                    Small("Part of the FastSME open business software portfolio."),
                    cls="public-footer",
                ),
                cls="public-shell",
            )
        ),
    )


@rt("/robots.txt")
def robots():
    return Response(
        f"User-agent: *\nAllow: /\nDisallow: /analytics\nDisallow: /posts\nSitemap: {settings().service_url}/sitemap.xml\n",
        media_type="text/plain",
    )


@rt("/sitemap.xml")
def sitemap():
    base = settings().service_url
    return Response(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{base}/</loc></url>"
        f"<url><loc>{base}/signin</loc></url>"
        f"<url><loc>{base}/register</loc></url>"
        "</urlset>",
        media_type="application/xml",
    )


@rt("/signin")
async def signin(request, sess):
    if _context(sess):
        return RedirectResponse("/", status_code=303)
    error = ""
    if request.method == "POST":
        form = await request.form()
        if not verify_csrf(sess, form.get("csrf")):
            error = "Your session expired. Please try again."
        else:
            email = str(form.get("email") or "").strip().lower()
            password = str(form.get("password") or "")
            with session_scope() as session:
                user = session.scalar(select(User).where(func.lower(User.email) == email))
                if user and verify_password(password, user.password_hash):
                    workspace = workspace_for_user(session, user.id)
                    _login(sess, user, workspace)
                    return RedirectResponse("/", status_code=303)
            error = "Invalid email or password."
    return auth_page(
        "Welcome back",
        "Sign in to plan, publish, and measure your content.",
        flash(error, "error"),
        google_button(),
        (Div("or continue with email", cls="divider") if settings().google_client_id else ""),
        Form(
            Input(type="hidden", name="csrf", value=csrf_token(sess)),
            Input(
                type="email",
                name="email",
                placeholder="Email address",
                required=True,
                autofocus=True,
            ),
            Input(
                type="password",
                name="password",
                placeholder="Password",
                autocomplete="current-password",
                required=True,
            ),
            Button("Sign in", type="submit", cls="btn primary"),
            method="post",
            action="/signin",
        ),
        Div("New to FastSocial? ", A("Create an account", href="/register"), cls="auth-foot"),
    )


@rt("/register")
async def register(request, sess):
    if _context(sess):
        return RedirectResponse("/", status_code=303)
    error = ""
    if request.method == "POST":
        form = await request.form()
        if not verify_csrf(sess, form.get("csrf")):
            error = "Your session expired. Please try again."
        else:
            email = str(form.get("email") or "").strip().lower()
            name = str(form.get("name") or "").strip()
            password = str(form.get("password") or "")
            if "@" not in email:
                error = "Enter a valid email address."
            elif len(password) < 8:
                error = "Use at least 8 characters for your password."
            else:
                with session_scope() as session:
                    existing = session.scalar(select(User).where(func.lower(User.email) == email))
                    if existing:
                        error = "An account already exists for this email."
                    else:
                        user = get_or_create_user(session, email, name=name)
                        user.password_hash = hash_password(password)
                        workspace = workspace_for_user(session, user.id)
                        session.flush()
                        _login(sess, user, workspace)
                        return RedirectResponse("/", status_code=303)
    return auth_page(
        "Create your workspace",
        "Start personally. Team roles and approvals are ready when you need them.",
        flash(error, "error"),
        google_button("Sign up with Google"),
        (Div("or create a password", cls="divider") if settings().google_client_id else ""),
        Form(
            Input(type="hidden", name="csrf", value=csrf_token(sess)),
            Input(type="text", name="name", placeholder="Your name", required=True),
            Input(type="email", name="email", placeholder="Email address", required=True),
            Input(
                type="password",
                name="password",
                placeholder="Password · 8 characters minimum",
                autocomplete="new-password",
                minlength="8",
                required=True,
            ),
            Button("Create account", type="submit", cls="btn primary"),
            method="post",
            action="/register",
        ),
        Div("Already have an account? ", A("Sign in", href="/signin"), cls="auth-foot"),
    )


@rt("/auth/logout", methods=["POST"])
async def logout(request, sess):
    form = await request.form()
    if verify_csrf(sess, form.get("csrf")):
        sess.clear()
    return RedirectResponse("/signin", status_code=303)


@rt("/auth/google")
def google_auth(sess):
    cfg = settings()
    if not cfg.google_client_id:
        return RedirectResponse("/signin?error=google_not_configured", status_code=303)
    state = secrets.token_urlsafe(32)
    sess["google_oauth_state"] = state
    params = {
        "client_id": cfg.google_client_id,
        "redirect_uri": cfg.google_callback_url,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "offline",
        "prompt": "select_account",
    }
    return RedirectResponse(
        f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}", status_code=302
    )


@rt("/auth/google/callback")
async def google_callback(code: str = "", state: str = "", error: str = "", sess=None):
    cfg = settings()
    if error or not state or not secrets.compare_digest(state, sess.pop("google_oauth_state", "")):
        return RedirectResponse("/signin?error=google_oauth_failed", status_code=303)
    async with httpx.AsyncClient(timeout=20) as client:
        token_response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": cfg.google_client_id,
                "client_secret": cfg.google_client_secret,
                "redirect_uri": cfg.google_callback_url,
                "grant_type": "authorization_code",
            },
        )
        if not token_response.is_success:
            return RedirectResponse("/signin?error=google_token_failed", status_code=303)
        token = token_response.json()["access_token"]
        profile_response = await client.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {token}"},
        )
    if not profile_response.is_success:
        return RedirectResponse("/signin?error=google_profile_failed", status_code=303)
    profile = profile_response.json()
    if not profile.get("email_verified") or not cfg.google_email_allowed(profile.get("email", "")):
        return RedirectResponse("/signin?error=google_account_not_allowed", status_code=303)
    with session_scope() as session:
        user = get_or_create_user(
            session,
            profile["email"],
            name=profile.get("name", ""),
            google_subject=profile.get("sub"),
        )
        user.avatar_url = profile.get("picture", "")
        user.email_verified = bool(profile.get("email_verified", True))
        workspace = workspace_for_user(session, user.id)
        session.flush()
        _login(sess, user, workspace)
    return RedirectResponse("/", status_code=303)


@rt("/")
def dashboard(sess):
    ctx = _context(sess)
    if not ctx:
        return _landing()
    with session_scope() as session:
        upcoming = list(
            session.scalars(
                select(Post)
                .where(
                    Post.workspace_id == ctx.workspace.id,
                    Post.status.in_([PostStatus.scheduled, PostStatus.pending_approval]),
                )
                .options(selectinload(Post.targets).selectinload(PostTarget.social_account))
                .order_by(Post.scheduled_at)
                .limit(8)
            )
        )
        published_count = (
            session.scalar(
                select(func.count(Post.id)).where(
                    Post.workspace_id == ctx.workspace.id, Post.status == PostStatus.published
                )
            )
            or 0
        )
        metric_totals = session.execute(
            select(
                func.coalesce(func.sum(PostMetric.impressions), 0),
                func.coalesce(
                    func.sum(PostMetric.likes + PostMetric.comments + PostMetric.shares), 0
                ),
            )
            .join(PostTarget, PostMetric.post_target_id == PostTarget.id)
            .join(Post, PostTarget.post_id == Post.id)
            .where(Post.workspace_id == ctx.workspace.id)
        ).one()
    rows = [
        Div(
            Div(
                P(item.content.get("text", "") or "Untitled draft"),
                Small(_format_datetime(item.scheduled_at, ctx.workspace.timezone)),
                cls="post-copy",
            ),
            Div(
                *[
                    Span(
                        PLATFORM_MARKS.get(t.social_account.platform, "?"),
                        cls=f"platform-mark {t.social_account.platform}",
                    )
                    for t in item.targets
                ],
                cls="post-platforms",
            ),
            status_badge(item.status),
            cls="post-row",
        )
        for item in upcoming
    ]
    upcoming_card = Div(
        Div(
            H2("Upcoming posts"),
            A("View calendar", href="/calendar", cls="btn small"),
            cls="card-head",
        ),
        Div(*rows, cls="post-list")
        if rows
        else Div(
            empty_state(
                "＋",
                "Your queue is clear",
                "Create your first post and choose when it should go live.",
                "New Post",
                "/new-post",
            ),
            cls="card-body",
        ),
        cls="card",
    )
    health_items = [
        Div(
            platform_pill(account.platform, account.display_name or account.username),
            status_badge(account.status),
            cls="connected-account",
        )
        for account in ctx.accounts
    ]
    connection_card = Div(
        Div(
            H2("Connection health"),
            A("Manage", href="/integrations", cls="btn small"),
            cls="card-head",
        ),
        Div(
            *(health_items or [P("No social accounts connected yet.")]),
            cls="card-body connected-accounts",
        ),
        cls="card",
    )
    return _app_page(
        ctx,
        "Dashboard",
        "/",
        page_intro(
            "OVERVIEW",
            "Good to see you.",
            "Plan once, publish everywhere, and keep a clean view of what is working.",
            A("+ New Post", href="/new-post", cls="btn primary"),
        ),
        Div(
            stat_card("Connected accounts", len(ctx.accounts), "Across X, LinkedIn, and Bluesky"),
            stat_card("Published posts", published_count, "All-time in this workspace"),
            stat_card("Impressions", f"{int(metric_totals[0]):,}", "Latest collected snapshots"),
            stat_card("Engagements", f"{int(metric_totals[1]):,}", "Likes, comments, and shares"),
            cls="stats-grid",
        ),
        Div(upcoming_card, connection_card, cls="content-grid"),
    )


@rt("/compose")
async def compose(request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    # The former manual composer remains as a bookmark-compatible entry point.
    # All creation now starts in the agentic New Post workflow.
    return RedirectResponse("/new-post", status_code=303)

    # Kept temporarily below for schema-compatible rollback during the local upgrade window.
    error = ""
    if request.method == "POST":
        form = await request.form()
        if not verify_csrf(sess, form.get("csrf")):
            error = "Your session expired. Please try again."
        else:
            try:
                target_ids = [uuid.UUID(value) for value in form.getlist("target_ids")]
                media_ids = [uuid.UUID(value) for value in form.getlist("media_ids")]
                publish_now = form.get("publish_mode") == "now"
                scheduled_at = (
                    utcnow()
                    if publish_now
                    else _parse_datetime(
                        str(form.get("scheduled_at") or ""), ctx.workspace.timezone
                    )
                )
                if form.get("action") != "draft" and not scheduled_at:
                    raise ValueError("Choose a publish date and time, or select Publish now.")
                with session_scope() as session:
                    post = create_post(
                        session,
                        workspace=session.merge(ctx.workspace),
                        user_id=ctx.user.id,
                        text=str(form.get("text") or ""),
                        target_ids=target_ids,
                        media_ids=media_ids,
                        scheduled_at=scheduled_at,
                        save_draft=form.get("action") == "draft",
                        recurrence_rule=str(form.get("recurrence_rule") or ""),
                    )
                    post_id = post.id
                if publish_now and post.status == PostStatus.scheduled:
                    await publish_post(post_id)
                return RedirectResponse(f"/posts/{post_id}?saved=1", status_code=303)
            except (ValueError, TypeError) as exc:
                error = str(exc)
    with session_scope() as session:
        media_items = list(
            session.scalars(
                select(Media)
                .where(Media.workspace_id == ctx.workspace.id)
                .order_by(desc(Media.created_at))
                .limit(20)
            )
        )
    account_options = [
        Label(
            Input(
                type="checkbox",
                name="target_ids",
                value=str(account.id),
                checked=True,
            ),
            Span(
                PLATFORM_MARKS.get(account.platform, "?"), cls=f"platform-mark {account.platform}"
            ),
            Div(
                Span(account.display_name or account.username),
                Small(f"{PLATFORM_NAMES.get(account.platform)} · {account.provider.value}"),
            ),
            cls="account-option",
        )
        for account in ctx.accounts
        if account.status == AccountStatus.connected
    ]
    media_options = [
        Label(
            Input(type="checkbox", name="media_ids", value=str(item.id)),
            Span(item.filename),
            cls="account-option",
        )
        for item in media_items
    ]
    composer_form = Form(
        csrf_input(sess),
        Div(
            Div(
                H2("Post content"),
                Div(
                    Label("Write once, then tailor per network", fr="post-text"),
                    Textarea(
                        name="text",
                        id="post-text",
                        placeholder="What would you like to share?",
                        required=True,
                    ),
                    Div(
                        Span(
                            "Length is validated in Python against every selected network when submitted."
                        ),
                        cls="form-help",
                    ),
                    cls="field",
                ),
                Div(
                    Span("Media library", cls="field-label"),
                    Div(
                        *(
                            media_options
                            or [P("Upload media first, or publish text-only.", cls="form-help")]
                        ),
                        cls="account-options",
                    ),
                    A("Open media library", href="/media", cls="btn small"),
                    cls="field",
                ),
                cls="form-card",
            ),
            Div(
                Div(
                    H2("Publish to"),
                    Div(
                        *(
                            account_options
                            or [
                                P(
                                    "Connect an account to publish. Drafts can still be saved.",
                                    cls="form-help",
                                )
                            ]
                        ),
                        cls="account-options",
                    ),
                    cls="field",
                ),
                Div(
                    H2("When"),
                    Div(
                        Label(
                            Input(
                                type="radio", name="publish_mode", value="schedule", checked=True
                            ),
                            " Schedule",
                        ),
                        Label(
                            Input(type="radio", name="publish_mode", value="now"), " Publish now"
                        ),
                        cls="schedule-tabs",
                    ),
                    Input(type="datetime-local", name="scheduled_at", id="scheduled-at"),
                    Select(
                        Option("Does not repeat", value="", selected=True),
                        Option("Every day", value="daily"),
                        Option("Every week", value="weekly"),
                        Option("Every month", value="monthly"),
                        name="recurrence_rule",
                    ),
                    Small(f"Times use {ctx.workspace.timezone}", cls="form-help"),
                    cls="field",
                ),
                Div(
                    Button("Save draft", name="action", value="draft", cls="btn"),
                    Button(
                        "Schedule post",
                        name="action",
                        value="schedule",
                        cls="btn primary",
                        disabled=not bool(ctx.accounts),
                    ),
                    cls="form-actions",
                ),
                cls="form-card",
            ),
            cls="form-grid",
        ),
        method="post",
        action="/compose",
    )
    return _app_page(
        ctx,
        "Create post",
        "/compose",
        page_intro(
            "COMPOSER",
            "Create once. Shape for every network.",
            "FastSocial validates each target before anything reaches the publishing queue.",
        ),
        flash(error, "error"),
        composer_form,
    )


@rt("/api/ai/compose", methods=["POST"])
async def ai_compose(request, sess):
    ctx = _context(sess)
    if not ctx:
        return JSONResponse({"error": "authentication_required"}, status_code=401)
    form = await request.form()
    if not verify_csrf(sess, form.get("csrf")):
        return JSONResponse({"error": "invalid_csrf"}, status_code=403)
    return JSONResponse({"error": "agentic_new_post_required", "url": "/new-post"}, status_code=410)

    # Compatibility implementation retained until the next schema release.
    try:
        with session_scope() as session:
            resolved = resolve_model(
                session,
                workspace_id=ctx.workspace.id,
                user_email=ctx.user.email,
                provider=ctx.workspace.default_model_provider,
                purpose="text",
            )
        result = await invoke_json(
            resolved,
            system_prompt=(
                "Create safe social copy and return only JSON with keys x, linkedin, bluesky. "
                "Keep X within 280 characters and Bluesky within 300 characters."
            ),
            user_prompt=(
                f"Tone: {str(form.get('tone') or 'clear and useful')}\n"
                f"Brief: {str(form.get('prompt') or '')}"
            ),
        )
        return JSONResponse(result)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=400)


def _model_gate_message(ctx: PageContext, provider: str) -> str:
    with session_scope() as session:
        credential = session.scalar(
            select(AIProviderCredential).where(
                AIProviderCredential.workspace_id == ctx.workspace.id,
                AIProviderCredential.provider == provider,
            )
        )
    if credential:
        return f"Workspace {provider} key {credential.masked_hint}"
    server_key = settings().xai_api_key if provider == "xai" else settings().openai_api_key
    if settings().server_model_access_allowed(ctx.user.email) and server_key:
        return f"FastSocial server {provider} key"
    return "BYOK required"


def _workflow_steps(stage: WorkflowStage):
    current = {
        WorkflowStage.create: 0,
        WorkflowStage.generate: 0,
        WorkflowStage.review: 1,
        WorkflowStage.post: 2,
        WorkflowStage.complete: 3,
        WorkflowStage.failed: 0,
    }.get(stage, 0)
    labels = ("Create / Generate", "Review", "Post")
    return Div(
        *[
            Div(
                Span("✓" if current > index else str(index + 1), cls="workflow-step-number"),
                Span(label),
                cls=f"workflow-step{' done' if current > index else ''}{' active' if current == index else ''}",
            )
            for index, label in enumerate(labels)
        ],
        cls="workflow-steps",
    )


def _new_post_form(ctx: PageContext, sess: dict, error: str = ""):
    account_options = [
        Label(
            Input(
                type="checkbox",
                name="target_ids",
                value=str(account.id),
                checked=True,
                form="new-post-form",
            ),
            Span(
                PLATFORM_MARKS.get(account.platform, "?"), cls=f"platform-mark {account.platform}"
            ),
            Span(account.display_name or account.username or PLATFORM_NAMES.get(account.platform)),
            cls="account-option",
        )
        for account in ctx.accounts
        if account.status == AccountStatus.connected
    ]
    return Div(
        _workflow_steps(WorkflowStage.generate),
        flash(error, "error"),
        Div(
            Form(
                csrf_input(sess),
                Div(
                    Span("CREATIVE BRIEF", cls="eyebrow accent"),
                    H1("What should we create?"),
                    P(
                        "Describe the audience, idea, evidence, desired action, and anything the agents must avoid."
                    ),
                    cls="agent-workbench-head",
                ),
                Div(
                    Label("Brief"),
                    Textarea(
                        name="brief",
                        placeholder="Create a concise launch post for founders explaining…",
                        required=True,
                        autofocus=True,
                    ),
                    cls="field",
                ),
                Div(
                    Label("Workflow"),
                    Label(
                        Input(
                            type="radio",
                            name="workflow_mode",
                            value="review",
                            checked=ctx.workspace.default_workflow_mode == WorkflowMode.review,
                        ),
                        Div(
                            Strong("Review"),
                            Small("Generate, inspect and confirm before queueing."),
                        ),
                        cls="choice-card",
                    ),
                    Label(
                        Input(
                            type="radio",
                            name="workflow_mode",
                            value="yolo",
                            checked=ctx.workspace.default_workflow_mode == WorkflowMode.yolo,
                        ),
                        Div(Strong("YOLO"), Small("Generate, review and deliver autonomously.")),
                        cls="choice-card warning",
                    ),
                    cls="field choice-grid",
                ),
                Div(
                    Label("Generate media"),
                    Div(
                        Label(Input(type="checkbox", name="media_kinds", value="image"), " Image"),
                        Label(Input(type="checkbox", name="media_kinds", value="video"), " Video"),
                        cls="schedule-tabs",
                    ),
                    cls="field",
                ),
                Div(
                    Label("YOLO delivery"),
                    Select(
                        Option("Publish now", value="now", selected=True),
                        Option("Schedule", value="schedule"),
                        Option("Save as draft", value="draft"),
                        name="delivery",
                    ),
                    Input(type="datetime-local", name="scheduled_at"),
                    Small("Review mode always pauses before delivery."),
                    cls="field",
                ),
                Button("Create with agents →", type="submit", cls="btn primary"),
                method="post",
                action="/new-post",
                id="new-post-form",
                cls="agent-brief-form",
            ),
            Div(
                H2("Publish to"),
                Div(
                    *(
                        account_options
                        or [
                            P(
                                "Connect a social account to publish; generation can still create a draft.",
                                cls="form-help",
                            )
                        ]
                    ),
                    cls="account-options",
                ),
                H2("Model access", style="margin-top:22px"),
                P(
                    f"{ctx.workspace.default_model_provider.upper()} · {_model_gate_message(ctx, ctx.workspace.default_model_provider)}",
                    cls="form-help",
                ),
                A("Configure models", href="/integrations#ai-models", cls="btn small"),
                cls="agent-context-panel",
            ),
            cls="agent-workbench new-agent-workbench",
        ),
    )


@rt("/new-post")
async def new_post(request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    if request.method == "GET":
        return _app_page(
            ctx,
            "New Post",
            "/new-post",
            _new_post_form(ctx, sess),
        )
    form = await request.form()
    if not verify_csrf(sess, form.get("csrf")):
        return _app_page(
            ctx, "New Post", "/new-post", _new_post_form(ctx, sess, "Your session expired.")
        )
    brief = str(form.get("brief") or "").strip()
    try:
        if not brief:
            raise ValueError("Creative brief is required")
        mode = WorkflowMode(
            str(form.get("workflow_mode") or ctx.workspace.default_workflow_mode.value)
        )
        target_ids = [uuid.UUID(value) for value in form.getlist("target_ids")]
        target_map = {str(account.id): account.platform for account in ctx.accounts}
        platforms = list(
            dict.fromkeys(target_map[str(item)] for item in target_ids if str(item) in target_map)
        )
        state = {
            "provider": ctx.workspace.default_model_provider,
            "target_ids": [str(item) for item in target_ids],
            "platforms": platforms,
            "media_kinds": [str(item) for item in form.getlist("media_kinds")],
            "delivery": str(form.get("delivery") or "now"),
            "scheduled_at": str(form.get("scheduled_at") or ""),
        }
        if state["delivery"] == "schedule" and not _parse_datetime(
            state["scheduled_at"], ctx.workspace.timezone
        ):
            raise ValueError("Choose a schedule time")
        with session_scope() as session:
            resolve_model(
                session,
                workspace_id=ctx.workspace.id,
                user_email=ctx.user.email,
                provider=ctx.workspace.default_model_provider,
                purpose="text",
            )
            chat = create_chat_session(
                session,
                workspace_id=ctx.workspace.id,
                user_id=ctx.user.id,
                brief=brief,
                workflow_mode=mode,
                state=state,
            )
            chat_id = chat.id
        artifact_id = await generate_chat_artifact(chat_id, user_email=ctx.user.email)
        if mode == WorkflowMode.yolo:
            with session_scope() as session:
                artifact = session.get(ContentArtifact, artifact_id)
                prompts = dict(artifact.content)
            for kind in state["media_kinds"]:
                prompt = str(prompts.get(f"{kind}_prompt") or brief)
                await generate_media_for_chat(
                    chat_id,
                    user_id=ctx.user.id,
                    user_email=ctx.user.email,
                    kind=kind,
                    prompt=prompt,
                )
            await _deliver_chat(chat_id, ctx, delivery=state["delivery"])
        return RedirectResponse(f"/chats/{chat_id}", status_code=303)
    except Exception as exc:  # noqa: BLE001
        return _app_page(ctx, "New Post", "/new-post", _new_post_form(ctx, sess, str(exc)))


def _chat_records(ctx: PageContext, chat_id: str):
    try:
        parsed = uuid.UUID(chat_id)
    except ValueError:
        return None, [], [], None
    with session_scope() as session:
        chat = session.scalar(
            select(ChatSession).where(
                ChatSession.id == parsed,
                ChatSession.workspace_id == ctx.workspace.id,
            )
        )
        if not chat:
            return None, [], [], None
        messages = list(
            session.scalars(
                select(ChatMessage)
                .where(ChatMessage.chat_session_id == chat.id)
                .order_by(ChatMessage.created_at)
            )
        )
        events = list(
            session.scalars(
                select(AgentEvent)
                .where(AgentEvent.chat_session_id == chat.id)
                .order_by(AgentEvent.sequence)
            )
        )
        artifact = latest_artifact(session, chat.id)
        return chat, messages, events, artifact


def _chat_page(
    ctx: PageContext,
    sess: dict,
    chat,
    messages,
    events,
    artifact,
    message: str = "",
    error: str = "",
):
    content = dict(artifact.content) if artifact else {}
    completed = chat.stage == WorkflowStage.complete
    awaiting_approval = bool(artifact and artifact.status == ArtifactStatus.review)
    variants = content.get("variants") if isinstance(content.get("variants"), dict) else {}
    state = dict(chat.state or {})
    media_ids = [uuid.UUID(item) for item in state.get("generated_media_ids", [])]
    with session_scope() as session:
        media_items = (
            list(session.scalars(select(Media).where(Media.id.in_(media_ids)))) if media_ids else []
        )
    chat_panel = Div(
        Div(
            H2("Creation chat"),
            Span(chat.workflow_mode.value.upper(), cls=f"mode-badge {chat.workflow_mode.value}"),
            cls="card-head",
        ),
        Div(
            *[
                Div(
                    Div(item.content, cls="chat-bubble"),
                    Small(
                        "You"
                        if item.role == ChatRole.user
                        else (item.agent_slug or "FastSocial agents")
                    ),
                    cls=f"chat-message {item.role.value}",
                )
                for item in messages
            ],
            cls="agent-chat-messages",
        ),
        Details(
            Summary(f"Agent activity · {len(events)} events"),
            Ul(
                *[
                    Li(Span(event.stage.title(), cls="event-stage"), event.label)
                    for event in events
                ],
                cls="agent-events",
            ),
            cls="agent-trace",
            open=chat.stage in {WorkflowStage.generate, WorkflowStage.failed},
        ),
        Form(
            csrf_input(sess),
            Textarea(
                name="message",
                placeholder="Refine the angle, tone, evidence, or media direction…",
                required=True,
            ),
            Button("Send →", type="submit", cls="btn primary"),
            method="post",
            action=f"/chats/{chat.id}/messages",
            cls="agent-followup",
        )
        if chat.stage not in {WorkflowStage.complete}
        else "",
        cls="card agent-chat-panel",
    )
    if artifact:
        review = content.get("review") if isinstance(content.get("review"), dict) else {}
        artifact_panel = Div(
            Div(
                Div(H2("Post artifact"), Small(f"{artifact.provider}:{artifact.model_name}")),
                Span(artifact.status.value.upper(), cls="mode-badge"),
                cls="card-head",
            ),
            Form(
                csrf_input(sess),
                Div(
                    Label("Master post"),
                    Textarea(
                        content.get("text", ""),
                        name="text",
                        required=True,
                        readonly=True if completed else None,
                    ),
                    cls="field",
                ),
                *[
                    Div(
                        Label(f"{PLATFORM_NAMES.get(platform, platform.title())} variant"),
                        Textarea(
                            value,
                            name=f"variant_{platform}",
                            readonly=True if completed else None,
                        ),
                        cls="field compact",
                    )
                    for platform, value in variants.items()
                ],
                Div(
                    H3("Editorial review"),
                    P(str(review.get("summary") or "Ready for review.")),
                    Ul(*[Li(str(item)) for item in review.get("risks", [])])
                    if review.get("risks")
                    else "",
                    cls="review-box",
                ),
                (
                    Div(
                        Label("Schedule time"),
                        Input(
                            type="datetime-local",
                            name="scheduled_at",
                            value=str(state.get("scheduled_at") or ""),
                        ),
                        cls="field",
                    )
                    if not completed and not awaiting_approval
                    else ""
                ),
                (
                    Div(
                        (
                            Button("Approve for posting →", type="submit", cls="btn primary")
                            if awaiting_approval
                            else Div(
                                Button(
                                    "Save draft",
                                    type="submit",
                                    name="delivery",
                                    value="draft",
                                    cls="btn",
                                ),
                                Button(
                                    "Schedule",
                                    type="submit",
                                    name="delivery",
                                    value="schedule",
                                    cls="btn",
                                    disabled=True if not state.get("target_ids") else None,
                                ),
                                Button(
                                    "Publish now",
                                    type="submit",
                                    name="delivery",
                                    value="now",
                                    cls="btn primary",
                                    disabled=True if not state.get("target_ids") else None,
                                ),
                                cls="form-actions",
                            )
                        ),
                        cls="form-actions",
                    )
                    if not completed
                    else Div(
                        A("View post →", href=f"/posts/{chat.post_id}", cls="btn primary"),
                        cls="form-actions",
                    )
                ),
                method="post",
                action=(
                    f"/chats/{chat.id}/approve" if awaiting_approval else f"/chats/{chat.id}/post"
                ),
            ),
            Div(
                Form(
                    csrf_input(sess),
                    Input(type="hidden", name="kind", value="image"),
                    Input(type="hidden", name="prompt", value=content.get("image_prompt", "")),
                    Button("Generate image", type="submit", cls="btn"),
                    method="post",
                    action=f"/chats/{chat.id}/media",
                ),
                Form(
                    csrf_input(sess),
                    Input(type="hidden", name="kind", value="video"),
                    Input(type="hidden", name="prompt", value=content.get("video_prompt", "")),
                    Button("Generate video", type="submit", cls="btn"),
                    method="post",
                    action=f"/chats/{chat.id}/media",
                ),
                cls="form-actions left media-actions",
            )
            if not completed
            else "",
            Div(
                *[
                    Div(
                        (
                            NotStr(
                                f'<img src="{media_storage().url(item.storage_key)}" alt="Generated media">'
                            )
                            if item.mime_type.startswith("image/")
                            else NotStr(
                                f'<video controls preload="metadata" src="{media_storage().url(item.storage_key)}"></video>'
                            )
                        ),
                        Small(item.filename),
                        cls="generated-media-item",
                    )
                    for item in media_items
                ],
                cls="generated-media-strip",
            )
            if media_items
            else "",
            cls="card agent-artifact-panel",
        )
    else:
        artifact_panel = empty_state(
            "✦", "No artifact yet", "Send a refinement or start a new post."
        )
    return Div(
        _workflow_steps(chat.stage),
        flash(message),
        flash(error, "error"),
        Div(chat_panel, artifact_panel, cls="agent-workbench"),
    )


@rt("/chats/{chat_id}")
def chat_detail(chat_id: str, sess, saved: str = "", error: str = ""):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    chat, messages, events, artifact = _chat_records(ctx, chat_id)
    if not chat:
        return Response("Not found", status_code=404)
    saved_message = {
        "approved": "Content approved. Choose how to save or deliver it when you are ready.",
        "posted": "Post delivered to the publishing pipeline.",
        "media": "Generated media saved to the library.",
    }.get(saved, "")
    return _app_page(
        ctx,
        chat.title,
        f"/chats/{chat.id}",
        _chat_page(ctx, sess, chat, messages, events, artifact, saved_message, error),
    )


@rt("/chats/{chat_id}/messages", methods=["POST"])
async def chat_message(chat_id: str, request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    chat, _messages, _events, _artifact = _chat_records(ctx, chat_id)
    if not chat:
        return Response("Not found", status_code=404)
    form = await request.form()
    if not verify_csrf(sess, form.get("csrf")):
        return Response("Forbidden", status_code=403)
    value = str(form.get("message") or "").strip()
    if not value:
        return RedirectResponse(f"/chats/{chat.id}?error=Message+is+required", status_code=303)
    with session_scope() as session:
        row = session.get(ChatSession, chat.id)
        session.add(ChatMessage(chat_session_id=row.id, role=ChatRole.user, content=value))
        row.status = "active"
        row.stage = WorkflowStage.create
    try:
        await generate_chat_artifact(chat.id, user_email=ctx.user.email)
        return RedirectResponse(f"/chats/{chat.id}", status_code=303)
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(f"/chats/{chat.id}?error={quote_plus(str(exc))}", status_code=303)


@rt("/chats/{chat_id}/media", methods=["POST"])
async def chat_media(chat_id: str, request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    chat, _messages, _events, _artifact = _chat_records(ctx, chat_id)
    if not chat:
        return Response("Not found", status_code=404)
    form = await request.form()
    if not verify_csrf(sess, form.get("csrf")):
        return Response("Forbidden", status_code=403)
    try:
        prompt = str(form.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("Generation prompt is required")
        await generate_media_for_chat(
            chat.id,
            user_id=ctx.user.id,
            user_email=ctx.user.email,
            kind=str(form.get("kind") or ""),
            prompt=prompt,
        )
        return RedirectResponse(f"/chats/{chat.id}?saved=media", status_code=303)
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(f"/chats/{chat.id}?error={quote_plus(str(exc))}", status_code=303)


async def _deliver_chat(chat_id: uuid.UUID, ctx: PageContext, *, delivery: str, form=None):
    with session_scope() as session:
        chat = session.get(ChatSession, chat_id)
        artifact = latest_artifact(session, chat_id)
        if not chat or not artifact or chat.workspace_id != ctx.workspace.id:
            raise ValueError("Post artifact not found")
        if chat.post_id:
            return chat.post_id
        if artifact.status != ArtifactStatus.ready:
            raise ValueError("Approve the current artifact before posting")
        content = dict(artifact.content)
        if form is not None:
            content["text"] = str(form.get("text") or "").strip()
            variants = dict(content.get("variants") or {})
            for platform in list(variants):
                variants[platform] = str(
                    form.get(f"variant_{platform}") or variants[platform]
                ).strip()
            content["variants"] = variants
            artifact.content = content
        state = dict(chat.state or {})
        scheduled_value = (
            str(form.get("scheduled_at") or "")
            if form is not None
            else str(state.get("scheduled_at") or "")
        )
        scheduled_at = (
            utcnow()
            if delivery == "now"
            else _parse_datetime(scheduled_value, ctx.workspace.timezone)
            if delivery == "schedule"
            else None
        )
        if delivery == "schedule" and not scheduled_at:
            raise ValueError("Choose a schedule time")
        target_ids = [uuid.UUID(item) for item in state.get("target_ids", [])]
        media_ids = [uuid.UUID(item) for item in state.get("generated_media_ids", [])]
        workspace = session.get(Workspace, chat.workspace_id)
        post = create_post(
            session,
            workspace=workspace,
            user_id=ctx.user.id,
            text=content.get("text", ""),
            target_ids=target_ids,
            media_ids=media_ids,
            scheduled_at=scheduled_at,
            save_draft=delivery == "draft" or not target_ids,
            platform_text=content.get("variants") or {},
        )
        chat.post_id = post.id
        chat.stage = WorkflowStage.post
        chat.status = "completed"
        artifact.status = (
            ArtifactStatus.posted if delivery == "now" and target_ids else ArtifactStatus.ready
        )
        record_event(
            session,
            chat.id,
            "post",
            "completed",
            "Post handed to the deterministic publishing pipeline",
            delivery=delivery,
        )
        post_id = post.id
        should_publish = (
            delivery == "now" and bool(target_ids) and post.status == PostStatus.scheduled
        )
    if should_publish:
        await publish_post(post_id)
    with session_scope() as session:
        chat = session.get(ChatSession, chat_id)
        chat.stage = WorkflowStage.complete
    return post_id


def _content_from_review_form(artifact: ContentArtifact, form) -> dict:
    content = dict(artifact.content)
    content["text"] = str(form.get("text") or "").strip()
    variants = dict(content.get("variants") or {})
    for platform in list(variants):
        variants[platform] = str(form.get(f"variant_{platform}") or "").strip()
    content["variants"] = variants
    return content


@rt("/chats/{chat_id}/approve", methods=["POST"])
async def chat_approve(chat_id: str, request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    chat, _messages, _events, artifact = _chat_records(ctx, chat_id)
    if not chat or not artifact:
        return Response("Not found", status_code=404)
    form = await request.form()
    if not verify_csrf(sess, form.get("csrf")):
        return Response("Forbidden", status_code=403)
    try:
        with session_scope() as session:
            row = session.get(ChatSession, chat.id)
            current = latest_artifact(session, chat.id)
            if not current or current.status != ArtifactStatus.review:
                raise ValueError("This artifact is not awaiting approval")
            content = _content_from_review_form(current, form)
            state = dict(row.state or {})
            platforms = [str(item) for item in state.get("platforms", [])]
            errors = {}
            for platform in platforms:
                errors.update(
                    validate_content(
                        str((content.get("variants") or {}).get(platform) or content["text"]),
                        [platform],
                    )
                )
            if errors:
                raise ValueError("; ".join(errors.values()))
            current.content = content
            current.status = ArtifactStatus.ready
            row.stage = WorkflowStage.post
            row.status = "active"
            record_event(
                session,
                row.id,
                "review",
                "approved",
                "Content approved for posting",
                artifact_version=current.version,
            )
        return RedirectResponse(f"/chats/{chat.id}?saved=approved", status_code=303)
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(f"/chats/{chat.id}?error={quote_plus(str(exc))}", status_code=303)


@rt("/chats/{chat_id}/post", methods=["POST"])
async def chat_post(chat_id: str, request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    chat, _messages, _events, _artifact = _chat_records(ctx, chat_id)
    if not chat:
        return Response("Not found", status_code=404)
    form = await request.form()
    if not verify_csrf(sess, form.get("csrf")):
        return Response("Forbidden", status_code=403)
    try:
        await _deliver_chat(chat.id, ctx, delivery=str(form.get("delivery") or "draft"), form=form)
        return RedirectResponse(f"/chats/{chat.id}?saved=posted", status_code=303)
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(f"/chats/{chat.id}?error={quote_plus(str(exc))}", status_code=303)


def _post_query(workspace_id):
    return (
        select(Post)
        .where(Post.workspace_id == workspace_id)
        .options(selectinload(Post.targets).selectinload(PostTarget.social_account))
        .order_by(desc(Post.created_at))
    )


@rt("/posts")
def posts_page(sess, status: str = ""):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    with session_scope() as session:
        query = _post_query(ctx.workspace.id)
        if status:
            try:
                query = query.where(Post.status == PostStatus(status))
            except ValueError:
                pass
        posts = list(session.scalars(query.limit(100)))
    rows = [
        Tr(
            Td(A((post.content.get("text", "") or "Untitled")[:90], href=f"/posts/{post.id}")),
            Td(
                Div(
                    *[
                        Span(
                            PLATFORM_MARKS.get(t.social_account.platform, "?"),
                            cls=f"platform-mark {t.social_account.platform}",
                        )
                        for t in post.targets
                    ],
                    cls="post-platforms",
                )
            ),
            Td(status_badge(post.status)),
            Td(
                _format_datetime(post.scheduled_at or post.published_at, ctx.workspace.timezone),
                cls="mono",
            ),
            Td(A("View", href=f"/posts/{post.id}", cls="btn small")),
        )
        for post in posts
    ]
    content = (
        Div(
            Table(
                Thead(Tr(Th("Content"), Th("Networks"), Th("Status"), Th("Publish time"), Th(""))),
                Tbody(*rows),
            ),
            cls="card table-wrap",
        )
        if rows
        else empty_state(
            "≡",
            "No posts yet",
            "Draft, schedule, or publish your first piece of content.",
            "New Post",
            "/new-post",
        )
    )
    return _app_page(
        ctx,
        "Posts",
        "/posts",
        page_intro(
            "LIBRARY",
            "Every post, one dependable record.",
            "Filter drafts, scheduled work, published posts, and failures from the same workspace.",
        ),
        content,
        action=A("+ New Post", href="/new-post", cls="btn primary"),
    )


@rt("/posts/{post_id}")
def post_detail(post_id: str, sess, saved: str = ""):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    try:
        post_uuid = uuid.UUID(post_id)
    except ValueError:
        return Response("Not found", status_code=404)
    with session_scope() as session:
        post = session.scalar(
            select(Post)
            .where(Post.id == post_uuid, Post.workspace_id == ctx.workspace.id)
            .options(
                selectinload(Post.targets).selectinload(PostTarget.social_account),
                selectinload(Post.media_links),
            )
        )
        if not post:
            return Response("Not found", status_code=404)
        targets = list(post.targets)
        activity = list(
            session.scalars(
                select(AuditLog)
                .where(
                    AuditLog.workspace_id == ctx.workspace.id,
                    AuditLog.entity_id == str(post.id),
                )
                .order_by(desc(AuditLog.created_at))
            )
        )
    target_rows = [
        Tr(
            Td(
                platform_pill(
                    target.social_account.platform,
                    target.social_account.display_name or target.social_account.username,
                )
            ),
            Td(target.social_account.provider.value.title()),
            Td(status_badge(target.status)),
            Td(target.platform_post_id or "—", cls="mono"),
            Td(target.error_message or "—"),
        )
        for target in targets
    ]
    approval_controls = ""
    if post.status == PostStatus.pending_approval and ctx.membership.role in {
        WorkspaceRole.owner,
        WorkspaceRole.admin,
    }:
        approval_controls = Div(
            Form(
                csrf_input(sess),
                Input(type="hidden", name="decision", value="reject"),
                Input(type="text", name="comment", placeholder="Reason for rejection"),
                Button("Reject", type="submit", cls="btn danger"),
                method="post",
                action=f"/posts/{post.id}/approval",
            ),
            Form(
                csrf_input(sess),
                Input(type="hidden", name="decision", value="approve"),
                Button("Approve and schedule", type="submit", cls="btn primary"),
                method="post",
                action=f"/posts/{post.id}/approval",
            ),
            cls="form-actions",
        )
    schedule_controls = ""
    if post.status in {
        PostStatus.draft,
        PostStatus.scheduled,
        PostStatus.failed,
        PostStatus.partially_failed,
    } and ctx.membership.role in {WorkspaceRole.owner, WorkspaceRole.admin, WorkspaceRole.editor}:
        schedule_controls = Div(
            Form(
                csrf_input(sess),
                Input(type="datetime-local", name="scheduled_at", required=True),
                Button("Reschedule", type="submit", cls="btn"),
                method="post",
                action=f"/posts/{post.id}/reschedule",
            ),
            Form(
                csrf_input(sess),
                Button("Cancel post", type="submit", cls="btn danger"),
                method="post",
                action=f"/posts/{post.id}/cancel",
            ),
            cls="form-actions",
        )
    return _app_page(
        ctx,
        "Post details",
        f"/posts/{post_id}",
        flash("Post saved." if saved else ""),
        page_intro(
            "POST",
            (post.content.get("text", "") or "Untitled post")[:80],
            f"Created {_format_datetime(post.created_at, ctx.workspace.timezone)}",
            A("Back to posts", href="/posts", cls="btn"),
        ),
        Div(
            Div(H2("Content"), status_badge(post.status), cls="card-head"),
            Div(
                P(post.content.get("text", "")),
                P(
                    f"Scheduled: {_format_datetime(post.scheduled_at, ctx.workspace.timezone)}",
                    cls="form-help",
                ),
                approval_controls,
                schedule_controls,
                cls="card-body",
            ),
            cls="card",
        ),
        Br(),
        Div(
            Div(H2("Publishing targets"), cls="card-head"),
            Div(
                Table(
                    Thead(
                        Tr(
                            Th("Account"),
                            Th("Connection"),
                            Th("Status"),
                            Th("Post ID"),
                            Th("Error"),
                        )
                    ),
                    Tbody(*target_rows),
                ),
                cls="table-wrap",
            ),
            cls="card",
        ),
        Br(),
        Div(
            Div(H2("Activity"), cls="card-head"),
            Div(
                *[
                    Div(
                        Strong(item.action.replace(".", " ").title()),
                        Small(_format_datetime(item.created_at, ctx.workspace.timezone)),
                        cls="connected-account",
                    )
                    for item in activity
                ],
                cls="card-body",
            ),
            cls="card",
        ),
    )


@rt("/posts/{post_id}/approval", methods=["POST"])
async def post_approval(post_id: str, request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    if ctx.membership.role not in {WorkspaceRole.owner, WorkspaceRole.admin}:
        return Response("Forbidden", status_code=403)
    form = await request.form()
    if not verify_csrf(sess, form.get("csrf")):
        return Response("Forbidden", status_code=403)
    try:
        post_uuid = uuid.UUID(post_id)
    except ValueError:
        return Response("Not found", status_code=404)
    decision = str(form.get("decision") or "")
    with session_scope() as session:
        post = session.scalar(
            select(Post).where(Post.id == post_uuid, Post.workspace_id == ctx.workspace.id)
        )
        if not post or post.status != PostStatus.pending_approval:
            return Response("Post is not awaiting approval", status_code=409)
        approved = decision == "approve"
        session.add(
            PostApproval(
                post_id=post.id,
                requested_by=post.created_by,
                reviewed_by=ctx.user.id,
                status=ApprovalStatus.approved if approved else ApprovalStatus.rejected,
                comment=str(form.get("comment") or "").strip(),
                reviewed_at=utcnow(),
            )
        )
        if approved:
            post.status = PostStatus.scheduled
            post.scheduled_at = post.scheduled_at or utcnow()
            action = "post.approved"
        else:
            post.status = PostStatus.draft
            action = "post.rejected"
        audit(session, ctx.workspace.id, ctx.user.id, action, post)
    return RedirectResponse(f"/posts/{post_id}", status_code=303)


@rt("/posts/{post_id}/reschedule", methods=["POST"])
async def post_reschedule(post_id: str, request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    if ctx.membership.role == WorkspaceRole.viewer:
        return Response("Forbidden", status_code=403)
    form = await request.form()
    if not verify_csrf(sess, form.get("csrf")):
        return Response("Forbidden", status_code=403)
    scheduled_at = _parse_datetime(str(form.get("scheduled_at") or ""), ctx.workspace.timezone)
    if not scheduled_at:
        return Response("A schedule time is required", status_code=400)
    try:
        post_uuid = uuid.UUID(post_id)
    except ValueError:
        return Response("Not found", status_code=404)
    with session_scope() as session:
        post = session.scalar(
            select(Post).where(Post.id == post_uuid, Post.workspace_id == ctx.workspace.id)
        )
        if not post or post.status == PostStatus.published:
            return Response("Post cannot be rescheduled", status_code=409)
        post.scheduled_at = scheduled_at
        post.status = PostStatus.scheduled
        for target in post.targets:
            if target.status != TargetStatus.published:
                target.status = TargetStatus.pending
                target.next_retry_at = None
        audit(session, ctx.workspace.id, ctx.user.id, "post.rescheduled", post)
    return RedirectResponse(f"/posts/{post_id}", status_code=303)


@rt("/posts/{post_id}/cancel", methods=["POST"])
async def post_cancel(post_id: str, request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    if ctx.membership.role == WorkspaceRole.viewer:
        return Response("Forbidden", status_code=403)
    form = await request.form()
    if not verify_csrf(sess, form.get("csrf")):
        return Response("Forbidden", status_code=403)
    try:
        post_uuid = uuid.UUID(post_id)
    except ValueError:
        return Response("Not found", status_code=404)
    with session_scope() as session:
        post = session.scalar(
            select(Post)
            .where(Post.id == post_uuid, Post.workspace_id == ctx.workspace.id)
            .options(selectinload(Post.targets))
        )
        if not post or post.status == PostStatus.published:
            return Response("Post cannot be cancelled", status_code=409)
        post.status = PostStatus.cancelled
        for target in post.targets:
            if target.status != TargetStatus.published:
                target.status = TargetStatus.cancelled
        audit(session, ctx.workspace.id, ctx.user.id, "post.cancelled", post)
    return RedirectResponse(f"/posts/{post_id}", status_code=303)


@rt("/calendar")
def calendar_page(sess, year: int = 0, month: int = 0):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    today = date.today()
    year = year or today.year
    month = month or today.month
    first = date(year, month, 1)
    last = date(year, month, calendar_module.monthrange(year, month)[1])
    start_utc = datetime.combine(
        first, datetime.min.time(), tzinfo=ZoneInfo(ctx.workspace.timezone)
    ).astimezone(UTC)
    end_utc = datetime.combine(
        last, datetime.max.time(), tzinfo=ZoneInfo(ctx.workspace.timezone)
    ).astimezone(UTC)
    with session_scope() as session:
        posts = list(
            session.scalars(
                select(Post).where(
                    Post.workspace_id == ctx.workspace.id,
                    Post.scheduled_at >= start_utc,
                    Post.scheduled_at <= end_utc,
                )
            )
        )
    by_day: dict[int, list[Post]] = {}
    for post in posts:
        value = post.scheduled_at
        if value and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        if value:
            by_day.setdefault(value.astimezone(ZoneInfo(ctx.workspace.timezone)).day, []).append(
                post
            )
    weeks = calendar_module.Calendar(firstweekday=0).monthdatescalendar(year, month)
    day_cells = []
    for week in weeks:
        for day in week:
            items = by_day.get(day.day, []) if day.month == month else []
            day_cells.append(
                Div(
                    Span(str(day.day), cls="calendar-day-number"),
                    *[
                        A(
                            (item.content.get("text", "") or "Untitled")[:42],
                            href=f"/posts/{item.id}",
                            cls="calendar-post",
                        )
                        for item in items
                    ],
                    cls=f"calendar-day{' muted' if day.month != month else ''}",
                )
            )
    prev_month = first.replace(day=1) - __import__("datetime").timedelta(days=1)
    next_month = last + __import__("datetime").timedelta(days=1)
    calendar_view = Div(
        Div(
            *[Div(day) for day in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")],
            cls="calendar-head",
        ),
        Div(*day_cells, cls="calendar-grid"),
        cls="calendar",
    )
    controls = Div(
        A("←", href=f"/calendar?year={prev_month.year}&month={prev_month.month}", cls="btn"),
        A("Today", href="/calendar", cls="btn"),
        A("→", href=f"/calendar?year={next_month.year}&month={next_month.month}", cls="btn"),
        cls="form-actions",
    )
    return _app_page(
        ctx,
        "Calendar",
        "/calendar",
        page_intro(
            "SCHEDULE",
            first.strftime("%B %Y"),
            "Your complete publishing plan in workspace time.",
            controls,
        ),
        calendar_view,
    )


@rt("/skills")
def skills_page(sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    with session_scope() as session:
        skills = list(session.scalars(select(SkillDefinition).order_by(SkillDefinition.name)))
        edited = {
            slug
            for (slug,) in session.execute(
                select(WorkspaceSkillVersion.skill_slug).where(
                    WorkspaceSkillVersion.workspace_id == ctx.workspace.id,
                    WorkspaceSkillVersion.status == SkillVersionStatus.published,
                )
            )
        }
    cards = [
        A(
            Div(
                Span("✦", cls="skill-icon"),
                Div(H2(skill.name), P(skill.description or "Agent operating instructions.")),
                Span(
                    "CUSTOM" if skill.slug in edited else f"v{skill.upstream_version or '1'}",
                    cls="mode-badge",
                ),
                cls="skill-card-content",
            ),
            href=f"/skills/{skill.slug}",
            cls="skill-card",
        )
        for skill in skills
    ]
    return _app_page(
        ctx,
        "Skills",
        "/skills",
        page_intro(
            "AGENT OPERATING SYSTEM",
            f"{len(skills)} editable marketing skills.",
            "FastSocial routes each brief through the relevant skills before the deterministic publishing pipeline.",
        ),
        Div(*cards, cls="skills-grid"),
    )


@rt("/skills/{slug}")
async def skill_editor(slug: str, request, sess, saved: str = ""):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    can_edit = ctx.membership.role in {WorkspaceRole.owner, WorkspaceRole.admin}
    error = ""
    with session_scope() as session:
        definition = session.get(SkillDefinition, slug)
    if not definition:
        return Response("Not found", status_code=404)
    if request.method == "POST":
        form = await request.form()
        if not can_edit:
            return Response("Forbidden", status_code=403)
        if not verify_csrf(sess, form.get("csrf")):
            error = "Your session expired."
        else:
            content = str(form.get("content") or "").strip()
            if not content:
                error = "Skill content cannot be empty."
            else:
                with session_scope() as session:
                    publish_skill_version(
                        session,
                        workspace_id=ctx.workspace.id,
                        slug=slug,
                        content=content,
                        changed_by=ctx.user.id,
                    )
                    audit(session, ctx.workspace.id, ctx.user.id, "skill.published", definition)
                return RedirectResponse(f"/skills/{slug}?saved=1", status_code=303)
    with session_scope() as session:
        content = skill_content(session, ctx.workspace.id, slug)
        versions = list(
            session.scalars(
                select(WorkspaceSkillVersion)
                .where(
                    WorkspaceSkillVersion.workspace_id == ctx.workspace.id,
                    WorkspaceSkillVersion.skill_slug == slug,
                )
                .order_by(desc(WorkspaceSkillVersion.version))
            )
        )
    editor = Form(
        csrf_input(sess),
        Div(
            Button("Editor", type="button", cls="skill-tab active", data_tab="editor"),
            Button("Markdown", type="button", cls="skill-tab", data_tab="markdown"),
            cls="skill-tabs",
        ),
        Textarea(content, name="content", id="skill-markdown", cls="skill-markdown", rows="30"),
        Div(id="skill-editor", cls="skill-editor"),
        Div(
            A("Cancel", href="/skills", cls="btn"),
            Button("Publish new version", type="submit", cls="btn primary", disabled=not can_edit),
            cls="form-actions",
        ),
        method="post",
        action=f"/skills/{slug}",
        id="skill-form",
        cls="card skill-edit-card",
    )
    history = Div(
        Div(
            H2("Version history"),
            Span(f"{len(versions)} custom", cls="mode-badge"),
            cls="card-head",
        ),
        *[
            Div(
                Strong(f"Version {row.version}"),
                Small(_format_datetime(row.created_at, ctx.workspace.timezone)),
                P(row.content[:180].replace("\n", " ") + ("…" if len(row.content) > 180 else "")),
                cls="skill-version",
            )
            for row in versions
        ],
        P("The upstream baseline is active until you publish a custom version.", cls="form-help")
        if not versions
        else "",
        cls="card skill-history",
    )
    return _app_page(
        ctx,
        definition.name,
        f"/skills/{slug}",
        Link(rel="stylesheet", href="https://cdn.quilljs.com/2.0.3/quill.snow.css"),
        Script(src="https://cdn.quilljs.com/2.0.3/quill.js"),
        Script(src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"),
        page_intro(
            "EDITABLE SKILL",
            definition.name,
            definition.description[:360] + ("…" if len(definition.description) > 360 else ""),
        ),
        flash("Skill version published." if saved else ""),
        flash(error, "error"),
        Div(editor, history, cls="skill-editor-layout"),
        Script(src="/static/skills.js"),
    )


def _integration_card(platform: str, accounts: list[SocialAccount]):
    direct_ready = {
        "x": bool(settings().x_client_id and settings().x_client_secret),
        "linkedin": bool(settings().linkedin_client_id and settings().linkedin_client_secret),
        "bluesky": settings().bluesky_app_password_enabled,
    }[platform]
    paths = [
        (
            "Direct OAuth",
            "FastSocial connects to the platform and stores encrypted tokens.",
            ConnectionProvider.direct,
            direct_ready,
        ),
        (
            "Arcade MCP",
            "Arcade owns downstream authorization and exposes publishing tools.",
            ConnectionProvider.arcade,
            bool(settings().arcade_api_key and settings().arcade_mcp_url),
        ),
        (
            "Composio MCP",
            "Composio manages connected accounts, refresh, and tool execution.",
            ConnectionProvider.composio,
            bool(settings().composio_api_key and settings().composio_mcp_url),
        ),
    ]
    if not settings().production:
        paths.append(
            (
                "Local demo",
                "A deterministic local account that can never reach the real network.",
                ConnectionProvider.mock,
                True,
            )
        )
    path_cards = [
        Div(
            H3(label),
            P(description),
            Span(
                "Configured" if ready else "Configuration required",
                cls=f"status-badge {'connected' if ready else 'draft'}",
            ),
            A(
                "Connect",
                href=f"/integrations/connect/{platform}/{provider.value}",
                cls="btn small",
                aria_disabled="false" if ready else "true",
            ),
            cls="connection-path",
        )
        for label, description, provider, ready in paths
    ]
    existing = [
        Div(
            Span(PLATFORM_MARKS[platform], cls=f"platform-mark {platform}"),
            Div(
                Span(account.display_name or account.username or account.external_account_id),
                Small(
                    f"{account.provider.value.title()} · {account.username or account.external_account_id}"
                ),
                cls="connected-account-copy",
            ),
            status_badge(account.status),
            A("Manage", href=f"/integrations/accounts/{account.id}", cls="btn small"),
            cls="connected-account",
        )
        for account in accounts
    ]
    return Div(
        Div(
            Div(
                Span(PLATFORM_MARKS[platform], cls=f"platform-mark {platform}"),
                H2(PLATFORM_NAMES[platform]),
                cls="integration-title",
            ),
            Span(
                f"{len(accounts)} connected",
                cls="status-badge connected" if accounts else "status-badge draft",
            ),
            cls="integration-card-head",
        ),
        P(
            {
                "x": "Publish posts and collect public and authorized metrics.",
                "linkedin": "Publish personal or organization content using LinkedIn's versioned Posts API.",
                "bluesky": "Publish through the open AT Protocol using a revocable app password.",
            }[platform]
        ),
        Div(*path_cards, cls="connection-paths"),
        (
            Div(Strong("Connected accounts"), *existing, cls="connected-accounts")
            if existing
            else ""
        ),
        cls="integration-card",
        id=platform,
    )


def _ai_provider_card(
    ctx: PageContext,
    sess: dict,
    provider: str,
    credential: AIProviderCredential | None,
    profiles: dict[tuple[str, str], ModelProfile],
):
    configured = bool(credential)
    server_available = settings().server_model_access_allowed(ctx.user.email) and bool(
        settings().xai_api_key if provider == "xai" else settings().openai_api_key
    )
    access = (
        f"Workspace key {credential.masked_hint}"
        if credential
        else "FastSocial server key"
        if server_available
        else "Bring your own key"
    )
    return Div(
        Div(
            Div(H2("xAI" if provider == "xai" else "OpenAI"), Small(access)),
            Span(
                "CONNECTED" if configured or server_available else "BYOK REQUIRED",
                cls="status-badge connected"
                if configured or server_available
                else "status-badge draft",
            ),
            cls="integration-card-head",
        ),
        P(
            "Your workspace key is encrypted at rest and takes precedence over the private server key. "
            "Model IDs are fully configurable for bring-your-own-model workflows."
        ),
        Form(
            csrf_input(sess),
            Input(type="hidden", name="provider", value=provider),
            Div(
                Label("API key"),
                Input(
                    type="password",
                    name="api_key",
                    placeholder="Leave blank to keep the existing key",
                    autocomplete="off",
                ),
                cls="field",
            ),
            *[
                Div(
                    Label(f"{purpose.title()} model"),
                    Input(
                        type="text",
                        name=f"{purpose}_model",
                        value=(
                            profiles[(provider, purpose)].model_name
                            if (provider, purpose) in profiles
                            else default_model(provider, purpose)
                        ),
                        required=True,
                    ),
                    cls="field",
                )
                for purpose in ("text", "image", "video")
            ],
            Div(
                Button(
                    "Save provider", type="submit", name="action", value="save", cls="btn primary"
                ),
                Button("Test access", type="submit", name="action", value="test", cls="btn"),
                Button(
                    "Remove BYOK", type="submit", name="action", value="remove", cls="btn danger"
                )
                if credential
                else "",
                cls="form-actions left",
            ),
            method="post",
            action="/integrations/models",
            cls="ai-provider-form",
        ),
        (
            P(f"Last test: {credential.status}. {credential.last_error}", cls="form-help")
            if credential and credential.last_tested_at
            else ""
        ),
        cls="integration-card ai-provider-card",
    )


@rt("/integrations")
def integrations_page(sess, saved: str = "", error: str = ""):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    grouped = {
        platform: [account for account in ctx.accounts if account.platform == platform]
        for platform in PLATFORM_NAMES
    }
    healthy = sum(account.status == AccountStatus.connected for account in ctx.accounts)
    with session_scope() as session:
        credentials = {
            item.provider: item
            for item in session.scalars(
                select(AIProviderCredential).where(
                    AIProviderCredential.workspace_id == ctx.workspace.id
                )
            )
        }
        profiles = {
            (item.provider, item.purpose): item
            for item in session.scalars(
                select(ModelProfile).where(ModelProfile.workspace_id == ctx.workspace.id)
            )
        }
    return _app_page(
        ctx,
        "Integrations",
        "/integrations",
        page_intro(
            "CONNECT",
            "One workspace. Three ways to connect.",
            "Use direct platform credentials, Arcade MCP, or Composio MCP account by account. Tokens are never displayed after connection.",
            Form(
                csrf_input(sess),
                Button("Check connections", type="submit", cls="btn"),
                method="post",
                action="/integrations/health",
            ),
        ),
        flash(
            "Connection health refreshed."
            if saved == "health"
            else ("Integration connected." if saved else "")
        ),
        flash(error, "error"),
        Div(
            stat_card("Connected", len(ctx.accounts), "Social identities in this workspace"),
            stat_card("Healthy", healthy, "Passed the latest connection state"),
            stat_card(
                "Needs attention", len(ctx.accounts) - healthy, "Reconnect or inspect configuration"
            ),
            cls="integration-summary",
        ),
        Div(
            Div(
                Span("AI MODELS", cls="eyebrow accent"),
                H2("BYOK / BYOM"),
                P("Choose xAI or OpenAI independently for text, image, and video generation."),
                cls="section-heading",
                id="ai-models",
            ),
            Div(
                *[
                    _ai_provider_card(ctx, sess, provider, credentials.get(provider), profiles)
                    for provider in ("xai", "openai")
                ],
                cls="ai-provider-grid",
            ),
        ),
        Div(
            *[_integration_card(platform, grouped[platform]) for platform in PLATFORM_NAMES],
            style="display:grid;gap:16px",
        ),
    )


@rt("/integrations/models", methods=["POST"])
async def integrations_models(request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    if ctx.membership.role not in {WorkspaceRole.owner, WorkspaceRole.admin}:
        return Response("Forbidden", status_code=403)
    form = await request.form()
    if not verify_csrf(sess, form.get("csrf")):
        return Response("Forbidden", status_code=403)
    provider = str(form.get("provider") or "").lower()
    action = str(form.get("action") or "save")
    if provider not in {"xai", "openai"}:
        return Response("Bad request", status_code=400)
    try:
        with session_scope() as session:
            credential = session.scalar(
                select(AIProviderCredential).where(
                    AIProviderCredential.workspace_id == ctx.workspace.id,
                    AIProviderCredential.provider == provider,
                )
            )
            if action == "remove":
                if credential:
                    session.delete(credential)
                audit(
                    session,
                    ctx.workspace.id,
                    ctx.user.id,
                    "model_key.removed",
                    ctx.workspace,
                    {"provider": provider},
                )
                return RedirectResponse(
                    "/integrations?saved=model_removed#ai-models", status_code=303
                )
            api_key = str(form.get("api_key") or "").strip()
            if api_key:
                if credential is None:
                    credential = AIProviderCredential(
                        workspace_id=ctx.workspace.id,
                        provider=provider,
                        api_key_encrypted=encrypt_text(api_key),
                        masked_hint=f"••••{api_key[-4:]}",
                        created_by=ctx.user.id,
                    )
                    session.add(credential)
                else:
                    credential.api_key_encrypted = encrypt_text(api_key)
                    credential.masked_hint = f"••••{api_key[-4:]}"
                    credential.status = "connected"
                    credential.last_error = ""
            for purpose in ("text", "image", "video"):
                model_name = str(form.get(f"{purpose}_model") or "").strip()
                if not model_name:
                    raise ValueError(f"{purpose.title()} model is required")
                profile = session.scalar(
                    select(ModelProfile).where(
                        ModelProfile.workspace_id == ctx.workspace.id,
                        ModelProfile.provider == provider,
                        ModelProfile.purpose == purpose,
                    )
                )
                if profile:
                    profile.model_name = model_name
                else:
                    session.add(
                        ModelProfile(
                            workspace_id=ctx.workspace.id,
                            provider=provider,
                            purpose=purpose,
                            model_name=model_name,
                        )
                    )
            audit(
                session,
                ctx.workspace.id,
                ctx.user.id,
                "model_provider.saved",
                ctx.workspace,
                {"provider": provider},
            )
        if action == "test":
            with session_scope() as session:
                resolved = resolve_model(
                    session,
                    workspace_id=ctx.workspace.id,
                    user_email=ctx.user.email,
                    provider=provider,
                    purpose="text",
                )
            models = await test_model_connection(resolved)
            with session_scope() as session:
                credential = session.scalar(
                    select(AIProviderCredential).where(
                        AIProviderCredential.workspace_id == ctx.workspace.id,
                        AIProviderCredential.provider == provider,
                    )
                )
                if credential:
                    credential.status = "connected"
                    credential.last_tested_at = utcnow()
                    credential.last_error = ""
            return RedirectResponse(
                f"/integrations?saved={quote_plus(f'{provider} access verified; {len(models)} models visible')}#ai-models",
                status_code=303,
            )
        return RedirectResponse("/integrations?saved=model#ai-models", status_code=303)
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(
            f"/integrations?error={quote_plus(str(exc))}#ai-models", status_code=303
        )


@rt("/integrations/health", methods=["POST"])
async def integrations_health(request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    form = await request.form()
    if not verify_csrf(sess, form.get("csrf")):
        return Response("Forbidden", status_code=403)
    await check_account_health(ctx.workspace.id)
    return RedirectResponse("/integrations?saved=health", status_code=303)


@rt("/integrations/connect/{platform}/{provider}")
async def integration_connect(platform: str, provider: str, request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    allowed_providers = {
        item.value
        for item in ConnectionProvider
        if item != ConnectionProvider.mock or not settings().production
    }
    if platform not in PLATFORM_NAMES or provider not in allowed_providers:
        return Response("Not found", status_code=404)
    provider_enum = ConnectionProvider(provider)
    error = ""
    if request.method == "POST":
        form = await request.form()
        if not verify_csrf(sess, form.get("csrf")):
            error = "Your session expired."
        elif provider_enum == ConnectionProvider.direct and platform == "bluesky":
            handle = str(form.get("username") or "").strip()
            password = str(form.get("app_password") or "").strip()
            if not handle or not password:
                error = "Handle and app password are required."
            else:
                with session_scope() as session:
                    account = SocialAccount(
                        workspace_id=ctx.workspace.id,
                        platform=platform,
                        provider=provider_enum,
                        external_account_id=handle,
                        username=handle,
                        display_name=handle,
                        access_token_encrypted=encrypt_text(password),
                        scopes=["atproto"],
                    )
                    session.add(account)
                    session.flush()
                    audit(
                        session,
                        ctx.workspace.id,
                        ctx.user.id,
                        "integration.connected",
                        account,
                        {"provider": provider},
                    )
                return RedirectResponse("/integrations?saved=1#bluesky", status_code=303)
        elif provider_enum in {ConnectionProvider.arcade, ConnectionProvider.composio}:
            external_id = str(form.get("external_account_id") or "").strip()
            username = str(form.get("username") or "").strip()
            publish_tool = str(form.get("publish_tool") or "").strip()
            if not external_id or not publish_tool:
                error = "Connected account ID and publish tool name are required."
            else:
                metadata = {
                    "managed_user_id": str(form.get("managed_user_id") or ctx.user.id),
                    "publish_tool": publish_tool,
                    "metrics_tool": str(form.get("metrics_tool") or "").strip(),
                    "account_metrics_tool": str(form.get("account_metrics_tool") or "").strip(),
                    "health_tool": str(form.get("health_tool") or "").strip(),
                }
                with session_scope() as session:
                    account = SocialAccount(
                        workspace_id=ctx.workspace.id,
                        platform=platform,
                        provider=provider_enum,
                        external_account_id=external_id,
                        username=username,
                        display_name=username or external_id,
                        account_metadata=metadata,
                    )
                    session.add(account)
                    session.flush()
                    audit(
                        session,
                        ctx.workspace.id,
                        ctx.user.id,
                        "integration.connected",
                        account,
                        {"provider": provider},
                    )
                return RedirectResponse(f"/integrations?saved=1#{platform}", status_code=303)
        elif provider_enum == ConnectionProvider.mock and not settings().production:
            username = str(form.get("username") or f"demo-{platform}").strip()
            with session_scope() as session:
                account = SocialAccount(
                    workspace_id=ctx.workspace.id,
                    platform=platform,
                    provider=provider_enum,
                    external_account_id=f"mock-{platform}-{uuid.uuid4().hex[:10]}",
                    username=username,
                    display_name=f"Demo {PLATFORM_NAMES[platform]}",
                )
                session.add(account)
                session.flush()
                audit(
                    session,
                    ctx.workspace.id,
                    ctx.user.id,
                    "integration.connected",
                    account,
                    {"provider": "mock"},
                )
            return RedirectResponse(f"/integrations?saved=1#{platform}", status_code=303)
    if provider_enum == ConnectionProvider.direct and platform in {"x", "linkedin"}:
        return RedirectResponse(f"/oauth/{platform}/start", status_code=303)
    fields = []
    if provider_enum == ConnectionProvider.direct:
        fields = [
            Div(
                Label("Bluesky handle"),
                Input(type="text", name="username", placeholder="name.bsky.social", required=True),
                cls="field",
            ),
            Div(
                Label("App password"),
                Input(
                    type="password",
                    name="app_password",
                    placeholder="xxxx-xxxx-xxxx-xxxx",
                    autocomplete="off",
                    required=True,
                ),
                Small(
                    "Create a dedicated app password in Bluesky settings. Never use your main password."
                ),
                cls="field",
            ),
        ]
    elif provider_enum in {ConnectionProvider.arcade, ConnectionProvider.composio}:
        fields = [
            Div(
                Label("Connected account ID"),
                Input(type="text", name="external_account_id", required=True),
                cls="field",
            ),
            Div(
                Label("Display name or username"), Input(type="text", name="username"), cls="field"
            ),
            Div(
                Label("Managed user ID"),
                Input(type="text", name="managed_user_id", value=str(ctx.user.id)),
                cls="field",
            ),
            Div(
                Label("Publish MCP tool"),
                Input(type="text", name="publish_tool", placeholder="X.CreatePost", required=True),
                cls="field",
            ),
            Div(
                Label("Post metrics tool"),
                Input(type="text", name="metrics_tool", placeholder="Optional"),
                cls="field",
            ),
            Div(
                Label("Account metrics tool"),
                Input(type="text", name="account_metrics_tool", placeholder="Optional"),
                cls="field",
            ),
            Div(
                Label("Health-check tool"),
                Input(type="text", name="health_tool", placeholder="Optional"),
                cls="field",
            ),
        ]
    else:
        fields = [
            Div(
                Label("Demo username"),
                Input(type="text", name="username", value=f"demo-{platform}"),
                Small("This account generates deterministic IDs and synthetic metrics locally."),
                cls="field",
            )
        ]
    form = Form(
        csrf_input(sess),
        *fields,
        Div(
            A("Cancel", href="/integrations", cls="btn"),
            Button("Save connection", type="submit", cls="btn primary"),
            cls="form-actions",
        ),
        method="post",
        action=f"/integrations/connect/{platform}/{provider}",
        cls="form-card",
    )
    return _app_page(
        ctx,
        "Connect integration",
        "/integrations",
        page_intro(
            provider_enum.value.upper(),
            f"Connect {PLATFORM_NAMES[platform]} through {provider_enum.value.title()}",
            "Configuration is encrypted or delegated to the managed connector.",
        ),
        flash(error, "error"),
        form,
    )


@rt("/oauth/x/start")
def x_oauth_start(sess):
    ctx = _context(sess)
    cfg = settings()
    if not ctx or not cfg.x_client_id:
        return RedirectResponse("/integrations?error=X+OAuth+is+not+configured", status_code=303)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    sess["x_oauth_state"] = state
    sess["x_code_verifier"] = verifier
    params = {
        "response_type": "code",
        "client_id": cfg.x_client_id,
        "redirect_uri": f"{cfg.service_url}/oauth/x/callback",
        "scope": "tweet.read tweet.write users.read offline.access",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return RedirectResponse(
        f"https://x.com/i/oauth2/authorize?{urlencode(params)}", status_code=302
    )


@rt("/oauth/x/callback")
async def x_oauth_callback(code: str = "", state: str = "", error: str = "", sess=None):
    ctx = _context(sess)
    cfg = settings()
    if (
        not ctx
        or error
        or not state
        or not secrets.compare_digest(state, sess.pop("x_oauth_state", ""))
    ):
        return RedirectResponse("/integrations?error=X+authorization+failed", status_code=303)
    verifier = sess.pop("x_code_verifier", "")
    async with httpx.AsyncClient(timeout=25) as client:
        token_response = await client.post(
            "https://api.x.com/2/oauth2/token",
            data={
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": f"{cfg.service_url}/oauth/x/callback",
                "code_verifier": verifier,
                "client_id": cfg.x_client_id,
            },
            auth=(cfg.x_client_id, cfg.x_client_secret) if cfg.x_client_secret else None,
        )
        if not token_response.is_success:
            return RedirectResponse("/integrations?error=X+token+exchange+failed", status_code=303)
        tokens = token_response.json()
        profile_response = await client.get(
            "https://api.x.com/2/users/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
            params={"user.fields": "profile_image_url,name,username"},
        )
    if not profile_response.is_success:
        return RedirectResponse("/integrations?error=X+profile+lookup+failed", status_code=303)
    profile = profile_response.json()["data"]
    with session_scope() as session:
        account = SocialAccount(
            workspace_id=ctx.workspace.id,
            platform="x",
            provider=ConnectionProvider.direct,
            external_account_id=profile["id"],
            username=profile.get("username", ""),
            display_name=profile.get("name", ""),
            avatar_url=profile.get("profile_image_url", ""),
            access_token_encrypted=encrypt_text(tokens["access_token"]),
            refresh_token_encrypted=encrypt_text(tokens.get("refresh_token")),
            scopes=str(tokens.get("scope", "")).split(),
            token_expires_at=utcnow()
            + __import__("datetime").timedelta(seconds=int(tokens.get("expires_in", 7200))),
        )
        session.add(account)
        session.flush()
        audit(
            session,
            ctx.workspace.id,
            ctx.user.id,
            "integration.connected",
            account,
            {"provider": "direct"},
        )
    return RedirectResponse("/integrations?saved=1#x", status_code=303)


@rt("/oauth/linkedin/start")
def linkedin_oauth_start(sess):
    ctx = _context(sess)
    cfg = settings()
    if not ctx or not cfg.linkedin_client_id:
        return RedirectResponse(
            "/integrations?error=LinkedIn+OAuth+is+not+configured", status_code=303
        )
    state = secrets.token_urlsafe(32)
    sess["linkedin_oauth_state"] = state
    params = {
        "response_type": "code",
        "client_id": cfg.linkedin_client_id,
        "redirect_uri": f"{cfg.service_url}/oauth/linkedin/callback",
        "state": state,
        "scope": "openid profile email w_member_social",
    }
    return RedirectResponse(
        f"https://www.linkedin.com/oauth/v2/authorization?{urlencode(params)}", status_code=302
    )


@rt("/oauth/linkedin/callback")
async def linkedin_oauth_callback(code: str = "", state: str = "", error: str = "", sess=None):
    ctx = _context(sess)
    cfg = settings()
    if (
        not ctx
        or error
        or not state
        or not secrets.compare_digest(state, sess.pop("linkedin_oauth_state", ""))
    ):
        return RedirectResponse(
            "/integrations?error=LinkedIn+authorization+failed", status_code=303
        )
    async with httpx.AsyncClient(timeout=25) as client:
        token_response = await client.post(
            "https://www.linkedin.com/oauth/v2/accessToken",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": f"{cfg.service_url}/oauth/linkedin/callback",
                "client_id": cfg.linkedin_client_id,
                "client_secret": cfg.linkedin_client_secret,
            },
        )
        if not token_response.is_success:
            return RedirectResponse(
                "/integrations?error=LinkedIn+token+exchange+failed", status_code=303
            )
        tokens = token_response.json()
        profile_response = await client.get(
            "https://api.linkedin.com/v2/userinfo",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
    if not profile_response.is_success:
        return RedirectResponse(
            "/integrations?error=LinkedIn+profile+lookup+failed", status_code=303
        )
    profile = profile_response.json()
    with session_scope() as session:
        account = SocialAccount(
            workspace_id=ctx.workspace.id,
            platform="linkedin",
            provider=ConnectionProvider.direct,
            external_account_id=f"urn:li:person:{profile['sub']}",
            username=profile.get("email", ""),
            display_name=profile.get("name", "LinkedIn member"),
            avatar_url=profile.get("picture", ""),
            access_token_encrypted=encrypt_text(tokens["access_token"]),
            refresh_token_encrypted=encrypt_text(tokens.get("refresh_token")),
            scopes=["openid", "profile", "email", "w_member_social"],
            token_expires_at=utcnow()
            + __import__("datetime").timedelta(seconds=int(tokens.get("expires_in", 5184000))),
        )
        session.add(account)
        session.flush()
        audit(
            session,
            ctx.workspace.id,
            ctx.user.id,
            "integration.connected",
            account,
            {"provider": "direct"},
        )
    return RedirectResponse("/integrations?saved=1#linkedin", status_code=303)


@rt("/integrations/accounts/{account_id}")
def integration_account(account_id: str, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    try:
        account_uuid = uuid.UUID(account_id)
    except ValueError:
        return Response("Not found", status_code=404)
    account = next((item for item in ctx.accounts if item.id == account_uuid), None)
    if not account:
        return Response("Not found", status_code=404)
    return _app_page(
        ctx,
        "Integration account",
        "/integrations",
        page_intro(
            "ACCOUNT",
            account.display_name or account.username,
            f"{PLATFORM_NAMES.get(account.platform)} through {account.provider.value.title()}",
        ),
        Div(
            Div(H2("Connection details"), status_badge(account.status), cls="card-head"),
            Div(
                P(f"External account: {account.external_account_id}", cls="mono"),
                P(f"Scopes: {', '.join(account.scopes) or 'Managed by provider'}"),
                P(
                    f"Last checked: {_format_datetime(account.last_health_check_at, ctx.workspace.timezone)}"
                ),
                (P(account.last_error, cls="flash error") if account.last_error else ""),
                Form(
                    csrf_input(sess),
                    Button("Disconnect account", cls="btn danger", type="submit"),
                    method="post",
                    action=f"/integrations/accounts/{account.id}/disconnect",
                ),
                cls="card-body",
            ),
            cls="card",
        ),
    )


@rt("/integrations/accounts/{account_id}/disconnect", methods=["POST"])
async def integration_disconnect(account_id: str, request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    form = await request.form()
    if not verify_csrf(sess, form.get("csrf")):
        return Response("Forbidden", status_code=403)
    try:
        account_uuid = uuid.UUID(account_id)
    except ValueError:
        return Response("Not found", status_code=404)
    with session_scope() as session:
        account = session.scalar(
            select(SocialAccount).where(
                SocialAccount.id == account_uuid, SocialAccount.workspace_id == ctx.workspace.id
            )
        )
        if account:
            account.status = AccountStatus.disabled
            account.access_token_encrypted = None
            account.refresh_token_encrypted = None
            audit(session, ctx.workspace.id, ctx.user.id, "integration.disconnected", account)
    return RedirectResponse("/integrations", status_code=303)


@rt("/media")
async def media_page(request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    error = ""
    saved = False
    if request.method == "POST":
        form = await request.form()
        if not verify_csrf(sess, form.get("csrf")):
            error = "Your session expired."
        else:
            files = form.getlist("files")
            try:
                with session_scope() as session:
                    for upload in files:
                        body = await upload.read()
                        store_media(
                            session,
                            workspace_id=ctx.workspace.id,
                            user_id=ctx.user.id,
                            filename=upload.filename,
                            mime_type=upload.content_type or "application/octet-stream",
                            body=body,
                        )
                saved = bool(files)
            except ValueError as exc:
                error = str(exc)
    with session_scope() as session:
        items = list(
            session.scalars(
                select(Media)
                .where(Media.workspace_id == ctx.workspace.id)
                .order_by(desc(Media.created_at))
            )
        )
    cards = []
    for item in items:
        preview = (
            Div(
                NotStr(
                    f'<img src="{media_storage().url(item.storage_key)}" alt="{item.alt_text or item.filename}">'
                ),
                cls="media-preview",
            )
            if item.mime_type.startswith("image/")
            else Div("▶ Video", cls="media-preview")
        )
        cards.append(
            Div(
                preview,
                Div(
                    Span(item.filename),
                    Small(f"{item.mime_type} · {item.size_bytes / 1024 / 1024:.1f} MB"),
                    cls="media-meta",
                ),
                cls="media-card",
            )
        )
    uploader = Form(
        csrf_input(sess),
        Div(
            H2("Upload images or video"),
            P(
                "Images are validated in Python before storage. Production media is stored privately in Cloudflare R2."
            ),
            Input(
                type="file",
                name="files",
                multiple=True,
                accept="image/*,video/mp4,video/quicktime",
                required=True,
            ),
            cls="dropzone",
        ),
        Button("Upload media", type="submit", cls="btn primary"),
        method="post",
        action="/media",
        enctype="multipart/form-data",
        style="display:grid;gap:12px;margin-bottom:22px",
    )
    return _app_page(
        ctx,
        "Media",
        "/media",
        page_intro(
            "LIBRARY",
            "Reusable media, kept private.",
            "Upload once, attach to multiple drafts, and deliver signed links to publishing workers.",
        ),
        flash("Media uploaded." if saved else ""),
        flash(error, "error"),
        uploader,
        (
            Div(*cards, cls="media-grid")
            if cards
            else empty_state(
                "▧",
                "No media yet",
                "Upload images or videos to make them available in the composer.",
            )
        ),
    )


@rt("/media/file/{key:path}")
def local_media_file(key: str, sess):
    ctx = _context(sess)
    storage = media_storage()
    if not ctx or not isinstance(storage, LocalStorage):
        return Response("Not found", status_code=404)
    with session_scope() as session:
        item = session.scalar(
            select(Media).where(Media.workspace_id == ctx.workspace.id, Media.storage_key == key)
        )
        if not item:
            return Response("Not found", status_code=404)
        return Response(
            storage.get(key),
            media_type=item.mime_type,
            headers={"Cache-Control": "private, max-age=3600"},
        )


def _analytics_svg(data: dict) -> str:
    width, height, pad = 920, 270, 38
    series = [data.get("impressions", []), data.get("engagements", [])]
    maximum = max([value for values in series for value in values] or [1])
    count = max(len(data.get("labels", [])), 1)

    def points(values: list[int]) -> str:
        result = []
        for index, value in enumerate(values):
            x = pad + (index * (width - 2 * pad) / max(count - 1, 1))
            y = height - pad - (value / maximum * (height - 2 * pad))
            result.append(f"{x:.1f},{y:.1f}")
        return " ".join(result)

    grid = "".join(
        f'<line x1="{pad}" y1="{pad + row * 48}" x2="{width - pad}" y2="{pad + row * 48}" stroke="#e4e9e6" stroke-width="1"/>'
        for row in range(5)
    )
    labels = data.get("labels", [])
    first_label = labels[0] if labels else ""
    last_label = labels[-1] if labels else ""
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Impressions and engagements over time" xmlns="http://www.w3.org/2000/svg">'
        f"{grid}"
        f'<polyline points="{points(series[0])}" fill="none" stroke="#4f8f73" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<polyline points="{points(series[1])}" fill="none" stroke="#d49035" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<text x="{pad}" y="{height - 8}" fill="#68746f" font-size="11">{first_label}</text>'
        f'<text x="{width - pad}" y="{height - 8}" fill="#68746f" font-size="11" text-anchor="end">{last_label}</text>'
        '<circle cx="650" cy="18" r="5" fill="#4f8f73"/><text x="660" y="22" fill="#68746f" font-size="11">Impressions</text>'
        '<circle cx="770" cy="18" r="5" fill="#d49035"/><text x="780" y="22" fill="#68746f" font-size="11">Engagements</text>'
        "</svg>"
    )


@rt("/analytics")
def analytics_page(sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    with session_scope() as session:
        rows = session.execute(
            select(
                func.date(PostMetric.collected_at).label("day"),
                func.sum(PostMetric.impressions).label("impressions"),
                func.sum(PostMetric.likes + PostMetric.comments + PostMetric.shares).label(
                    "engagements"
                ),
            )
            .join(PostTarget, PostMetric.post_target_id == PostTarget.id)
            .join(Post, PostTarget.post_id == Post.id)
            .where(Post.workspace_id == ctx.workspace.id)
            .group_by(func.date(PostMetric.collected_at))
            .order_by(func.date(PostMetric.collected_at))
            .limit(90)
        ).all()
        totals = {
            "impressions": sum(int(row.impressions or 0) for row in rows),
            "engagements": sum(int(row.engagements or 0) for row in rows),
        }
        account_rows = list(
            session.execute(
                select(AccountMetricDaily, SocialAccount)
                .join(SocialAccount, AccountMetricDaily.social_account_id == SocialAccount.id)
                .where(SocialAccount.workspace_id == ctx.workspace.id)
                .order_by(desc(AccountMetricDaily.metric_date))
                .limit(30)
            )
        )
    chart_data = {
        "labels": [str(row.day) for row in rows],
        "impressions": [int(row.impressions or 0) for row in rows],
        "engagements": [int(row.engagements or 0) for row in rows],
    }
    account_table = [
        Tr(
            Td(platform_pill(account.platform, account.display_name or account.username)),
            Td(metric.metric_date.isoformat()),
            Td(f"{metric.followers:,}"),
            Td(f"{metric.impressions:,}"),
            Td(f"{metric.engagement:,}"),
        )
        for metric, account in account_rows
    ]
    chart = Div(
        Div(
            H2("Performance over time"),
            A("Export CSV", href="/analytics/export.csv", cls="btn small"),
            cls="card-head",
        ),
        Div(NotStr(_analytics_svg(chart_data)), cls="card-body chart-wrap"),
        cls="card",
    )
    table = (
        Div(
            Div(H2("Account snapshots"), cls="card-head"),
            Div(
                Table(
                    Thead(
                        Tr(
                            Th("Account"),
                            Th("Date"),
                            Th("Followers"),
                            Th("Impressions"),
                            Th("Engagement"),
                        )
                    ),
                    Tbody(*account_table),
                ),
                cls="table-wrap",
            ),
            cls="card",
        )
        if account_table
        else ""
    )
    return _app_page(
        ctx,
        "Analytics",
        "/analytics",
        page_intro(
            "MEASURE",
            "Signals that help you improve.",
            "Normalized metrics retain the raw provider response, so new fields can be added without losing history.",
        ),
        Div(
            stat_card("Impressions", f"{totals['impressions']:,}"),
            stat_card("Engagements", f"{totals['engagements']:,}"),
            stat_card(
                "Engagement rate",
                f"{(totals['engagements'] / totals['impressions'] * 100):.1f}%"
                if totals["impressions"]
                else "—",
            ),
            stat_card("Snapshots", len(rows)),
            cls="stats-grid",
        ),
        (
            Div(chart, table, style="display:grid;gap:18px")
            if rows
            else empty_state(
                "⌁",
                "Analytics will appear here",
                "Publish through a connected or mock account, then run the metrics collector.",
            )
        ),
    )


@rt("/analytics/export.csv")
def analytics_export(sess):
    ctx = _context(sess)
    if not ctx:
        return Response("Unauthorized", status_code=401)
    with session_scope() as session:
        rows = session.execute(
            select(PostMetric, PostTarget, SocialAccount, Post)
            .join(PostTarget, PostMetric.post_target_id == PostTarget.id)
            .join(SocialAccount, PostTarget.social_account_id == SocialAccount.id)
            .join(Post, PostTarget.post_id == Post.id)
            .where(Post.workspace_id == ctx.workspace.id)
            .order_by(desc(PostMetric.collected_at))
        ).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "collected_at",
            "platform",
            "account",
            "post_id",
            "impressions",
            "reach",
            "likes",
            "comments",
            "shares",
            "clicks",
            "saves",
        ]
    )
    for metric, target, account, _post in rows:
        writer.writerow(
            [
                metric.collected_at.isoformat(),
                account.platform,
                account.username,
                target.platform_post_id,
                metric.impressions,
                metric.reach,
                metric.likes,
                metric.comments,
                metric.shares,
                metric.clicks,
                metric.saves,
            ]
        )
    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=fastsocial-analytics.csv"},
    )


@rt("/approvals")
def approvals_page(sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    with session_scope() as session:
        posts = list(
            session.scalars(
                _post_query(ctx.workspace.id).where(Post.status == PostStatus.pending_approval)
            )
        )
    content = (
        empty_state(
            "✓",
            "No posts waiting for approval",
            "Personal workspaces publish without approval. Turn approvals on when you add a team.",
        )
        if not posts
        else Div(
            *[
                Div(
                    Div(
                        P(post.content.get("text", "")),
                        Small(_format_datetime(post.scheduled_at, ctx.workspace.timezone)),
                        cls="post-copy",
                    ),
                    A("Review", href=f"/posts/{post.id}", cls="btn small"),
                    cls="post-row",
                )
                for post in posts
            ],
            cls="card post-list",
        )
    )
    return _app_page(
        ctx,
        "Approvals",
        "/approvals",
        page_intro(
            "REVIEW",
            "Keep team publishing intentional.",
            "Owners and admins can approve or reject submitted posts. Your personal workspace currently bypasses this step.",
        ),
        content,
    )


@rt("/team")
async def team_page(request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    message = error = ""
    if request.method == "POST":
        form = await request.form()
        if not verify_csrf(sess, form.get("csrf")):
            error = "Your session expired."
        elif ctx.membership.role not in {WorkspaceRole.owner, WorkspaceRole.admin}:
            error = "Only workspace owners and admins can add members."
        else:
            email = str(form.get("email") or "").strip().lower()
            try:
                role = WorkspaceRole(str(form.get("role") or "viewer"))
                if role == WorkspaceRole.owner:
                    raise ValueError("Ownership transfer is handled separately.")
                if "@" not in email:
                    raise ValueError("Enter a valid email address.")
                with session_scope() as session:
                    user = get_or_create_user(session, email)
                    existing = membership_for(session, ctx.workspace.id, user.id)
                    if existing:
                        existing.role = role
                        action = "member.role_updated"
                    else:
                        session.add(
                            WorkspaceMember(
                                workspace_id=ctx.workspace.id, user_id=user.id, role=role
                            )
                        )
                        action = "member.added"
                    audit(
                        session,
                        ctx.workspace.id,
                        ctx.user.id,
                        action,
                        user,
                        {"role": role.value},
                    )
                message = "Member access saved."
            except ValueError as exc:
                error = str(exc)
    with session_scope() as session:
        members = list(
            session.execute(
                select(WorkspaceMember, User)
                .join(User)
                .where(WorkspaceMember.workspace_id == ctx.workspace.id)
            )
        )
    rows = [
        Tr(
            Td(user.name or "—"),
            Td(user.email),
            Td(member.role.value.title()),
            Td(_format_datetime(member.created_at, ctx.workspace.timezone)),
        )
        for member, user in members
    ]
    member_form = ""
    if ctx.membership.role in {WorkspaceRole.owner, WorkspaceRole.admin}:
        member_form = Form(
            csrf_input(sess),
            Div(
                Label("Member email"), Input(type="email", name="email", required=True), cls="field"
            ),
            Div(
                Label("Role"),
                Select(
                    Option("Admin", value="admin"),
                    Option("Editor", value="editor", selected=True),
                    Option("Viewer", value="viewer"),
                    name="role",
                ),
                cls="field",
            ),
            Button("Add or update member", type="submit", cls="btn primary"),
            method="post",
            action="/team",
            cls="form-card",
        )
    return _app_page(
        ctx,
        "Team",
        "/team",
        page_intro(
            "MEMBERS",
            "Personal today. Company-ready tomorrow.",
            "Roles and workspace isolation are already active; invitations can be enabled with transactional email at deployment.",
        ),
        flash(message),
        flash(error, "error"),
        Div(
            Div(
                Table(
                    Thead(Tr(Th("Name"), Th("Email"), Th("Role"), Th("Joined"))),
                    Tbody(*rows),
                ),
                cls="card table-wrap",
            ),
            member_form,
            cls="content-grid",
        ),
    )


@rt("/settings")
async def settings_page(request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    message = error = ""
    if request.method == "POST":
        form = await request.form()
        if not verify_csrf(sess, form.get("csrf")):
            error = "Your session expired."
        else:
            timezone_name = str(form.get("timezone") or "Europe/Tallinn")
            try:
                ZoneInfo(timezone_name)
                with session_scope() as session:
                    user = session.get(User, ctx.user.id)
                    workspace = session.get(Workspace, ctx.workspace.id)
                    user.name = str(form.get("name") or "").strip()
                    workspace.name = str(form.get("workspace_name") or "").strip() or workspace.name
                    workspace.timezone = timezone_name
                    if ctx.membership.role in {WorkspaceRole.owner, WorkspaceRole.admin}:
                        workspace.approval_required = form.get("approval_required") == "on"
                        workspace.default_workflow_mode = WorkflowMode(
                            str(form.get("default_workflow_mode") or "review")
                        )
                        provider = str(form.get("default_model_provider") or "xai").lower()
                        if provider not in {"xai", "openai"}:
                            raise ValueError("Unsupported model provider")
                        workspace.default_model_provider = provider
                    audit(session, workspace.id, user.id, "settings.updated", workspace)
                message = "Settings saved."
                ctx = _context(sess)
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
    form = Form(
        csrf_input(sess),
        Div(Label("Your name"), Input(type="text", name="name", value=ctx.user.name), cls="field"),
        Div(
            Label("Workspace name"),
            Input(type="text", name="workspace_name", value=ctx.workspace.name),
            cls="field",
        ),
        Div(
            Label("Timezone"),
            Input(type="text", name="timezone", value=ctx.workspace.timezone),
            Small("Use an IANA timezone such as Europe/Tallinn."),
            cls="field",
        ),
        Label(
            Input(
                type="checkbox", name="approval_required", checked=ctx.workspace.approval_required
            ),
            " Require approval before team posts are scheduled",
            cls="account-option",
        ),
        Div(
            Label("Default creation workflow"),
            Select(
                Option(
                    "Review — human confirmation",
                    value="review",
                    selected=ctx.workspace.default_workflow_mode == WorkflowMode.review,
                ),
                Option(
                    "YOLO — autonomous delivery",
                    value="yolo",
                    selected=ctx.workspace.default_workflow_mode == WorkflowMode.yolo,
                ),
                name="default_workflow_mode",
            ),
            Small("Every New Post can still override this default."),
            cls="field",
        ),
        Div(
            Label("Default model provider"),
            Select(
                Option("xAI", value="xai", selected=ctx.workspace.default_model_provider == "xai"),
                Option(
                    "OpenAI",
                    value="openai",
                    selected=ctx.workspace.default_model_provider == "openai",
                ),
                name="default_model_provider",
            ),
            Small("Configure keys and per-purpose model IDs in Integrations."),
            cls="field",
        ),
        Button("Save settings", type="submit", cls="btn primary"),
        method="post",
        action="/settings",
        cls="form-card",
    )
    signout = Form(
        csrf_input(sess),
        Button("Sign out", type="submit", cls="btn danger"),
        method="post",
        action="/auth/logout",
    )
    return _app_page(
        ctx,
        "Settings",
        "/settings",
        page_intro(
            "CONFIGURE",
            "Workspace and account settings.",
            "Personal publishing stays approval-free by default. Enable review when the workspace becomes collaborative.",
        ),
        flash(message),
        flash(error, "error"),
        Div(
            form,
            Div(
                Div(H2("Session"), cls="card-head"),
                Div(P(ctx.user.email), signout, cls="card-body"),
                cls="card",
            ),
            cls="content-grid",
        ),
    )
