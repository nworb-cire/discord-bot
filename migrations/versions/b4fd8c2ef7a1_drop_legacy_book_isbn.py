"""Drop legacy book ISBN

Revision ID: b4fd8c2ef7a1
Revises: e3f1a8d9b2c4
Create Date: 2026-05-10 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b4fd8c2ef7a1"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "e3f1a8d9b2c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column("books", "isbn")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("books", sa.Column("isbn", sa.String(length=13), nullable=True))
    op.execute(
        """
        UPDATE books
        SET isbn = COALESCE(isbn_13, isbn_10)
        WHERE isbn_13 IS NOT NULL OR isbn_10 IS NOT NULL
        """
    )
    op.create_unique_constraint(None, "books", ["isbn"])
