"""Versioned rules, research provenance and marked paper equity."""

import sqlalchemy as sa
from alembic import op

revision = "0003_strategy_research"
down_revision = "0002_watchlist"
branch_labels = None
depends_on = None


def upgrade():
    # Add columns in place: SQLite table recreation would conflict with existing
    # positions/orders that reference strategies when foreign keys are enabled.
    for column in (
        sa.Column("rules", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("peak_equity", sa.Numeric(28, 10), nullable=True),
        sa.Column(
            "max_drawdown", sa.Numeric(18, 10), nullable=False, server_default="0"
        ),
        sa.Column(
            "risk_halted", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    ):
        op.add_column("strategies", column)
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "ALTER TABLE strategies ADD COLUMN origin_job_id INTEGER REFERENCES backtest_jobs(id)"
        )
    else:
        op.add_column(
            "strategies", sa.Column("origin_job_id", sa.Integer(), nullable=True)
        )
        op.create_foreign_key(
            "fk_strategy_origin_job",
            "strategies",
            "backtest_jobs",
            ["origin_job_id"],
            ["id"],
        )
    op.create_table(
        "strategy_equity",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=False
        ),
        sa.Column("bar_time", sa.String(40), nullable=False),
        sa.Column("equity", sa.Numeric(28, 10), nullable=False),
        sa.Column("drawdown", sa.Numeric(18, 10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("strategy_id", "bar_time"),
    )
    op.create_index(
        "ix_strategy_equity_strategy_id", "strategy_equity", ["strategy_id"]
    )


def downgrade():
    op.drop_table("strategy_equity")
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint("fk_strategy_origin_job", "strategies", type_="foreignkey")
    for name in (
        "rules",
        "origin_job_id",
        "peak_equity",
        "max_drawdown",
        "risk_halted",
    ):
        op.drop_column("strategies", name)
