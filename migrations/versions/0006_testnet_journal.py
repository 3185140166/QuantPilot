"""Durable executions and transfer-adjusted Testnet accounting."""

import sqlalchemy as sa
from alembic import op

revision = "0006_testnet_journal"
down_revision = "0005_testnet_trading"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "testnet_orders",
        sa.Column("order_type", sa.String(12), nullable=False, server_default="Market"),
    )
    op.add_column(
        "testnet_orders", sa.Column("limit_price", sa.Numeric(28, 10), nullable=True)
    )
    op.add_column(
        "testnet_orders", sa.Column("opening_link_id", sa.String(36), nullable=True)
    )
    op.add_column(
        "testnet_orders",
        sa.Column(
            "cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "testnet_accounts",
        sa.Column("journal_start_ms", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "testnet_accounts",
        sa.Column("journal_cursor_ms", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "testnet_accounts",
        sa.Column("net_flows", sa.Numeric(28, 10), nullable=False, server_default="0"),
    )
    op.add_column(
        "testnet_accounts", sa.Column("ledger_warning", sa.String(240), nullable=True)
    )
    op.add_column(
        "testnet_accounts",
        sa.Column("baseline_cash", sa.Numeric(28, 10), nullable=True),
    )
    op.create_table(
        "testnet_executions",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("order_id", sa.String(100), nullable=False),
        sa.Column("link_id", sa.String(36), nullable=False),
        sa.Column("symbol", sa.String(24), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("exec_type", sa.String(40), nullable=False),
        sa.Column("quantity", sa.Numeric(28, 10), nullable=False),
        sa.Column("price", sa.Numeric(28, 10), nullable=False),
        sa.Column("fee", sa.Numeric(28, 10), nullable=False),
        sa.Column("closed_size", sa.Numeric(28, 10), nullable=False),
        sa.Column("at_ms", sa.BigInteger(), nullable=False),
        sa.Column("exit_reason", sa.String(60), nullable=True),
        sa.Column("owned", sa.Boolean(), nullable=False),
        sa.Column("opening_link_id", sa.String(36), nullable=True),
    )
    op.create_index(
        "ix_testnet_executions_order_id", "testnet_executions", ["order_id"]
    )
    op.create_index("ix_testnet_executions_at_ms", "testnet_executions", ["at_ms"])
    op.create_table(
        "testnet_ledger",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("kind", sa.String(60), nullable=False),
        sa.Column("symbol", sa.String(24), nullable=False),
        sa.Column("order_id", sa.String(100), nullable=False),
        sa.Column("change", sa.Numeric(28, 10), nullable=False),
        sa.Column("cash_flow", sa.Numeric(28, 10), nullable=False),
        sa.Column("fee", sa.Numeric(28, 10), nullable=False),
        sa.Column("funding", sa.Numeric(28, 10), nullable=False),
        sa.Column("at_ms", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_testnet_ledger_at_ms", "testnet_ledger", ["at_ms"])


def downgrade():
    op.drop_column("testnet_orders", "limit_price")
    op.drop_column("testnet_orders", "order_type")
    op.drop_column("testnet_orders", "opening_link_id")
    op.drop_column("testnet_orders", "cancel_requested")
    op.drop_table("testnet_ledger")
    op.drop_table("testnet_executions")
    for name in (
        "baseline_cash",
        "ledger_warning",
        "net_flows",
        "journal_cursor_ms",
        "journal_start_ms",
    ):
        op.drop_column("testnet_accounts", name)
