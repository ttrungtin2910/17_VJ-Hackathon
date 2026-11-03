"""Hồ sơ retrieval ngữ nghĩa cho garment."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261103_0008"
down_revision = "20261103_0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "garments",
        sa.Column("retrieval_json", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=True),
    )
    op.add_column("recommendation_sessions", sa.Column("request_text", sa.Text(), nullable=True))
    op.add_column("recommendation_sessions", sa.Column("retrieval_query", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("recommendation_sessions", "retrieval_query")
    op.drop_column("recommendation_sessions", "request_text")
    op.drop_column("garments", "retrieval_json")
