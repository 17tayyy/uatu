// The map's sources and layers, bottom to top, and the symbols they draw with.
import type { ExpressionSpecification, Map as MapLibre } from "maplibre-gl";
import { C, KIND } from "../lib/constants";
import { EMPTY } from "./instance";

function icon(draw: (g: CanvasRenderingContext2D, size: number) => void, size = 32): ImageData {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const g = canvas.getContext("2d")!;
  draw(g, size);
  return g.getImageData(0, 0, size, size);
}

function arrow(fill: string): ImageData {
  return icon((g, s) => {
    g.beginPath();
    g.moveTo(s / 2, 3);
    g.lineTo(s - 8, s - 4);
    g.lineTo(s / 2, s - 10);
    g.lineTo(8, s - 4);
    g.closePath();
    g.lineJoin = "round";
    g.lineWidth = 3;
    g.strokeStyle = C.bg;
    g.stroke();
    g.fillStyle = fill;
    g.fill();
  });
}

function diamond(fill: string): ImageData {
  return icon((g, s) => {
    g.beginPath();
    g.moveTo(s / 2, 3);
    g.lineTo(s - 3, s / 2);
    g.lineTo(s / 2, s - 3);
    g.lineTo(3, s / 2);
    g.closePath();
    g.lineJoin = "round";
    g.lineWidth = 3;
    g.strokeStyle = C.bg;
    g.stroke();
    g.fillStyle = fill;
    g.fill();
  }, 28);
}

/** Diagonal lines over a dark veil: the sea a pass did not image. A texture, so it reads without colour. */
function hatch(): ImageData {
  return icon((g, s) => {
    g.fillStyle = "rgba(11, 13, 16, 0.5)";
    g.fillRect(0, 0, s, s);
    g.strokeStyle = "rgba(180, 186, 195, 0.4)";
    g.lineWidth = 1.5;
    for (const offset of [-s, 0, s]) {
      g.beginPath();
      g.moveTo(offset, s);
      g.lineTo(offset + s, 0);
      g.stroke();
    }
  }, 16);
}

const kindColor = [
  "match",
  ["get", "kind"],
  ...Object.entries(KIND).flatMap(([k, v]) => [k, v.color]),
  C.muted,
] as unknown as ExpressionSpecification;
const kindRank = [
  "match",
  ["get", "kind"],
  ...Object.entries(KIND).flatMap(([k, v]) => [k, -v.rank]),
  -9,
] as unknown as ExpressionSpecification;

/** As lib/listing.ts, for map styles: 2 sanctioned, 1 watched, 0 the rest. */
export const vesselListing: ExpressionSpecification = [
  "case",
  ["any", ["==", ["get", "sanctioned"], true], ["==", ["get", "risk"], "sanctioned_owner"]],
  2,
  ["any", ["==", ["get", "risk"], "watchlisted"], ["==", ["get", "watched"], true]],
  1,
  0,
];
const listingColor: ExpressionSpecification = ["match", vesselListing, 2, C.critical, 1, C.serious, C.text2];

export const GEOJSON_SOURCES = [
  "land",
  "zones",
  "unimaged",
  "coverage",
  "density",
  "infra",
  "footprint",
  "pass-links",
  "detections",
  "pass-ais",
  "events",
  "time-trails",
  "vessels",
  "track",
  "link",
  "selected",
] as const;

