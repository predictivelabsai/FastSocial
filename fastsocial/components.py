from __future__ import annotations

from fasthtml.common import (
    H1,
    A,
    Aside,
    Body,
    Button,
    Details,
    Div,
    Form,
    Head,
    Html,
    Input,
    Label,
    Link,
    Main,
    Meta,
    Nav,
    NotStr,
    P,
    Small,
    Span,
    Summary,
    Title,
)

from fastsocial import __version__
from fastsocial.config import settings
from fastsocial.security import csrf_token

ICONS = {
    "dashboard": "⌂",
    "compose": "+",
    "calendar": "□",
    "posts": "≡",
    "media": "▧",
    "analytics": "⌁",
    "approvals": "✓",
    "integrations": "⇄",
    "team": "♙",
    "settings": "⚙",
    "skills": "✎",
    "chat": "✦",
}

PLATFORM_NAMES = {"x": "X", "linkedin": "LinkedIn", "bluesky": "Bluesky"}
PLATFORM_MARKS = {"x": "X", "linkedin": "in", "bluesky": "☁"}


def head(title: str):
    description = (
        "Plan, schedule, publish, and measure content across X, LinkedIn, and Bluesky "
        "from a private personal-first workspace."
    )
    return Head(
        Meta(charset="utf-8"),
        Meta(name="viewport", content="width=device-width, initial-scale=1"),
        Meta(name="color-scheme", content="light"),
        Meta(name="description", content=description),
        Meta(name="theme-color", content="#4f8f73"),
        Meta(property="og:title", content=f"{title} · FastSocial"),
        Meta(property="og:description", content=description),
        Meta(property="og:type", content="website"),
        Meta(property="og:url", content=settings().service_url),
        Meta(name="twitter:card", content="summary"),
        Title(f"{title} · FastSocial"),
        Link(rel="canonical", href=settings().service_url),
        Link(rel="icon", href="/static/favicon.svg", type="image/svg+xml"),
        Link(rel="preconnect", href="https://fonts.googleapis.com"),
        Link(rel="preconnect", href="https://fonts.gstatic.com", crossorigin="anonymous"),
        Link(
            href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@600;700&display=swap",
            rel="stylesheet",
        ),
        Link(rel="stylesheet", href="/static/app.css"),
    )


def csrf_input(sess: dict):
    return Input(type="hidden", name="csrf", value=csrf_token(sess))


def flash(message: str = "", kind: str = "success"):
    return Div(message, cls=f"flash {kind}") if message else ""


def auth_page(title: str, subtitle: str, *children):
    return Html(
        head(title),
        Body(
            Div(
                A(Span("F", cls="brand-glyph"), Span("FastSocial"), href="/", cls="auth-brand"),
                Div(H1(title), P(subtitle), *children, cls="auth-card"),
                Small("Your publishing system, under your control."),
                cls="auth-shell",
            )
        ),
    )


def sidebar(
    current_path: str,
    user,
    workspace,
    accounts: list,
    pending_approvals: int = 0,
    chat_sessions: list | None = None,
    logout_csrf: str = "",
):
    items = [
        ("Dashboard", "/", "dashboard"),
        ("Calendar", "/calendar", "calendar"),
        ("Posts", "/posts", "posts"),
        ("Media", "/media", "media"),
        ("Analytics", "/analytics", "analytics"),
    ]
    main_links = [
        A(
            Span(ICONS[key], cls="nav-icon"),
            Span(label),
            href=href,
            cls=f"nav-link{' active' if current_path == href or (href != '/' and current_path.startswith(href)) else ''}",
        )
        for label, href, key in items
    ]
    counts = {
        platform: sum(item.platform == platform for item in accounts) for platform in PLATFORM_NAMES
    }
    integration_links = [
        A(
            Span(PLATFORM_MARKS[platform], cls=f"platform-mark {platform}"),
            Span(name),
            Span(str(counts[platform]), cls="nav-count"),
            href=f"/integrations#{platform}",
            cls="nav-sublink",
        )
        for platform, name in PLATFORM_NAMES.items()
    ]
    integrations = Details(
        Summary(
            Span(ICONS["integrations"], cls="nav-icon"),
            Span("Integrations"),
            Span(str(len(accounts)), cls="nav-count"),
        ),
        Div(*integration_links, cls="nav-submenu"),
        open=current_path.startswith("/integrations"),
        cls=f"nav-details{' active' if current_path.startswith('/integrations') else ''}",
    )
    lower = [
        A(
            Span(ICONS["skills"], cls="nav-icon"),
            Span("Skills"),
            href="/skills",
            cls=f"nav-link{' active' if current_path.startswith('/skills') else ''}",
        ),
        A(
            Span(ICONS["approvals"], cls="nav-icon"),
            Span("Approvals"),
            (Span(str(pending_approvals), cls="nav-count alert") if pending_approvals else ""),
            href="/approvals",
            cls=f"nav-link{' active' if current_path.startswith('/approvals') else ''}",
        ),
        A(
            Span(ICONS["team"], cls="nav-icon"),
            Span("Team"),
            href="/team",
            cls=f"nav-link{' active' if current_path.startswith('/team') else ''}",
        ),
        A(
            Span(ICONS["settings"], cls="nav-icon"),
            Span("Settings"),
            href="/settings",
            cls=f"nav-link{' active' if current_path.startswith('/settings') else ''}",
        ),
    ]
    initials = "".join(word[:1] for word in (user.name or user.email).split())[:2].upper()
    return Aside(
        Div(
            A(Span("F", cls="brand-glyph"), Span("FastSocial"), href="/", cls="brand"),
            Span(f"v{__version__}", cls="version"),
            cls="sidebar-head",
        ),
        Div(
            Span("WORKSPACE", cls="eyebrow"),
            Div(
                Span(workspace.name[:1].upper(), cls="workspace-avatar"),
                Div(Span(workspace.name), Small("Personal workspace")),
                cls="workspace-switcher",
            ),
            cls="workspace-block",
        ),
        Nav(
            A(
                Span("+", cls="nav-icon"),
                Span("New Post"),
                href="/new-post",
                cls=f"new-post-link{' active' if current_path == '/new-post' else ''}",
            ),
            Span("CHAT HISTORY", cls="nav-section-label"),
            Div(
                *[
                    A(
                        Span("●", cls="chat-session-dot"),
                        Span(item.title, cls="chat-session-title"),
                        href=f"/chats/{item.id}",
                        title=item.title,
                        cls=f"chat-session-link{' active' if current_path == f'/chats/{item.id}' else ''}",
                    )
                    for item in (chat_sessions or [])
                ],
                P("No post chats yet.", cls="chat-history-empty") if not chat_sessions else "",
                cls="chat-history-list",
            ),
            Span("PUBLISH", cls="nav-section-label"),
            *main_links,
            Span("CONNECT", cls="nav-section-label"),
            integrations,
            Span("MANAGE", cls="nav-section-label"),
            *lower,
            cls="sidebar-nav",
        ),
        Div(
            Span(initials, cls="user-avatar"),
            Div(Span(user.name or user.email.split("@", 1)[0]), Small(user.email), cls="user-copy"),
            Div(
                A("⚙", href="/settings", cls="user-settings", title="Profile settings"),
                Form(
                    Input(type="hidden", name="csrf", value=logout_csrf),
                    Button("Log out", type="submit", cls="user-logout"),
                    method="post",
                    action="/auth/logout",
                ),
                cls="user-actions",
            ),
            cls="sidebar-user",
        ),
        cls="sidebar",
    )


