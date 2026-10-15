from datetime import datetime
from enum import StrEnum

from uatu.web.reviews.schemas import ReviewRead
from uatu.web.schemas import Degrees, FeatureCollection, Geometry, OneDecimal, OutputSchema, Whole


class DetectionKind(StrEnum):
    """How a detection reads on the map: one of these, decided in this order."""

    FIXED = "fixed"  # a place detected pass after pass: platform, buoy, long-term mooring
    MATCHED = "matched"  # paired with an AIS vessel
    NO_COVERAGE = "no_coverage"  # no AIS paired, but AIS is not really received there
    RECEPTION_GAP = "reception_gap"  # no AIS paired; a vessel silent < 1 h would be there
    WENT_SILENT = "went_silent"  # no AIS paired; a vessel silent for longer would be there
    UNIDENTIFIED = "unidentified"  # no AIS paired, none silent nearby, AIS well received
    UNPAIRED = "unpaired"  # pass from before AIS recording began


class PassSummary(OutputSchema):
    id: str  # platform-datatake-zone, e.g. S1C-013AD6-estrecho
    platform: str
    zone: str
    acquired_at: datetime
    orbit_direction: str
    coverage: float  # fraction of the zone imaged, all frames together
    frames: int  # products the pass came in
    detections: int
    matched: int
    no_coverage: int
    fixed: int
    candidates: int  # went silent or unidentified: the ones worth a look
    pending: int  # unpaired where AIS is received (candidates and reception gaps) with no verdict yet
    footprint: Geometry
    has_image: bool


class DetectionProperties(OutputSchema):
    id: int
    kind: DetectionKind
    seen_at: datetime
    length_m: Whole
    contrast_db: OneDecimal | None
    vh_db: OneDecimal | None
    mmsi: int | None  # paired AIS vessel
    vessel_name: str | None
    distance_m: Whole | None  # to where its AIS says the radar should see it, Doppler shift included
    # Paired: where its AIS put it when the radar looked. A vessel moving towards or away from the radar
    # shows shifted along the satellite's track by doppler_shift_m: the echo is not on the vessel.
    ais_lon: Degrees | None
    ais_lat: Degrees | None
    doppler_shift_m: Whole | None
    suspected_mmsi: int | None
    suspect_name: str | None
    suspect_silent_minutes: Whole | None
    fixed_passes_seen: int | None
    # For unpaired detections: AIS heard around them at the time (see ReceptionRule)
    ais_vessels_nearby: int | None
    ais_reports_nearby: int | None
    review: ReviewRead | None  # your verdict, if given


class AisCheckProperties(OutputSchema):
    mmsi: int
    name: str | None
    status: str  # detected | undetected | not_imaged
    length_m: int | None
    sog: OneDecimal | None


class ImageOverlay(OutputSchema):
    url: str  # the whole frame at 80 m, as one image
    coordinates: list[list[float]]  # lon/lat of top-left, top-right, bottom-right, bottom-left
    tiles: str | None = None  # at 20 m, as map tiles: a {z}/{x}/{y} URL template (passes since tiles exist)
    bounds: list[float] | None = None  # lon/lat west, south, east, north of the tiles


class ReceptionRule(OutputSchema):
    """When AIS counts as well received around an unpaired detection, so that its silence means something."""

    radius_m: float
    window_hours: float  # either side of the pass
    min_vessels: int
    min_reports_per_vessel: float


class PassDetail(PassSummary):
    images: list[ImageOverlay]  # one per frame with a radar overlay
    reception_rule: ReceptionRule
    detections_geojson: FeatureCollection[DetectionProperties]
    ais_geojson: FeatureCollection[AisCheckProperties]