export function addLayers(map: MapLibre, recentRadarTiles: string): void {
  for (const [name, image] of [
    ["arrow", arrow(C.text2)],
    ["arrow-sanctioned", arrow(C.critical)],
    ["arrow-watch", arrow(C.serious)],
    ["hatch", hatch()],
    ["diamond-3", diamond(C.critical)],
    ["diamond-2", diamond(C.serious)],
    ["diamond-1", diamond(C.muted)],
  ] as const) {
    map.addImage(name, image, { pixelRatio: 2 });
  }
  for (const id of GEOJSON_SOURCES) map.addSource(id, { type: "geojson", data: EMPTY });

  map.addLayer({ id: "land", type: "fill", source: "land", paint: { "fill-color": C.land } });
  // The latest radar image of every place: the passes of the last week, newest on top.
  map.addSource("radar-recent", { type: "raster", tiles: [recentRadarTiles], tileSize: 256, minzoom: 5, maxzoom: 14 });
  map.addLayer({
    id: "radar-recent",
    type: "raster",
    source: "radar-recent",
    paint: { "raster-opacity": 0.95, "raster-brightness-max": 0.9, "raster-fade-duration": 0 },
  });
  // Over the radar of other days: the sea the pass on the map did not image.
  map.addLayer({
    id: "unimaged",
    type: "fill",
    source: "unimaged",
    paint: { "fill-pattern": "hatch", "fill-antialias": false },
  });
  map.addLayer({
    id: "coverage",
    type: "fill",
    source: "coverage",
    paint: {
      "fill-color": [
        "match",
        ["get", "reception"],
        "good",
        C.coverage.good,
        "patchy",
        C.coverage.patchy,
        C.coverage.poor,
      ],
      "fill-opacity": 0.5,
      "fill-outline-color": C.bg,
    },
  });
  // Where traffic gathers: vessel-hours per ~2 km cell, one blue from faint to bright.
  map.addLayer({
    id: "density",
    type: "heatmap",
    source: "density",
    paint: {
      "heatmap-weight": ["get", "weight"],
      "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 5, 0.7, 11, 1.6],
      "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 5, 5, 8, 12, 11, 34],
      "heatmap-color": [
        "interpolate",
        ["linear"],
        ["heatmap-density"],
        0,
        "rgba(29,78,168,0)",
        0.15,
        C.coverage.poor,
        0.45,
        C.coverage.patchy,
        0.75,
        C.coverage.good,
        1,
        "#e6f0fc",
      ],
      "heatmap-opacity": 0.8,
    },
  });
  map.addLayer({ id: "coast", type: "line", source: "land", paint: { "line-color": C.coast, "line-width": 0.8 } });
  map.addLayer({
    id: "infra-cables",
    type: "line",
    source: "infra",
    filter: ["==", ["get", "kind"], "telecom_cable"],
    paint: { "line-color": C.infra, "line-width": 1, "line-dasharray": [3, 2], "line-opacity": 0.8 },
  });
  map.addLayer({
    id: "infra-pipes",
    type: "line",
    source: "infra",
    filter: ["!=", ["get", "kind"], "telecom_cable"],
    paint: { "line-color": C.infra, "line-width": 1.2, "line-opacity": 0.8 },
  });
  map.addLayer({
    id: "infra-points",
    type: "circle",
    source: "infra",
    paint: { "circle-radius": 2.5, "circle-color": C.infra, "circle-stroke-width": 1, "circle-stroke-color": C.bg },
  });
  // The zones' edges: AIS is recorded and radar processed inside them only.
  map.addLayer({
    id: "zone-edges",
    type: "line",
    source: "zones",
    paint: {
      "line-color": ["case", ["get", "selected"], C.text2, C.muted],
      "line-width": 1,
      "line-opacity": ["case", ["get", "selected"], 0.8, 0.45],
      "line-dasharray": [4, 3],
    },
  });
  map.addLayer({
    id: "footprint",
    type: "line",
    source: "footprint",
    paint: { "line-color": C.muted, "line-width": 1, "line-opacity": 0.5 },
  });
  map.addLayer({
    id: "track",
    type: "line",
    source: "track",
    layout: { "line-join": "round", "line-cap": "round" },
    paint: { "line-color": C.text, "line-width": 1.6, "line-opacity": 0.8 },
  });
  // Each paired echo to where its vessel was by AIS: the Doppler effect shifts moving ships along the track.
  map.addLayer({
    id: "pass-links",
    type: "line",
    source: "pass-links",
    minzoom: 9,
    paint: { "line-color": C.text2, "line-width": 1, "line-opacity": 0.7, "line-dasharray": [2, 2] },
  });
  map.addLayer({
    id: "pass-ais",
    type: "circle",
    source: "pass-ais",
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 8, 4, 12, 8],
      "circle-color": "rgba(0,0,0,0)",
      "circle-stroke-width": 1.2,
      "circle-stroke-color": C.text2,
    },
  });
  map.addLayer({
    id: "detections",
    type: "circle",
    source: "detections",
    layout: { "circle-sort-key": kindRank },
    paint: {
      "circle-radius": [
        "interpolate",
        ["linear"],
        ["zoom"],
        8,
        ["interpolate", ["linear"], ["get", "length_m"], 0, 2.5, 300, 5],
        12,
        ["interpolate", ["linear"], ["get", "length_m"], 0, 7, 300, 12],
      ],
      // Close in, a ring around the ship's echo in the radar image rather than a dot hiding it.
      "circle-color": kindColor,
      "circle-opacity": ["interpolate", ["linear"], ["zoom"], 9.5, 1, 10.5, 0.12],
      "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 9.5, 1.5, 10.5, 2],
      "circle-stroke-color": [
        "step",
        ["zoom"],
        ["match", ["get", "kind"], "fixed", C.muted, C.bg],
        10,
        ["match", ["get", "kind"], "fixed", C.muted, kindColor],
      ],
    },
  });
  // The time machine: each vessel's last hour behind it.
  map.addLayer({
    id: "time-trails",
    type: "line",
    source: "time-trails",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": listingColor,
      "line-width": ["interpolate", ["linear"], ["zoom"], 7, 0.8, 12, 1.6],
      "line-opacity": ["match", vesselListing, 0, 0.35, 0.8],
    },
  });
  map.addLayer({
    id: "vessels-stopped",
    type: "circle",
    source: "vessels",
    filter: ["any", ["==", ["get", "cog"], null], ["<", ["coalesce", ["get", "sog"], 0], 0.5]],
    paint: {
      "circle-radius": ["match", vesselListing, 0, 2.5, 4.5],
      "circle-color": listingColor,
      "circle-stroke-width": 1,
      "circle-stroke-color": C.bg,
    },
  });
  map.addLayer({
    id: "vessels-moving",
    type: "symbol",
    source: "vessels",
    filter: ["all", ["!=", ["get", "cog"], null], [">=", ["coalesce", ["get", "sog"], 0], 0.5]],
    layout: {
      "icon-image": ["match", vesselListing, 2, "arrow-sanctioned", 1, "arrow-watch", "arrow"],
      "icon-size": [
        "interpolate",
        ["linear"],
        ["zoom"],
        7,
        ["match", vesselListing, 0, 0.6, 0.9],
        12,
        ["match", vesselListing, 0, 1, 1.4],
      ],
      "icon-rotate": ["get", "cog"],
      "icon-rotation-alignment": "map",
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
      "symbol-sort-key": vesselListing,
    },
  });
  map.addLayer({
    id: "events",
    type: "symbol",
    source: "events",
    layout: {
      "icon-image": ["concat", "diamond-", ["to-string", ["get", "severity"]]],
      "icon-size": ["interpolate", ["linear"], ["zoom"], 7, 0.75, 12, 1.1],
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
      "symbol-sort-key": ["get", "severity"],
    },
  });
  // The selection, and a line to what it pairs with (a detection and its vessel's AIS).
  map.addLayer({
    id: "link",
    type: "line",
    source: "link",
    filter: ["==", ["geometry-type"], "LineString"],
    paint: { "line-color": C.text, "line-width": 1.5, "line-dasharray": [2, 2] },
  });
  map.addLayer({
    id: "link-end",
    type: "circle",
    source: "link",
    filter: ["==", ["geometry-type"], "Point"],
    paint: {
      "circle-radius": 6,
      "circle-color": "rgba(0,0,0,0)",
      "circle-stroke-width": 1.5,
      "circle-stroke-color": C.text,
    },
  });
  map.addLayer({
    id: "selected",
    type: "circle",
    source: "selected",
    paint: {
      "circle-radius": 14,
      "circle-color": "rgba(232,234,237,0.08)",
      "circle-stroke-width": 2,
      "circle-stroke-color": C.text,
    },
  });
}

/** Optical imagery on land, under the radar (which shows the sea). */
export function addSatellite(
  map: MapLibre,
  layer: { tiles: string[]; tile_size: number; max_zoom: number; attribution: string },
  visible: boolean,
): void {
  if (map.getSource("satellite")) return;
  map.addSource("satellite", {
    type: "raster",
    tiles: layer.tiles,
    tileSize: layer.tile_size,
    maxzoom: layer.max_zoom,
    attribution: layer.attribution,
  });
  map.addLayer(
    {
      id: "satellite",
      type: "raster",
      source: "satellite",
      layout: { visibility: visible ? "visible" : "none" },
      paint: { "raster-fade-duration": 0, "raster-saturation": -0.2, "raster-brightness-max": 0.85 },
    },
    "radar-recent",
  );
}
