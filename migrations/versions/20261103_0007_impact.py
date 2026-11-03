"""Lịch sử lượt mặc và thành tựu bền vững."""

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261103_0007"
down_revision = "20261103_0006"
branch_labels = None
depends_on = None


def upgrade():
    json_type = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table(
        "wear_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("outfit_id", sa.String(36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("item_ids", json_type, nullable=False),
        sa.Column("worn_on", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "outfit_id", "worn_on", name="uq_wear_user_outfit_day"),
    )
    op.create_index("ix_wear_events_user_id", "wear_events", ["user_id"])
    op.create_table(
        "impact_progress",
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("data", json_type, nullable=False),
    )
    bind = op.get_bind()
    table = sa.table(
        "impact_progress", sa.column("user_id", sa.String()), sa.column("data", json_type)
    )
    timestamp = datetime.now(timezone.utc).isoformat()
    for user_id in bind.execute(sa.text("SELECT id FROM users")).scalars():
        outfits = list(
            bind.execute(
                sa.text(
                    "SELECT o.id FROM recommended_outfits o JOIN recommendation_sessions s ON o.session_id=s.id WHERE s.user_id=:user_id"
                ),
                {"user_id": user_id},
            ).scalars()
        )
        count = bind.execute(
            sa.text(
                "SELECT count(*) FROM garments WHERE user_id=:user_id AND catalog_status='active'"
            ),
            {"user_id": user_id},
        ).scalar_one()
        badges = {}
        if count >= 1:
            badges["first-item"] = timestamp
        if count >= 10:
            badges["collector"] = timestamp
        if len(outfits) >= 5:
            badges["stylist"] = timestamp
        bind.execute(
            table.insert().values(
                user_id=user_id, data={"outfits": outfits, "badges": badges, "weeks": {}}
            )
        )


def downgrade():
    op.drop_table("impact_progress")
    op.drop_index("ix_wear_events_user_id", table_name="wear_events")
    op.drop_table("wear_events")
