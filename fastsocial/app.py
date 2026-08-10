from __future__ import annotations

import logging

from fasthtml.common import fast_app
from starlette.responses import JSONResponse

from fastsocial.config import settings
from fastsocial.db import init_db
from fastsocial.scheduler import start_scheduler
from fastsocial.storage import media_storage

log = logging.getLogger(__name__)

app, rt = fast_app(
    live=False,
    pico=False,
    static_path=".",
    secret_key=settings().app_secret,
    htmx=True,
)
# FastHTML registers its broad ``/{name}.{extension}`` matcher first. Keep
# explicit application routes ahead of it so dynamic discovery files win.
static_route = app.routes.pop(0)


@rt("/healthz")
def healthz():
    storage = media_storage().health()
    return JSONResponse(
        {
            "status": "ok" if storage.get("ok") else "degraded",
            "service": "fastsocial",
            "storage": storage,
        }
    )


if settings().auto_create_schema:
    init_db()

from fastsocial import routes as _routes  # noqa: E402,F401

app.routes.append(static_route)

start_scheduler()
