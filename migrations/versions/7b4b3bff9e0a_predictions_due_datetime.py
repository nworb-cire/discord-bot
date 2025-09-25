"""Store predictions due time as local datetime

Revision ID: 7b4b3bff9e0a
Revises: f21802b4bbf0
Create Date: 2025-09-24 06:10:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7b4b3bff9e0a"
down_revision: Union[str, Sequence[str], None] = "78781a5c30bd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.add_column(
        "predictions",
        sa.Column("due_at", sa.DateTime(timezone=False), nullable=True),
    )
    op.execute("UPDATE predictions SET due_at = due_date::timestamp")
    op.alter_column("predictions", "due_at", nullable=False)
    op.drop_column("predictions", "due_date")


def downgrade() -> None:
    """Downgrade schema."""

    op.add_column(
        "predictions",
        sa.Column("due_date", sa.Date(), nullable=True),
    )
    op.execute("UPDATE predictions SET due_date = due_at::date")
    op.alter_column("predictions", "due_date", nullable=False)
    op.drop_column("predictions", "due_at")
