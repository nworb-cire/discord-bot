"""Add Goodreads ratings to books.

Revision ID: 5c2a9ee479ad
Revises: b4fd8c2ef7a1
Create Date: 2026-08-23 11:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5c2a9ee479ad"
down_revision: Union[str, Sequence[str], None] = "b4fd8c2ef7a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "books",
        sa.Column("goodreads_rating", sa.Numeric(precision=3, scale=2), nullable=True),
    )
    op.add_column(
        "books", sa.Column("goodreads_rating_count", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("books", "goodreads_rating_count")
    op.drop_column("books", "goodreads_rating")
