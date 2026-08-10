"""Deadline engine tables.

Revision ID: 003
Revises: 002
Create Date: 2026-06-01
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scheduled_deadlines",
        sa.Column("deadline_id", sa.String(), primary_key=True),
        sa.Column("matter_id", sa.String(), nullable=False),
        sa.Column("rule_ref", JSONB(), nullable=False),
        sa.Column("trace", JSONB(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False, unique=True),
        sa.Column("flagged_past_due_on_create", sa.Boolean(), nullable=False,
            server_default="false"),
        sa.Column("escalation_level", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "scheduled_reminders",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("deadline_id", sa.String(), sa.ForeignKey("scheduled_deadlines.deadline_id"),
            nullable=False),
        sa.Column("fire_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("offset_data", JSONB(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="armed"),
        sa.Column("idempotency_key", sa.String(), nullable=False, unique=True),
        sa.Column("redundant_arm_ids", JSONB(), nullable=False, server_default="[]"),
    )
    op.create_table(
        "rule_version_registry",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("rule_id", sa.String(), nullable=False),
        sa.Column("jurisdiction", sa.String(), nullable=False),
        sa.Column("rule_version", sa.String(16), nullable=False),
        sa.Column("yaml_blob", sa.Text(), nullable=False),
        sa.Column("loaded_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("rule_version_registry")
    op.drop_table("scheduled_reminders")
    op.drop_table("scheduled_deadlines")
