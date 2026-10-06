"""Persist the decision context before exchange IO."""

import sqlalchemy as sa
from alembic import op

revision = "0008_order_context"
down_revision = "0007_testnet_recovery"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("testnet_orders", sa.Column("context", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("testnet_orders", "context")