def app_page(
    title: str,
    current_path: str,
    user,
    workspace,
    accounts: list,
    *children,
    pending_approvals: int = 0,
    chat_sessions: list | None = None,
    logout_csrf: str = "",
    action=None,
):
    return Html(
        head(title),
        Body(
            Input(type="checkbox", id="nav-toggle", cls="nav-toggle"),
            Label("", fr="nav-toggle", cls="mobile-overlay"),
            sidebar(
                current_path,
                user,
                workspace,
                accounts,
                pending_approvals,
                chat_sessions,
                logout_csrf,
            ),
            Main(
                Div(
                    Div(
                        Label("☰", fr="nav-toggle", cls="mobile-menu"),
                        Div(H1(title), cls="page-title"),
                    ),
                    action or "",
                    cls="topbar",
                ),
                Div(*children, cls="page-content"),
                cls="main",
            ),
        ),
    )


def page_intro(kicker: str, title: str, description: str, action=None):
    return Div(
        Div(Span(kicker, cls="eyebrow accent"), H1(title), P(description)),
        action or "",
        cls="page-intro",
    )


def stat_card(label: str, value, detail: str = "", tone: str = ""):
    return Div(
        Span(label, cls="stat-label"),
        Span(str(value), cls="stat-value"),
        Small(detail),
        cls=f"stat-card {tone}",
    )


def status_badge(status) -> Span:
    value = getattr(status, "value", status)
    label = str(value).replace("_", " ").title()
    return Span(Span(cls="status-dot"), label, cls=f"status-badge {value}")


def platform_pill(platform: str, label: str | None = None):
    return Span(
        Span(PLATFORM_MARKS.get(platform, platform[:1].upper()), cls=f"platform-mark {platform}"),
        label or PLATFORM_NAMES.get(platform, platform.title()),
        cls="platform-pill",
    )


def empty_state(icon: str, title: str, text: str, action_label: str = "", action_href: str = ""):
    return Div(
        Span(icon, cls="empty-icon"),
        H1(title),
        P(text),
        (A(action_label, href=action_href, cls="btn primary") if action_label else ""),
        cls="empty-state",
    )


GOOGLE_SVG = NotStr(
    '<svg width="18" height="18" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg">'
    '<path d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 01-1.8 2.72v2.26h2.91c1.7-1.57 2.69-3.87 2.69-6.62z" fill="#4285F4"/>'
    '<path d="M9 18c2.43 0 4.47-.81 5.96-2.18l-2.91-2.26c-.81.54-1.84.86-3.05.86-2.34 0-4.33-1.58-5.04-3.71H.96v2.33A9 9 0 009 18z" fill="#34A853"/>'
    '<path d="M3.96 10.71A5.4 5.4 0 013.68 9c0-.59.1-1.17.28-1.71V4.96H.96A9 9 0 000 9c0 1.45.35 2.86.96 4.04l3-2.33z" fill="#FBBC05"/>'
    '<path d="M9 3.58c1.32 0 2.51.45 3.44 1.35l2.58-2.58C13.46.89 11.43 0 9 0A9 9 0 00.96 4.96l3 2.33C4.67 5.16 6.66 3.58 9 3.58z" fill="#EA4335"/>'
    "</svg>"
)


def google_button(label: str = "Continue with Google"):
    if not settings().google_client_id:
        return ""
    return A(GOOGLE_SVG, Span(label), href="/auth/google", cls="btn google")
