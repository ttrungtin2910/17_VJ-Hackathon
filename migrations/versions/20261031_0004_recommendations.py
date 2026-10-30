"""Phiên phối đồ xác định, item snapshot và latest feedback riêng, không dùng Look.

Revision ID: 20261031_0004
Revises: 20261030_0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261031_0004"
down_revision = "20261030_0003"
branch_labels = None
depends_on = None
JsonType = sa.JSON().with_variant(JSONB(), "postgresql")


def timestamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "recommendation_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        *timestamps(),
        sa.Column(
            "user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("context_json", JsonType, nullable=False),
        sa.Column("location", sa.String(200)),
        sa.Column("engine_version", sa.String(40), nullable=False),
        sa.Column("weights_snapshot", JsonType, nullable=False),
        sa.Column("reason", sa.String(240)),
        sa.Column("missing_categories", JsonType, nullable=False),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_recommendation_user_key"),
    )
    op.create_table(
        "recommended_outfits",
        sa.Column("id", sa.String(36), primary_key=True),
        *timestamps(),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("recommendation_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("score", sa.Numeric(8, 3), nullable=False),
        sa.Column("breakdown", JsonType, nullable=False),
        sa.Column("explanation", JsonType, nullable=False),
        sa.Column("features_snapshot", JsonType, nullable=False),
        sa.Column("creation_features", JsonType, nullable=False),
        sa.UniqueConstraint("session_id", "rank", name="uq_recommended_outfit_rank"),
    )
    op.create_table(
        "recommended_items",
        sa.Column("id", sa.String(36), primary_key=True),
        *timestamps(),
        sa.Column(
            "outfit_id",
            sa.String(36),
            sa.ForeignKey("recommended_outfits.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "garment_id",
            sa.String(36),
            sa.ForeignKey("garments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("role", sa.String(40), nullable=False),
        sa.Column("snapshot", JsonType, nullable=False),
        sa.UniqueConstraint("outfit_id", "garment_id", name="uq_recommended_item_garment"),
        sa.UniqueConstraint("outfit_id", "role", name="uq_recommended_item_role"),
    )
    op.create_table(
        "recommendation_feedback",
        sa.Column("id", sa.String(36), primary_key=True),
        *timestamps(),
        sa.Column(
            "user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "outfit_id",
            sa.String(36),
            sa.ForeignKey("recommended_outfits.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("signal", sa.String(10), nullable=False),
        sa.Column("features_snapshot", JsonType, nullable=False),
        sa.UniqueConstraint("user_id", "outfit_id", name="uq_recommendation_feedback_user_outfit"),
    )
    for table, columns in (
        ("recommendation_sessions", ("user_id",)),
        ("recommended_outfits", ("session_id",)),
        ("recommended_items", ("outfit_id", "garment_id")),
        ("recommendation_feedback", ("user_id", "outfit_id")),
    ):
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    for table in (
        "recommendation_feedback",
        "recommended_items",
        "recommended_outfits",
        "recommendation_sessions",
    ):
        op.drop_table(table)
