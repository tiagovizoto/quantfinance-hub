"""histórico de patrimônio (net_worth_snapshots)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-16 10:30:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "net_worth_snapshots",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("net_worth", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("liquidity", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("investments", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("loans", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("card_debt", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("day", name=op.f("pk_net_worth_snapshots")),
    )


def downgrade() -> None:
    op.drop_table("net_worth_snapshots")
