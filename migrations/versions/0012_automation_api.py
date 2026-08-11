"""Scoped automation API and MCP tokens.

Revision ID: 0012
Revises: 0011
"""

from __future__ import annotations

from alembic import op

from fastsocial import models

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    models.AutomationToken.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    models.AutomationToken.__table__.drop(op.get_bind(), checkfirst=True)
