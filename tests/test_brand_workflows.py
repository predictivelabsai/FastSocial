from __future__ import annotations

import base64
import uuid

from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from starlette.testclient import TestClient

from fastsocial.app import app
from fastsocial.db import session_scope
from fastsocial.models import Post, PostMedia, User, Workspace
from fastsocial.services import create_post, store_media, workspace_for_user


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
            "name": "Brand Operator",
            "email": email,
            "password": "local-test-password",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_brand_switching_and_cross_brand_repurposing_copy_media():
    email = f"brands-{uuid.uuid4()}@example.com"
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    with TestClient(app) as client:
        _register(client, email)
        with session_scope() as session:
            user = session.scalar(select(User).where(User.email == email))
            personal = workspace_for_user(session, user.id)
            media = store_media(
                session,
                workspace_id=personal.id,
                user_id=user.id,
                filename="brand-source.png",
                mime_type="image/png",
                body=png,
            )
            source = create_post(
                session,
                workspace=personal,
                user_id=user.id,
                text="A reusable launch lesson for every brand.",
                target_ids=[],
                media_ids=[media.id],
                save_draft=True,
            )
            personal_id, source_id = personal.id, source.id

        brands = client.get("/brands")
        assert brands.status_code == 200
        assert "One operating system for every brand" in brands.text
        created = client.post(
            "/brands",
            data={
                "csrf": _csrf(brands),
                "name": "Acme Europe",
                "timezone": "Europe/Tallinn",
            },
            follow_redirects=False,
        )
        assert created.status_code == 303
        with session_scope() as session:
            destination = session.scalar(select(Workspace).where(Workspace.name == "Acme Europe"))
            destination_id = destination.id

        active = client.get("/brands")
        assert "Acme Europe" in active.text
        assert 'action="/brands/' in active.text
        switched_back = client.post(
            f"/brands/{personal_id}/switch",
            data={"csrf": _csrf(active), "next": "/posts"},
            follow_redirects=False,
        )
        assert switched_back.status_code == 303
        assert switched_back.headers["location"] == "/posts"

        source_page = client.get(f"/posts/{source_id}")
        assert source_page.status_code == 200
        assert "Repurpose across brands" in source_page.text
        repurposed = client.post(
            f"/posts/{source_id}/repurpose",
            data={
                "csrf": _csrf(source_page),
                "destination_ids": str(destination_id),
                "include_media": "on",
            },
            follow_redirects=False,
        )
        assert repurposed.status_code == 303
        assert "saved=repurposed" in repurposed.headers["location"]

        with session_scope() as session:
            clone = session.scalar(
                select(Post)
                .where(Post.workspace_id == destination_id)
                .options(selectinload(Post.media_links).selectinload(PostMedia.media))
            )
            assert clone.content["text"] == "A reusable launch lesson for every brand."
            assert clone.content["repurposed_from"]["post_id"] == str(source_id)
            assert clone.status.value == "draft"
            assert len(clone.targets) == 0
            assert len(clone.media_links) == 1
            assert clone.media_links[0].media.workspace_id == destination_id
            assert clone.media_links[0].media.storage_key != media.storage_key

        personal_page = client.get("/brands")
        switched_destination = client.post(
            f"/brands/{destination_id}/switch",
            data={"csrf": _csrf(personal_page), "next": "https://example.com/escape"},
            follow_redirects=False,
        )
        assert switched_destination.status_code == 303
        assert switched_destination.headers["location"] == "/"
        assert "A reusable launch lesson" in client.get("/posts").text
        assert client.get(f"/posts/{source_id}").status_code == 404

        with TestClient(app) as outsider:
            _register(outsider, f"brand-outsider-{uuid.uuid4()}@example.com")
            outsider_brands = outsider.get("/brands")
            blocked = outsider.post(
                f"/brands/{destination_id}/switch",
                data={"csrf": _csrf(outsider_brands), "next": "/"},
                follow_redirects=False,
            )
            assert blocked.status_code == 404
