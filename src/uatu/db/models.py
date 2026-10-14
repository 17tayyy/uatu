"""Uatu's database schema. Change it here, then generate a migration with `alembic revision --autogenerate`."""

import uuid
from datetime import datetime
from typing import Any, ClassVar

from geoalchemy2 import Geography, WKBElement
from sqlalchemy import (
    CHAR,
    REAL,
    BigInteger,
    Computed,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    MetaData,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
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


class RecordedZone(Base):
    """A zone the recorder subscribes to, and since when: scenes acquired before have no AIS to pair with."""

    __tablename__ = "recorded_zones"

    key: Mapped[str] = mapped_column(String(16), primary_key=True, comment="see uatu.config.ZONES")
    since: Mapped[datetime]


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


class SarScene(Base):
    """A Sentinel-1 frame over a zone, from discovery to its pairing with AIS.

    A frame over two zones is two scenes: it is downloaded once and analysed on each zone's grid.
    """

    __tablename__ = "sar_scenes"
    __table_args__ = (
        Index("ix_sar_scenes_footprint", "footprint", postgresql_using="gist"),
        UniqueConstraint("name", "zone"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, comment="the product id for scenes before zones")
    product_id: Mapped[uuid.UUID] = mapped_column(comment="Copernicus Data Space product id")
    zone: Mapped[str] = mapped_column(String(16), comment="key of the zone it was analysed for, see uatu.config")
    name: Mapped[str]
    platform: Mapped[str] = mapped_column(CHAR(3))
    datatake: Mapped[str] = mapped_column(comment="satellite pass; shared by the frames of one pass")
    orbit_direction: Mapped[str]
    relative_orbit: Mapped[int] = mapped_column(SmallInteger)
    acquired_from: Mapped[datetime]
    acquired_to: Mapped[datetime]
    footprint: Mapped[WKBElement] = mapped_column(Geography("GEOMETRY", srid=4326, spatial_index=False))
    aoi_coverage: Mapped[float] = mapped_column(REAL, comment="fraction of the zone's sea in the frame")
    status: Mapped[str] = mapped_column(comment="queued | processed | failed | skipped")
    error: Mapped[str | None]
    discovered_at: Mapped[datetime] = mapped_column(server_default=func.now())
    processed_at: Mapped[datetime | None]
    notified_at: Mapped[datetime | None] = mapped_column(comment="when the pass it belongs to was announced")
    detections: Mapped[int | None]
    matched: Mapped[int | None]
    dark: Mapped[int | None]
    no_coverage: Mapped[int | None]
    ais_vessels: Mapped[int | None] = mapped_column(comment="AIS vessels placed at acquisition time")
    ais_undetected: Mapped[int | None] = mapped_column(comment="AIS vessels in the searched sea the radar missed")


class SarDetection(Base):
    """A ship detected on a scene, and what AIS says about it."""

    __tablename__ = "sar_detections"
    __table_args__ = (
        Index("ix_sar_detections_scene_id", "scene_id"),
        Index("ix_sar_detections_seen_at", "seen_at"),
        Index("ix_sar_detections_geom", "geom", postgresql_using="gist"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    scene_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sar_scenes.id", ondelete="CASCADE"))
    seen_at: Mapped[datetime] = mapped_column(comment="when the radar looked at this spot")
    lat: Mapped[float] = mapped_column(Double)
    lon: Mapped[float] = mapped_column(Double)
    geom: Mapped[WKBElement] = mapped_column(
        Geography("POINT", srid=4326, spatial_index=False),
        Computed("ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography", persisted=True),
    )
    status: Mapped[str] = mapped_column(comment="matched | dark | no_coverage | unpaired (no AIS pairing run)")
    mmsi: Mapped[int | None] = mapped_column(comment="AIS vessel it was paired with")
    distance_m: Mapped[float | None] = mapped_column(REAL, comment="to the vessel's expected position")
    ais_vessels_nearby: Mapped[int | None] = mapped_column(SmallInteger, comment="AIS vessels heard around it")
    ais_reports_nearby: Mapped[int | None] = mapped_column(comment="their AIS reports: how well AIS is received there")
    pixels: Mapped[int]
    length_m: Mapped[float] = mapped_column(REAL, comment="rough size from the radar image")
    peak_db: Mapped[float] = mapped_column(REAL)
    vv_db: Mapped[float] = mapped_column(REAL)
    vh_db: Mapped[float | None] = mapped_column(REAL)
    background_db: Mapped[float | None] = mapped_column(REAL)
    fixed_object_id: Mapped[int | None] = mapped_column(
        ForeignKey("sar_fixed_objects.id", ondelete="SET NULL"), comment="recurring object it belongs to"
    )
    suspected_mmsi: Mapped[int | None] = mapped_column(
        comment="for an unpaired detection: a vessel that had gone silent and would be about here"
    )
    suspect_distance_m: Mapped[float | None] = mapped_column(REAL, comment="to that vessel's estimated position")
    suspect_silent_minutes: Mapped[float | None] = mapped_column(REAL, comment="since its nearest AIS report")


class SarFixedObject(Base):
    """A place where the radar keeps detecting something, pass after pass: platforms, buoys, fish
    farms, long-term moorings. Rebuilt from all detections; not a vessel to chase."""

    __tablename__ = "sar_fixed_objects"
    __table_args__ = (Index("ix_sar_fixed_objects_geom", "geom", postgresql_using="gist"),)

    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    lat: Mapped[float] = mapped_column(Double)
    lon: Mapped[float] = mapped_column(Double)
    geom: Mapped[WKBElement] = mapped_column(
        Geography("POINT", srid=4326, spatial_index=False),
        Computed("ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography", persisted=True),
    )
    passes_seen: Mapped[int] = mapped_column(comment="satellite passes that detected it")
    passes_covering: Mapped[int] = mapped_column(comment="processed passes whose footprint covers it")
    persistence: Mapped[float] = mapped_column(REAL, comment="passes_seen / passes_covering")
    fixed: Mapped[bool] = mapped_column(comment="persistent enough to be a structure, not traffic")
    detections: Mapped[int]
    length_m: Mapped[float] = mapped_column(REAL, comment="median detected size")
    first_seen: Mapped[datetime]
    last_seen: Mapped[datetime]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class SarAisCheck(Base):
    """An AIS vessel that a scene should have shown, and whether the radar detected it.

    The record of what the detector misses, e.g. as a function of vessel length.
    """

    __tablename__ = "sar_ais_checks"
    __table_args__ = (Index("ix_sar_ais_checks_scene_id", "scene_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    scene_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sar_scenes.id", ondelete="CASCADE"))
    mmsi: Mapped[int]
    status: Mapped[str] = mapped_column(comment="detected | undetected | not_imaged")
    seen_at: Mapped[datetime]
    lat: Mapped[float] = mapped_column(Double)
    lon: Mapped[float] = mapped_column(Double)
    length_m: Mapped[int | None] = mapped_column(SmallInteger)
    ship_type: Mapped[int | None] = mapped_column(SmallInteger)
    sog: Mapped[float | None] = mapped_column(REAL)
    report_gap_s: Mapped[float] = mapped_column(REAL, comment="to its nearest real AIS report")
    doppler_shift_m: Mapped[float] = mapped_column(REAL)


class SanctionedVessel(Base):
    """A vessel on a sanctions list (from OpenSanctions), by IMO number."""

    __tablename__ = "sanctioned_vessels"

    imo: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str]
    aliases: Mapped[str | None]
    mmsis: Mapped[str | None] = mapped_column(comment="MMSIs listed for it, ';'-separated")
    countries: Mapped[str | None] = mapped_column(comment="flags and other linked countries, ';'-separated")
    programs: Mapped[str | None] = mapped_column(comment="sanctions programmes, ';'-separated (EU-RUS, US-RUSHAR...)")
    sanctions: Mapped[str | None]
    source_id: Mapped[str] = mapped_column(comment="OpenSanctions entity id")
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Infrastructure(Base):
    """Critical maritime infrastructure: submarine cables, pipelines, platforms, ports."""

    __tablename__ = "infrastructure"
    __table_args__ = (Index("ix_infrastructure_geom", "geom", postgresql_using="gist"),)

    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    kind: Mapped[str] = mapped_column(comment="telecom_cable | pipeline | platform | port | landing_point")
    name: Mapped[str]
    status: Mapped[str | None] = mapped_column(comment="operational | abandoned | ...")
    source: Mapped[str] = mapped_column(comment="where it comes from, e.g. emodnet:cicacables, telegeography")
    geom: Mapped[WKBElement] = mapped_column(Geography("GEOMETRY", srid=4326, spatial_index=False))
    properties: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class AisEvent(Base):
    """Something notable in the AIS record: a vessel going silent, two vessels meeting at sea..."""

    __tablename__ = "ais_events"
    __table_args__ = (
        Index("ix_ais_events_start_at", "start_at"),
        Index("ix_ais_events_mmsi", "mmsi"),
        Index("ix_ais_events_geom", "geom", postgresql_using="gist"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    key: Mapped[str] = mapped_column(unique=True, comment="deterministic, so re-running never duplicates")
    kind: Mapped[str] = mapped_column(comment="what happened, see uatu/ais/events.py (ais_gap, encounter...)")
    severity: Mapped[int] = mapped_column(SmallInteger, comment="1 info, 2 notable, 3 alert")
    mmsi: Mapped[int]
    other_mmsi: Mapped[int | None] = mapped_column(comment="the other vessel of an encounter or shared IMO")
    start_at: Mapped[datetime]
    end_at: Mapped[datetime | None] = mapped_column(comment="NULL while still ongoing")
    lat: Mapped[float] = mapped_column(Double)
    lon: Mapped[float] = mapped_column(Double)
    geom: Mapped[WKBElement] = mapped_column(
        Geography("POINT", srid=4326, spatial_index=False),
        Computed("ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography", persisted=True),
    )
    details: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
