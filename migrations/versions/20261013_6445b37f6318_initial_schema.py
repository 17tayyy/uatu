"""initial schema

Revision ID: 6445b37f6318
Revises:
Create Date: 2026-10-13 17:12:52.022778

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geography

# revision identifiers, used by Alembic.
revision: str = "6445b37f6318"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.create_table(
        "ingest_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("messages", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("close_reason", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingest_sessions")),
    )
    op.create_table(
        "positions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("mmsi", sa.Integer(), nullable=False),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), nullable=False, comment="reception time reported by aisstream.io"
        ),
        sa.Column("msg_type", sa.SmallInteger(), nullable=False, comment="AIS message id"),
        sa.Column("lat", sa.Double(), nullable=False),
        sa.Column("lon", sa.Double(), nullable=False),
        sa.Column("sog", sa.REAL(), nullable=True, comment="speed over ground, knots"),
        sa.Column("cog", sa.REAL(), nullable=True, comment="course over ground, degrees"),
        sa.Column("heading", sa.SmallInteger(), nullable=True, comment="true heading of the bow, degrees"),
        sa.Column("nav_status", sa.SmallInteger(), nullable=True, comment="AIS navigational status, class A only"),
        sa.Column("position_accuracy", sa.Boolean(), nullable=True, comment="true = DGPS-quality fix (< 10 m)"),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_positions")),
    )
    op.create_index("ix_positions_geom", "positions", ["geom"], unique=False, postgresql_using="gist")
    op.create_index("ix_positions_mmsi_received_at", "positions", ["mmsi", "received_at"], unique=False)
    op.create_index("ix_positions_received_at", "positions", ["received_at"], unique=False, postgresql_using="brin")
    op.create_table(
        "static_reports",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("mmsi", sa.Integer(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("msg_type", sa.SmallInteger(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("callsign", sa.String(), nullable=True),
        sa.Column("imo", sa.Integer(), nullable=True),
        sa.Column("ship_type", sa.SmallInteger(), nullable=True),
        sa.Column("dim_a", sa.SmallInteger(), nullable=True, comment="metres from the AIS antenna to the bow"),
        sa.Column("dim_b", sa.SmallInteger(), nullable=True, comment="metres from the AIS antenna to the stern"),
        sa.Column("dim_c", sa.SmallInteger(), nullable=True, comment="metres from the AIS antenna to port"),
        sa.Column("dim_d", sa.SmallInteger(), nullable=True, comment="metres from the AIS antenna to starboard"),
        sa.Column("draught", sa.REAL(), nullable=True, comment="metres"),
        sa.Column("destination", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_static_reports")),
    )
    op.create_index("ix_static_reports_mmsi_received_at", "static_reports", ["mmsi", "received_at"], unique=False)
    op.create_table(
        "vessels",
        sa.Column("mmsi", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("ais_class", sa.CHAR(length=1), nullable=False, comment="'A' or 'B'"),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("callsign", sa.String(), nullable=True),
        sa.Column("imo", sa.Integer(), nullable=True),
        sa.Column("ship_type", sa.SmallInteger(), nullable=True),
        sa.Column("dim_a", sa.SmallInteger(), nullable=True),
        sa.Column("dim_b", sa.SmallInteger(), nullable=True),
        sa.Column("dim_c", sa.SmallInteger(), nullable=True),
        sa.Column("dim_d", sa.SmallInteger(), nullable=True),
        sa.Column("length_m", sa.SmallInteger(), sa.Computed("dim_a + dim_b", persisted=True), nullable=True),
        sa.Column("beam_m", sa.SmallInteger(), sa.Computed("dim_c + dim_d", persisted=True), nullable=True),
        sa.Column("draught", sa.REAL(), nullable=True),
        sa.Column("destination", sa.String(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_static_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("mmsi", name=op.f("pk_vessels")),
    )
    # Periods when Uatu was not receiving AIS, between the last message of one session
    # and the start of the next.
    op.execute("""
        CREATE VIEW ingest_gaps AS
        SELECT gap_start, gap_end, gap_end - gap_start AS duration
        FROM (
            SELECT coalesce(last_message_at, connected_at)         AS gap_start,
                   lead(connected_at) OVER (ORDER BY connected_at) AS gap_end
            FROM ingest_sessions
        ) s
        WHERE gap_end > gap_start
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP VIEW ingest_gaps")
    op.drop_table("vessels")
    op.drop_index("ix_static_reports_mmsi_received_at", table_name="static_reports")
    op.drop_table("static_reports")
    op.drop_index("ix_positions_received_at", table_name="positions", postgresql_using="brin")
    op.drop_index("ix_positions_mmsi_received_at", table_name="positions")
    op.drop_index("ix_positions_geom", table_name="positions", postgresql_using="gist")
    op.drop_table("positions")
    op.drop_table("ingest_sessions")
