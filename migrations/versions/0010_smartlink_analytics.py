"""SmartLink rich items and event analytics.

Revision ID: 0010
Revises: 0009
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from fastsocial import models

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("smartlink_items")}
    if "item_type" not in columns:
        op.add_column(
            "smartlink_items",
            sa.Column("item_type", sa.String(length=30), nullable=False, server_default="link"),
        )
    if "description" not in columns:
        op.add_column(
            "smartlink_items",
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
        )
    if "media_url" not in columns:
        op.add_column(
            "smartlink_items",
            sa.Column("media_url", sa.Text(), nullable=False, server_default=""),
        )
    models.SmartLinkEvent.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    models.SmartLinkEvent.__table__.drop(op.get_bind(), checkfirst=True)
    op.drop_column("smartlink_items", "media_url")
    op.drop_column("smartlink_items", "description")
    op.drop_column("smartlink_items", "item_type")
