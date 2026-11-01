"""Operation upload bền vững, additive, không sửa asset/garment hiện có."""

import sqlalchemy as sa
from alembic import op

revision = "20261101_0005"
down_revision = "20261031_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "upload_operations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("request_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reserved_asset_id", sa.String(36), unique=True, nullable=False),
        sa.Column("object_key", sa.String(512), unique=True, nullable=False),
        sa.Column("asset_id", sa.String(36), sa.ForeignKey("media_assets.id", ondelete="RESTRICT")),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("user_id", "request_key", name="uq_upload_user_key"),
    )
    op.create_index("ix_upload_operations_user_id", "upload_operations", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_upload_operations_user_id", table_name="upload_operations")
    op.drop_table("upload_operations")
