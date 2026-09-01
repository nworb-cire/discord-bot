"""Add secret predictions.

Revision ID: a4c9d82e1f70
Revises: 5c2a9ee479ad
Create Date: 2026-09-01 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a4c9d82e1f70"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "5c2a9ee479ad"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "predictions",
        sa.Column("secret", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("predictions", "message_id", nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM predictions WHERE message_id IS NULL")
    op.alter_column("predictions", "message_id", nullable=False)
    op.drop_column("predictions", "secret")
