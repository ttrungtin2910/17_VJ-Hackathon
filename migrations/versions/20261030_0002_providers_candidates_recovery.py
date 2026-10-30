"""Draft catalogue và lease/retry cho worker, không sửa dữ liệu legacy.

Revision ID: 20261030_0002
Revises: 20261018_0001
"""

import sqlalchemy as sa
from alembic import op

revision = "20261030_0002"
down_revision = "20261018_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_jobs", sa.Column("lock_token", sa.String(36)))
    op.add_column("ai_jobs", sa.Column("next_attempt_at", sa.DateTime(timezone=True)))
    op.create_index("ix_ai_jobs_next_attempt_at", "ai_jobs", ["next_attempt_at"])
    # SQLite cũng chạy được migration; PostgreSQL giữ FK và UNIQUE thực.
    with op.batch_alter_table("looks") as batch:
        batch.add_column(sa.Column("source_job_id", sa.String(36)))
        batch.create_foreign_key(
            "fk_looks_source_job", "ai_jobs", ["source_job_id"], ["id"], ondelete="RESTRICT"
        )
        batch.create_unique_constraint("uq_looks_source_job_id", ["source_job_id"])
    op.create_table(
        "garment_candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "asset_id",
            sa.String(36),
            sa.ForeignKey("media_assets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("ai_jobs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("candidate_index", sa.Integer(), nullable=False),
        sa.Column("attributes_json", sa.JSON(), nullable=False),
        sa.Column("original_attributes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("garment_id", sa.String(36), sa.ForeignKey("garments.id", ondelete="RESTRICT")),
        sa.UniqueConstraint("job_id", "candidate_index", name="uq_candidates_job_index"),
        sa.UniqueConstraint("garment_id", name="uq_candidates_garment_id"),
    )
    for column in ("user_id", "asset_id", "job_id"):
        op.create_index(f"ix_garment_candidates_{column}", "garment_candidates", [column])


def downgrade() -> None:
    op.drop_table("garment_candidates")
    with op.batch_alter_table("looks") as batch:
        batch.drop_constraint("uq_looks_source_job_id", type_="unique")
        batch.drop_constraint("fk_looks_source_job", type_="foreignkey")
        batch.drop_column("source_job_id")
    op.drop_index("ix_ai_jobs_next_attempt_at", table_name="ai_jobs")
    op.drop_column("ai_jobs", "next_attempt_at")
    op.drop_column("ai_jobs", "lock_token")
