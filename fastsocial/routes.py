from __future__ import annotations

import base64
import calendar as calendar_module
import csv
import hashlib
import io
import secrets
import uuid
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote_plus, urlencode, urlparse
from zoneinfo import ZoneInfo

import httpx
from fasthtml.common import (
    H1,
    H2,
    H3,
    A,
    Aside,
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
    Section,
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
    AdCampaignDaily,
    AgentEvent,
    AIProviderCredential,
    ApprovalStatus,
    ArtifactStatus,
    AuditLog,
    AutolistItem,
    ChatMessage,
    ChatRole,
    ChatSession,
    CompetitorMetricDaily,
    CompetitorProfile,
    ConnectionProvider,
    ContentArtifact,
    ContentAutolist,
    InboxConversation,
    Media,
    ModelProfile,
    Post,
    PostApproval,
    PostMetric,
    PostStatus,
    PostTarget,
    ReportRun,
    ReportSchedule,
    SavedReply,
    SkillDefinition,
    SkillVersionStatus,
    SmartLinkItem,
    SmartLinkPage,
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
from fastsocial.reporting import execute_report_schedule, render_report_html, report_summary
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
    next_autolist_run,
    process_due_autolists,
    publish_post,
    send_inbox_reply,
    slugify,
    store_media,
    validate_content,
    workspace_for_user,
)
from fastsocial.skills_service import publish_skill_version, skill_content
from fastsocial.storage import LocalStorage, media_storage


