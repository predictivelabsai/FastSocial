"""Metricool-parity foundations for competitors, inbox, reports, and SmartLinks.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

from alembic import op

from fastsocial import models

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


NEW_TABLES = (
    models.CompetitorProfile.__table__,
    models.CompetitorMetricDaily.__table__,
    models.InboxConversation.__table__,
    models.InboxMessage.__table__,
    models.ReportSchedule.__table__,
    models.SmartLinkPage.__table__,
    models.SmartLinkItem.__table__,
)


def upgrade() -> None:
    bind = op.get_bind()
    for table in NEW_TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(NEW_TABLES):
        table.drop(bind, checkfirst=True)
