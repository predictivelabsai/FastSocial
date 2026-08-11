from __future__ import annotations

import html
import uuid
from datetime import timedelta

import httpx
from sqlalchemy import func, select

from fastsocial.config import settings
from fastsocial.db import session_scope
from fastsocial.models import (
    AccountMetricDaily,
    CompetitorMetricDaily,
    CompetitorProfile,
    Post,
    PostMetric,
    PostTarget,
    ReportRun,
    ReportSchedule,
    SocialAccount,
    Workspace,
    utcnow,
)
from fastsocial.storage import media_storage


def report_summary(session, workspace_id: uuid.UUID, days: int) -> dict:
    since = utcnow() - timedelta(days=days)
    latest = (
        select(
            PostMetric.post_target_id.label("target_id"),
            func.max(PostMetric.collected_at).label("latest_at"),
        )
        .group_by(PostMetric.post_target_id)
        .subquery()
    )
    rows = session.execute(
        select(PostMetric, Post, SocialAccount)
        .join(
            latest,
            (latest.c.target_id == PostMetric.post_target_id)
            & (latest.c.latest_at == PostMetric.collected_at),
        )
        .join(PostTarget, PostMetric.post_target_id == PostTarget.id)
        .join(Post, PostTarget.post_id == Post.id)
        .join(SocialAccount, PostTarget.social_account_id == SocialAccount.id)
        .where(Post.workspace_id == workspace_id, PostMetric.collected_at >= since)
    ).all()
    competitors = session.execute(
        select(CompetitorProfile, CompetitorMetricDaily)
        .join(CompetitorMetricDaily, CompetitorMetricDaily.competitor_id == CompetitorProfile.id)
        .where(
            CompetitorProfile.workspace_id == workspace_id,
            CompetitorMetricDaily.metric_date >= since.date(),
        )
        .order_by(CompetitorMetricDaily.metric_date.desc())
    ).all()
    accounts = session.execute(
        select(AccountMetricDaily, SocialAccount)
        .join(SocialAccount, AccountMetricDaily.social_account_id == SocialAccount.id)
        .where(
            SocialAccount.workspace_id == workspace_id,
            AccountMetricDaily.metric_date >= since.date(),
        )
    ).all()
    return {
        "days": days,
        "rows": rows,
        "competitors": competitors,
        "accounts": accounts,
        "totals": {
            "impressions": sum(metric.impressions for metric, _, _ in rows),
            "reach": sum(metric.reach for metric, _, _ in rows),
            "engagements": sum(
                metric.likes + metric.comments + metric.shares + metric.saves
                for metric, _, _ in rows
            ),
            "clicks": sum(metric.clicks for metric, _, _ in rows),
        },
    }


def render_report_html(workspace_name: str, report: dict) -> bytes:
    totals = report["totals"]
    top = sorted(
        report["rows"],
        key=lambda row: row[0].likes + row[0].comments + row[0].shares + row[0].saves,
        reverse=True,
    )[:10]
    post_rows = (
        "".join(
            "<tr>"
            f"<td>{html.escape(account.platform.title())}</td>"
            f"<td>{html.escape((post.content.get('text') or 'Untitled')[:180])}</td>"
            f"<td>{metric.impressions:,}</td>"
            f"<td>{metric.likes + metric.comments + metric.shares + metric.saves:,}</td>"
            f"<td>{metric.clicks:,}</td>"
            "</tr>"
            for metric, post, account in top
        )
        or '<tr><td colspan="5">No collected post metrics yet.</td></tr>'
    )
    competitor_rows = (
        "".join(
            "<tr>"
            f"<td>{html.escape(profile.display_name or profile.handle)}</td>"
            f"<td>{html.escape(profile.platform.title())}</td>"
            f"<td>{metric.followers:,}</td><td>{metric.engagement_rate:.2f}%</td>"
            "</tr>"
            for profile, metric in report["competitors"][:20]
        )
        or '<tr><td colspan="4">No competitor snapshots in this period.</td></tr>'
    )
    document = f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(workspace_name)} report</title>
