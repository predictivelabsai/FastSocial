"""Live provider collection runs.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

from alembic import op

from fastsocial import models

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    models.CollectionRun.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    models.CollectionRun.__table__.drop(op.get_bind(), checkfirst=True)
