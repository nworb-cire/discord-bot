"""Add structured book metadata

Revision ID: e3f1a8d9b2c4
Revises: 8e1130666f00
Create Date: 2026-05-10 08:45:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e3f1a8d9b2c4"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "8e1130666f00"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("books", sa.Column("isbn_10", sa.String(length=10), nullable=True))
    op.add_column("books", sa.Column("isbn_13", sa.String(length=13), nullable=True))
    op.add_column("books", sa.Column("authors", sa.JSON(), nullable=True))
    op.add_column("books", sa.Column("primary_author", sa.Text(), nullable=True))

    op.execute(
        """
        UPDATE books
        SET isbn_10 = isbn
        WHERE isbn IS NOT NULL AND length(isbn) = 10
        """
    )
    op.execute(
        """
        UPDATE books
        SET isbn_13 = isbn
        WHERE isbn IS NOT NULL AND length(isbn) = 13
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("books", "primary_author")
    op.drop_column("books", "authors")
    op.drop_column("books", "isbn_13")
    op.drop_column("books", "isbn_10")
