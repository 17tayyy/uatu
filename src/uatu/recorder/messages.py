"""Turn aisstream.io messages into rows for the positions and static_reports tables.

aisstream.io decodes AIS with go-ais and sends JSON like:

    {"MessageType": "PositionReport",
     "MetaData": {"MMSI": 224123450, "time_utc": "2026-10-13 06:31:02.123456789 +0000 UTC", ...},
     "Message": {"PositionReport": {"UserID": 224123450, "Latitude": 36.0, "Sog": 12.3, ...}}}

Field names inside "Message" follow go-ais. AIS "not available" sentinel values become None.
"""

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, NamedTuple


class PositionRow(NamedTuple):
    mmsi: int
    received_at: datetime
    msg_type: int
    lat: float
    lon: float
    sog: float | None
    cog: float | None
    heading: int | None
    nav_status: int | None
    position_accuracy: bool | None


class StaticRow(NamedTuple):
    mmsi: int
    received_at: datetime
    msg_type: int
    name: str | None = None
    callsign: str | None = None
    imo: int | None = None
    ship_type: int | None = None
    dim_a: int | None = None
    dim_b: int | None = None
    dim_c: int | None = None
    dim_d: int | None = None
    draught: float | None = None
    destination: str | None = None


type Row = PositionRow | StaticRow


class InvalidMessage(ValueError):
    """The message is malformed or fails basic AIS sanity checks."""


def parse_message(msg: dict[str, Any]) -> list[Row]:
    """Rows to store for one aisstream.io message; [] for message types Uatu does not store."""
    kind = msg.get("MessageType")
    handler = _HANDLERS.get(kind)  # type: ignore[arg-type]
    if handler is None:
        return []
    try:
        body = msg["Message"][kind]
        if body.get("Valid") is False:
            raise InvalidMessage(f"{kind} flagged invalid by the decoder")
        meta = msg.get("MetaData") or {}
        mmsi = int(body.get("UserID") or meta.get("MMSI") or 0)
        if not 0 < mmsi <= 999_999_999:
            raise InvalidMessage(f"bad MMSI {mmsi}")
        return handler(body, mmsi, parse_time_utc(meta.get("time_utc")))
    except InvalidMessage:
        raise
    except (KeyError, TypeError, ValueError) as e:
        raise InvalidMessage(f"{kind}: {e!r}") from e


# "2026-10-13 06:31:02.123456789 +0000 UTC" (Go's time.Time.String()).
_TIME_RE = re.compile(r"(\d{4})-(\d\d)-(\d\d)[ T](\d\d):(\d\d):(\d\d)(?:\.(\d+))?\s*([+-]\d\d):?(\d\d)")


