from datetime import datetime
from enum import StrEnum

from uatu.web.schemas import FeatureCollection, Geometry, OneDecimal, OutputSchema, Whole


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
    id: str  # platform-datatake, e.g. S1C-013AD6
    platform: str
    acquired_at: datetime
    orbit_direction: str
    coverage: float  # fraction of the area of interest imaged, all frames together
    frames: int  # products the pass came in
    detections: int
    matched: int
    no_coverage: int
    fixed: int
    candidates: int  # went silent or unidentified: the ones worth a look
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
    distance_m: Whole | None
    suspected_mmsi: int | None
    suspect_name: str | None
    suspect_silent_minutes: Whole | None
    fixed_passes_seen: int | None


class AisCheckProperties(OutputSchema):
    mmsi: int
    name: str | None
    status: str  # detected | undetected | not_imaged
    length_m: int | None
    sog: OneDecimal | None


class ImageOverlay(OutputSchema):
    url: str
    coordinates: list[list[float]]  # lon/lat of top-left, top-right, bottom-right, bottom-left


class PassDetail(PassSummary):
    images: list[ImageOverlay]  # one per frame with a radar overlay
    detections_geojson: FeatureCollection[DetectionProperties]
    ais_geojson: FeatureCollection[AisCheckProperties]
