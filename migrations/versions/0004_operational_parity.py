"""Operational planner, inbox, ads, and report automation.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from fastsocial import models

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


NEW_TABLES = (
    models.SavedReply.__table__,
    models.ReportRun.__table__,
    models.ContentAutolist.__table__,
    models.AutolistItem.__table__,
    models.AdCampaignDaily.__table__,
)


def upgrade() -> None:
    op.add_column(
        "inbox_conversations",
        sa.Column("priority", sa.String(length=20), nullable=False, server_default="normal"),
    )
    op.add_column(
        "inbox_conversations",
        sa.Column("assigned_to", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "ix_inbox_conversations_priority", "inbox_conversations", ["priority"], unique=False
    )
    op.create_index(
        "ix_inbox_conversations_assigned_to",
        "inbox_conversations",
        ["assigned_to"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_inbox_conversations_assigned_to_users",
        "inbox_conversations",
        "users",
        ["assigned_to"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "inbox_messages",
        sa.Column(
            "delivery_status", sa.String(length=40), nullable=False, server_default="received"
        ),
    )
    op.add_column(
        "inbox_messages", sa.Column("error_message", sa.Text(), nullable=False, server_default="")
    )
    op.create_index(
        "ix_inbox_messages_delivery_status",
        "inbox_messages",
        ["delivery_status"],
        unique=False,
    )
    op.add_column(
        "report_schedules",
        sa.Column("report_days", sa.Integer(), nullable=False, server_default="30"),
    )
    op.add_column(
        "report_schedules",
        sa.Column("output_format", sa.String(length=20), nullable=False, server_default="html"),
    )
    bind = op.get_bind()
    for table in NEW_TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(NEW_TABLES):
        table.drop(bind, checkfirst=True)
    op.drop_column("report_schedules", "output_format")
    op.drop_column("report_schedules", "report_days")
    op.drop_index("ix_inbox_messages_delivery_status", table_name="inbox_messages")
    op.drop_column("inbox_messages", "error_message")
    op.drop_column("inbox_messages", "delivery_status")
    op.drop_constraint(
        "fk_inbox_conversations_assigned_to_users",
        "inbox_conversations",
        type_="foreignkey",
    )
    op.drop_index("ix_inbox_conversations_assigned_to", table_name="inbox_conversations")
    op.drop_index("ix_inbox_conversations_priority", table_name="inbox_conversations")
    op.drop_column("inbox_conversations", "assigned_to")
    op.drop_column("inbox_conversations", "priority")