def parse_time_utc(value: str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    m = _TIME_RE.match(value)
    if m is None:
        raise InvalidMessage(f"unrecognised time_utc {value!r}")
    year, month, day, hour, minute, second = (int(g) for g in m.group(1, 2, 3, 4, 5, 6))
    micros = int((m[7] or "0")[:6].ljust(6, "0"))
    offset = int(m[8]) * 60 + (int(m[9]) if m[8][0] == "+" else -int(m[9]))
    tz = timezone(timedelta(minutes=offset))
    return datetime(year, month, day, hour, minute, second, micros, tzinfo=tz).astimezone(UTC)


# --- handlers, one per aisstream.io MessageType --------------------------------------------------


def _class_a_position(b: dict, mmsi: int, ts: datetime) -> list[Row]:  # types 1, 2, 3
    return [_require(_position(b, mmsi, ts, default_type=1, nav_status=b.get("NavigationalStatus")))]


def _class_b_position(b: dict, mmsi: int, ts: datetime) -> list[Row]:  # type 18
    return [_require(_position(b, mmsi, ts, default_type=18))]


def _extended_class_b(b: dict, mmsi: int, ts: datetime) -> list[Row]:  # type 19: position + static
    static = StaticRow(mmsi, ts, 19, name=_text(b.get("Name")), ship_type=_positive(b.get("Type")), **_dimensions(b))
    position = _position(b, mmsi, ts, default_type=19)
    return [static] if position is None else [position, static]


def _long_range(b: dict, mmsi: int, ts: datetime) -> list[Row]:  # type 27, for satellite reception
    return [
        _require(_position(b, mmsi, ts, default_type=27, nav_status=b.get("NavigationalStatus"), sog_unavailable=63))
    ]


def _ship_static_data(b: dict, mmsi: int, ts: datetime) -> list[Row]:  # type 5, class A
    return [
        StaticRow(
            mmsi,
            ts,
            5,
            name=_text(b.get("Name")),
            callsign=_text(b.get("CallSign")),
            imo=_positive(b.get("ImoNumber")),
            ship_type=_positive(b.get("Type")),
            **_dimensions(b),
            draught=_positive(b.get("MaximumStaticDraught")),
            destination=_text(b.get("Destination")),
        )
    ]


def _static_data_report(b: dict, mmsi: int, ts: datetime) -> list[Row]:  # type 24, class B, two parts
    if b.get("PartNumber"):
        part = b.get("ReportB") or {}
        if part.get("Valid") is False:
            raise InvalidMessage("StaticDataReport part B not decoded")
        row = StaticRow(
            mmsi,
            ts,
            24,
            callsign=_text(part.get("CallSign")),
            ship_type=_positive(part.get("ShipType")),
            **_dimensions(part),
        )
    else:
        part = b.get("ReportA") or {}
        if part.get("Valid") is False:
            raise InvalidMessage("StaticDataReport part A not decoded")
        row = StaticRow(mmsi, ts, 24, name=_text(part.get("Name")))
    return [row]


_HANDLERS: dict[str, Callable[[dict, int, datetime], list[Row]]] = {
    "PositionReport": _class_a_position,
    "StandardClassBPositionReport": _class_b_position,
    "ExtendedClassBPositionReport": _extended_class_b,
    "LongRangeAisBroadcastMessage": _long_range,
    "ShipStaticData": _ship_static_data,
    "StaticDataReport": _static_data_report,
}


# --- field helpers ---------------------------------------------------------------------------------


def _position(
    b: dict,
    mmsi: int,
    ts: datetime,
    *,
    default_type: int,
    nav_status: int | None = None,
    sog_unavailable: float = 102.3,
) -> PositionRow | None:
    """A position row, or None when the report carries no usable position."""
    lat, lon = float(b["Latitude"]), float(b["Longitude"])
    # 91/181 mean "not available"; (0, 0) is a common default from misconfigured transponders.
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None
    sog = b.get("Sog")
    cog = b.get("Cog")
    heading = b.get("TrueHeading")
    return PositionRow(
        mmsi=mmsi,
        received_at=ts,
        msg_type=int(b.get("MessageID") or default_type),
        lat=lat,
        lon=lon,
        sog=None if sog is None or sog >= sog_unavailable - 0.01 else float(sog),
        cog=None if cog is None or cog >= 360 else float(cog),
        heading=None if heading is None or heading >= 360 else int(heading),
        nav_status=nav_status,
        position_accuracy=b.get("PositionAccuracy"),
    )


def _require(position: PositionRow | None) -> PositionRow:
    if position is None:
        raise InvalidMessage("position not available")
    return position


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value.replace("@", " ").strip() or None  # AIS pads 6-bit text with '@'


def _positive[N: (int, float)](value: N | None) -> N | None:
    return value if value is not None and value > 0 else None


def _dimensions(b: dict) -> dict[str, int | None]:
    """dim_a..dim_d from an AIS Dimension field; 0 + 0 means unknown length (A+B) or beam (C+D)."""
    dim = b.get("Dimension") or {}
    a, b_, c, d = (int(dim.get(k) or 0) for k in "ABCD")
    known_length, known_beam = a + b_ > 0, c + d > 0
    return {
        "dim_a": a if known_length else None,
        "dim_b": b_ if known_length else None,
        "dim_c": c if known_beam else None,
        "dim_d": d if known_beam else None,
    }
