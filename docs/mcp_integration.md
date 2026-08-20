# MCP Integration

This guide is for a semi-technical FastSocial operator: someone comfortable copying environment
variables, reading a docs page, and pasting a tool name into a form, but not necessarily writing
code. It explains the two directions in which FastSocial speaks the
[Model Context Protocol (MCP)](https://modelcontextprotocol.io):

1. **FastSocial as an MCP client** — FastSocial connects *out* to a managed gateway
   (**Arcade** or **Composio**) that owns the OAuth for a social network. FastSocial never sees the
   network's tokens; it calls named tools on the gateway. This is how you reach Facebook, Instagram,
   Threads, TikTok, YouTube, Pinterest, Google Business, and Twitch without building each direct
   integration yourself.
2. **FastSocial as an MCP server** — FastSocial exposes *its own* MCP endpoint (`/mcp`) so that an
   AI assistant (Claude Desktop, an IDE agent, a custom agent) or an automation platform can read
   your posts and analytics and create or schedule drafts on your behalf.

You can use either direction on its own, or both at once.

---

## Quick reference

| | FastSocial as MCP **client** (outbound) | FastSocial as MCP **server** (inbound) |
| --- | --- | --- |
| Purpose | Publish/collect on social networks via a managed gateway | Let an agent drive FastSocial |
| You configure | `ARCADE_*` / `COMPOSIO_*` env vars + per-account tool names | An automation token in **Integrations** |
| Endpoint | The gateway's URL (Arcade/Composio) | `https://fastsocial.org/mcp` |
| Auth FastSocial uses | `Authorization: Bearer <gateway API key>` + user header | Caller sends `Authorization: Bearer <automation token>` |
| Who owns network OAuth | The gateway (Arcade/Composio) | N/A (FastSocial owns its own data) |
| Code | `fastsocial/social/mcp.py` (`ManagedMCPClient`) | `fastsocial/routes.py` (`/mcp`, `_mcp_tools`) |

---

## Part 1 — FastSocial as an MCP client (managed social gateways)

### 1.1 When to use this instead of a direct connection

FastSocial publishes **directly** to X, LinkedIn, Bluesky, and Facebook Pages — those store encrypted
tokens in PostgreSQL and need no gateway. For every other network, or when you would rather not run
app review and token refresh yourself, connect through a managed MCP gateway. FastSocial stores only
the managed account identifier and the tool names to call; the gateway owns downstream authorization
and refresh.

### 1.2 Concepts

- **Gateway** — Arcade or Composio. It exposes a single MCP endpoint that speaks JSON-RPC 2.0
  (`tools/call`).
- **Managed user ID** — the identifier the gateway uses to look up *which* end-user's connected
  account to act on. FastSocial sends it in a per-gateway header (`Arcade-User-ID` for Arcade,
  `X-User-ID` for Composio). It defaults to your FastSocial user ID but is editable per account.
- **Capability → tool mapping** — FastSocial has generic capabilities (publish, post metrics,
  account metrics, health, inbox collection/reply/moderation, ads metrics, competitor metrics,
  listening). For each one you paste the gateway's tool name (for example `X.CreatePost` or a
  Composio slug). Catalog names are deployment-specific, so FastSocial does not hard-code them.

The capability slots FastSocial reads (all optional; leave blank to disable that capability):

| Capability | Metadata key | Typical use |
| --- | --- | --- |
| Publish | `publish_tool` | Create a post/tweet/reel |
| Post metrics | `metrics_tool` | Per-post likes/comments/shares |
| Account metrics | `account_metrics_tool` | Followers, impressions, engagement |
| Health | `health_tool` | Connection check |
| Inbox collect | `inbox_collect_tool` | Pull comments/DMs into the unified inbox |
| Inbox reply | `inbox_reply_tool` | Send a reply |
| Inbox moderation | `inbox_moderation_tool` | Hide/delete/like a conversation |
| Ads metrics | `ads_metrics_tool` | Campaign spend/CPC/ROAS |
| Competitor metrics | `competitor_metrics_tool` | Competitor follower/engagement snapshots |
| Listening | `listening_tool` | Keyword/hashtag mentions |

