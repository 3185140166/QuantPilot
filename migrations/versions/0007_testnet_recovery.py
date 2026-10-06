"""Execution ordering, retired ownership and research provenance."""

import sqlalchemy as sa
from alembic import op

revision = "0007_testnet_recovery"
down_revision = "0006_testnet_journal"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "testnet_accounts", sa.Column("research_origin", sa.JSON(), nullable=True)
    )
    op.add_column(
        "testnet_orders", sa.Column("closed_at_ms", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "testnet_executions", sa.Column("sequence", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "testnet_ledger", sa.Column("trade_id", sa.String(100), nullable=True)
    )


def downgrade():
    for table, name in (
        ("testnet_ledger", "trade_id"),
        ("testnet_executions", "sequence"),
        ("testnet_orders", "closed_at_ms"),
        ("testnet_accounts", "research_origin"),
    ):
        op.drop_column(table, name)
