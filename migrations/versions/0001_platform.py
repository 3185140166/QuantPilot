"""Initial durable private platform schema."""

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0001_platform"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    money = sa.Numeric(28, 10)
    stamp = sa.DateTime(timezone=True)
    op.create_table(
        "strategies",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("exchange", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(24), nullable=False),
        sa.Column("interval", sa.String(8), nullable=False),
        *[
            sa.Column(name, money, nullable=False)
            for name in ["quantity", "initial_equity", "cash", "max_notional"]
        ],
        sa.Column("fee_rate", sa.Numeric(18, 10), nullable=False),
        sa.Column("slippage_bps", sa.Numeric(18, 10), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("signal", sa.String(8), nullable=False),
        sa.Column("last_bar", sa.String(40)),
        sa.Column("last_error", sa.String(160)),
        sa.Column("created_at", stamp, nullable=False),
    )
    op.create_table(
        "positions",
        sa.Column(
            "strategy_id", sa.Integer, sa.ForeignKey("strategies.id"), primary_key=True
        ),
        sa.Column("side", sa.String(8), nullable=False),
        *[
            sa.Column(name, money, nullable=False)
            for name in ["quantity", "entry_price", "mark_price"]
        ],
        sa.Column("opened_at", stamp, nullable=False),
    )
    op.create_table(
        "paper_orders",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "strategy_id", sa.Integer, sa.ForeignKey("strategies.id"), nullable=False
        ),
        sa.Column("symbol", sa.String(24), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("action", sa.String(8), nullable=False),
        *[
            sa.Column(name, money, nullable=False)
            for name in ["quantity", "price", "fee", "realized_pnl"]
        ],
        sa.Column("bar_time", sa.String(40), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("created_at", stamp, nullable=False),
        sa.UniqueConstraint("strategy_id", "bar_time", "action"),
    )
    op.create_index("ix_paper_orders_strategy_id", "paper_orders", ["strategy_id"])
    op.create_table(
        "events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("strategy_id", sa.Integer, sa.ForeignKey("strategies.id")),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("message", sa.String(240), nullable=False),
        sa.Column("created_at", stamp, nullable=False),
    )
    op.create_table(
        "worker_leases",
        sa.Column("name", sa.String(24), primary_key=True),
        sa.Column("owner", sa.String(40), nullable=False),
        sa.Column("expires_at", stamp, nullable=False),
        sa.Column("heartbeat_at", stamp, nullable=False),
    )
    op.bulk_insert(
        sa.table(
            "worker_leases",
            sa.column("name", sa.String),
            sa.column("owner", sa.String),
            sa.column("expires_at", stamp),
            sa.column("heartbeat_at", stamp),
        ),
        [
            {
                "name": name,
                "owner": "",
                "expires_at": datetime(2000, 1, 1, tzinfo=timezone.utc),
                "heartbeat_at": datetime(2000, 1, 1, tzinfo=timezone.utc),
            }
            for name in ["engine", "research"]
        ],
    )
    op.create_table(
        "backtest_jobs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("parameters", sa.JSON, nullable=False),
        sa.Column("result", sa.JSON),
        sa.Column("error", sa.String(160)),
        sa.Column("created_at", stamp, nullable=False),
        sa.Column("completed_at", stamp),
    )
    op.create_index("ix_backtest_jobs_status", "backtest_jobs", ["status"])


def downgrade():
    for name in [
        "backtest_jobs",
        "worker_leases",
        "events",
        "paper_orders",
        "positions",
        "strategies",
    ]:
        op.drop_table(name)
