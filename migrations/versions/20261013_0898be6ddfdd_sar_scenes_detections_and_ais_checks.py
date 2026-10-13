"""sar scenes, detections and ais checks

Revision ID: 0898be6ddfdd
Revises: 6445b37f6318
Create Date: 2026-10-13 22:16:45.392924

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geography

# revision identifiers, used by Alembic.
revision: str = "0898be6ddfdd"
down_revision: str | Sequence[str] | None = "6445b37f6318"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "sar_scenes",
        sa.Column("id", sa.Uuid(), nullable=False, comment="Copernicus Data Space product id"),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("platform", sa.CHAR(length=3), nullable=False),
        sa.Column("datatake", sa.String(), nullable=False, comment="satellite pass; shared by the frames of one pass"),
        sa.Column("orbit_direction", sa.String(), nullable=False),
        sa.Column("relative_orbit", sa.SmallInteger(), nullable=False),
        sa.Column("acquired_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acquired_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "footprint",
            Geography(
                srid=4326,
                dimension=2,
                spatial_index=False,
                from_text="ST_GeogFromText",
                name="geography",
                nullable=False,
            ),
            nullable=False,
        ),
        sa.Column("aoi_coverage", sa.REAL(), nullable=False, comment="fraction of the area of interest in the frame"),
        sa.Column("status", sa.String(), nullable=False, comment="queued | processed | failed"),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("discovered_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detections", sa.Integer(), nullable=True),
        sa.Column("matched", sa.Integer(), nullable=True),
        sa.Column("dark", sa.Integer(), nullable=True),
        sa.Column("no_coverage", sa.Integer(), nullable=True),
        sa.Column("ais_vessels", sa.Integer(), nullable=True, comment="AIS vessels placed at acquisition time"),
        sa.Column(
            "ais_undetected", sa.Integer(), nullable=True, comment="AIS vessels in the searched sea the radar missed"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sar_scenes")),
        sa.UniqueConstraint("name", name=op.f("uq_sar_scenes_name")),
    )
    op.create_index("ix_sar_scenes_footprint", "sar_scenes", ["footprint"], unique=False, postgresql_using="gist")
    op.create_table(
        "sar_ais_checks",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("scene_id", sa.Uuid(), nullable=False),
        sa.Column("mmsi", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, comment="detected | undetected | not_imaged"),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lat", sa.Double(), nullable=False),
        sa.Column("lon", sa.Double(), nullable=False),
        sa.Column("length_m", sa.SmallInteger(), nullable=True),
        sa.Column("ship_type", sa.SmallInteger(), nullable=True),
        sa.Column("sog", sa.REAL(), nullable=True),
        sa.Column("report_gap_s", sa.REAL(), nullable=False, comment="to its nearest real AIS report"),
        sa.Column("doppler_shift_m", sa.REAL(), nullable=False),
        sa.ForeignKeyConstraint(
            ["scene_id"], ["sar_scenes.id"], name=op.f("fk_sar_ais_checks_scene_id_sar_scenes"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sar_ais_checks")),
    )
    op.create_index("ix_sar_ais_checks_scene_id", "sar_ais_checks", ["scene_id"], unique=False)
    op.create_table(
        "sar_detections",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("scene_id", sa.Uuid(), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False, comment="when the radar looked at this spot"),
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
        sa.Column(
            "status",
            sa.String(),
            nullable=False,
            comment="matched | dark | no_coverage | unpaired (no AIS pairing run)",
        ),
        sa.Column("mmsi", sa.Integer(), nullable=True, comment="AIS vessel it was paired with"),
        sa.Column("distance_m", sa.REAL(), nullable=True, comment="to the vessel's expected position"),
        sa.Column("ais_vessels_nearby", sa.SmallInteger(), nullable=True, comment="AIS vessels heard around it"),
        sa.Column("pixels", sa.Integer(), nullable=False),
        sa.Column("length_m", sa.REAL(), nullable=False, comment="rough size from the radar image"),
        sa.Column("peak_db", sa.REAL(), nullable=False),
        sa.Column("vv_db", sa.REAL(), nullable=False),
        sa.Column("vh_db", sa.REAL(), nullable=True),
        sa.Column("background_db", sa.REAL(), nullable=True),
        sa.ForeignKeyConstraint(
            ["scene_id"], ["sar_scenes.id"], name=op.f("fk_sar_detections_scene_id_sar_scenes"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sar_detections")),
    )
    op.create_index("ix_sar_detections_geom", "sar_detections", ["geom"], unique=False, postgresql_using="gist")
    op.create_index("ix_sar_detections_scene_id", "sar_detections", ["scene_id"], unique=False)
    op.create_index("ix_sar_detections_seen_at", "sar_detections", ["seen_at"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_sar_detections_seen_at", table_name="sar_detections")
    op.drop_index("ix_sar_detections_scene_id", table_name="sar_detections")
    op.drop_index("ix_sar_detections_geom", table_name="sar_detections", postgresql_using="gist")
    op.drop_table("sar_detections")
    op.drop_index("ix_sar_ais_checks_scene_id", table_name="sar_ais_checks")
    op.drop_table("sar_ais_checks")
    op.drop_index("ix_sar_scenes_footprint", table_name="sar_scenes", postgresql_using="gist")
    op.drop_table("sar_scenes")
