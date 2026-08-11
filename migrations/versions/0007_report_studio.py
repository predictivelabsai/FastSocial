"""Report Studio narratives and data connectors.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

from alembic import op

from fastsocial import models

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


TABLES = (
    models.ReportNarrative.__table__,
    models.ReportConnector.__table__,
)


def upgrade() -> None:
    bind = op.get_bind()
    for table in TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(TABLES):
        table.drop(bind, checkfirst=True)