class PageContext:
    def __init__(
        self,
        user,
        workspace,
        membership,
        accounts,
        pending_approvals,
        chat_sessions,
        logout_csrf,
    ):
        self.user = user
        self.workspace = workspace
        self.membership = membership
        self.accounts = accounts
        self.pending_approvals = pending_approvals
        self.chat_sessions = chat_sessions
        self.logout_csrf = logout_csrf


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
        return PageContext(
            user,
            workspace,
            membership,
            accounts,
            pending,
            chat_sessions,
            csrf_token(sess),
        )


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
        logout_csrf=ctx.logout_csrf,
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
        (
            "Plan with intelligence",
            "Use month, week, and list planning views with best-time recommendations learned from performance.",
        ),
        (
            "Benchmark and report",
            "Compare competitor growth, build unified brand reports, and export clean performance data.",
        ),
        (
            "Convert every click",
            "Publish branded SmartLinks with live view and click measurement beside your social workflow.",
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
    suggestions = (
        (
            "Launch a product",
            "Create a launch post with a sharp hook, three concrete benefits, and a clear call to action.",
        ),
        (
            "Teach something useful",
            "Turn one useful lesson from my work into an educational post with a practical takeaway.",
        ),
        (
            "Share a point of view",
            "Develop a thoughtful contrarian point of view for my audience without sounding provocative for its own sake.",
        ),
        (
            "Build a content series",
            "Propose the first post in a five-part content series that builds authority with my target audience.",
        ),
    )
    composer = Form(
        csrf_input(sess),
        Div(
            Textarea(
                name="brief",
                placeholder="Describe the post you want to create…",
                autofocus=True,
                rows="3",
            ),
            Button("↑", type="submit", cls="chat-send", title="Create post"),
            cls="chat-composer-box",
        ),
        Div(
            *[
                Button(
                    Strong(label),
                    Small(prompt),
                    type="submit",
                    name="suggestion",
                    value=prompt,
                    cls="prompt-suggestion-card",
                )
                for label, prompt in suggestions
            ],
            cls="prompt-suggestions",
        ),
        Details(
            Summary("Creation controls"),
            Div(
                Div(
                    Span("Workflow", cls="composer-control-label"),
                    Label(
                        Input(
                            type="radio",
                            name="workflow_mode",
                            value="review",
                            checked=ctx.workspace.default_workflow_mode == WorkflowMode.review,
                        ),
                        "Review",
                        cls="composer-chip",
                    ),
                    Label(
                        Input(
                            type="radio",
                            name="workflow_mode",
                            value="yolo",
                            checked=ctx.workspace.default_workflow_mode == WorkflowMode.yolo,
                        ),
                        "YOLO",
                        cls="composer-chip warning",
                    ),
                    cls="composer-control-group",
                ),
                Div(
                    Span("Create with", cls="composer-control-label"),
                    Label(
                        Input(type="checkbox", name="media_kinds", value="image"),
                        "Image",
                        cls="composer-chip",
                    ),
                    Label(
                        Input(type="checkbox", name="media_kinds", value="video"),
                        "Video",
                        cls="composer-chip",
                    ),
                    cls="composer-control-group",
                ),
                Div(
                    Span("YOLO delivery", cls="composer-control-label"),
                    Select(
                        Option("Publish now", value="now", selected=True),
                        Option("Schedule", value="schedule"),
                        Option("Save as draft", value="draft"),
                        name="delivery",
                    ),
                    Input(type="datetime-local", name="scheduled_at"),
                    cls="composer-control-group delivery-controls",
                ),
                cls="composer-controls-grid",
            ),
            cls="composer-controls",
        ),
        method="post",
        action="/new-post",
        id="new-post-form",
        cls="creation-composer new-post-composer",
    )
    artifact_panel = Aside(
        Div(
            Div(Span("✦", cls="artifact-title-icon"), H2("Artifacts")),
            Span("Waiting", cls="artifact-status"),
            cls="creation-pane-head artifact-pane-head",
        ),
        Div(
            Div(
                Span("▧", cls="artifact-empty-icon"),
                H2("Your work will appear here"),
                P(
                    "Draft copy, platform variants, generated images, and videos stay beside the chat."
                ),
                cls="artifact-empty",
            ),
            Div(
                H3("Publish to"),
                Div(
                    *(
                        account_options
                        or [
                            P(
                                "No social accounts connected yet. You can still create and save a draft.",
                                cls="form-help",
                            )
                        ]
                    ),
                    cls="account-options artifact-account-options",
                ),
                Div(
                    Span("Model", cls="artifact-meta-label"),
                    Strong(ctx.workspace.default_model_provider.upper()),
                    Small(_model_gate_message(ctx, ctx.workspace.default_model_provider)),
                    A("Configure", href="/integrations#ai-models"),
                    cls="artifact-model-card",
                ),
                cls="artifact-setup",
            ),
            cls="creation-artifact-body",
        ),
        cls="creation-artifact-pane",
    )
    return Div(
        flash(error, "error"),
        Div(
            Section(
                Div(
                    Div(H2("New post"), Small("Agentic creation workspace")),
                    Span("READY", cls="mode-badge"),
                    cls="creation-pane-head",
                ),
                Div(
                    Div(
                        Span("✦", cls="creation-welcome-icon"),
                        H1("What should we create?"),
                        P(
                            "Chat with your marketing agents. Give them an idea, a goal, or a rough thought—they will generate, review, and prepare the post."
                        ),
                        cls="creation-welcome",
                    ),
                    cls="creation-message-scroll empty-conversation",
                ),
                composer,
                cls="creation-chat-pane",
            ),
            artifact_panel,
            cls="creation-workspace new-creation-workspace",
        ),
        cls="creation-page-shell",
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
    brief = str(form.get("suggestion") or form.get("brief") or "").strip()
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
    suggestions = (
        ("Sharper hook", "Rewrite this with a sharper opening hook and keep the claims factual."),
        ("More concise", "Make every platform variant more concise without losing the core idea."),
        ("New angle", "Create a fresh alternative angle for the same audience and goal."),
        (
            "Check tone",
            "Review the tone, evidence, and call to action, then improve anything weak.",
        ),
    )
    action_forms = []
    if artifact and not completed:
        action_forms.extend(
            [
                Form(
                    csrf_input(sess),
                    Input(type="hidden", name="kind", value="image"),
                    Input(type="hidden", name="prompt", value=content.get("image_prompt", "")),
                    Button("▧ Generate image", type="submit", cls="chat-action-chip"),
                    method="post",
                    action=f"/chats/{chat.id}/media",
                ),
                Form(
                    csrf_input(sess),
                    Input(type="hidden", name="kind", value="video"),
                    Input(type="hidden", name="prompt", value=content.get("video_prompt", "")),
                    Button("▶ Generate video", type="submit", cls="chat-action-chip"),
                    method="post",
                    action=f"/chats/{chat.id}/media",
                ),
            ]
        )
        if awaiting_approval:
            action_forms.append(
                Form(
                    csrf_input(sess),
                    Button("✓ Approve for posting", type="submit", cls="chat-action-chip primary"),
                    method="post",
                    action=f"/chats/{chat.id}/approve",
                )
            )
        else:
            action_forms.extend(
                [
                    Form(
                        csrf_input(sess),
                        Button(
                            "Save draft",
                            type="submit",
                            name="delivery",
                            value="draft",
                            cls="chat-action-chip",
                        ),
                        method="post",
                        action=f"/chats/{chat.id}/post",
                    ),
                    Form(
                        csrf_input(sess),
                        Input(
                            type="datetime-local",
                            name="scheduled_at",
                            value=str(state.get("scheduled_at") or ""),
                            aria_label="Schedule time",
                        ),
                        Button(
                            "Schedule",
                            type="submit",
                            name="delivery",
                            value="schedule",
                            cls="chat-action-chip",
                            disabled=True if not state.get("target_ids") else None,
                        ),
                        method="post",
                        action=f"/chats/{chat.id}/post",
                        cls="chat-schedule-action",
                    ),
                    Form(
                        csrf_input(sess),
                        Button(
                            "Publish now",
                            type="submit",
                            name="delivery",
                            value="now",
                            cls="chat-action-chip primary",
                            disabled=True if not state.get("target_ids") else None,
                        ),
                        method="post",
                        action=f"/chats/{chat.id}/post",
                    ),
                ]
            )
    chat_panel = Section(
        Div(
            Div(H2(chat.title), Small("Creation chat")),
            Span(chat.workflow_mode.value.upper(), cls=f"mode-badge {chat.workflow_mode.value}"),
            cls="creation-pane-head",
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
            cls="creation-message-scroll agent-chat-messages",
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
        (
            Div(
                Div(*action_forms, cls="chat-action-row") if action_forms else "",
                Form(
                    csrf_input(sess),
                    Div(
                        Textarea(
                            name="message",
                            placeholder="Ask the agents to refine, rethink, or repurpose this post…",
                            rows="3",
                        ),
                        Button("↑", type="submit", cls="chat-send", title="Send message"),
                        cls="chat-composer-box",
                    ),
                    Div(
                        *[
                            Button(
                                Strong(label),
                                type="submit",
                                name="suggestion",
                                value=prompt,
                                cls="prompt-suggestion-card compact",
                            )
                            for label, prompt in suggestions
                        ],
                        cls="prompt-suggestions",
                    ),
                    method="post",
                    action=f"/chats/{chat.id}/messages",
                    cls="creation-composer",
                ),
                cls="creation-composer-dock",
            )
            if not completed
            else Div(
                A("Open completed post →", href=f"/posts/{chat.post_id}", cls="btn primary"),
                cls="creation-complete-dock",
            )
        ),
        cls="creation-chat-pane",
    )
    if artifact:
        review = content.get("review") if isinstance(content.get("review"), dict) else {}
        artifact_panel = Aside(
            Div(
                Div(
                    Span("✦", cls="artifact-title-icon"),
                    Div(H2("Artifacts"), Small(f"{artifact.provider}:{artifact.model_name}")),
                ),
                Span(artifact.status.value.upper(), cls="artifact-status"),
                cls="creation-pane-head artifact-pane-head",
            ),
            Div(
                Div(
                    Span("MASTER POST", cls="artifact-meta-label"),
                    P(str(content.get("text") or ""), cls="artifact-copy"),
                    cls="artifact-card primary-artifact",
                ),
                *[
                    Div(
                        Div(
                            Span(
                                PLATFORM_MARKS.get(platform, "?"),
                                cls=f"platform-mark {platform}",
                            ),
                            Strong(f"{PLATFORM_NAMES.get(platform, platform.title())} variant"),
                            cls="artifact-card-title",
                        ),
                        P(str(value), cls="artifact-copy variant-copy"),
                        cls="artifact-card",
                    )
                    for platform, value in variants.items()
                ],
                Div(
                    Div(
                        Span("✓", cls="review-check"),
                        H3("Editorial review"),
                        cls="artifact-card-title",
                    ),
                    P(str(review.get("summary") or "Ready for review.")),
                    Ul(*[Li(str(item)) for item in review.get("risks", [])])
                    if review.get("risks")
                    else "",
                    cls="artifact-card review-box",
                ),
                Div(
                    H3("Generated media"),
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
                    ),
                    cls="artifact-media-section",
                )
                if media_items
                else Div(
                    Span("▧", cls="artifact-empty-icon small"),
                    P("Generated images and videos will appear here."),
                    cls="artifact-media-empty",
                ),
                cls="creation-artifact-body",
            ),
            cls="creation-artifact-pane",
        )
    else:
        artifact_panel = Aside(
            Div(
                Div(Span("✦", cls="artifact-title-icon"), H2("Artifacts")), cls="creation-pane-head"
            ),
            Div(
                Span("▧", cls="artifact-empty-icon"),
                H2("No artifact yet"),
                P("Keep chatting and the generated work will appear here."),
                cls="artifact-empty",
            ),
            cls="creation-artifact-pane",
        )
    return Div(
        flash(message),
        flash(error, "error"),
        Div(chat_panel, artifact_panel, cls="creation-workspace"),
        cls="creation-page-shell",
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
    value = str(form.get("suggestion") or form.get("message") or "").strip()
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
            if form.get("text") is not None:
                content["text"] = str(form.get("text") or "").strip()
            variants = dict(content.get("variants") or {})
            for platform in list(variants):
                submitted = form.get(f"variant_{platform}")
                if submitted is not None:
                    variants[platform] = str(submitted).strip()
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
        delivery_label = {
            "draft": "I saved the approved content as a draft.",
            "schedule": "I scheduled the approved post for delivery.",
            "now": "I handed the approved post to the publishing pipeline.",
        }.get(delivery, "I completed the post delivery step.")
        session.add(
            ChatMessage(
                chat_session_id=chat.id,
                role=ChatRole.assistant,
                agent_slug="publisher",
                content=delivery_label,
            )
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
    if form.get("text") is not None:
        content["text"] = str(form.get("text") or "").strip()
    variants = dict(content.get("variants") or {})
    for platform in list(variants):
        submitted = form.get(f"variant_{platform}")
        if submitted is not None:
            variants[platform] = str(submitted).strip()
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
            session.add(
                ChatMessage(
                    chat_session_id=row.id,
                    role=ChatRole.assistant,
                    agent_slug="editorial-review",
                    content="Approved. The artifact is locked and ready to save, schedule, or publish.",
                )
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


def _best_time_slots(workspace_id: uuid.UUID, timezone: str) -> list[dict]:
    latest = (
        select(
            PostMetric.post_target_id.label("target_id"),
            func.max(PostMetric.collected_at).label("latest_at"),
        )
        .group_by(PostMetric.post_target_id)
        .subquery()
    )
    with session_scope() as session:
        rows = session.execute(
            select(Post.published_at, SocialAccount.platform, PostMetric)
            .join(PostTarget, PostTarget.post_id == Post.id)
            .join(SocialAccount, SocialAccount.id == PostTarget.social_account_id)
            .join(latest, latest.c.target_id == PostTarget.id)
            .join(
                PostMetric,
                (PostMetric.post_target_id == latest.c.target_id)
                & (PostMetric.collected_at == latest.c.latest_at),
            )
            .where(Post.workspace_id == workspace_id, Post.published_at.is_not(None))
        ).all()
    scored: dict[tuple[str, int, int], list[int]] = {}
    zone = ZoneInfo(timezone)
    for published_at, platform, metric in rows:
        value = published_at.replace(tzinfo=UTC) if not published_at.tzinfo else published_at
        local = value.astimezone(zone)
        score = int(
            metric.likes
            + metric.comments * 2
            + metric.shares * 3
            + metric.clicks * 2
            + metric.saves * 2
        )
        scored.setdefault((platform, local.weekday(), local.hour), []).append(score)
    if scored:
        ranked = sorted(
            (
                {
                    "platform": platform,
                    "weekday": weekday,
                    "hour": hour,
                    "score": round(sum(values) / len(values)),
                    "samples": len(values),
                    "source": "Your performance",
                }
                for (platform, weekday, hour), values in scored.items()
            ),
            key=lambda item: (item["score"], item["samples"]),
            reverse=True,
        )
        return ranked[:5]
    return [
        {
            "platform": "linkedin",
            "weekday": 1,
            "hour": 9,
            "score": 0,
            "samples": 0,
            "source": "Starter benchmark",
        },
        {
            "platform": "x",
            "weekday": 2,
            "hour": 12,
            "score": 0,
            "samples": 0,
            "source": "Starter benchmark",
        },
        {
            "platform": "bluesky",
            "weekday": 3,
            "hour": 17,
            "score": 0,
            "samples": 0,
            "source": "Starter benchmark",
        },
    ]


@rt("/calendar")
def calendar_page(sess, year: int = 0, month: int = 0, view: str = "month", focus: str = ""):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    today = date.today()
    view = view if view in {"month", "week", "list"} else "month"
    try:
        focus_date = date.fromisoformat(focus) if focus else today
    except ValueError:
        focus_date = today
    year = year or today.year
    month = month or today.month
    first = date(year, month, 1)
    last = date(year, month, calendar_module.monthrange(year, month)[1])
    if view == "week":
        range_start = focus_date - timedelta(days=focus_date.weekday())
        range_end = range_start + timedelta(days=6)
    elif view == "list":
        range_start = today - timedelta(days=7)
        range_end = today + timedelta(days=90)
    else:
        range_start, range_end = first, last
    start_utc = datetime.combine(
        range_start, datetime.min.time(), tzinfo=ZoneInfo(ctx.workspace.timezone)
    ).astimezone(UTC)
    end_utc = datetime.combine(
        range_end, datetime.max.time(), tzinfo=ZoneInfo(ctx.workspace.timezone)
    ).astimezone(UTC)
    with session_scope() as session:
        posts = list(
            session.scalars(
                select(Post)
                .where(
                    Post.workspace_id == ctx.workspace.id,
                    Post.scheduled_at >= start_utc,
                    Post.scheduled_at <= end_utc,
                )
                .options(selectinload(Post.targets).selectinload(PostTarget.social_account))
                .order_by(Post.scheduled_at)
            )
        )
    by_date: dict[date, list[Post]] = {}
    for post in posts:
        value = post.scheduled_at
        if value and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        if value:
            by_date.setdefault(
                value.astimezone(ZoneInfo(ctx.workspace.timezone)).date(), []
            ).append(post)
    prev_month = first.replace(day=1) - __import__("datetime").timedelta(days=1)
    next_month = last + __import__("datetime").timedelta(days=1)
    if view == "month":
        day_cells = []
        for week in calendar_module.Calendar(firstweekday=0).monthdatescalendar(year, month):
            for day in week:
                day_cells.append(
                    Div(
                        Span(str(day.day), cls="calendar-day-number"),
                        *[
                            A(
                                Div(
                                    *[
                                        Span(
                                            PLATFORM_MARKS.get(target.social_account.platform, "?"),
                                            cls=f"platform-mark {target.social_account.platform}",
                                        )
                                        for target in item.targets[:2]
                                    ],
                                    Span((item.content.get("text", "") or "Untitled")[:34]),
                                ),
                                href=f"/posts/{item.id}",
                                cls="calendar-post",
                                draggable="true",
                                data_post_id=str(item.id),
                            )
                            for item in by_date.get(day, [])
                        ],
                        cls=f"calendar-day{' muted' if day.month != month else ''}",
                        data_date=day.isoformat(),
                    )
                )
        planner_view = Div(
            Div(
                *[Div(day) for day in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")],
                cls="calendar-head",
            ),
            Div(*day_cells, cls="calendar-grid"),
            cls="calendar",
        )
        controls = Div(
            A(
                "←",
                href=f"/calendar?year={prev_month.year}&month={prev_month.month}",
                cls="btn",
            ),
            A("Today", href="/calendar", cls="btn"),
            A(
                "→",
                href=f"/calendar?year={next_month.year}&month={next_month.month}",
                cls="btn",
            ),
            cls="form-actions",
        )
        heading = first.strftime("%B %Y")
    elif view == "week":
        days = [range_start + timedelta(days=index) for index in range(7)]
        planner_view = Div(
            *[
                Div(
                    Div(Strong(day.strftime("%a")), Span(day.strftime("%d %b"))),
                    *[
                        A(
                            Small(
                                _format_datetime(item.scheduled_at, ctx.workspace.timezone).split(
                                    " ", 1
                                )[-1]
                            ),
                            P((item.content.get("text", "") or "Untitled")[:70]),
                            href=f"/posts/{item.id}",
                            cls="week-post",
                            draggable="true",
                            data_post_id=str(item.id),
                        )
                        for item in by_date.get(day, [])
                    ],
                    P("No posts", cls="week-empty") if not by_date.get(day) else "",
                    cls=f"week-day{' today' if day == today else ''}",
                    data_date=day.isoformat(),
                )
                for day in days
            ],
            cls="week-planner",
        )
        controls = Div(
            A(
                "←",
                href=f"/calendar?view=week&focus={range_start - timedelta(days=7)}",
                cls="btn",
            ),
            A("This week", href="/calendar?view=week", cls="btn"),
            A(
                "→",
                href=f"/calendar?view=week&focus={range_start + timedelta(days=7)}",
                cls="btn",
            ),
            cls="form-actions",
        )
        heading = f"{range_start.strftime('%d %b')} – {range_end.strftime('%d %b %Y')}"
    else:
        planner_view = (
            Div(
                *[
                    A(
                        Div(
                            Strong(_format_datetime(item.scheduled_at, ctx.workspace.timezone)),
                            Small(item.status.value.replace("_", " ").title()),
                        ),
                        P((item.content.get("text", "") or "Untitled")[:180]),
                        Div(
                            *[
                                platform_pill(
                                    target.social_account.platform,
                                    target.social_account.display_name
                                    or target.social_account.username,
                                )
                                for target in item.targets
                            ]
                        ),
                        href=f"/posts/{item.id}",
                        cls="planner-list-row",
                    )
                    for item in posts
                ],
                cls="card planner-list",
            )
            if posts
            else empty_state("□", "The plan is clear", "No posts in the next 90 days.")
        )
        controls = A("New Post", href="/new-post", cls="btn primary")
        heading = "Upcoming plan"
    best_slots = _best_time_slots(ctx.workspace.id, ctx.workspace.timezone)
    weekday_names = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
    best_time_panel = Div(
        Div(
            Div(H2("Best times to publish"), Small("Learns from your latest post metrics")),
            Span("LIVE" if best_slots[0]["samples"] else "STARTER", cls="mode-badge"),
            cls="card-head",
        ),
        Div(
            *[
                Div(
                    Span(
                        PLATFORM_MARKS.get(slot["platform"], slot["platform"][:1].upper()),
                        cls=f"platform-mark {slot['platform']}",
                    ),
                    Div(
                        Strong(f"{weekday_names[slot['weekday']]} · {slot['hour']:02d}:00"),
                        Small(
                            f"{slot['source']}"
                            + (f" · {slot['samples']} posts" if slot["samples"] else "")
                        ),
                    ),
                    cls="best-time-row",
                )
                for slot in best_slots
            ],
            cls="best-times-list",
        ),
        cls="card best-times-card",
    )
    view_tabs = Div(
        A(
            "Month",
            href=f"/calendar?year={year}&month={month}",
            cls=f"btn small{' primary' if view == 'month' else ''}",
        ),
        A(
            "Week",
            href=f"/calendar?view=week&focus={focus_date}",
            cls=f"btn small{' primary' if view == 'week' else ''}",
        ),
        A(
            "List",
            href="/calendar?view=list",
            cls=f"btn small{' primary' if view == 'list' else ''}",
        ),
        cls="planner-view-tabs",
    )
    return _app_page(
        ctx,
        "Planner",
        "/calendar",
        page_intro(
            "PLAN & PUBLISH",
            heading,
            "Visual scheduling, best-time guidance, and every destination in workspace time.",
            controls,
        ),
        view_tabs,
        Div(
            planner_view,
            best_time_panel,
            cls="planner-layout planner-drop-root",
            data_csrf=csrf_token(sess),
        ),
        Script(src="/static/planner.js", defer=True),
    )


@rt("/api/planner/reschedule", methods=["POST"])
async def planner_reschedule(request, sess):
    ctx = _context(sess)
    if not ctx:
        return Response("Unauthorized", status_code=401)
    if ctx.membership.role == WorkspaceRole.viewer:
        return Response("Forbidden", status_code=403)
    form = await request.form()
    if not verify_csrf(sess, form.get("csrf")):
        return Response("Forbidden", status_code=403)
    try:
        post_id = uuid.UUID(str(form.get("post_id") or ""))
        target_date = date.fromisoformat(str(form.get("target_date") or ""))
    except ValueError:
        return Response("Invalid post or date", status_code=400)
    with session_scope() as session:
        post = session.scalar(
            select(Post)
            .where(Post.id == post_id, Post.workspace_id == ctx.workspace.id)
            .options(selectinload(Post.targets))
        )
        if not post:
            return Response("Not found", status_code=404)
        if post.status in {PostStatus.published, PostStatus.cancelled}:
            return Response("Post cannot be rescheduled", status_code=409)
        zone = ZoneInfo(ctx.workspace.timezone)
        current = post.scheduled_at or utcnow()
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        local = current.astimezone(zone)
        post.scheduled_at = local.replace(
            year=target_date.year, month=target_date.month, day=target_date.day
        ).astimezone(UTC)
        post.status = PostStatus.scheduled if post.targets else PostStatus.draft
        for target in post.targets:
            if target.status != TargetStatus.published:
                target.status = TargetStatus.pending
                target.next_retry_at = None
        audit(
            session,
            ctx.workspace.id,
            ctx.user.id,
            "post.rescheduled.drag",
            post,
            {"target_date": target_date.isoformat()},
        )
    return Response(status_code=204, headers={"HX-Refresh": "true"})


@rt("/autolists", methods=["GET"])
def autolists_page(sess, saved: str = "", error: str = ""):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    with session_scope() as session:
        lists = list(
            session.scalars(
                select(ContentAutolist)
                .where(ContentAutolist.workspace_id == ctx.workspace.id)
                .options(selectinload(ContentAutolist.items))
                .order_by(desc(ContentAutolist.created_at))
            )
        )
    cards = []
    for autolist in lists:
        target_names = [
            account.display_name or account.username
            for account in ctx.accounts
            if str(account.id) in autolist.target_ids
        ]
        item_rows = [
            Div(
                Div(Strong(f"#{index + 1}"), P(item.text)),
                Small(f"Used {item.used_count}×"),
                cls="autolist-item",
            )
            for index, item in enumerate(autolist.items)
        ]
        cards.append(
            Div(
                Div(
                    Div(
                        H2(autolist.name),
                        Small(
                            f"{autolist.cadence.title()} at {autolist.publish_time} · "
                            + (", ".join(target_names) or "No destinations")
                        ),
                    ),
                    Span("ACTIVE" if autolist.active else "PAUSED", cls="mode-badge"),
                    cls="card-head",
                ),
                Div(*item_rows, cls="autolist-items")
                if item_rows
                else P(
                    "Add evergreen posts below to activate this list.", cls="card-body form-help"
                ),
                Div(
                    Form(
                        csrf_input(sess),
                        Textarea(
                            name="text",
                            placeholder="Write an evergreen post to rotate…",
                            required=True,
                            rows="3",
                        ),
                        Button("Add content", type="submit", cls="btn primary small"),
                        method="post",
                        action=f"/autolists/{autolist.id}/items",
                    ),
                    Div(
                        Form(
                            csrf_input(sess),
                            Button(
                                "Pause" if autolist.active else "Resume",
                                type="submit",
                                cls="btn small",
                            ),
                            method="post",
                            action=f"/autolists/{autolist.id}/toggle",
                        ),
                        Form(
                            csrf_input(sess),
                            Button("Run now", type="submit", cls="btn small"),
                            method="post",
                            action=f"/autolists/{autolist.id}/run",
                        ),
                        cls="form-actions",
                    ),
                    cls="autolist-controls",
                ),
                Small(
                    f"Next run: {_format_datetime(autolist.next_run_at, ctx.workspace.timezone)}",
                    cls="autolist-next",
                ),
                cls="card autolist-card",
            )
        )
    create_form = Form(
        csrf_input(sess),
        Div(
            Div(
                Label("List name"),
                Input(name="name", placeholder="Evergreen insights", required=True),
                cls="field",
            ),
            Div(
                Label("Cadence"),
                Select(
                    Option("Daily", value="daily"),
                    Option("Weekly", value="weekly", selected=True),
                    Option("Monthly", value="monthly"),
                    name="cadence",
                ),
                cls="field",
            ),
            Div(
                Label("Publish time"),
                Input(type="time", name="publish_time", value="09:00", required=True),
                cls="field",
            ),
            cls="autolist-create-grid",
        ),
        Div(
            Label("Destinations"),
            Div(
                *[
                    Label(
                        Input(type="checkbox", name="target_ids", value=str(account.id)),
                        f" {account.display_name or account.username} ({PLATFORM_NAMES.get(account.platform, account.platform.title())})",
                        cls="check-row",
                    )
                    for account in ctx.accounts
                ],
                cls="target-checks",
            ),
            cls="field",
        ),
        Button("Create autolist", type="submit", cls="btn primary"),
        method="post",
        action="/autolists",
        cls="card autolist-create",
    )
    return _app_page(
        ctx,
        "Autolists",
        "/autolists",
        flash("Autolist updated." if saved else ""),
        flash(error, "error"),
        page_intro(
            "EVERGREEN LIBRARY",
            "Keep proven content in motion.",
            "Rotate a reusable post library on a daily, weekly, or monthly cadence. Every generated post remains visible in Planner.",
            A("Open Planner", href="/calendar", cls="btn"),
        ),
        Div(*cards, cls="autolist-grid") if cards else "",
        Div(H2("Create an autolist"), cls="section-heading"),
        create_form,
    )


@rt("/autolists", methods=["POST"])
async def autolist_create(request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    if ctx.membership.role == WorkspaceRole.viewer:
        return Response("Forbidden", status_code=403)
    form = await request.form()
    if not verify_csrf(sess, form.get("csrf")):
        return Response("Forbidden", status_code=403)
    try:
        name = str(form.get("name") or "").strip()
        cadence = str(form.get("cadence") or "weekly")
        publish_time = str(form.get("publish_time") or "09:00")
        target_ids = [uuid.UUID(value) for value in form.getlist("target_ids")]
        valid_targets = {account.id for account in ctx.accounts}
        if not name or cadence not in {"daily", "weekly", "monthly"}:
            raise ValueError("Enter a name and valid cadence")
        if not target_ids or not set(target_ids).issubset(valid_targets):
            raise ValueError("Choose at least one connected destination")
        datetime.strptime(publish_time, "%H:%M")
        with session_scope() as session:
            autolist = ContentAutolist(
                workspace_id=ctx.workspace.id,
                name=name,
                cadence=cadence,
                publish_time=publish_time,
                timezone=ctx.workspace.timezone,
                target_ids=[str(value) for value in target_ids],
                next_run_at=next_autolist_run(
                    cadence=cadence,
                    publish_time=publish_time,
                    timezone=ctx.workspace.timezone,
                ),
                created_by=ctx.user.id,
            )
            session.add(autolist)
            session.flush()
            audit(session, ctx.workspace.id, ctx.user.id, "autolist.created", autolist)
        return RedirectResponse("/autolists?saved=1", status_code=303)
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(f"/autolists?error={quote_plus(str(exc))}", status_code=303)


def _workspace_autolist(session, autolist_id: str, workspace_id: uuid.UUID):
    try:
        parsed = uuid.UUID(autolist_id)
    except ValueError:
        return None
    return session.scalar(
        select(ContentAutolist)
        .where(ContentAutolist.id == parsed, ContentAutolist.workspace_id == workspace_id)
        .options(selectinload(ContentAutolist.items))
    )


@rt("/autolists/{autolist_id}/items", methods=["POST"])
async def autolist_item_add(autolist_id: str, request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    if ctx.membership.role == WorkspaceRole.viewer:
        return Response("Forbidden", status_code=403)
    form = await request.form()
    if not verify_csrf(sess, form.get("csrf")):
        return Response("Forbidden", status_code=403)
    text = str(form.get("text") or "").strip()
    if not text:
        return RedirectResponse("/autolists?error=Post+text+is+required", status_code=303)
    with session_scope() as session:
        autolist = _workspace_autolist(session, autolist_id, ctx.workspace.id)
        if not autolist:
            return Response("Not found", status_code=404)
        item = AutolistItem(autolist_id=autolist.id, text=text, position=len(autolist.items))
        session.add(item)
        audit(session, ctx.workspace.id, ctx.user.id, "autolist.item.created", item)
    return RedirectResponse("/autolists?saved=1", status_code=303)


@rt("/autolists/{autolist_id}/toggle", methods=["POST"])
async def autolist_toggle(autolist_id: str, request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    if ctx.membership.role == WorkspaceRole.viewer:
        return Response("Forbidden", status_code=403)
    form = await request.form()
    if not verify_csrf(sess, form.get("csrf")):
        return Response("Forbidden", status_code=403)
    with session_scope() as session:
        autolist = _workspace_autolist(session, autolist_id, ctx.workspace.id)
        if not autolist:
            return Response("Not found", status_code=404)
        autolist.active = not autolist.active
        if autolist.active and not autolist.next_run_at:
            autolist.next_run_at = next_autolist_run(
                cadence=autolist.cadence,
                publish_time=autolist.publish_time,
                timezone=autolist.timezone,
            )
        audit(session, ctx.workspace.id, ctx.user.id, "autolist.toggled", autolist)
    return RedirectResponse("/autolists?saved=1", status_code=303)


@rt("/autolists/{autolist_id}/run", methods=["POST"])
async def autolist_run_now(autolist_id: str, request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    if ctx.membership.role == WorkspaceRole.viewer:
        return Response("Forbidden", status_code=403)
    form = await request.form()
    if not verify_csrf(sess, form.get("csrf")):
        return Response("Forbidden", status_code=403)
    with session_scope() as session:
        autolist = _workspace_autolist(session, autolist_id, ctx.workspace.id)
        if not autolist:
            return Response("Not found", status_code=404)
        if not autolist.items:
            return RedirectResponse("/autolists?error=Add+content+before+running", status_code=303)
        autolist.active = True
        autolist.next_run_at = utcnow()
    await process_due_autolists()
    return RedirectResponse("/autolists?saved=1", status_code=303)


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


COMPETITOR_PLATFORMS = (
    "instagram",
    "facebook",
    "tiktok",
    "youtube",
    "linkedin",
    "x",
    "threads",
    "bluesky",
    "twitch",
    "pinterest",
)


@rt("/competitors", methods=["GET"])
def competitors_page(sess, saved: str = "", error: str = ""):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    with session_scope() as session:
        profiles = list(
            session.scalars(
                select(CompetitorProfile)
                .where(CompetitorProfile.workspace_id == ctx.workspace.id)
                .options(selectinload(CompetitorProfile.snapshots))
                .order_by(CompetitorProfile.platform, CompetitorProfile.display_name)
            )
        )
    cards = []
    for profile in profiles:
        snapshots = sorted(profile.snapshots, key=lambda item: item.metric_date, reverse=True)
        latest = snapshots[0] if snapshots else None
        oldest = snapshots[-1] if snapshots else None
        growth = (latest.followers - oldest.followers) if latest and oldest else 0
        cards.append(
            Div(
                Div(
                    Div(
                        Span(
                            PLATFORM_MARKS.get(profile.platform, profile.platform[:1].upper()),
                            cls=f"platform-mark {profile.platform}",
                        ),
                        Div(
                            H2(profile.display_name or profile.handle),
                            Small(f"{profile.platform.title()} · @{profile.handle.lstrip('@')}"),
                        ),
                        cls="competitor-title",
                    ),
                    Span("TRACKING" if profile.active else "PAUSED", cls="mode-badge"),
                    cls="integration-card-head",
                ),
                Div(
                    Div(Span("Followers"), Strong(f"{latest.followers:,}" if latest else "—")),
                    Div(Span("Growth"), Strong(f"{growth:+,}" if snapshots else "—")),
                    Div(
                        Span("Engagement rate"),
                        Strong(f"{latest.engagement_rate:.2f}%" if latest else "—"),
                    ),
                    cls="competitor-stats",
                ),
                P(
                    f"Last snapshot {latest.metric_date.isoformat()} · {len(snapshots)} data points"
                    if latest
                    else "Add the first snapshot now; official collectors can update the same history later.",
                    cls="form-help",
                ),
                Details(
                    Summary("Add or update snapshot"),
                    Form(
                        csrf_input(sess),
                        Input(
                            type="date",
                            name="metric_date",
                            value=date.today().isoformat(),
                            required=True,
                        ),
                        Input(type="number", name="followers", placeholder="Followers", min="0"),
                        Input(type="number", name="posts", placeholder="Posts", min="0"),
                        Input(
                            type="number",
                            name="engagement",
                            placeholder="Engagements",
                            min="0",
                        ),
                        Input(type="number", name="reach", placeholder="Reach", min="0"),
                        Input(
                            type="number",
                            name="engagement_rate",
                            placeholder="Engagement rate %",
                            min="0",
                            step="0.01",
                        ),
                        Button("Save snapshot", type="submit", cls="btn primary small"),
                        method="post",
                        action=f"/competitors/{profile.id}/snapshot",
                        cls="competitor-snapshot-form",
                    ),
                ),
                cls="competitor-card",
            )
        )
    add_form = Form(
        csrf_input(sess),
        Div(
            Div(
                Label("Network"),
                Select(
                    *[
                        Option(platform.title(), value=platform)
                        for platform in COMPETITOR_PLATFORMS
                    ],
                    name="platform",
                ),
                cls="field",
            ),
            Div(
                Label("Handle"),
                Input(name="handle", placeholder="competitor", required=True),
                cls="field",
            ),
            Div(
                Label("Display name"),
                Input(name="display_name", placeholder="Competitor name"),
                cls="field",
            ),
            Div(
                Label("Profile URL"),
                Input(name="profile_url", placeholder="https://…"),
                cls="field",
            ),
            cls="competitor-add-grid",
        ),
        Button("Start tracking", type="submit", cls="btn primary"),
        method="post",
        action="/competitors",
        cls="card competitor-add-form",
    )
    return _app_page(
        ctx,
        "Competitors",
        "/competitors",
        flash("Competitor tracking updated." if saved else ""),
        flash(error, "error"),
        page_intro(
            "BENCHMARK",
            "Know what changed around you.",
            "Track competitor growth and engagement beside your own metrics. Manual snapshots and future official collectors share one history.",
            A("Export CSV", href="/competitors/export.csv", cls="btn"),
        ),
        Div(
            stat_card("Tracked profiles", len(profiles)),
            stat_card("Networks", len({item.platform for item in profiles})),
            stat_card(
                "Snapshots", sum(len(item.snapshots) for item in profiles), "Historical data points"
            ),
            stat_card("Brand", "Personal", ctx.workspace.name),
            cls="stats-grid",
        ),
        Div(*cards, cls="competitor-grid") if cards else "",
        Div(H2("Track another profile"), cls="section-heading"),
        add_form,
    )


@rt("/competitors", methods=["POST"])
async def competitor_add(request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    if ctx.membership.role == WorkspaceRole.viewer:
        return Response("Forbidden", status_code=403)
    form = await request.form()
    if not verify_csrf(sess, form.get("csrf")):
        return Response("Forbidden", status_code=403)
    platform = str(form.get("platform") or "").strip().lower()
    handle = str(form.get("handle") or "").strip().lstrip("@").lower()
    try:
        if platform not in COMPETITOR_PLATFORMS or not handle:
            raise ValueError("Choose a supported network and enter a handle")
        profile_url = str(form.get("profile_url") or "").strip()
        if profile_url and urlparse(profile_url).scheme not in {"http", "https"}:
            raise ValueError("Profile URL must start with http:// or https://")
        with session_scope() as session:
            existing = session.scalar(
                select(CompetitorProfile).where(
                    CompetitorProfile.workspace_id == ctx.workspace.id,
                    CompetitorProfile.platform == platform,
                    CompetitorProfile.handle == handle,
                )
            )
            if existing:
                raise ValueError("That competitor is already being tracked")
            profile = CompetitorProfile(
                workspace_id=ctx.workspace.id,
                platform=platform,
                handle=handle,
                display_name=str(form.get("display_name") or "").strip() or handle,
                profile_url=profile_url,
            )
            session.add(profile)
            session.flush()
            audit(session, ctx.workspace.id, ctx.user.id, "competitor.created", profile)
        return RedirectResponse("/competitors?saved=1", status_code=303)
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(f"/competitors?error={quote_plus(str(exc))}", status_code=303)


@rt("/competitors/{competitor_id}/snapshot", methods=["POST"])
async def competitor_snapshot(competitor_id: str, request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    if ctx.membership.role == WorkspaceRole.viewer:
        return Response("Forbidden", status_code=403)
    form = await request.form()
    if not verify_csrf(sess, form.get("csrf")):
        return Response("Forbidden", status_code=403)
    try:
        parsed = uuid.UUID(competitor_id)
        metric_date = date.fromisoformat(str(form.get("metric_date") or ""))
        values = {
            "followers": max(0, int(form.get("followers") or 0)),
            "posts": max(0, int(form.get("posts") or 0)),
            "engagement": max(0, int(form.get("engagement") or 0)),
            "reach": max(0, int(form.get("reach") or 0)),
            "engagement_rate": max(0, float(form.get("engagement_rate") or 0)),
        }
        with session_scope() as session:
            profile = session.scalar(
                select(CompetitorProfile).where(
                    CompetitorProfile.id == parsed,
                    CompetitorProfile.workspace_id == ctx.workspace.id,
                )
            )
            if not profile:
                return Response("Not found", status_code=404)
            snapshot = session.scalar(
                select(CompetitorMetricDaily).where(
                    CompetitorMetricDaily.competitor_id == profile.id,
                    CompetitorMetricDaily.metric_date == metric_date,
                )
            )
            if snapshot:
                for key, value in values.items():
                    setattr(snapshot, key, value)
                snapshot.collected_at = utcnow()
            else:
                snapshot = CompetitorMetricDaily(
                    competitor_id=profile.id, metric_date=metric_date, **values
                )
                session.add(snapshot)
            audit(
                session,
                ctx.workspace.id,
                ctx.user.id,
                "competitor.snapshot.saved",
                profile,
                {"metric_date": metric_date.isoformat()},
            )
        return RedirectResponse("/competitors?saved=1", status_code=303)
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(f"/competitors?error={quote_plus(str(exc))}", status_code=303)


@rt("/competitors/export.csv")
def competitors_export(sess):
    ctx = _context(sess)
    if not ctx:
        return Response("Unauthorized", status_code=401)
    with session_scope() as session:
        rows = session.execute(
            select(CompetitorProfile, CompetitorMetricDaily)
            .join(
                CompetitorMetricDaily,
                CompetitorMetricDaily.competitor_id == CompetitorProfile.id,
            )
            .where(CompetitorProfile.workspace_id == ctx.workspace.id)
            .order_by(
                CompetitorProfile.platform,
                CompetitorProfile.handle,
                CompetitorMetricDaily.metric_date,
            )
        ).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "platform",
            "handle",
            "date",
            "followers",
            "posts",
            "engagement",
            "reach",
            "engagement_rate",
        ]
    )
    for profile, metric in rows:
        writer.writerow(
            [
                profile.platform,
                profile.handle,
                metric.metric_date,
                metric.followers,
                metric.posts,
                metric.engagement,
                metric.reach,
                metric.engagement_rate,
            ]
        )
    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=fastsocial-competitors.csv"},
    )


def _report_data(workspace_id: uuid.UUID, days: int = 30) -> dict:
    since = utcnow() - timedelta(days=days)
    latest_metrics = (
        select(
            PostMetric.post_target_id.label("target_id"),
            func.max(PostMetric.collected_at).label("latest_at"),
        )
        .group_by(PostMetric.post_target_id)
        .subquery()
    )
    with session_scope() as session:
        metric_rows = session.execute(
            select(PostMetric, Post, SocialAccount)
            .join(
                latest_metrics,
                (latest_metrics.c.target_id == PostMetric.post_target_id)
                & (latest_metrics.c.latest_at == PostMetric.collected_at),
            )
            .join(PostTarget, PostMetric.post_target_id == PostTarget.id)
            .join(Post, PostTarget.post_id == Post.id)
            .join(SocialAccount, PostTarget.social_account_id == SocialAccount.id)
            .where(Post.workspace_id == workspace_id, PostMetric.collected_at >= since)
            .order_by(desc(PostMetric.collected_at))
        ).all()
        account_rows = session.execute(
            select(AccountMetricDaily, SocialAccount)
            .join(SocialAccount, AccountMetricDaily.social_account_id == SocialAccount.id)
            .where(
                SocialAccount.workspace_id == workspace_id,
                AccountMetricDaily.metric_date >= since.date(),
            )
            .order_by(desc(AccountMetricDaily.metric_date))
        ).all()
        competitor_rows = session.execute(
            select(CompetitorProfile, CompetitorMetricDaily)
            .join(
                CompetitorMetricDaily,
                CompetitorMetricDaily.competitor_id == CompetitorProfile.id,
            )
            .where(
                CompetitorProfile.workspace_id == workspace_id,
                CompetitorMetricDaily.metric_date >= since.date(),
            )
            .order_by(desc(CompetitorMetricDaily.metric_date))
        ).all()
    totals = {
        "impressions": sum(row[0].impressions for row in metric_rows),
        "reach": sum(row[0].reach for row in metric_rows),
        "engagements": sum(
            row[0].likes + row[0].comments + row[0].shares + row[0].saves for row in metric_rows
        ),
        "clicks": sum(row[0].clicks for row in metric_rows),
    }
    seen_posts = set()
    top_posts = []
    for metric, post, account in sorted(
        metric_rows,
        key=lambda row: row[0].likes + row[0].comments + row[0].shares + row[0].saves,
        reverse=True,
    ):
        if post.id in seen_posts:
            continue
        seen_posts.add(post.id)
        top_posts.append((metric, post, account))
    return {
        "since": since,
        "days": days,
        "metric_rows": metric_rows,
        "account_rows": account_rows,
        "competitor_rows": competitor_rows,
        "totals": totals,
        "top_posts": top_posts[:5],
    }


AD_PLATFORMS = ("meta", "google", "tiktok")


@rt("/ads")
def ads_page(sess, days: int = 30, saved: str = "", error: str = ""):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    days = days if days in {7, 30, 90, 365} else 30
    since = date.today() - timedelta(days=days)
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(AdCampaignDaily)
                .where(
                    AdCampaignDaily.workspace_id == ctx.workspace.id,
                    AdCampaignDaily.metric_date >= since,
                )
                .order_by(desc(AdCampaignDaily.metric_date), AdCampaignDaily.campaign_name)
            )
        )
    spend = sum(item.spend for item in rows)
    impressions = sum(item.impressions for item in rows)
    clicks = sum(item.clicks for item in rows)
    conversions = sum(item.conversions for item in rows)
    revenue = sum(item.revenue for item in rows)
    currency = rows[0].currency if rows else "EUR"
    table_rows = [
        Tr(
            Td(platform_pill(item.platform, item.platform.title())),
            Td(item.campaign_name),
            Td(item.metric_date.isoformat()),
            Td(f"{item.spend:,.2f} {item.currency}"),
            Td(f"{item.impressions:,}"),
            Td(f"{item.clicks:,}"),
            Td(f"{item.conversions:,.1f}"),
            Td(f"{(item.revenue / item.spend):.2f}×" if item.spend else "—"),
        )
        for item in rows
    ]
    import_form = Form(
        csrf_input(sess),
        Div(
            Div(
                Label("Ad network"),
                Select(
                    *[Option(value.title(), value=value) for value in AD_PLATFORMS], name="platform"
                ),
                cls="field",
            ),
            Div(Label("Campaign ID"), Input(name="campaign_id", required=True), cls="field"),
            Div(Label("Campaign name"), Input(name="campaign_name", required=True), cls="field"),
            Div(
                Label("Date"),
                Input(
                    type="date", name="metric_date", value=date.today().isoformat(), required=True
                ),
                cls="field",
            ),
            Div(
                Label("Currency"),
                Input(name="currency", value="EUR", maxlength="3", required=True),
                cls="field",
            ),
            Div(
                Label("Spend"),
                Input(type="number", name="spend", min="0", step="0.01", value="0"),
                cls="field",
            ),
            Div(
                Label("Impressions"),
                Input(type="number", name="impressions", min="0", value="0"),
                cls="field",
            ),
            Div(
                Label("Clicks"),
                Input(type="number", name="clicks", min="0", value="0"),
                cls="field",
            ),
            Div(
                Label("Conversions"),
                Input(type="number", name="conversions", min="0", step="0.01", value="0"),
                cls="field",
            ),
            Div(
                Label("Revenue"),
                Input(type="number", name="revenue", min="0", step="0.01", value="0"),
                cls="field",
            ),
            cls="ads-import-grid",
        ),
        Button("Save campaign snapshot", type="submit", cls="btn primary"),
        method="post",
        action="/ads/import",
        cls="card ads-import-form",
    )
    return _app_page(
        ctx,
        "Ads",
        "/ads",
        flash("Campaign snapshot saved." if saved else ""),
        flash(error, "error"),
        page_intro(
            "PAID + ORGANIC",
            "Campaign performance in context.",
            "Unify Meta, Google, and TikTok spend, traffic, conversion, and return metrics beside organic reporting.",
            Div(
                *[
                    A(f"{value}d", href=f"/ads?days={value}", cls="btn small")
                    for value in (7, 30, 90, 365)
                ],
                A("Export CSV", href=f"/ads/export.csv?days={days}", cls="btn primary"),
                cls="report-actions",
            ),
        ),
        Div(
            stat_card("Spend", f"{spend:,.2f} {currency}"),
            stat_card("Impressions", f"{impressions:,}"),
            stat_card("CPC", f"{(spend / clicks):.2f} {currency}" if clicks else "—"),
            stat_card(
                "ROAS",
                f"{(revenue / spend):.2f}×" if spend else "—",
                f"{conversions:,.1f} conversions",
            ),
            cls="stats-grid",
        ),
        Div(
            Table(
                Thead(
                    Tr(
                        Th("Network"),
                        Th("Campaign"),
                        Th("Date"),
                        Th("Spend"),
                        Th("Impressions"),
                        Th("Clicks"),
                        Th("Conversions"),
                        Th("ROAS"),
                    )
                ),
                Tbody(*table_rows),
            ),
            cls="card table-wrap",
        )
        if table_rows
        else empty_state(
            "◈",
            "No paid media data yet",
            "Import a snapshot below or connect a provider collector.",
        ),
        Div(H2("Add campaign data"), cls="section-heading"),
        import_form,
    )


@rt("/ads/import", methods=["POST"])
async def ads_import(request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    if ctx.membership.role == WorkspaceRole.viewer:
        return Response("Forbidden", status_code=403)
    form = await request.form()
    if not verify_csrf(sess, form.get("csrf")):
        return Response("Forbidden", status_code=403)
    try:
        platform = str(form.get("platform") or "")
        campaign_id = str(form.get("campaign_id") or "").strip()
        campaign_name = str(form.get("campaign_name") or "").strip()
        metric_date = date.fromisoformat(str(form.get("metric_date") or ""))
        if platform not in AD_PLATFORMS or not campaign_id or not campaign_name:
            raise ValueError("Network, campaign ID, and campaign name are required")
        values = {
            "currency": str(form.get("currency") or "EUR").upper()[:3],
            "spend": max(0, float(form.get("spend") or 0)),
            "impressions": max(0, int(form.get("impressions") or 0)),
            "clicks": max(0, int(form.get("clicks") or 0)),
            "conversions": max(0, float(form.get("conversions") or 0)),
            "revenue": max(0, float(form.get("revenue") or 0)),
        }
        with session_scope() as session:
            row = session.scalar(
                select(AdCampaignDaily).where(
                    AdCampaignDaily.workspace_id == ctx.workspace.id,
                    AdCampaignDaily.platform == platform,
                    AdCampaignDaily.external_campaign_id == campaign_id,
                    AdCampaignDaily.metric_date == metric_date,
                )
            )
            if row:
                for key, value in values.items():
                    setattr(row, key, value)
                row.campaign_name = campaign_name
                row.collected_at = utcnow()
            else:
                row = AdCampaignDaily(
                    workspace_id=ctx.workspace.id,
                    platform=platform,
                    external_campaign_id=campaign_id,
                    campaign_name=campaign_name,
                    metric_date=metric_date,
                    **values,
                )
                session.add(row)
            audit(session, ctx.workspace.id, ctx.user.id, "ads.snapshot.saved", row)
        return RedirectResponse("/ads?saved=1", status_code=303)
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(f"/ads?error={quote_plus(str(exc))}", status_code=303)


@rt("/ads/export.csv")
def ads_export(sess, days: int = 30):
    ctx = _context(sess)
    if not ctx:
        return Response("Unauthorized", status_code=401)
    days = days if days in {7, 30, 90, 365} else 30
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(AdCampaignDaily)
                .where(
                    AdCampaignDaily.workspace_id == ctx.workspace.id,
                    AdCampaignDaily.metric_date >= date.today() - timedelta(days=days),
                )
                .order_by(AdCampaignDaily.metric_date)
            )
        )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "platform",
            "campaign_id",
            "campaign",
            "date",
            "currency",
            "spend",
            "impressions",
            "clicks",
            "conversions",
            "revenue",
        ]
    )
    for item in rows:
        writer.writerow(
            [
                item.platform,
                item.external_campaign_id,
                item.campaign_name,
                item.metric_date,
                item.currency,
                item.spend,
                item.impressions,
                item.clicks,
                item.conversions,
                item.revenue,
            ]
        )
    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=fastsocial-ads.csv"},
    )


@rt("/reports")
def reports_page(sess, days: int = 30, saved: str = "", error: str = ""):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    days = days if days in {7, 30, 90, 365} else 30
    report = _report_data(ctx.workspace.id, days)
    totals = report["totals"]
    with session_scope() as session:
        schedules = list(
            session.scalars(
                select(ReportSchedule)
                .where(ReportSchedule.workspace_id == ctx.workspace.id)
                .order_by(desc(ReportSchedule.created_at))
            )
        )
        runs = session.execute(
            select(ReportRun, ReportSchedule)
            .join(ReportSchedule, ReportRun.schedule_id == ReportSchedule.id)
            .where(ReportSchedule.workspace_id == ctx.workspace.id)
            .order_by(desc(ReportRun.created_at))
            .limit(10)
        ).all()
    top_rows = [
        Tr(
            Td(platform_pill(account.platform, account.display_name or account.username)),
            Td((post.content.get("text", "") or "Untitled")[:100]),
            Td(f"{metric.impressions:,}"),
            Td(f"{metric.likes + metric.comments + metric.shares + metric.saves:,}"),
            Td(f"{metric.clicks:,}"),
        )
        for metric, post, account in report["top_posts"]
    ]
    schedule_rows = [
        Div(
            Div(
                Strong(item.name),
                Small(
                    f"{item.frequency.title()} · {item.report_days} days · "
                    f"{', '.join(item.recipients)}"
                ),
            ),
            Div(
                Span("ACTIVE" if item.active else "PAUSED", cls="mode-badge"),
                Form(
                    csrf_input(sess),
                    Button("Run now", type="submit", cls="btn small"),
                    method="post",
                    action=f"/reports/schedules/{item.id}/run",
                ),
                cls="report-row-actions",
            ),
            cls="report-schedule-row",
        )
        for item in schedules
    ]
    run_rows = [
        Div(
            Div(
                Strong(schedule.name),
                Small(_format_datetime(run.created_at, ctx.workspace.timezone)),
            ),
            Div(
                Span(run.status.upper(), cls=f"mode-badge report-{run.status}"),
                A("Open", href=f"/reports/runs/{run.id}", cls="btn small")
                if run.storage_key
                else "",
            ),
            cls="report-schedule-row",
        )
        for run, schedule in runs
    ]
    return _app_page(
        ctx,
        "Reports",
        "/reports",
        flash("Report schedule saved." if saved else ""),
        flash(error, "error"),
        page_intro(
            "REPORTING STUDIO",
            f"{days}-day brand report",
            "A client-ready view of organic performance, account growth, and competitor context.",
            Div(
                A("7d", href="/reports?days=7", cls="btn small"),
                A("30d", href="/reports?days=30", cls="btn small"),
                A("90d", href="/reports?days=90", cls="btn small"),
                A("1y", href="/reports?days=365", cls="btn small"),
                A("Print view", href=f"/reports/print?days={days}", cls="btn"),
                A("Export CSV", href=f"/reports/export.csv?days={days}", cls="btn primary"),
                cls="report-actions",
            ),
        ),
        Div(
            stat_card("Impressions", f"{totals['impressions']:,}"),
            stat_card("Reach", f"{totals['reach']:,}"),
            stat_card("Engagements", f"{totals['engagements']:,}"),
            stat_card("Clicks", f"{totals['clicks']:,}"),
            cls="stats-grid",
        ),
        Div(
            Div(
                Div(H2("Top content"), Small("Ranked by total engagement"), cls="card-head"),
                Div(
                    Table(
                        Thead(
                            Tr(
                                Th("Account"),
                                Th("Post"),
                                Th("Impressions"),
                                Th("Engagements"),
                                Th("Clicks"),
                            )
                        ),
                        Tbody(*top_rows),
                    ),
                    cls="table-wrap",
                )
                if top_rows
                else Div(
                    P("Publish and collect metrics to populate top-content analysis."),
                    cls="card-body form-help",
                ),
                cls="card",
            ),
            Div(
                Div(
                    H2("Scheduled delivery"),
                    Span(str(len(schedules)), cls="mode-badge"),
                    cls="card-head",
                ),
                Div(*schedule_rows, cls="report-schedules") if schedule_rows else "",
                Form(
                    csrf_input(sess),
                    Input(name="name", placeholder="Monthly brand report", required=True),
                    Select(
                        Option("Weekly", value="weekly"),
                        Option("Monthly", value="monthly", selected=True),
                        name="frequency",
                    ),
                    Select(
                        Option("Last 7 days", value="7"),
                        Option("Last 30 days", value="30", selected=True),
                        Option("Last 90 days", value="90"),
                        Option("Last year", value="365"),
                        name="report_days",
                    ),
                    Input(
                        type="email",
                        name="recipients",
                        placeholder="you@example.com",
                        multiple=True,
                        required=True,
                    ),
                    Label(
                        Input(type="checkbox", name="sections", value="performance", checked=True),
                        " Performance",
                    ),
                    Label(
                        Input(type="checkbox", name="sections", value="competitors", checked=True),
                        " Competitors",
                    ),
                    Button("Save schedule", type="submit", cls="btn primary"),
                    method="post",
                    action="/reports/schedules",
                    cls="report-schedule-form",
                ),
                Small(
                    "Reports always generate to private object storage. Email delivery uses Postmark when configured.",
                    cls="report-delivery-note",
                ),
                Div(H2("Recent runs"), cls="card-head"),
                Div(*run_rows, cls="report-schedules")
                if run_rows
                else P("No report runs yet.", cls="card-body form-help"),
                cls="card",
            ),
            cls="reports-layout",
        ),
    )


@rt("/reports/schedules", methods=["POST"])
async def report_schedule_add(request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    if ctx.membership.role == WorkspaceRole.viewer:
        return Response("Forbidden", status_code=403)
    form = await request.form()
    if not verify_csrf(sess, form.get("csrf")):
        return Response("Forbidden", status_code=403)
    try:
        name = str(form.get("name") or "").strip()
        frequency = str(form.get("frequency") or "monthly")
        report_days = int(form.get("report_days") or 30)
        recipients = [
            value.strip().lower()
            for value in str(form.get("recipients") or "").split(",")
            if value.strip()
        ]
        if (
            not name
            or frequency not in {"weekly", "monthly"}
            or report_days not in {7, 30, 90, 365}
            or not recipients
        ):
            raise ValueError("Name, frequency, and at least one recipient are required")
        if any("@" not in value for value in recipients):
            raise ValueError("Enter valid recipient email addresses")
        next_run = utcnow() + timedelta(days=7 if frequency == "weekly" else 30)
        with session_scope() as session:
            schedule = ReportSchedule(
                workspace_id=ctx.workspace.id,
                name=name,
                frequency=frequency,
                recipients=recipients,
                sections=form.getlist("sections") or ["performance"],
                report_days=report_days,
                next_run_at=next_run,
                created_by=ctx.user.id,
            )
            session.add(schedule)
            session.flush()
            audit(session, ctx.workspace.id, ctx.user.id, "report.schedule.created", schedule)
        return RedirectResponse("/reports?saved=1", status_code=303)
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(f"/reports?error={quote_plus(str(exc))}", status_code=303)


@rt("/reports/schedules/{schedule_id}/run", methods=["POST"])
async def report_schedule_run(schedule_id: str, request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    if ctx.membership.role == WorkspaceRole.viewer:
        return Response("Forbidden", status_code=403)
    form = await request.form()
    if not verify_csrf(sess, form.get("csrf")):
        return Response("Forbidden", status_code=403)
    try:
        parsed = uuid.UUID(schedule_id)
    except ValueError:
        return Response("Not found", status_code=404)
    with session_scope() as session:
        schedule = session.scalar(
            select(ReportSchedule).where(
                ReportSchedule.id == parsed,
                ReportSchedule.workspace_id == ctx.workspace.id,
            )
        )
        if not schedule:
            return Response("Not found", status_code=404)
    run = await execute_report_schedule(parsed)
    if run.status == "failed":
        return RedirectResponse(f"/reports?error={quote_plus(run.error_message)}", status_code=303)
    return RedirectResponse("/reports?saved=1", status_code=303)


@rt("/reports/runs/{run_id}")
def report_run_open(run_id: str, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    try:
        parsed = uuid.UUID(run_id)
    except ValueError:
        return Response("Not found", status_code=404)
    with session_scope() as session:
        run = session.scalar(
            select(ReportRun)
            .join(ReportSchedule, ReportRun.schedule_id == ReportSchedule.id)
            .where(ReportRun.id == parsed, ReportSchedule.workspace_id == ctx.workspace.id)
        )
        if not run or not run.storage_key:
            return Response("Not found", status_code=404)
        body = media_storage().get(run.storage_key)
    return Response(
        body,
        media_type="text/html",
        headers={"Content-Disposition": f"inline; filename=fastsocial-report-{run_id}.html"},
    )


@rt("/reports/print")
def report_print(sess, days: int = 30):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    days = days if days in {7, 30, 90, 365} else 30
    with session_scope() as session:
        report = report_summary(session, ctx.workspace.id, days)
    return Response(render_report_html(ctx.workspace.name, report), media_type="text/html")


@rt("/reports/export.csv")
def reports_export(sess, days: int = 30):
    ctx = _context(sess)
    if not ctx:
        return Response("Unauthorized", status_code=401)
    days = days if days in {7, 30, 90, 365} else 30
    report = _report_data(ctx.workspace.id, days)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["FastSocial brand report", ctx.workspace.name, f"Last {days} days"])
    writer.writerow([])
    writer.writerow(["Summary", "Value"])
    for key, value in report["totals"].items():
        writer.writerow([key, value])
    writer.writerow([])
    writer.writerow(["Top posts", "Platform", "Impressions", "Engagements", "Clicks"])
    for metric, post, account in report["top_posts"]:
        writer.writerow(
            [
                (post.content.get("text", "") or "Untitled")[:160],
                account.platform,
                metric.impressions,
                metric.likes + metric.comments + metric.shares + metric.saves,
                metric.clicks,
            ]
        )
    writer.writerow([])
    writer.writerow(["Competitors", "Platform", "Date", "Followers", "Engagement rate"])
    for profile, metric in report["competitor_rows"]:
        writer.writerow(
            [
                profile.display_name or profile.handle,
                profile.platform,
                metric.metric_date,
                metric.followers,
                metric.engagement_rate,
            ]
        )
    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=fastsocial-brand-report.csv"},
    )


@rt("/inbox", methods=["GET"])
def inbox_page(sess, status: str = "all"):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    with session_scope() as session:
        query = (
            select(InboxConversation)
            .where(InboxConversation.workspace_id == ctx.workspace.id)
            .options(selectinload(InboxConversation.messages))
            .order_by(desc(InboxConversation.last_message_at))
        )
        if status in {"unread", "open", "resolved"}:
            query = query.where(InboxConversation.status == status)
        conversations = list(session.scalars(query.limit(100)))
    rows = [
        A(
            Span(
                PLATFORM_MARKS.get(item.platform, item.platform[:1].upper()),
                cls=f"platform-mark {item.platform}",
            ),
            Div(
                Div(
                    Strong(item.participant_name or item.participant_handle or "Social user"),
                    Span(item.kind.title(), cls="mode-badge"),
                ),
                P(item.last_message_preview or "No preview available"),
                Small(_format_datetime(item.last_message_at, ctx.workspace.timezone)),
            ),
            Span(item.status.upper(), cls=f"inbox-state {item.status}"),
            href=f"/inbox/{item.id}",
            cls="inbox-row",
        )
        for item in conversations
    ]
    return _app_page(
        ctx,
        "Inbox",
        "/inbox",
        page_intro(
            "COMMUNITY",
            "One place for every conversation.",
            "Triage comments, direct messages, and reviews, assign ownership, and reply through connected providers.",
        ),
        Div(
            A("All", href="/inbox", cls="btn small"),
            A("Unread", href="/inbox?status=unread", cls="btn small"),
            A("Open", href="/inbox?status=open", cls="btn small"),
            A("Resolved", href="/inbox?status=resolved", cls="btn small"),
            cls="inbox-filters",
        ),
        Div(*rows, cls="card inbox-list")
        if rows
        else empty_state(
            "✉",
            "Inbox is clear",
            "Messages and comments will appear when an integration with inbox scopes is connected.",
            "Manage integrations",
            "/integrations",
        ),
    )


@rt("/inbox/{conversation_id}", methods=["GET"])
def inbox_conversation_page(conversation_id: str, sess, saved: str = "", error: str = ""):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    try:
        parsed = uuid.UUID(conversation_id)
    except ValueError:
        return Response("Not found", status_code=404)
    with session_scope() as session:
        conversation = session.scalar(
            select(InboxConversation)
            .where(
                InboxConversation.id == parsed,
                InboxConversation.workspace_id == ctx.workspace.id,
            )
            .options(selectinload(InboxConversation.messages))
        )
        if not conversation:
            return Response("Not found", status_code=404)
        replies = list(
            session.scalars(
                select(SavedReply)
                .where(SavedReply.workspace_id == ctx.workspace.id)
                .order_by(SavedReply.title)
            )
        )
        members = session.execute(
            select(User, WorkspaceMember)
            .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
            .where(WorkspaceMember.workspace_id == ctx.workspace.id)
            .order_by(User.name, User.email)
        ).all()
    messages = [
        Div(
            Div(
                Strong(
                    message.sender_name
                    or ("You" if message.direction == "outbound" else "Social user")
                ),
                Small(_format_datetime(message.sent_at, ctx.workspace.timezone)),
                cls="inbox-message-head",
            ),
            P(message.body),
            Small(
                message.delivery_status.upper()
                + (f" · {message.error_message}" if message.error_message else ""),
                cls=f"delivery-state {message.delivery_status}",
            )
            if message.direction == "outbound"
            else "",
            cls=f"inbox-message {message.direction}",
        )
        for message in sorted(conversation.messages, key=lambda item: item.sent_at)
    ]
    reply_options = "\n\n".join(f"/{item.shortcut} — {item.title}" for item in replies)
    return _app_page(
        ctx,
        "Inbox",
        "/inbox",
        flash("Conversation updated." if saved else ""),
        flash(error, "error"),
        page_intro(
            conversation.kind.upper(),
            conversation.participant_name
            or conversation.participant_handle
            or "Social conversation",
            f"{conversation.platform.title()} · {conversation.status.title()} · {conversation.priority.title()} priority",
            A("← Inbox", href="/inbox", cls="btn"),
        ),
        Div(
            Div(
                Div(*messages, cls="inbox-thread")
                if messages
                else P("No messages have been collected yet.", cls="card-body form-help"),
                Form(
                    csrf_input(sess),
                    Textarea(name="body", placeholder="Write a reply…", required=True, rows="4"),
                    Small(
                        f"Saved shortcuts: {reply_options}"
                        if replies
                        else "Create saved replies in the panel.",
                        cls="form-help",
                    ),
                    Button("Send reply", type="submit", cls="btn primary"),
                    method="post",
                    action=f"/inbox/{conversation.id}/reply",
                    cls="inbox-reply-form",
                ),
                cls="card inbox-thread-card",
            ),
            Div(
                Div(
                    H2("Triage"),
                    Form(
                        csrf_input(sess),
                        Label("Status"),
                        Select(
                            *[
                                Option(
                                    value.title(),
                                    value=value,
                                    selected=conversation.status == value,
                                )
                                for value in ("unread", "open", "resolved")
                            ],
                            name="status",
                        ),
                        Label("Priority"),
                        Select(
                            *[
                                Option(
                                    value.title(),
                                    value=value,
                                    selected=conversation.priority == value,
                                )
                                for value in ("low", "normal", "high", "urgent")
                            ],
                            name="priority",
                        ),
                        Label("Assigned to"),
                        Select(
                            Option("Unassigned", value=""),
                            *[
                                Option(
                                    user.name or user.email,
                                    value=str(user.id),
                                    selected=conversation.assigned_to == user.id,
                                )
                                for user, _membership in members
                            ],
                            name="assigned_to",
                        ),
                        Button("Update", type="submit", cls="btn"),
                        method="post",
                        action=f"/inbox/{conversation.id}/triage",
                        cls="inbox-triage-form",
                    ),
                    cls="card inbox-side-card",
                ),
                Div(
                    H2("Saved replies"),
                    *[
                        Div(Strong(f"/{item.shortcut}"), P(item.body), cls="saved-reply")
                        for item in replies
                    ],
                    Form(
                        csrf_input(sess),
                        Input(name="title", placeholder="Launch timing", required=True),
                        Input(name="shortcut", placeholder="launch", required=True),
                        Textarea(
                            name="body", placeholder="Reusable response…", required=True, rows="3"
                        ),
                        Input(type="hidden", name="return_to", value=str(conversation.id)),
                        Button("Save reply", type="submit", cls="btn small"),
                        method="post",
                        action="/inbox/saved-replies",
                    ),
                    cls="card inbox-side-card",
                ),
                cls="inbox-sidebar",
            ),
            cls="inbox-detail-layout",
        ),
    )


@rt("/inbox/{conversation_id}/triage", methods=["POST"])
async def inbox_triage(conversation_id: str, request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    if ctx.membership.role == WorkspaceRole.viewer:
        return Response("Forbidden", status_code=403)
    form = await request.form()
    if not verify_csrf(sess, form.get("csrf")):
        return Response("Forbidden", status_code=403)
    try:
        parsed = uuid.UUID(conversation_id)
        assigned = uuid.UUID(str(form.get("assigned_to"))) if form.get("assigned_to") else None
    except ValueError:
        return Response("Invalid conversation", status_code=400)
    status = str(form.get("status") or "open")
    priority = str(form.get("priority") or "normal")
    if status not in {"unread", "open", "resolved"} or priority not in {
        "low",
        "normal",
        "high",
        "urgent",
    }:
        return Response("Invalid triage state", status_code=400)
    with session_scope() as session:
        conversation = session.scalar(
            select(InboxConversation).where(
                InboxConversation.id == parsed,
                InboxConversation.workspace_id == ctx.workspace.id,
            )
        )
        if not conversation:
            return Response("Not found", status_code=404)
        if assigned and not membership_for(session, ctx.workspace.id, assigned):
            return Response("Invalid assignee", status_code=400)
        conversation.status = status
        conversation.priority = priority
        conversation.assigned_to = assigned
        audit(session, ctx.workspace.id, ctx.user.id, "inbox.triaged", conversation)
    return RedirectResponse(f"/inbox/{conversation_id}?saved=1", status_code=303)


@rt("/inbox/{conversation_id}/reply", methods=["POST"])
async def inbox_reply(conversation_id: str, request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    if ctx.membership.role == WorkspaceRole.viewer:
        return Response("Forbidden", status_code=403)
    form = await request.form()
    if not verify_csrf(sess, form.get("csrf")):
        return Response("Forbidden", status_code=403)
    body = str(form.get("body") or "").strip()
    try:
        parsed = uuid.UUID(conversation_id)
    except ValueError:
        return Response("Not found", status_code=404)
    with session_scope() as session:
        conversation = session.scalar(
            select(InboxConversation).where(
                InboxConversation.id == parsed,
                InboxConversation.workspace_id == ctx.workspace.id,
            )
        )
        if not conversation:
            return Response("Not found", status_code=404)
    if not body:
        return RedirectResponse(
            f"/inbox/{conversation_id}?error=Reply+is+required", status_code=303
        )
    try:
        await send_inbox_reply(parsed, ctx.user.id, body)
        return RedirectResponse(f"/inbox/{conversation_id}?saved=1", status_code=303)
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(
            f"/inbox/{conversation_id}?error={quote_plus(str(exc))}", status_code=303
        )


@rt("/inbox/saved-replies", methods=["POST"])
async def saved_reply_create(request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    if ctx.membership.role == WorkspaceRole.viewer:
        return Response("Forbidden", status_code=403)
    form = await request.form()
    if not verify_csrf(sess, form.get("csrf")):
        return Response("Forbidden", status_code=403)
    title = str(form.get("title") or "").strip()
    shortcut = slugify(str(form.get("shortcut") or "").strip())[:80]
    body = str(form.get("body") or "").strip()
    return_to = str(form.get("return_to") or "")
    if not title or not shortcut or not body:
        return RedirectResponse(
            f"/inbox/{return_to}?error=Complete+all+saved+reply+fields", status_code=303
        )
    try:
        with session_scope() as session:
            reply = SavedReply(
                workspace_id=ctx.workspace.id,
                title=title,
                shortcut=shortcut,
                body=body,
                created_by=ctx.user.id,
            )
            session.add(reply)
            session.flush()
            audit(session, ctx.workspace.id, ctx.user.id, "inbox.saved_reply.created", reply)
        return RedirectResponse(f"/inbox/{return_to}?saved=1", status_code=303)
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(f"/inbox/{return_to}?error={quote_plus(str(exc))}", status_code=303)


@rt("/smartlinks", methods=["GET"])
def smartlinks_page(sess, saved: str = "", error: str = ""):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    with session_scope() as session:
        pages = list(
            session.scalars(
                select(SmartLinkPage)
                .where(SmartLinkPage.workspace_id == ctx.workspace.id)
                .options(selectinload(SmartLinkPage.items))
                .order_by(desc(SmartLinkPage.created_at))
            )
        )
    cards = [
        A(
            Div(
                Div(
                    Div(H2(page.title), Small(f"fastsocial.org/s/{page.slug}")),
                    Span("LIVE" if page.published else "DRAFT", cls="mode-badge"),
                    cls="integration-card-head",
                ),
                Div(
                    Div(Strong(f"{page.view_count:,}"), Span("views")),
                    Div(Strong(str(len(page.items))), Span("links")),
                    Div(
                        Strong(f"{sum(item.click_count for item in page.items):,}"),
                        Span("clicks"),
                    ),
                    cls="smartlink-stats",
                ),
            ),
            href=f"/smartlinks/{page.id}",
            cls="smartlink-card",
        )
        for page in pages
    ]
    create_form = Form(
        csrf_input(sess),
        Input(name="title", placeholder="My links", required=True),
        Input(name="slug", placeholder="your-name", required=True),
        Select(
            Option("Sage", value="sage"),
            Option("Midnight", value="midnight"),
            Option("Sunrise", value="sunrise"),
            name="theme",
        ),
        Button("Create SmartLink", type="submit", cls="btn primary"),
        method="post",
        action="/smartlinks",
        cls="smartlink-create-form",
    )
    return _app_page(
        ctx,
        "SmartLinks",
        "/smartlinks",
        flash("SmartLink saved." if saved else ""),
        flash(error, "error"),
        page_intro(
            "LINK IN BIO",
            "Turn attention into measurable action.",
            "Publish branded link pages, track views and clicks, and keep every destination under your control.",
        ),
        Div(
            stat_card("Pages", len(pages)),
            stat_card("Live", sum(item.published for item in pages)),
            stat_card("Views", f"{sum(item.view_count for item in pages):,}"),
            stat_card(
                "Clicks",
                f"{sum(link.click_count for page in pages for link in page.items):,}",
            ),
            cls="stats-grid",
        ),
        Div(*cards, cls="smartlink-grid") if cards else "",
        Div(H2("Create a SmartLink"), cls="section-heading"),
        create_form,
    )


@rt("/smartlinks", methods=["POST"])
async def smartlink_create(request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    if ctx.membership.role == WorkspaceRole.viewer:
        return Response("Forbidden", status_code=403)
    form = await request.form()
    if not verify_csrf(sess, form.get("csrf")):
        return Response("Forbidden", status_code=403)
    try:
        title = str(form.get("title") or "").strip()
        requested_slug = slugify(str(form.get("slug") or ""))
        theme = str(form.get("theme") or "sage")
        if not title or len(requested_slug) < 3 or theme not in {"sage", "midnight", "sunrise"}:
            raise ValueError("Enter a title and a unique slug with at least three characters")
        with session_scope() as session:
            if session.scalar(select(SmartLinkPage).where(SmartLinkPage.slug == requested_slug)):
                raise ValueError("That public slug is already in use")
            page = SmartLinkPage(
                workspace_id=ctx.workspace.id,
                slug=requested_slug,
                title=title,
                theme=theme,
                created_by=ctx.user.id,
            )
            session.add(page)
            session.flush()
            page_id = page.id
            audit(session, ctx.workspace.id, ctx.user.id, "smartlink.created", page)
        return RedirectResponse(f"/smartlinks/{page_id}", status_code=303)
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(f"/smartlinks?error={quote_plus(str(exc))}", status_code=303)


def _smartlink_record(ctx: PageContext, page_id: str):
    try:
        parsed = uuid.UUID(page_id)
    except ValueError:
        return None
    with session_scope() as session:
        return session.scalar(
            select(SmartLinkPage)
            .where(
                SmartLinkPage.id == parsed,
                SmartLinkPage.workspace_id == ctx.workspace.id,
            )
            .options(selectinload(SmartLinkPage.items))
        )


@rt("/smartlinks/{page_id}")
async def smartlink_detail(page_id: str, request, sess, saved: str = "", error: str = ""):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    page = _smartlink_record(ctx, page_id)
    if not page:
        return Response("Not found", status_code=404)
    if request.method == "POST":
        if ctx.membership.role == WorkspaceRole.viewer:
            return Response("Forbidden", status_code=403)
        form = await request.form()
        if not verify_csrf(sess, form.get("csrf")):
            return Response("Forbidden", status_code=403)
        with session_scope() as session:
            row = session.scalar(
                select(SmartLinkPage).where(
                    SmartLinkPage.id == page.id,
                    SmartLinkPage.workspace_id == ctx.workspace.id,
                )
            )
            row.title = str(form.get("title") or "").strip() or row.title
            row.bio = str(form.get("bio") or "").strip()
            theme = str(form.get("theme") or row.theme)
            row.theme = theme if theme in {"sage", "midnight", "sunrise"} else row.theme
            row.published = form.get("published") == "on"
            audit(session, ctx.workspace.id, ctx.user.id, "smartlink.updated", row)
        return RedirectResponse(f"/smartlinks/{page.id}?saved=1", status_code=303)
    preview_links = [
        Div(
            Div(Strong(item.label), Small(item.url)),
            Span(f"{item.click_count:,} clicks", cls="mode-badge"),
            cls="smartlink-editor-item",
        )
        for item in page.items
    ]
    edit_form = Form(
        csrf_input(sess),
        Div(Label("Title"), Input(name="title", value=page.title), cls="field"),
        Div(Label("Bio"), Textarea(page.bio, name="bio", rows="4"), cls="field"),
        Div(
            Label("Theme"),
            Select(
                *[
                    Option(value.title(), value=value, selected=page.theme == value)
                    for value in ("sage", "midnight", "sunrise")
                ],
                name="theme",
            ),
            cls="field",
        ),
        Label(Input(type="checkbox", name="published", checked=page.published), " Published"),
        Button("Save page", type="submit", cls="btn primary"),
        method="post",
        action=f"/smartlinks/{page.id}",
        cls="card smartlink-edit-form",
    )
    add_item = Form(
        csrf_input(sess),
        Input(name="label", placeholder="Link label", required=True),
        Input(type="url", name="url", placeholder="https://example.com", required=True),
        Button("Add link", type="submit", cls="btn primary"),
        method="post",
        action=f"/smartlinks/{page.id}/items",
        cls="smartlink-item-form",
    )
    public_url = f"{settings().service_url}/s/{page.slug}"
    return _app_page(
        ctx,
        page.title,
        "/smartlinks",
        flash("SmartLink updated." if saved else ""),
        flash(error, "error"),
        page_intro(
            "SMARTLINK EDITOR",
            page.title,
            public_url,
            A("Open public page", href=f"/s/{page.slug}", target="_blank", cls="btn"),
        ),
        Div(
            Div(
                edit_form,
                Div(H2("Links"), *preview_links, add_item, cls="card smartlink-links-card"),
            ),
            Div(
                Div(
                    Span("FS", cls="smartlink-public-avatar"),
                    H1(page.title),
                    P(page.bio or "Your bio will appear here."),
                    *[
                        A(item.label, href=item.url, cls="smartlink-public-link")
                        for item in page.items
                        if item.active
                    ],
                    cls=f"smartlink-public-page theme-{page.theme} embedded",
                ),
                cls="smartlink-preview-shell",
            ),
            cls="smartlink-editor-layout",
        ),
    )


@rt("/smartlinks/{page_id}/items", methods=["POST"])
async def smartlink_item_add(page_id: str, request, sess):
    ctx = _context(sess)
    if not ctx:
        return _signin_redirect()
    if ctx.membership.role == WorkspaceRole.viewer:
        return Response("Forbidden", status_code=403)
    page = _smartlink_record(ctx, page_id)
    if not page:
        return Response("Not found", status_code=404)
    form = await request.form()
    if not verify_csrf(sess, form.get("csrf")):
        return Response("Forbidden", status_code=403)
    label = str(form.get("label") or "").strip()
    url = str(form.get("url") or "").strip()
    parsed = urlparse(url)
    if not label or parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return RedirectResponse(
            f"/smartlinks/{page.id}?error={quote_plus('Enter a label and a valid http(s) URL')}",
            status_code=303,
        )
    with session_scope() as session:
        item = SmartLinkItem(
            page_id=page.id,
            label=label,
            url=url,
            position=max([value.position for value in page.items] or [-1]) + 1,
        )
        session.add(item)
        session.flush()
        audit(session, ctx.workspace.id, ctx.user.id, "smartlink.item.created", item)
    return RedirectResponse(f"/smartlinks/{page.id}?saved=1", status_code=303)


@rt("/s/{slug}")
def smartlink_public(slug: str):
    with session_scope() as session:
        page = session.scalar(
            select(SmartLinkPage)
            .where(SmartLinkPage.slug == slugify(slug), SmartLinkPage.published.is_(True))
            .options(selectinload(SmartLinkPage.items))
        )
        if not page:
            return Response("SmartLink not found", status_code=404)
        page.view_count += 1
        title, bio, theme = page.title, page.bio, page.theme
        items = [(item.id, item.label) for item in page.items if item.active]
    return Html(
        head(title),
        Body(
            Div(
                Span("FS", cls="smartlink-public-avatar"),
                H1(title),
                P(bio),
                *[
                    A(label, href=f"/s/{slug}/go/{item_id}", cls="smartlink-public-link")
                    for item_id, label in items
                ],
                Small("Powered by FastSocial"),
                cls=f"smartlink-public-page theme-{theme}",
            )
        ),
    )


@rt("/s/{slug}/go/{item_id}")
def smartlink_redirect(slug: str, item_id: str):
    try:
        parsed_item = uuid.UUID(item_id)
    except ValueError:
        return Response("Not found", status_code=404)
    with session_scope() as session:
        item = session.scalar(
            select(SmartLinkItem)
            .join(SmartLinkPage, SmartLinkItem.page_id == SmartLinkPage.id)
            .where(
                SmartLinkItem.id == parsed_item,
                SmartLinkItem.active.is_(True),
                SmartLinkPage.slug == slugify(slug),
                SmartLinkPage.published.is_(True),
            )
        )
        if not item or urlparse(item.url).scheme not in {"http", "https"}:
            return Response("Not found", status_code=404)
        item.click_count += 1
        destination = item.url
    return RedirectResponse(destination, status_code=302)


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
