"""Competitor favorites and top-content intelligence.

Revision ID: 0011
Revises: 0010
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from fastsocial import models

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("competitor_profiles")}
    indexes = {item["name"] for item in inspector.get_indexes("competitor_profiles")}
    if "favorite" not in columns:
        op.add_column(
            "competitor_profiles",
            sa.Column("favorite", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if "ix_competitor_profiles_favorite" not in indexes:
        op.create_index(
            "ix_competitor_profiles_favorite",
            "competitor_profiles",
            ["favorite"],
            unique=False,
        )
    models.CompetitorPost.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    models.CompetitorPost.__table__.drop(op.get_bind(), checkfirst=True)
    op.drop_index("ix_competitor_profiles_favorite", table_name="competitor_profiles")
    op.drop_column("competitor_profiles", "favorite")
