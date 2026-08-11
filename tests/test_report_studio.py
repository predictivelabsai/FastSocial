from __future__ import annotations

import importlib
import re
from types import SimpleNamespace

from bs4 import BeautifulSoup
from sqlalchemy import select
from starlette.testclient import TestClient

from fastsocial.app import app
from fastsocial.db import session_scope
from fastsocial.models import ReportConnector, ReportNarrative, ReportRun, ReportSchedule, User
from fastsocial.services import workspace_for_user

routes = importlib.import_module("fastsocial.routes")


def _csrf(response) -> str:
    field = BeautifulSoup(response.text, "html.parser").select_one('input[name="csrf"]')
    assert field is not None
    return field["value"]


def _register(client: TestClient, email: str) -> None:
    page = client.get("/register")
    response = client.post(
        "/register",
        data={
            "csrf": _csrf(page),
            "name": "Report Owner",
            "email": email,
            "password": "local-test-password",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_native_report_exports_and_revocable_connector():
    with TestClient(app) as client:
        _register(client, "report-formats@example.com")
        reports = client.get("/reports")
        assert "AI Report Studio" in reports.text
        assert "Data connectors" in reports.text

        pdf = client.get("/reports/export.pdf")
        assert pdf.status_code == 200, pdf.text
        assert pdf.content.startswith(b"%PDF")
        pptx = client.get("/reports/export.pptx")
        assert pptx.status_code == 200
        assert pptx.content.startswith(b"PK")
        feed_export = client.get("/reports/export.json")
        assert feed_export.status_code == 200
        assert feed_export.json()["schema_version"] == "1.0"

        scheduled = client.post(
            "/reports/schedules",
            data={
                "csrf": _csrf(reports),
                "name": "PDF board brief",
                "frequency": "monthly",
                "report_days": "30",
                "output_format": "pdf",
                "recipients": "board@example.com",
                "sections": ["performance", "competitors"],
            },
            follow_redirects=False,
        )
        assert scheduled.status_code == 303
        with session_scope() as session:
            schedule = session.scalar(
                select(ReportSchedule).where(ReportSchedule.name == "PDF board brief")
            )
            schedule_id = schedule.id
        reports = client.get("/reports")
        run_response = client.post(
            f"/reports/schedules/{schedule_id}/run",
            data={"csrf": _csrf(reports)},
            follow_redirects=False,
        )
        assert run_response.status_code == 303
        with session_scope() as session:
            run = session.scalar(select(ReportRun).where(ReportRun.schedule_id == schedule_id))
            run_id = run.id
            assert run.storage_key.endswith(".pdf")
        stored_pdf = client.get(f"/reports/runs/{run_id}")
        assert stored_pdf.status_code == 200
        assert stored_pdf.content.startswith(b"%PDF")

        created = client.post(
            "/reports/connectors",
            data={"csrf": _csrf(reports), "name": "Looker Studio"},
            follow_redirects=False,
        )
        assert created.status_code == 303
        revealed = client.get(created.headers["location"])
        match = re.search(r"shown again: ([A-Za-z0-9_-]+)", revealed.text)
        assert match
        token = match.group(1)
        with session_scope() as session:
            connector = session.scalar(
                select(ReportConnector).where(ReportConnector.name == "Looker Studio")
            )
            connector_id = connector.id

        assert client.get(f"/api/connectors/{connector_id}/report?token=wrong").status_code == 401
        live_feed = client.get(f"/api/connectors/{connector_id}/report?token={token}&days=7")
        assert live_feed.status_code == 200
        assert live_feed.json()["period_days"] == 7
        reports = client.get("/reports")
        revoked = client.post(
            f"/reports/connectors/{connector_id}/revoke",
            data={"csrf": _csrf(reports)},
            follow_redirects=False,
        )
        assert revoked.status_code == 303
        assert client.get(f"/api/connectors/{connector_id}/report?token={token}").status_code == 401


def test_report_studio_grounds_and_persists_narrative(monkeypatch):
    async def fake_invoke(_resolved, *, system_prompt: str, user_prompt: str):
        assert "Do not invent numbers" in system_prompt
        assert "VERIFIED METRICS" in user_prompt
        return {
            "title": "Monthly growth brief",
            "executive_summary": "The workspace has sparse data and should collect more metrics.",
            "insights": ["No post metrics are available yet."],
            "recommendations": ["Connect a publishing account and collect a baseline."],
        }

    monkeypatch.setattr(routes, "invoke_json", fake_invoke)
    monkeypatch.setattr(
        routes,
        "resolve_model",
        lambda *_args, **_kwargs: SimpleNamespace(provider="xai", model_name="test-report-model"),
    )
    email = "report-studio@example.com"
    with TestClient(app) as client:
        _register(client, email)
        reports = client.get("/reports")
        generated = client.post(
            "/reports/studio",
            data={
                "csrf": _csrf(reports),
                "prompt": "What should I do next?",
                "provider": "xai",
                "report_days": "30",
            },
            follow_redirects=False,
        )
        assert generated.status_code == 303
        detail = client.get(generated.headers["location"])
        assert "Monthly growth brief" in detail.text
        assert "No post metrics are available yet." in detail.text

    with session_scope() as session:
        user = session.scalar(select(User).where(User.email == email))
        workspace = workspace_for_user(session, user.id)
        narrative = session.scalar(
            select(ReportNarrative).where(ReportNarrative.workspace_id == workspace.id)
        )
        assert narrative.model_name == "test-report-model"
        assert narrative.recommendations == ["Connect a publishing account and collect a baseline."]
