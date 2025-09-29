"""Add ballot_message_id to elections

Revision ID: 8e1130666f00
Revises: 7b4b3bff9e0a
Create Date: 2025-07-09 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "8e1130666f00"
down_revision: Union[str, Sequence[str], None] = "7b4b3bff9e0a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "elections",
        sa.Column("ballot_message_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "elections",
        sa.Column("vote_reaction_frozen", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("elections", "vote_reaction_frozen", server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("elections", "vote_reaction_frozen")
    op.drop_column("elections", "ballot_message_id")
