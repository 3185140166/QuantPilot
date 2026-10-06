"""Separate, durable testnet execution and shared risk budget."""

import sqlalchemy as sa
from alembic import op

revision = "0005_testnet_trading"
down_revision = "0004_optimization"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "testnet_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("flatten_requested", sa.Boolean(), nullable=False),
        sa.Column("baseline_equity", sa.Numeric(28, 10), nullable=True),
        sa.Column("equity", sa.Numeric(28, 10), nullable=False),
        sa.Column("peak_equity", sa.Numeric(28, 10), nullable=False),
        sa.Column("day", sa.String(10), nullable=True),
        sa.Column("day_equity", sa.Numeric(28, 10), nullable=False),
        sa.Column("risk_halted", sa.Boolean(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("last_bars", sa.JSON(), nullable=False),
        sa.Column("last_error", sa.String(240), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "testnet_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("link_id", sa.String(36), nullable=False),
        sa.Column("symbol", sa.String(24), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("action", sa.String(8), nullable=False),
        sa.Column("quantity", sa.Numeric(28, 10), nullable=False),
        sa.Column("stop_loss", sa.Numeric(28, 10), nullable=True),
        sa.Column("take_profit", sa.Numeric(28, 10), nullable=True),
        sa.Column("bar_time", sa.String(80), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("order_id", sa.String(80), nullable=True),
        sa.Column("filled_quantity", sa.Numeric(28, 10), nullable=False),
        sa.Column("position_closed", sa.Boolean(), nullable=False),
        sa.Column("average_price", sa.Numeric(28, 10), nullable=True),
        sa.Column("fee", sa.Numeric(28, 10), nullable=False),
        sa.Column("error", sa.String(240), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("link_id"),
        sa.UniqueConstraint("symbol", "bar_time", "action"),
    )
    op.create_table(
        "testnet_positions",
        sa.Column("symbol", sa.String(24), primary_key=True),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("quantity", sa.Numeric(28, 10), nullable=False),
        sa.Column("entry_price", sa.Numeric(28, 10), nullable=False),
        sa.Column("mark_price", sa.Numeric(28, 10), nullable=False),
        sa.Column("stop_loss", sa.Numeric(28, 10), nullable=False),
        sa.Column("opening_link_id", sa.String(36), nullable=False),
    )


def downgrade():
    op.drop_table("testnet_positions")
    op.drop_table("testnet_orders")
    op.drop_table("testnet_accounts")
