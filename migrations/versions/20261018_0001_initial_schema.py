"""create Garblo MVP schema

Revision ID: 20261018_0001
Revises:
Create Date: 2026-10-18
"""

from alembic import op
import sqlalchemy as sa


revision = "20261018_0001"
down_revision = None
branch_labels = None
depends_on = None


def identifiers() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "users",
        *identifiers(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("style_profile", sa.JSON(), nullable=False),
        sa.Column("social_opt_in", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_table(
        "consent_records",
        *identifiers(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("purpose", sa.String(length=80), nullable=False),
        sa.Column("policy_version", sa.String(length=80), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_consent_records_user_id", "consent_records", ["user_id"])
    op.create_index("ix_consent_records_purpose", "consent_records", ["purpose"])
    op.create_table(
        "media_assets",
        *identifiers(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("asset_type", sa.String(length=40), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.UniqueConstraint("object_key", name="uq_media_assets_object_key"),
    )
    op.create_index("ix_media_assets_user_id", "media_assets", ["user_id"])
    op.create_index("ix_media_assets_checksum", "media_assets", ["checksum"])
    op.create_table(
        "garments",
        *identifiers(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_asset_id", sa.String(length=36), sa.ForeignKey("media_assets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("category", sa.String(length=80)), sa.Column("color", sa.String(length=80)),
        sa.Column("material", sa.String(length=80)), sa.Column("season", sa.String(length=80)),
        sa.Column("tags", sa.JSON(), nullable=False), sa.Column("wear_count", sa.Integer(), nullable=False),
        sa.Column("last_worn_at", sa.DateTime(timezone=True)), sa.Column("catalog_status", sa.String(length=40), nullable=False),
    )
    op.create_index("ix_garments_user_id", "garments", ["user_id"])
    op.create_index("ix_garments_source_asset_id", "garments", ["source_asset_id"])
    op.create_table(
        "lookbooks",
        *identifiers(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False), sa.Column("theme", sa.String(length=120)),
        sa.Column("occasion", sa.String(length=120)), sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("cover_asset_id", sa.String(length=36), sa.ForeignKey("media_assets.id", ondelete="SET NULL")),
    )
    op.create_index("ix_lookbooks_user_id", "lookbooks", ["user_id"])
    op.create_table(
        "looks",
        *identifiers(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lookbook_id", sa.String(length=36), sa.ForeignKey("lookbooks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("generated_asset_id", sa.String(length=36), sa.ForeignKey("media_assets.id", ondelete="SET NULL")),
        sa.Column("generation_prompt", sa.Text(), nullable=False), sa.Column("model_reference", sa.String(length=160)),
        sa.Column("cmax_score", sa.Numeric(5, 2)), sa.Column("status", sa.String(length=40), nullable=False),
    )
    op.create_index("ix_looks_lookbook_id", "looks", ["lookbook_id"])
    op.create_table(
        "look_items",
        *identifiers(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("look_id", sa.String(length=36), sa.ForeignKey("looks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("garment_id", sa.String(length=36), sa.ForeignKey("garments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("role", sa.String(length=80)), sa.Column("layer_order", sa.Integer(), nullable=False),
        sa.UniqueConstraint("look_id", "garment_id", name="uq_look_items_look_garment"),
    )
    op.create_index("ix_look_items_look_id", "look_items", ["look_id"])
    op.create_index("ix_look_items_garment_id", "look_items", ["garment_id"])
    op.create_table(
        "ai_jobs",
        *identifiers(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_type", sa.String(length=40), nullable=False), sa.Column("input_ref", sa.JSON(), nullable=False),
        sa.Column("output_ref", sa.JSON(), nullable=False), sa.Column("model_provider", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False), sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=80)), sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_ai_jobs_user_id", "ai_jobs", ["user_id"])
    op.create_index("ix_ai_jobs_job_type", "ai_jobs", ["job_type"])
    op.create_index("ix_ai_jobs_status", "ai_jobs", ["status"])
    op.create_table(
        "approval_requests",
        *identifiers(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_type", sa.String(length=40), nullable=False), sa.Column("subject_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False), sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("decision_payload", sa.JSON(), nullable=False), sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_approval_requests_user_id", "approval_requests", ["user_id"])
    op.create_index("ix_approval_requests_subject_type", "approval_requests", ["subject_type"])
    op.create_index("ix_approval_requests_subject_id", "approval_requests", ["subject_id"])
    op.create_index("ix_approval_requests_status", "approval_requests", ["status"])
    op.create_table(
        "style_signals",
        *identifiers(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("look_id", sa.String(length=36), sa.ForeignKey("looks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("signal", sa.String(length=40), nullable=False), sa.Column("weight", sa.Numeric(6, 3), nullable=False),
    )
    op.create_index("ix_style_signals_user_id", "style_signals", ["user_id"])
    op.create_index("ix_style_signals_look_id", "style_signals", ["look_id"])
    op.create_table(
        "taste_matches",
        *identifiers(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("matched_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("similarity_score", sa.Numeric(5, 4), nullable=False), sa.Column("status", sa.String(length=30), nullable=False),
    )
    op.create_index("ix_taste_matches_user_id", "taste_matches", ["user_id"])
    op.create_index("ix_taste_matches_matched_user_id", "taste_matches", ["matched_user_id"])


def downgrade() -> None:
    for table in (
        "taste_matches", "style_signals", "approval_requests", "ai_jobs", "look_items", "looks", "lookbooks",
        "garments", "media_assets", "consent_records", "users",
    ):
        op.drop_table(table)

