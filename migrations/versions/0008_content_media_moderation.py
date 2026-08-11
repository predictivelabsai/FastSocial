"""Content library, managed media sources, and Inbox moderation.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

from alembic import op

from fastsocial import models

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


TABLES = (
    models.MediaSourceConnection.__table__,
    models.ContentTemplate.__table__,
    models.InboxConversationTag.__table__,
    models.InboxModerationAction.__table__,
)


def upgrade() -> None:
    bind = op.get_bind()
    for table in TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(TABLES):
        table.drop(bind, checkfirst=True)
