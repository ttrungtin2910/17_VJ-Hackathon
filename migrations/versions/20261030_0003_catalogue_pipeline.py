"""Catalogue v2 và audit từng physical call; chỉ thay đổi additive.

Revision ID: 20261030_0003
Revises: 20261030_0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261030_0003"
down_revision = "20261030_0002"
branch_labels = None
depends_on = None
JsonType = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("ai_jobs", sa.Column("execution_deadline_at", sa.DateTime(timezone=True)))
    with op.batch_alter_table("media_assets") as batch:
        batch.add_column(sa.Column("parent_asset_id", sa.String(36)))
        batch.create_foreign_key(
            "fk_assets_parent", "media_assets", ["parent_asset_id"], ["id"], ondelete="RESTRICT"
        )
        batch.create_index("ix_media_assets_parent_asset_id", ["parent_asset_id"])
    for table in ("garments", "garment_candidates"):
        op.add_column(
            table, sa.Column("details_json", JsonType, nullable=False, server_default="{}")
        )
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("component_asset_id", sa.String(36)))
            batch.create_foreign_key(
                f"fk_{table}_component_asset",
                "media_assets",
                ["component_asset_id"],
                ["id"],
                ondelete="RESTRICT",
            )
    op.add_column(
        "garment_candidates",
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "garment_candidates",
        sa.Column("review_json", JsonType, nullable=False, server_default="{}"),
    )
    op.create_table(
        "ai_call_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "job_id", sa.String(36), sa.ForeignKey("ai_jobs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("call_index", sa.Integer(), nullable=False),
        sa.Column("worker_attempt", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("prompt_version", sa.String(80)),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("usage", JsonType, nullable=False),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("error_code", sa.String(80)),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("validation_status", sa.String(30)),
        sa.UniqueConstraint("job_id", "call_index", name="uq_ai_calls_job_index"),
    )
    for column in ("job_id", "user_id"):
        op.create_index(f"ix_ai_call_attempts_{column}", "ai_call_attempts", [column])


def downgrade() -> None:
    op.drop_table("ai_call_attempts")
    op.drop_column("ai_jobs", "execution_deadline_at")
    op.drop_column("garment_candidates", "review_json")
    op.drop_column("garment_candidates", "schema_version")
    for table in ("garment_candidates", "garments"):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"fk_{table}_component_asset", type_="foreignkey")
            batch.drop_column("component_asset_id")
            batch.drop_column("details_json")
    with op.batch_alter_table("media_assets") as batch:
        batch.drop_index("ix_media_assets_parent_asset_id")
        batch.drop_constraint("fk_assets_parent", type_="foreignkey")
        batch.drop_column("parent_asset_id")
