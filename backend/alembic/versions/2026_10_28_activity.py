"""Add private, short-lived answer activity; audit history is unchanged."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a48c0b7e21de"
down_revision: str | Sequence[str] | None = "19fae5ec16aa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "answer_activity",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("steps", postgresql.JSONB(), nullable=False),
        sa.Column("exchange_id", sa.Uuid(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["exchange_id"], ["exchanges.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_answer_activity_user_created", "answer_activity", ["user_id", "created_at"])
    op.create_index("ix_answer_activity_created", "answer_activity", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_answer_activity_created", table_name="answer_activity")
    op.drop_index("ix_answer_activity_user_created", table_name="answer_activity")
    op.drop_table("answer_activity")
