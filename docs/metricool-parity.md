# Metricool parity map

FastSocial targets the functional surface described in the Metricool parity brief while keeping a
personal-first default, a Python/FastHTML/HTMX interface, and provider-independent integrations.
This map records the locally verifiable implementation rather than claiming identical third-party
API entitlements or pixel-identical screens.

| Capability | FastSocial implementation | Primary evidence |
| --- | --- | --- |
| Brands | Tenant-isolated workspaces, sidebar switching, roles, and cross-brand post repurposing | `/brands`; `tests/test_brand_workflows.py` |
| Planning and publishing | Drafts, scheduled publishing, month/week/list planner, drag rescheduling, multi-account targets, and idempotent retries | `/new-post`, `/calendar`, `/posts`; `tests/test_web.py` |
| Best posting times | Recommendations derived from normalized post metric snapshots | `/calendar`; `tests/test_metricool_parity.py` |
| Recurring content | Daily, weekly, and monthly round-robin Autolists | `/autolists`; `tests/test_metricool_parity.py` |
| Post library and bulk scheduling | Reusable templates plus atomic CSV import for drafts and scheduled campaigns | `/library`; `tests/test_metricool_parity.py` |
| Media and creative integrations | Local/R2 media plus managed Canva, Google Drive, and Adobe Express banks | `/media`, `/integrations`; `tests/test_metricool_parity.py` |
| Agentic creation | Persistent Create/Generate → Review → Post chats, editable skills, SSE trace, YOLO mode, text/image/video artifacts | `/new-post`, `/chats/{id}`, `/skills`; `tests/test_agentic.py` |
| Platform coverage | Direct X, LinkedIn, and Bluesky plus configurable Arcade/Composio MCP paths for Facebook, Instagram, Threads, TikTok, YouTube, Pinterest, Google Business, and Twitch | `/integrations`; `tests/test_web.py`, `tests/test_services.py` |
| Social analytics | Unified post/account snapshots, platform and content-type filters, audience segments, growth, and CSV exports | `/analytics`, `/analytics/audience.csv`; `tests/test_audience_analytics.py` |
| Competitor intelligence | Favorites, comparison snapshots, follower/engagement history, top-content format analysis, provider collection, and CSV export | `/competitors`; `tests/test_live_collectors.py`, `tests/test_metricool_parity.py` |
| Ads dashboards | Meta, Google, and TikTok campaign snapshots with spend, CPC, conversions, ROAS, and CSV | `/ads`; `tests/test_metricool_parity.py` |
| Listening | Keyword/hashtag tracking with normalized reach, engagement, sentiment, and direct X recent search | `/listening`; `tests/test_metricool_parity.py` |
| Inbox | Unified messages/comments/reviews with filtering, bulk actions, assignment, tags, moderation, notes, saved replies, and provider dispatch | `/inbox`; `tests/test_content_operations.py`, `tests/test_metricool_parity.py` |
| Website analytics | Privacy-safe pageview, visitor, referrer, and conversion analytics through a compact optional tracker | `/websites`; `static/tracker.js`, `tests/test_metricool_parity.py` |
| Reports and Studio | Native PDF and editable PowerPoint reports, grounded AI narratives, R2 artifacts, scheduled Postmark delivery, and revocable BI feeds | `/reports`; `tests/test_report_studio.py`, `tests/test_metricool_operations.py` |
| SmartLinks | Public themed pages, link/image/video items, cloning, daily views/visitors/clicks/CTR, referrer/UTM analysis, and CSV | `/smartlinks`; `tests/test_metricool_parity.py` |
| Teams and approval | Owner/admin/editor/viewer roles and optional submit/approve/reject flow; personal workspace bypasses approval | `/team`, `/approvals`; `tests/test_web.py` |
| Automation | Database-hashed scoped tokens, REST post/analytics operations, current stateless MCP tool discovery/calls, and Zapier/Make-compatible HTTP endpoints | `/integrations`, `/api/v1/*`, `/mcp`; `tests/test_automation_api.py` |

## Deliberate boundaries

- Publishing and metric availability still depend on each network's scopes, app review, plan, and
  rate limits. Managed Arcade/Composio paths are configurable because their catalog tool names can
  differ by deployment.
- FastSocial provides native feeds for BI tools instead of binding the core product to a single
  Looker Studio connector.
- Planner drag/drop, the Skills WYSIWYG adapter, the optional website tracker, and the HTMX SSE
  extension are the only focused browser scripts. Chat state, agent execution, validation,
  scheduling, publishing, analytics, and rendering remain Python/server-owned.