<style>body{{font:14px system-ui;color:#183129;max-width:1000px;margin:40px auto;padding:0 24px}}h1{{font-size:28px}}.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}.kpi{{padding:18px;background:#eef7f2;border-radius:10px}}.kpi b{{display:block;font-size:24px}}table{{width:100%;border-collapse:collapse;margin:18px 0 32px}}th,td{{padding:10px;border-bottom:1px solid #dce8e2;text-align:left}}small{{color:#657a72}}@media print{{body{{margin:0}}}}</style></head><body>
<small>FASTSOCIAL BRAND REPORT · LAST {report["days"]} DAYS</small><h1>{html.escape(workspace_name)}</h1>
<div class="kpis"><div class="kpi">Impressions<b>{totals["impressions"]:,}</b></div><div class="kpi">Reach<b>{totals["reach"]:,}</b></div><div class="kpi">Engagements<b>{totals["engagements"]:,}</b></div><div class="kpi">Clicks<b>{totals["clicks"]:,}</b></div></div>
<h2>Top content</h2><table><thead><tr><th>Network</th><th>Post</th><th>Impressions</th><th>Engagements</th><th>Clicks</th></tr></thead><tbody>{post_rows}</tbody></table>
<h2>Competitor context</h2><table><thead><tr><th>Competitor</th><th>Network</th><th>Followers</th><th>Engagement rate</th></tr></thead><tbody>{competitor_rows}</tbody></table>
<small>Generated {utcnow().strftime("%d %b %Y %H:%M UTC")} by FastSocial.</small></body></html>"""
    return document.encode()


async def execute_report_schedule(schedule_id: uuid.UUID) -> ReportRun:
    cfg = settings()
    with session_scope() as session:
        schedule = session.get(ReportSchedule, schedule_id)
        if not schedule:
            raise ValueError("Report schedule not found")
        workspace = session.get(Workspace, schedule.workspace_id)
        run = ReportRun(schedule_id=schedule.id, recipients=list(schedule.recipients))
        session.add(run)
        session.flush()
        report = report_summary(session, workspace.id, schedule.report_days)
        body = render_report_html(workspace.name, report)
        key = f"fastsocial/{workspace.id}/reports/{run.id}.html"
        try:
            media_storage().put(key, body, "text/html; charset=utf-8")
            run.storage_key = key
            run.status = "generated"
            if cfg.postmark_server_token and schedule.recipients:
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.post(
                        "https://api.postmarkapp.com/email",
                        headers={
                            "X-Postmark-Server-Token": cfg.postmark_server_token,
                            "Content-Type": "application/json",
                        },
                        json={
                            "From": cfg.report_from_email,
                            "To": ",".join(schedule.recipients),
                            "Subject": f"{workspace.name} · {schedule.name}",
                            "HtmlBody": body.decode(),
                            "MessageStream": "outbound",
                        },
                    )
                response.raise_for_status()
                run.status = "delivered"
            run.completed_at = utcnow()
            schedule.last_run_at = run.completed_at
            schedule.next_run_at = run.completed_at + timedelta(
                days=7 if schedule.frequency == "weekly" else 30
            )
        except Exception as exc:  # noqa: BLE001
            run.status = "failed"
            run.error_message = str(exc)
            run.completed_at = utcnow()
        return run


async def run_due_reports(limit: int = 10) -> int:
    with session_scope() as session:
        query = (
            select(ReportSchedule.id)
            .where(
                ReportSchedule.active.is_(True),
                ReportSchedule.next_run_at.is_not(None),
                ReportSchedule.next_run_at <= utcnow(),
            )
            .order_by(ReportSchedule.next_run_at)
            .limit(limit)
        )
        ids = list(session.scalars(query))
    for schedule_id in ids:
        await execute_report_schedule(schedule_id)
    return len(ids)
