"""Uatu's database schema. Change it here, then generate a migration with `alembic revision --autogenerate`."""

import uuid
from datetime import datetime
from typing import Any, ClassVar

from geoalchemy2 import Geography, WKBElement
from sqlalchemy import CHAR, REAL, BigInteger, Computed, DateTime, Double, Identity, Index, MetaData, SmallInteger
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_N_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: DateTime(timezone=True)}


class Position(Base):
    """One AIS position report (message types 1, 2, 3, 18, 19, 27). Append-only."""

    __tablename__ = "positions"
    __table_args__ = (
        Index("ix_positions_received_at", "received_at", postgresql_using="brin"),
        Index("ix_positions_mmsi_received_at", "mmsi", "received_at"),
        Index("ix_positions_geom", "geom", postgresql_using="gist"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    mmsi: Mapped[int]
    received_at: Mapped[datetime] = mapped_column(comment="reception time reported by aisstream.io")
    msg_type: Mapped[int] = mapped_column(SmallInteger, comment="AIS message id")
    lat: Mapped[float] = mapped_column(Double)
    lon: Mapped[float] = mapped_column(Double)
    sog: Mapped[float | None] = mapped_column(REAL, comment="speed over ground, knots")
    cog: Mapped[float | None] = mapped_column(REAL, comment="course over ground, degrees")
    heading: Mapped[int | None] = mapped_column(SmallInteger, comment="true heading of the bow, degrees")
    nav_status: Mapped[int | None] = mapped_column(SmallInteger, comment="AIS navigational status, class A only")
    position_accuracy: Mapped[bool | None] = mapped_column(comment="true = DGPS-quality fix (< 10 m)")
    geom: Mapped[WKBElement] = mapped_column(
        Geography("POINT", srid=4326, spatial_index=False),
        Computed("ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography", persisted=True),
    )


class StaticReport(Base):
    """Every static/voyage report as received (types 5, 19, 24). Append-only.

    Kept alongside `vessels` because the history of identity changes (renames, new
    callsigns, ship type changes) is itself a signal worth analysing.
    """

    __tablename__ = "static_reports"
    __table_args__ = (Index("ix_static_reports_mmsi_received_at", "mmsi", "received_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    mmsi: Mapped[int]
    received_at: Mapped[datetime]
    msg_type: Mapped[int] = mapped_column(SmallInteger)
    name: Mapped[str | None]
    callsign: Mapped[str | None]
    imo: Mapped[int | None]
    ship_type: Mapped[int | None] = mapped_column(SmallInteger)
    dim_a: Mapped[int | None] = mapped_column(SmallInteger, comment="metres from the AIS antenna to the bow")
    dim_b: Mapped[int | None] = mapped_column(SmallInteger, comment="metres from the AIS antenna to the stern")
    dim_c: Mapped[int | None] = mapped_column(SmallInteger, comment="metres from the AIS antenna to port")
    dim_d: Mapped[int | None] = mapped_column(SmallInteger, comment="metres from the AIS antenna to starboard")
    draught: Mapped[float | None] = mapped_column(REAL, comment="metres")
    destination: Mapped[str | None]


class Vessel(Base):
    """Latest known static data per MMSI, merged from static reports."""

    __tablename__ = "vessels"

    mmsi: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    ais_class: Mapped[str] = mapped_column(CHAR(1), comment="'A' or 'B'")
    name: Mapped[str | None]
    callsign: Mapped[str | None]
    imo: Mapped[int | None]
    ship_type: Mapped[int | None] = mapped_column(SmallInteger)
    dim_a: Mapped[int | None] = mapped_column(SmallInteger)
    dim_b: Mapped[int | None] = mapped_column(SmallInteger)
    dim_c: Mapped[int | None] = mapped_column(SmallInteger)
    dim_d: Mapped[int | None] = mapped_column(SmallInteger)
    length_m: Mapped[int | None] = mapped_column(SmallInteger, Computed("dim_a + dim_b", persisted=True))
    beam_m: Mapped[int | None] = mapped_column(SmallInteger, Computed("dim_c + dim_d", persisted=True))
    draught: Mapped[float | None] = mapped_column(REAL)
    destination: Mapped[str | None]
    first_seen_at: Mapped[datetime]
    last_static_at: Mapped[datetime]


class IngestSession(Base):
    """One websocket connection to aisstream.io.

    Time not covered by any session is time when Uatu was blind: a vessel missing from
    AIS then is no evidence of it going dark. See the `ingest_gaps` view.
    """

    __tablename__ = "ingest_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    connected_at: Mapped[datetime]
    last_message_at: Mapped[datetime | None]
    disconnected_at: Mapped[datetime | None]
    messages: Mapped[int] = mapped_column(BigInteger, server_default="0")
    close_reason: Mapped[str | None]
