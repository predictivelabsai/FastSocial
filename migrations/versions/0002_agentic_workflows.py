"""Agentic creation, skills, model profiles, and media provenance.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from fastsocial import models

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


NEW_TABLES = (
    models.AIProviderCredential.__table__,
    models.ModelProfile.__table__,
    models.ChatSession.__table__,
    models.ChatMessage.__table__,
    models.AgentEvent.__table__,
    models.ContentArtifact.__table__,
    models.SkillDefinition.__table__,
    models.WorkspaceSkillVersion.__table__,
    models.MediaGeneration.__table__,
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    workspace_columns = {item["name"] for item in inspector.get_columns("workspaces")}
    if "default_workflow_mode" not in workspace_columns:
        workflow_mode = sa.Enum("review", "yolo", name="workflowmode")
        workflow_mode.create(bind, checkfirst=True)
        op.add_column(
            "workspaces",
            sa.Column(
                "default_workflow_mode",
                workflow_mode,
                nullable=False,
                server_default="review",
            ),
        )
    if "default_model_provider" not in workspace_columns:
        op.add_column(
            "workspaces",
            sa.Column(
                "default_model_provider",
                sa.String(length=40),
                nullable=False,
                server_default="xai",
            ),
        )
    for table in NEW_TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(NEW_TABLES):
        table.drop(bind, checkfirst=True)
    inspector = sa.inspect(bind)
    workspace_columns = {item["name"] for item in inspector.get_columns("workspaces")}
    if "default_workflow_mode" in workspace_columns:
        op.drop_column("workspaces", "default_workflow_mode")
    if "default_model_provider" in workspace_columns:
        op.drop_column("workspaces", "default_model_provider")
    if bind.dialect.name == "postgresql":
        sa.Enum(name="workflowmode").drop(bind, checkfirst=True)
