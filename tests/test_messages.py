from datetime import UTC, datetime

import pytest

from uatu.recorder.messages import InvalidMessage, PositionRow, StaticRow, parse_message, parse_time_utc

MMSI = 224123450
T = datetime(2026, 10, 13, 6, 31, 2, 123456, tzinfo=UTC)


def msg(kind: str, body: dict, *, mmsi: int = MMSI, time_utc: str = "2026-10-13 06:31:02.123456789 +0000 UTC"):
    return {
        "MessageType": kind,
        "MetaData": {"MMSI": mmsi, "ShipName": "IGNORED", "time_utc": time_utc},
        "Message": {kind: {"UserID": mmsi, "Valid": True, **body}},
    }


def position_body(**overrides):
    return {
        "Latitude": 36.0,
        "Longitude": -5.6,
        "Sog": 12.3,
        "Cog": 81.5,
        "TrueHeading": 80,
        "PositionAccuracy": True,
    } | overrides


def test_class_a_position():
    rows = parse_message(msg("PositionReport", position_body(MessageID=3, NavigationalStatus=5)))
    assert rows == [PositionRow(MMSI, T, 3, 36.0, -5.6, 12.3, 81.5, 80, 5, True)]


def test_unavailable_values_become_none():
    body = position_body(MessageID=1, Sog=102.3, Cog=360, TrueHeading=511, NavigationalStatus=15)
    [row] = parse_message(msg("PositionReport", body))
    assert (row.sog, row.cog, row.heading, row.nav_status) == (None, None, None, 15)


@pytest.mark.parametrize(("lat", "lon"), [(91, 181), (0, 0)])
def test_position_without_fix_is_invalid(lat, lon):
    with pytest.raises(InvalidMessage):
        parse_message(msg("PositionReport", position_body(Latitude=lat, Longitude=lon)))


def test_class_b_position_has_no_nav_status():
    [row] = parse_message(msg("StandardClassBPositionReport", position_body(MessageID=18)))
    assert row.msg_type == 18
    assert row.nav_status is None


def test_extended_class_b_yields_position_and_static():
    body = position_body(MessageID=19, Name="SEA BREEZE@@@", Type=37, Dimension={"A": 8, "B": 4, "C": 2, "D": 2})
    position, static = parse_message(msg("ExtendedClassBPositionReport", body))
    assert isinstance(position, PositionRow)
    assert static == StaticRow(MMSI, T, 19, name="SEA BREEZE", ship_type=37, dim_a=8, dim_b=4, dim_c=2, dim_d=2)


def test_ship_static_data():
    body = {
        "MessageID": 5,
        "Name": "NORDIC TANKER@@@@@@@",
        "CallSign": "9HA1234",
        "ImoNumber": 9876543,
        "Type": 80,
        "Dimension": {"A": 150, "B": 33, "C": 16, "D": 16},
        "MaximumStaticDraught": 11.2,
        "Destination": "ALGECIRAS@@@@@@@@",
    }
    assert parse_message(msg("ShipStaticData", body)) == [
        StaticRow(
            MMSI, T, 5,
            name="NORDIC TANKER", callsign="9HA1234", imo=9876543, ship_type=80,
            dim_a=150, dim_b=33, dim_c=16, dim_d=16, draught=11.2, destination="ALGECIRAS",
        )
    ]  # fmt: skip


def test_static_with_unknown_fields():
    body = {"Name": "", "CallSign": "@@@@@@@", "ImoNumber": 0, "Type": 0, "MaximumStaticDraught": 0,
            "Dimension": {"A": 0, "B": 0, "C": 5, "D": 5}}  # fmt: skip
    [row] = parse_message(msg("ShipStaticData", body))
    assert row == StaticRow(MMSI, T, 5, dim_c=5, dim_d=5)


def test_static_data_report_parts():
    part_a = {"PartNumber": False, "ReportA": {"Valid": True, "Name": "LUNA"}, "ReportB": {"Valid": False}}
    part_b = {
        "PartNumber": True,
        "ReportA": {"Valid": False},
        "ReportB": {"Valid": True, "CallSign": "EA1234", "ShipType": 36,
                    "Dimension": {"A": 10, "B": 2, "C": 2, "D": 2}},
    }  # fmt: skip
    assert parse_message(msg("StaticDataReport", part_a)) == [StaticRow(MMSI, T, 24, name="LUNA")]
    assert parse_message(msg("StaticDataReport", part_b)) == [
        StaticRow(MMSI, T, 24, callsign="EA1234", ship_type=36, dim_a=10, dim_b=2, dim_c=2, dim_d=2)
    ]


def test_long_range_broadcast_sentinels():
    body = position_body(MessageID=27, Sog=63, Cog=511, NavigationalStatus=0)
    del body["TrueHeading"]
    [row] = parse_message(msg("LongRangeAisBroadcastMessage", body))
    assert (row.msg_type, row.sog, row.cog, row.heading) == (27, None, None, None)


def test_ignored_message_type():
    assert parse_message(msg("BaseStationReport", {"Latitude": 36.1, "Longitude": -5.4})) == []


@pytest.mark.parametrize(
    "bad",
    [
        msg("PositionReport", position_body(Valid=False)),
        msg("PositionReport", position_body(UserID=0), mmsi=0),
        msg("PositionReport", position_body(Latitude="north")),
        msg("PositionReport", position_body(), time_utc="yesterday"),
        {"MessageType": "PositionReport", "MetaData": {}, "Message": {}},
    ],
)
def test_malformed_messages_are_invalid(bad):
    with pytest.raises(InvalidMessage):
        parse_message(bad)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-10-13 06:31:02.123456789 +0000 UTC", T),
        ("2026-10-13 06:31:02 +0000 UTC", T.replace(microsecond=0)),
        ("2026-10-13 08:31:02.5 +0200 CEST", T.replace(microsecond=500000)),
        ("2026-10-13T06:31:02.123456+00:00", T),
    ],
)
def test_parse_time_utc(value, expected):
    assert parse_time_utc(value) == expected


def test_the_recorder_subscribes_to_every_zone(monkeypatch):
    from uatu.recorder.config import RecorderSettings

    monkeypatch.setenv("AISSTREAM_API_KEY", "test")
    monkeypatch.setenv("ZONES", "estrecho,cataluna")
    boxes = RecorderSettings(_env_file=None).bounding_boxes
    assert boxes == [[[35.5, -6.5], [36.7, -4.5]], [[40.5, 0.5], [42.5, 3.4]]]
    monkeypatch.delenv("ZONES")
    assert len(RecorderSettings(_env_file=None).bounding_boxes) == 6  # every zone by default
