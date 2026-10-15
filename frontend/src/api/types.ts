// The API's shapes, as generated from its OpenAPI schema (npm run api).
import type { components } from "./schema";

type S = components["schemas"];

export type Geometry = S["Geometry"];
export type Zones = S["Zones"];
export type ZoneInfo = S["ZoneInfo"];
export type MapArea = S["MapArea"];
export type Status = S["Status"];
export type PassSummary = S["PassSummary"];
export type PassDetail = S["PassDetail"];
export type Detection = S["DetectionProperties"];
export type DetectionFeature = S["Feature_DetectionProperties_"];
export type PendingDetection = S["PendingDetection"];
export type PendingFeature = S["Feature_PendingDetection_"];
export type AisCheck = S["AisCheckProperties"];
export type AisCheckFeature = S["Feature_AisCheckProperties_"];
export type ReceptionRule = S["ReceptionRule"];
export type EventProps = S["EventProperties"];
export type EventFeature = S["Feature_EventProperties_"];
export type LiveVessel = S["LiveVesselProperties"];
export type LiveVesselFeature = S["Feature_LiveVesselProperties_"];
export type VesselSummary = S["VesselSummary"];
export type VesselDetail = S["VesselDetail"];
export type CompanyRole = S["CompanyRole"];
export type CompanyDetail = S["CompanyDetail"];
export type FleetVessel = S["FleetVessel"];
export type StopOut = S["StopOut"];
export type TransitOut = S["TransitOut"];
export type Risk = S["Risk"];
export type Watch = S["Watch"];
export type WatchRead = S["WatchRead"];
export type WatchCreate = S["WatchCreate"];
export type ReviewRead = S["ReviewRead"];
export type ReviewWrite = S["ReviewWrite"];
export type HistoryChunk = S["HistoryChunk"];
export type HistoryTrack = S["HistoryTrack"];
export type HistoryRange = S["HistoryRange"];
export type OpticalImage = S["OpticalImage"];
export type SatelliteLayer = S["SatelliteLayer"];
export type ReportSummary = S["ReportSummary"];

export interface FeatureCollection<P = Record<string, unknown>> {
  type: "FeatureCollection";
  features: { type: "Feature"; geometry: Geometry; properties: P }[];
}
