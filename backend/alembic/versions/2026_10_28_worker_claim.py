"""Fence duplicate background deliveries without storing request bodies."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1374e0a9c82"
down_revision: str | Sequence[str] | None = "a48c0b7e21de"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "answer_activity", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("answer_activity", "claimed_at")
