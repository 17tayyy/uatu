"""dashboard users, sessions and detection reviews

Revision ID: b4e8d1c2a7f5
Revises: 7a1c5e2f9b30
Create Date: 2026-10-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4e8d1c2a7f5"
down_revision: str | Sequence[str] | None = "7a1c5e2f9b30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dashboard_users",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column("username", sa.String(length=40), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False, comment="argon2id"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dashboard_users")),
        sa.UniqueConstraint("username", name=op.f("uq_dashboard_users_username")),
    )
    op.create_table(
        "dashboard_sessions",
        sa.Column("token_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["dashboard_users.id"],
            name=op.f("fk_dashboard_sessions_user_id_dashboard_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("token_hash", name=op.f("pk_dashboard_sessions")),
    )
    op.create_index(op.f("ix_dashboard_sessions_user_id"), "dashboard_sessions", ["user_id"], unique=False)
    op.create_table(
        "detection_reviews",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("scene_id", sa.Uuid(), nullable=False),
        sa.Column("detection_id", sa.BigInteger(), nullable=True),
        sa.Column("lat", sa.Double(), nullable=False),
        sa.Column("lon", sa.Double(), nullable=False),
        sa.Column(
            "verdict",
            sa.String(),
            nullable=False,
            comment=(
                "vessel_without_ais | vessel_with_ais | reception_gap (a vessel AIS was not heard from) | not_a_vessel"
            ),
        ),
        sa.Column("mmsi", sa.Integer(), nullable=True, comment="the vessel it is, when known"),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "verdict IN ('vessel_without_ais', 'vessel_with_ais', 'reception_gap', 'not_a_vessel')",
            name=op.f("ck_detection_reviews_verdict"),
        ),
        sa.ForeignKeyConstraint(
            ["detection_id"],
            ["sar_detections.id"],
            name=op.f("fk_detection_reviews_detection_id_sar_detections"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["dashboard_users.id"],
            name=op.f("fk_detection_reviews_reviewed_by_dashboard_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["scene_id"],
            ["sar_scenes.id"],
            name=op.f("fk_detection_reviews_scene_id_sar_scenes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_detection_reviews")),
        sa.UniqueConstraint("detection_id", name=op.f("uq_detection_reviews_detection_id")),
    )
    op.create_index(op.f("ix_detection_reviews_scene_id"), "detection_reviews", ["scene_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_detection_reviews_scene_id"), table_name="detection_reviews")
    op.drop_table("detection_reviews")
    op.drop_index(op.f("ix_dashboard_sessions_user_id"), table_name="dashboard_sessions")
    op.drop_table("dashboard_sessions")
    op.drop_table("dashboard_users")
