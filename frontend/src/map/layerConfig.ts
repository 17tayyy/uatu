// The map's layers as the viewer switches them: grouped, each with the MapLibre layers it shows.

export type LayerKey =
  | "radar"
  | "recent"
  | "detections"
  | "passAis"
  | "unimaged"
  | "vessels"
  | "events"
  | "density"
  | "coverage"
  | "satellite"
  | "infrastructure";

export interface LayerDef {
  label: string;
  hint?: string;
  on: boolean;
  ids: string[];
}

export const LAYER_GROUPS: { title: string; layers: LayerKey[] }[] = [
  { title: "Radar", layers: ["radar", "detections", "passAis", "unimaged", "recent"] },
  { title: "AIS", layers: ["vessels", "events", "density", "coverage"] },
  { title: "Contexto", layers: ["satellite", "infrastructure"] },
];

export const RADAR_FRAMES = ["radar-0", "radar-1", "radar-2", "radar-3"]; // one per frame of a pass

export const LAYERS: Record<LayerKey, LayerDef> = {
  radar: { label: "Imagen de la pasada", on: true, ids: RADAR_FRAMES },
  detections: {
    label: "Detecciones",
    on: true,
    ids: ["footprint", "pass-links", "pass-wakes", "pass-wake-shift", "pass-wake-ship", "detections"],
  },
  passAis: { label: "AIS a la hora de la pasada", on: false, ids: ["pass-ais"] },
  unimaged: { label: "Rayar lo que no ve la pasada", on: true, ids: ["unimaged"] },
  recent: { label: "Radar reciente", hint: "7 días", on: true, ids: ["radar-recent"] },
  vessels: { label: "Barcos en vivo", on: true, ids: ["vessels-moving", "vessels-stopped", "time-trails"] },
  events: { label: "Eventos", on: true, ids: ["events"] },
  density: { label: "Densidad de tráfico", hint: "7 días", on: false, ids: ["density"] },
  coverage: { label: "Cobertura AIS", hint: "7 días", on: false, ids: ["coverage"] },
  satellite: { label: "Satélite óptico", hint: "tierra", on: true, ids: ["satellite"] },
  infrastructure: { label: "Cables y tuberías", on: false, ids: ["infra-cables", "infra-pipes", "infra-points"] },
};
