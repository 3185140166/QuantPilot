"""Persist exchange-specific personal watchlists."""

import sqlalchemy as sa
from alembic import op

revision = "0002_watchlist"
down_revision = "0001_platform"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "watchlist",
        sa.Column("exchange", sa.String(16), primary_key=True),
        sa.Column("symbol", sa.String(24), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("watchlist")