### 1.3 Configure a gateway (environment)

Add the keys for whichever gateway you use to your `.env` (see `.env.example`):

```env
# Arcade
ARCADE_API_KEY=
ARCADE_MCP_URL=

# Composio
COMPOSIO_API_KEY=
COMPOSIO_MCP_URL=
```

A gateway is considered configured once **both** its API key and MCP URL are set. Until then, the
matching option is disabled on the Integrations page.

### 1.4 Connect an account (UI)

1. Open **Integrations** and pick the network (for example Instagram).
2. Choose the **Arcade MCP** or **Composio MCP** provider.
3. Set the **Managed user ID** (defaults to your user ID; change it only if your gateway keys
   accounts differently).
4. Paste the tool name for each capability you want. Start with just `publish_tool`; add metrics and
   inbox tools later.
5. Save. FastSocial stores only these identifiers — never a social-network token.

You can revise the capability tool names any time from the account detail page without reconnecting.

### 1.5 How a call flows (what happens under the hood)

`ManagedMCPClient` sends a JSON-RPC `tools/call` request to the gateway URL:

```jsonc
POST <gateway MCP URL>
Authorization: Bearer <gateway API key>
Accept: application/json, text/event-stream
Arcade-User-ID: <managed user id>          // X-User-ID for Composio
{
  "jsonrpc": "2.0",
  "id": "<uuid>",
  "method": "tools/call",
  "params": { "name": "<your publish_tool>", "arguments": { "account_id": "...", "text": "..." } }
}
```

FastSocial accepts either a plain JSON response or an SSE (`text/event-stream`) stream, and reads the
result from `structuredContent`, a `data`/`items`/`results` array, or JSON embedded in a text content
block — so most gateway result shapes work without extra mapping.

### 1.6 Verify

- Use the **Health** action on the account (needs `health_tool`, or falls back to a
  configuration-only check).
- Publish one controlled text post and confirm the returned post ID.
- Trigger a collector (metrics/inbox) and confirm rows appear.

### 1.7 Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `… MCP is not configured` | Missing API key or MCP URL env var for that gateway |
| `MCP publish tool did not return a post ID` | Wrong `publish_tool` name, or the tool returns a non-standard key |
| `No publish MCP tool is configured` | The capability slot is blank on the account |
| 401/403 from gateway | API key invalid, or the managed user has no connected account |
| Empty metrics/inbox | Capability tool name blank, or the tool returned an unrecognized envelope |

---

## Part 2 — FastSocial as an MCP server (drive FastSocial from an agent)

FastSocial exposes a stateless MCP endpoint so an assistant can operate your workspace.

- **Endpoint:** `POST https://fastsocial.org/mcp`
- **Auth:** a workspace **automation token** as `Authorization: Bearer <token>`
- **Protocols:** advertises `2025-11-25`, and understands the stateless `2026-07-28` discovery flow
  (which also requires matching `Mcp-Method` / `Mcp-Name` request headers).

### 2.1 Create an automation token

1. Open **Integrations → Automation tokens**.
2. Grant the scopes you need:
   - `posts:read` — list posts
   - `posts:write` — create and schedule
   - `analytics:read` — read the analytics summary
3. Copy the token once — it is shown a single time and stored only as a hash.

### 2.2 Available tools

| Tool | Scope required | Description |
| --- | --- | --- |
| `list_posts` | `posts:read` | List recent posts in the workspace |
| `create_draft` | `posts:write` | Create an editable draft |
| `schedule_post` | `posts:write` | Create and schedule a post for target account IDs |
| `analytics_summary` | `analytics:read` | Normalized performance summary (7/30/90/365 days) |

### 2.3 Example calls

List the tools:

```bash
curl -s https://fastsocial.org/mcp \
  -H "Authorization: Bearer $FASTSOCIAL_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Create a draft:

```bash
curl -s https://fastsocial.org/mcp \
  -H "Authorization: Bearer $FASTSOCIAL_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call",
       "params":{"name":"create_draft","arguments":{"text":"Hello from MCP"}}}'
