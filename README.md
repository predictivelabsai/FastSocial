# FastSocial

FastSocial is a personal-first, team-ready social media management system built with Python,
FastHTML, HTMX, PostgreSQL, and Cloudflare R2. It publishes directly to X, LinkedIn, and Bluesky,
and supports Facebook, Instagram, Threads, TikTok, YouTube, Pinterest, Google Business, and Twitch
through configurable Arcade/Composio MCP gateways.

## What is included

- Password and Google OpenID Connect login
- Isolated workspaces with owner, admin, editor, and viewer roles
- FastVC-style left navigation and a dedicated integration status page
- Direct, Arcade MCP, and Composio MCP connection paths per social account
- Capability-level MCP mappings for publishing, metrics, Inbox, Ads, competitors, and listening
- Encrypted credentials and OAuth tokens
- Multi-account composer, drafts, scheduling, and month/week/list planner views with drag rescheduling
- Evergreen Autolists with daily, weekly, or monthly round-robin publishing
- Reusable post library plus atomic CSV bulk scheduling for drafts and timed campaigns
- Best-time recommendations derived from the latest post-performance snapshots
- Private local or Cloudflare R2 media library
- Managed Canva, Google Drive, and Adobe Express media banks through Arcade/Composio MCP
- Idempotent publishing worker with per-target retry state
- Post/account metrics, server-rendered SVG analytics, and CSV export
- Competitor profiles with historical growth/engagement snapshots and export
- Unified brand reports with native PDF/editable PowerPoint/R2 artifacts and scheduled Postmark delivery
- Grounded xAI/OpenAI Report Studio narratives plus revocable JSON feeds for BI, MCP, and custom dashboards
- Meta, Google, and TikTok Ads campaign snapshots with spend, CPC, conversion, ROAS, and CSV export
- Scheduled live provider collectors with per-account run history and exact failure visibility
- Keyword/hashtag listening with reach, engagement, sentiment, and direct X recent-search support
- Privacy-safe real-time website analytics for pageviews, visitors, referrers, and conversions
- Public SmartLinks with themes plus live view and click measurement
- Tenant-safe unified inbox with filters, bulk actions, notes, tags, moderation, assignment, saved replies, and provider dispatch
- Personal publishing without approval; optional team approval workflow
- Agentic Create / Generate → Review → Post chats with an optional autonomous YOLO mode
- Persisted, user-safe execution traces streamed with HTMX SSE while copy and media generate
- 49 editable, versioned marketing skills vendored from Corey's Marketing Skills
- Encrypted xAI/OpenAI BYOK, configurable BYOM profiles, and gated server-key access
- Text, image, and video generation with model and media provenance
- PostgreSQL migrations, Docker, local tests, and Coolify CI/CD scaffolding

The application and all business workflows are Python. The UI is rendered with FastHTML and HTMX;
live creation uses only the standard HTMX core and SSE extension, with no custom browser event code.
the Skills WYSIWYG uses a small isolated Quill adapter and has a Markdown fallback. Planner drag and
drop is a progressively enhanced, route-specific script; the underlying reschedule flow remains a
server-owned Python operation.

## Local setup

```bash
cp .env.example .env
docker compose up -d db
uv sync --extra dev
uv run alembic upgrade head
uv run python -m fastsocial
```

Open `http://localhost:5062`. Register with email/password; Google appears when its client
variables are configured.

For an entirely disposable SQLite test run, leave `DATABASE_URL` unset. Production and Docker
Compose use PostgreSQL.

## Tests

```bash
uv run ruff check fastsocial migrations tests
uv run ruff format --check fastsocial migrations tests
uv run pytest
```

All live publishing tests use explicitly configured accounts. Unit and browser smoke tests use
the deterministic mock provider and cannot post to a real network.

## Connection modes

`direct` stores encrypted platform tokens in PostgreSQL. X uses OAuth 2.0 with PKCE, LinkedIn uses
three-legged OAuth, and Bluesky uses a dedicated app password.

`arcade` and `composio` store only managed account/tool identifiers. FastSocial calls a configured
MCP gateway; the provider owns downstream authorization and refresh. Configure each supported tool
name independently on the integration page because gateway catalog names are deployment-specific.

Current reference documentation:

- [X create post and media APIs](https://docs.x.com/x-api/posts/create-post)
- [LinkedIn Posts API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api)
- [Arcade MCP gateways](https://docs.arcade.dev/en/guides/mcp-gateways)
- [Composio connected accounts](https://docs.composio.dev/docs/auth-configuration/connected-accounts)

## Production configuration

Start from `.env.coolify.sample`. Production requires a unique `APP_SECRET`, a Fernet
`TOKEN_ENCRYPTION_KEY`, PostgreSQL `DATABASE_URL`, dedicated R2 bucket credentials, and whichever
login/publishing providers are enabled.

`MODEL_PROVIDER` and `MODEL_NAME` select the server default. Server model keys are available only
to emails in `MODEL_SERVER_ALLOWED_EMAILS` (default: `kaljuvee@gmail.com`); every other signed-in
user must save an encrypted workspace key in Integrations. Text, image, and video model IDs can be
overridden independently for xAI and OpenAI.

The canonical domain is `https://fastsocial.org`, port `5062`, health route `/healthz`, and Google
callback `https://fastsocial.org/auth/google/callback`.

Use the sibling FastDevOps control plane after FastSocial is added to its service catalog:

```bash
python scripts/coolify.py validate
python scripts/coolify.py doctor
python scripts/coolify.py status
```
