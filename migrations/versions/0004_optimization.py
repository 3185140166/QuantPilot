"""Durable parameter optimization jobs and progress."""

import sqlalchemy as sa
from alembic import op

revision = "0004_optimization"
down_revision = "0003_strategy_research"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "optimization_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("completed_trials", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_optimization_jobs_status", "optimization_jobs", ["status"])


def downgrade():
    op.drop_table("optimization_jobs")
