"""add bitrix_item_id to orders

Revision ID: 20260422_01
Revises:
Create Date: 2026-04-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260422_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("bitrix_item_id", sa.Integer(), nullable=True))
    op.create_index(
        "ix_orders_bitrix_item_id", "orders", ["bitrix_item_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_orders_bitrix_item_id", table_name="orders")
    op.drop_column("orders", "bitrix_item_id")

