"""sar fixed objects and suspected identities

Revision ID: ca593f58ade4
Revises: 3e114f7b2ff6
Create Date: 2026-10-14 04:06:28.048319

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geography

# revision identifiers, used by Alembic.
revision: str = "ca593f58ade4"
down_revision: str | Sequence[str] | None = "3e114f7b2ff6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "sar_fixed_objects",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column("lat", sa.Double(), nullable=False),
        sa.Column("lon", sa.Double(), nullable=False),
        sa.Column(
            "geom",
            Geography(
                geometry_type="POINT",
                srid=4326,
                dimension=2,
                spatial_index=False,
                from_text="ST_GeogFromText",
                name="geography",
                nullable=False,
            ),
            sa.Computed("ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography", persisted=True),
            nullable=False,
        ),
        sa.Column("passes_seen", sa.Integer(), nullable=False, comment="satellite passes that detected it"),
        sa.Column(
            "passes_covering", sa.Integer(), nullable=False, comment="processed passes whose footprint covers it"
        ),
        sa.Column("persistence", sa.REAL(), nullable=False, comment="passes_seen / passes_covering"),
        sa.Column("fixed", sa.Boolean(), nullable=False, comment="persistent enough to be a structure, not traffic"),
        sa.Column("detections", sa.Integer(), nullable=False),
        sa.Column("length_m", sa.REAL(), nullable=False, comment="median detected size"),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sar_fixed_objects")),
    )
    op.create_index("ix_sar_fixed_objects_geom", "sar_fixed_objects", ["geom"], unique=False, postgresql_using="gist")
    op.add_column(
        "sar_detections",
        sa.Column("fixed_object_id", sa.Integer(), nullable=True, comment="recurring object it belongs to"),
    )
    op.add_column(
        "sar_detections",
        sa.Column(
            "suspected_mmsi",
            sa.Integer(),
            nullable=True,
            comment="for an unpaired detection: a vessel that had gone silent and would be about here",
        ),
    )
    op.add_column(
        "sar_detections",
        sa.Column("suspect_distance_m", sa.REAL(), nullable=True, comment="to that vessel's estimated position"),
    )
    op.add_column(
        "sar_detections",
        sa.Column("suspect_silent_minutes", sa.REAL(), nullable=True, comment="since its nearest AIS report"),
    )
    op.create_foreign_key(
        op.f("fk_sar_detections_fixed_object_id_sar_fixed_objects"),
        "sar_detections",
        "sar_fixed_objects",
        ["fixed_object_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        op.f("fk_sar_detections_fixed_object_id_sar_fixed_objects"), "sar_detections", type_="foreignkey"
    )
    op.drop_column("sar_detections", "suspect_silent_minutes")
    op.drop_column("sar_detections", "suspect_distance_m")
    op.drop_column("sar_detections", "suspected_mmsi")
    op.drop_column("sar_detections", "fixed_object_id")
    op.drop_index("ix_sar_fixed_objects_geom", table_name="sar_fixed_objects", postgresql_using="gist")
    op.drop_table("sar_fixed_objects")
