from __future__ import annotations

from bs4 import BeautifulSoup
from sqlalchemy import select
from starlette.testclient import TestClient

from fastsocial.app import app
from fastsocial.db import session_scope
from fastsocial.models import AutomationToken, Post, User
from fastsocial.services import workspace_for_user


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
            "name": "Automation User",
            "email": email,
            "password": "local-test-password",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def _create_token(client: TestClient, name: str = "Agent token") -> tuple[str, str]:
    integrations = client.get("/integrations")
    response = client.post(
        "/integrations/automation",
        data={
            "csrf": _csrf(integrations),
            "name": name,
            "scopes": ["posts:read", "posts:write", "analytics:read"],
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    reveal = client.get(response.headers["location"])
    markup = BeautifulSoup(reveal.text, "html.parser")
    token = markup.select_one(".automation-token-value")
    assert token is not None
    token_value = token.get_text(strip=True)
    assert token_value.startswith("fs_")
    with session_scope() as session:
        row = session.scalar(select(AutomationToken).where(AutomationToken.name == name))
        assert row is not None
        assert token_value not in row.token_hash
        return token_value, str(row.id)


def test_scoped_rest_and_latest_stateless_mcp_workflows_are_tenant_safe():
    with TestClient(app) as client:
        email = "automation-api@example.com"
        _register(client, email)
        token, token_id = _create_token(client)
        auth = {"authorization": f"Bearer {token}"}

        assert client.get("/api/v1/posts").status_code == 401
        created = client.post(
            "/api/v1/posts",
            headers=auth,
            json={"text": "A draft created through the automation API", "mode": "draft"},
        )
        assert created.status_code == 201
        assert created.json()["status"] == "draft"
        listed = client.get("/api/v1/posts", headers=auth)
        assert listed.status_code == 200
        assert listed.json()["posts"][0]["text"] == "A draft created through the automation API"
        analytics = client.get("/api/v1/analytics?days=30", headers=auth)
        assert analytics.status_code == 200
        assert analytics.json()["workspace"]

        discovery = client.post(
            "/mcp",
            headers={
                **auth,
                "MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": "server/discover",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {}},
        )
        assert discovery.status_code == 200
        assert discovery.headers["mcp-protocol-version"] == "2026-07-28"
        assert discovery.json()["result"]["protocolVersion"] == "2026-07-28"

        tools = client.post(
            "/mcp",
            headers={
                **auth,
                "MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": "tools/list",
            },
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert tools.status_code == 200
        assert tools.json()["result"]["cacheScope"] == "private"
        assert {item["name"] for item in tools.json()["result"]["tools"]} == {
            "list_posts",
            "create_draft",
            "schedule_post",
            "analytics_summary",
        }

        tool_call = client.post(
            "/mcp",
            headers={
                **auth,
                "MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": "tools/call",
                "Mcp-Name": "create_draft",
            },
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "create_draft",
                    "arguments": {"text": "A draft created through MCP"},
                },
                "_meta": {
                    "io.modelcontextprotocol/clientInfo": {
                        "name": "FastSocial test",
                        "version": "1.0",
                    }
                },
            },
        )
        assert tool_call.status_code == 200
        assert tool_call.json()["result"]["structuredContent"]["status"] == "draft"

        with session_scope() as session:
            user = session.scalar(select(User).where(User.email == email))
            workspace = workspace_for_user(session, user.id)
            posts = list(session.scalars(select(Post).where(Post.workspace_id == workspace.id)))
            assert workspace.id
            assert posts

        integrations = client.get("/integrations")
        revoked = client.post(
            f"/integrations/automation/{token_id}/revoke",
            data={"csrf": _csrf(integrations)},
            follow_redirects=False,
        )
        assert revoked.status_code == 303
        assert client.get("/api/v1/posts", headers=auth).status_code == 401
