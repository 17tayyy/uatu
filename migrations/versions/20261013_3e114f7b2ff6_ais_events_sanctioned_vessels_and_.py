"""ais events, sanctioned vessels and infrastructure

Revision ID: 3e114f7b2ff6
Revises: 0898be6ddfdd
Create Date: 2026-10-13 23:19:51.849068

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geography
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "3e114f7b2ff6"
down_revision: str | Sequence[str] | None = "0898be6ddfdd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "ais_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("key", sa.String(), nullable=False, comment="deterministic, so re-running never duplicates"),
        sa.Column(
            "kind",
            sa.String(),
            nullable=False,
            comment="what happened, see uatu/ais/events.py (ais_gap, encounter...)",
        ),
        sa.Column("severity", sa.SmallInteger(), nullable=False, comment="1 info, 2 notable, 3 alert"),
        sa.Column("mmsi", sa.Integer(), nullable=False),
        sa.Column("other_mmsi", sa.Integer(), nullable=True, comment="the other vessel of an encounter or shared IMO"),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True, comment="NULL while still ongoing"),
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
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ais_events")),
        sa.UniqueConstraint("key", name=op.f("uq_ais_events_key")),
    )
    op.create_index("ix_ais_events_geom", "ais_events", ["geom"], unique=False, postgresql_using="gist")
    op.create_index("ix_ais_events_mmsi", "ais_events", ["mmsi"], unique=False)
    op.create_index("ix_ais_events_start_at", "ais_events", ["start_at"], unique=False)
    op.create_table(
        "infrastructure",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "kind", sa.String(), nullable=False, comment="telecom_cable | pipeline | platform | port | landing_point"
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=True, comment="operational | abandoned | ..."),
        sa.Column(
            "source", sa.String(), nullable=False, comment="where it comes from, e.g. emodnet:cicacables, telegeography"
        ),
        sa.Column(
            "geom",
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
        sa.Column("properties", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_infrastructure")),
    )
    op.create_index("ix_infrastructure_geom", "infrastructure", ["geom"], unique=False, postgresql_using="gist")
    op.create_table(
        "sanctioned_vessels",
        sa.Column("imo", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("aliases", sa.String(), nullable=True),
        sa.Column("mmsis", sa.String(), nullable=True, comment="MMSIs listed for it, ';'-separated"),
        sa.Column("countries", sa.String(), nullable=True, comment="flags and other linked countries, ';'-separated"),
        sa.Column(
            "programs", sa.String(), nullable=True, comment="sanctions programmes, ';'-separated (EU-RUS, US-RUSHAR...)"
        ),
        sa.Column("sanctions", sa.String(), nullable=True),
        sa.Column("source_id", sa.String(), nullable=False, comment="OpenSanctions entity id"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("imo", name=op.f("pk_sanctioned_vessels")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("sanctioned_vessels")
    op.drop_index("ix_infrastructure_geom", table_name="infrastructure", postgresql_using="gist")
    op.drop_table("infrastructure")
    op.drop_index("ix_ais_events_start_at", table_name="ais_events")
    op.drop_index("ix_ais_events_mmsi", table_name="ais_events")
    op.drop_index("ix_ais_events_geom", table_name="ais_events", postgresql_using="gist")
    op.drop_table("ais_events")
