"""Hàng đợi dọn ảnh gốc tạm thời."""

import sqlalchemy as sa
from alembic import op

revision = "20261103_0006"
down_revision = "20261101_0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "asset_cleanup",
        sa.Column(
            "asset_id",
            sa.String(36),
            sa.ForeignKey("media_assets.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_asset_cleanup_next_attempt_at", "asset_cleanup", ["next_attempt_at"])


def downgrade():
    op.drop_table("asset_cleanup")
