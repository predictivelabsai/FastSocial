# FastSocial

FastSocial is a personal-first, team-ready social media management system built with Python,
FastHTML, HTMX, PostgreSQL, and Cloudflare R2. It schedules and publishes to X, LinkedIn, and
Bluesky through direct platform APIs or configurable Arcade/Composio MCP gateways.

## What is included

- Password and Google OpenID Connect login
- Isolated workspaces with owner, admin, editor, and viewer roles
- FastVC-style left navigation and a dedicated integration status page
- Direct, Arcade MCP, and Composio MCP connection paths per social account
- Encrypted credentials and OAuth tokens
- Multi-account composer, drafts, scheduling, queue, and calendar
- Private local or Cloudflare R2 media library
- Idempotent publishing worker with per-target retry state
- Post/account metrics, server-rendered SVG analytics, and CSV export
- Personal publishing without approval; optional team approval workflow
- xAI composition variants
- PostgreSQL migrations, Docker, local tests, and Coolify CI/CD scaffolding

The UI is generated in Python with FastHTML. There is no application-specific JavaScript;
HTMX is the progressive-enhancement layer supplied by FastHTML.

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
MCP gateway; the provider owns downstream authorization and refresh. Configure tool names on the
integration page because gateway catalog names are deployment-specific.

Current reference documentation:

- [X create post and media APIs](https://docs.x.com/x-api/posts/create-post)
- [LinkedIn Posts API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api)
- [Arcade MCP gateways](https://docs.arcade.dev/en/guides/mcp-gateways)
- [Composio connected accounts](https://docs.composio.dev/docs/auth-configuration/connected-accounts)

## Production configuration

Start from `.env.coolify.sample`. Production requires a unique `APP_SECRET`, a Fernet
`TOKEN_ENCRYPTION_KEY`, PostgreSQL `DATABASE_URL`, dedicated R2 bucket credentials, and whichever
login/publishing providers are enabled.

The canonical domain is `https://fastsocial.org`, port `5062`, health route `/healthz`, and Google
callback `https://fastsocial.org/auth/google/callback`.

Use the sibling FastDevOps control plane after FastSocial is added to its service catalog:

```bash
python scripts/coolify.py validate
python scripts/coolify.py doctor
python scripts/coolify.py status
```
