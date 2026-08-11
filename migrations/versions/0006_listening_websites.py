"""Listening, hashtags, and first-party website analytics.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

from alembic import op

from fastsocial import models

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


TABLES = (
    models.ListeningQuery.__table__,
    models.ListeningMention.__table__,
    models.WebsiteSite.__table__,
    models.WebsiteEvent.__table__,
)


def upgrade() -> None:
    bind = op.get_bind()
    for table in TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(TABLES):
        table.drop(bind, checkfirst=True)
