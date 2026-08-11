"""Normalized audience analytics.

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations

from alembic import op

from fastsocial import models

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    models.AudienceMetricDaily.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    models.AudienceMetricDaily.__table__.drop(op.get_bind(), checkfirst=True)