```

### 2.4 Connect from an MCP-capable client

Any client that supports a streamable-HTTP MCP server with a bearer token can point at
`https://fastsocial.org/mcp`. Give it the token as the `Authorization` header. The same endpoint also
backs the Zapier/Make-compatible REST operations under `/api/v1/*` for no-code automation.

---

## Rollout plan for the managed connectors

The two managed gateways are wired end-to-end in code (`ManagedMCPClient`, the Integrations UI, and
the capability mappings), but going live with each network still needs catalog-specific setup and
verification. Track that here.

### Phase 0 — Prerequisites (done in code)

- [x] `ARCADE_*` / `COMPOSIO_*` settings and env samples
- [x] `ManagedMCPClient` JSON-RPC + SSE handling
- [x] Per-account capability tool mapping UI and persistence
- [x] Provider dispatch through the unified inbox, ads, competitor, and listening collectors

### Phase 1 — Arcade (`arcade.dev`)

Reference: [Arcade MCP gateways](https://docs.arcade.dev/en/guides/mcp-gateways).

1. Create an Arcade project and generate an API key.
2. Note your MCP gateway URL and set `ARCADE_API_KEY` / `ARCADE_MCP_URL`.
3. In Arcade, connect one end-user account for the target network (start with one you control).
4. From Arcade's catalog, record the exact tool names for publish and metrics
   (for example `X.CreatePost`, plus the network's read tools).
5. In FastSocial, connect the network with provider **Arcade MCP**, set the managed user ID to match
   the Arcade end-user, and paste the tool names.
6. Verify: health → single text publish → post metrics → account metrics.
7. Expand to inbox, ads, competitors, and listening tools one capability at a time.
8. Document the confirmed tool-name catalog per network in an internal note.

**Owner:** _TBD_ · **Target network first:** _Instagram or Threads_ · **Status:** not started

### Phase 2 — Composio (`composio.dev`)

Reference: [Composio connected accounts](https://docs.composio.dev/docs/auth-configuration/connected-accounts).

1. Create a Composio account and an auth config for the target toolkit/network.
2. Generate an API key; set `COMPOSIO_API_KEY` / `COMPOSIO_MCP_URL`.
3. Create a **connected account** for your end-user; note the entity/user identifier Composio expects
   — this becomes the FastSocial **managed user ID** (sent as `X-User-ID`).
4. Record the tool slugs for each capability from the Composio catalog.
5. In FastSocial, connect the network with provider **Composio MCP** and paste the slugs.
6. Verify with the same health → publish → metrics sequence as Arcade.
7. Roll capabilities forward (inbox/ads/competitor/listening) as the toolkit supports them.

**Owner:** _TBD_ · **Target network first:** _TBD_ · **Status:** not started

### Phase 3 — Operational hardening

- [ ] Confirm rate-limit and retry behavior per gateway (429/5xx already flagged retryable).
- [ ] Decide the managed-user-ID convention (per FastSocial user vs. per workspace) and document it.
- [ ] Add a short internal runbook of confirmed tool names per network and gateway.
- [ ] Define token/credential rotation ownership (the gateway owns network tokens; you own the
      gateway API key).

### Decisions to confirm before wider rollout

- Which networks go through **Arcade** vs. **Composio** (avoid double-connecting the same network).
- The managed-user-ID scheme, since it drives which end-user the gateway acts as.
- Whether inbox/ads/competitor/listening are in scope for launch or a later phase per network.

---

## See also

- `README.md` — Connection modes overview
- `docs/0Auth_plan.md` — direct OAuth (X, LinkedIn, Facebook Pages)
- `docs/metricool-parity.md` — capability map and deliberate boundaries
- `fastsocial/social/mcp.py` — the managed client implementation
- Arcade: <https://docs.arcade.dev/en/guides/mcp-gateways>
- Composio: <https://docs.composio.dev/docs/auth-configuration/connected-accounts>
</content>
