// Uatu dashboard: one map, one panel. Plain ES module, no build step.
// Everything that comes from AIS (names, destinations...) is untrusted: it only ever reaches the
// page as text nodes, through h().

const API = "/api/v1";
const REFRESH_MS = 60_000;
const TZ = "Europe/Madrid";

const C = {
  page: "#0d0d0d",
  panel: "#1a1a19",
  line: "#2c2c2a",
  ink: "#f0efec",
  ink2: "#c3c2b7",
  muted: "#898781",
  blue: "#3987e5",
  good: "#0ca30c",
  warning: "#fab219",
  serious: "#ec835a",
  critical: "#d03b3b",
  fixed: "#383835",
  land: "#252523",
  coast: "#3d3d39",
  infra: "#6b6a64",
  coverage: { good: "#86b6ef", patchy: "#3987e5", poor: "#184f95" },
};

// How each radar detection reads, most worth a look first.
const KIND = {
  went_silent: { label: "Dejó de emitir", color: C.critical, rank: 0 },
  unidentified: { label: "Oscuro", color: C.critical, rank: 1 },
  reception_gap: { label: "Hueco de recepción", color: C.warning, rank: 2 },
  matched: { label: "Con AIS", color: C.blue, rank: 3 },
  no_coverage: { label: "Sin AIS · mala recepción", color: C.muted, rank: 4 },
  unpaired: { label: "Antes de grabar AIS", color: C.muted, rank: 5 },
  fixed: { label: "Objeto fijo", color: C.fixed, rank: 6 },
};
const TO_REVIEW = ["went_silent", "unidentified", "reception_gap"];

const SEVERITY = {
  3: { label: "Alerta", color: C.critical },
  2: { label: "Notable", color: C.serious },
  1: { label: "Info", color: C.muted },
};

// Who owns and manages vessels (OpenSanctions): roles, sources and other watchlists, in Spanish.
const ROLE = {
  owner: "Propietario",
  commercial_manager: "Gestor comercial",
  ism_manager: "Gestor ISM (seguridad)",
  linked: "Vinculada",
  insurer: "Aseguradora P&I",
};
const SOURCE = {
  sanctions: "listas de sanciones",
  ua_war_sanctions: "GUR (Ucrania)",
  paris_mou_banned: "Paris MoU",
  tokyo_mou_detention: "detención, MoU de Tokio",
  black_sea_mou_detention: "detención, MoU del mar Negro",
};
const WATCHLIST = {
  shadow_fleet: "Flota en la sombra rusa, según la inteligencia ucraniana (GUR)",
  war_and_sanctions: "En la lista de la inteligencia ucraniana (GUR) de barcos que trabajan para Rusia",
  port_ban: "Vetado en los puertos europeos (Paris MoU)",
};
const regionName = (() => {
  const names = new Intl.DisplayNames(["es"], { type: "region" });
  return (code) => {
    try {
      return names.of(code.toUpperCase());
    } catch {
      return code.toUpperCase();
    }
  };
})();

/** How a vessel stands with sanctions: 2 sanctioned or run by a sanctioned company, 1 on other watchlists or yours. */
function listing(p) {
  if (p.sanctioned === true) return { level: 2, label: "Sancionado" };
  if (p.risk === "sanctioned_owner") return { level: 2, label: "Gestor sancionado" };
  if (p.watched === true) return { level: 1, label: "En tu lista" };
  if (p.risk === "watchlisted") return { level: 1, label: "Vigilado" };
  return { level: 0, label: null };
}
const listingTag = (p) => {
  const l = listing(p);
  return l.level ? tag("arrow", l.level === 2 ? C.critical : C.serious, l.label) : null;
};

const RECEPTION = {
  good: { label: "Buena (≥ 3 mensajes por barco)", color: C.coverage.good },
  patchy: { label: "Irregular (1,5–3)", color: C.coverage.patchy },
  poor: { label: "Pobre (< 1,5)", color: C.coverage.poor },
};

const LAYERS = {
  satellite: { label: "Satélite (óptico)", hint: "tierra", on: true, ids: ["satellite"] },
  recent: { label: "Radar reciente", hint: "7 días", on: true, ids: ["radar-recent"] },
  unimaged: { label: "Rayar lo que no ve la pasada", on: true, ids: ["unimaged"] },
  radar: { label: "Imagen radar", on: true, ids: ["radar-0", "radar-1", "radar-2", "radar-3"] }, // one per frame
  detections: { label: "Detecciones radar", on: true, ids: ["footprint", "detections"] },
  passAis: { label: "AIS en la pasada", on: false, ids: ["pass-ais"] },
  vessels: { label: "Barcos en vivo", on: true, ids: ["vessels-moving", "vessels-stopped"] },
  events: { label: "Eventos AIS", on: true, ids: ["events"] },
  infrastructure: { label: "Infraestructura", on: false, ids: ["infra-cables", "infra-pipes", "infra-points"] },
  density: { label: "Densidad de tráfico", hint: "7 días", on: false, ids: ["density"] },
  coverage: { label: "Cobertura AIS", hint: "7 días", on: false, ids: ["coverage"] },
};

const EMPTY = { type: "FeatureCollection", features: [] };

const ALL = "todas";

const state = {
  zone: loadZonePref(), // a zone's key, or ALL for the whole coast
  zones: [], // the zones analysed, south-west to north-east
  wholeCoast: null,
  status: null,
  passes: null,
  pass: null, // the pass drawn on the map
  passNote: null, // why none is, when that needs saying
  live: EMPTY,
  events: EMPTY,
  eventFilter: { minSeverity: 2, hours: 48 },
  vessel: null, // the vessel whose track is drawn
  lastVessel: null, // the last vessel opened, for the way back from one of its companies
  vesselQuery: "",
  layers: loadLayerPrefs(),
  coverageLoaded: false,
  densityLoaded: false,
  time: null, // the time machine, when on: see enterTime
};

// --- small helpers ----------------------------------------------------------------------------

function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props ?? {})) {
    if (value == null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : String(child));
  }
  return el;
}

const SVG_NS = "http://www.w3.org/2000/svg";

function svg(shape, color) {
  const el = document.createElementNS(SVG_NS, "svg");
  el.setAttribute("viewBox", "0 0 12 12");
  el.setAttribute("aria-hidden", "true");
  const part = (tag, attrs) => {
    const p = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) p.setAttribute(k, v);
    el.append(p);
  };
  switch (shape) {
    case "circle":
      part("circle", { cx: 6, cy: 6, r: 4.5, fill: color, stroke: color === C.fixed ? C.muted : "none" });
      break;
    case "ring":
      part("circle", { cx: 6, cy: 6, r: 4, fill: "none", stroke: color, "stroke-width": 1.5 });
      break;
    case "diamond":
      part("path", { d: "M6 0.8 11.2 6 6 11.2 0.8 6Z", fill: color });
      break;
    case "arrow":
      part("path", { d: "M6 0.8 10.5 11 6 8.4 1.5 11Z", fill: color });
      break;
    case "dash":
      part("path", { d: "M0.5 6H11.5", stroke: color, "stroke-width": 1.5, "stroke-dasharray": "2.5 1.5" });
      break;
    case "line":
      part("path", { d: "M0.5 6H11.5", stroke: color, "stroke-width": 1.5 });
      break;
    case "square":
      part("rect", { x: 1, y: 1, width: 10, height: 10, rx: 1.5, fill: color });
      break;
    case "hatch":
      part("rect", { x: 1, y: 1, width: 10, height: 10, rx: 1.5, fill: "rgba(13,13,13,0.5)", stroke: C.line });
      part("path", { d: "M1 7 7 1M1 11 11 1M5 11 11 5", stroke: color, "stroke-width": 1 });
      break;
  }
  return el;
}

/** replaceChildren() without the `cond && el` leftovers (false, null) turning into text. */
function fill(el, ...children) {
  el.replaceChildren(...children.flat(Infinity).filter((c) => c != null && c !== false));
}

/** A status marker: the shape in its colour beside a text label, never colour alone. */
function tag(shape, color, label) {
  return h("span", { class: "tag" }, svg(shape, color), label);
}

const dayKey = new Intl.DateTimeFormat("en-CA", { timeZone: TZ });
const fmtTime = new Intl.DateTimeFormat("es-ES", { timeZone: TZ, hour: "2-digit", minute: "2-digit" });
const fmtDay = new Intl.DateTimeFormat("es-ES", { timeZone: TZ, day: "numeric", month: "short" });
const fmtFull = new Intl.DateTimeFormat("es-ES", { timeZone: TZ, dateStyle: "long", timeStyle: "short" });
const fmtNumber = new Intl.NumberFormat("es-ES");
const fmtMonth = new Intl.DateTimeFormat("es-ES", { timeZone: "UTC", month: "short", year: "numeric" });
const month = (isoDate) => fmtMonth.format(new Date(isoDate));
const capitalise = (text) => text.charAt(0).toUpperCase() + text.slice(1);

function when(iso) {
  const d = new Date(iso);
  const today = dayKey.format(new Date());
  const yesterday = dayKey.format(new Date(Date.now() - 86_400_000));
  const day = dayKey.format(d);
  const prefix = day === today ? "hoy" : day === yesterday ? "ayer" : fmtDay.format(d);
  return `${prefix} ${fmtTime.format(d)}`;
}

function ago(iso) {
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 60) return `hace ${Math.max(1, Math.round(seconds))} s`;
  if (seconds < 3600) return `hace ${Math.round(seconds / 60)} min`;
  if (seconds < 6 * 3600) return `hace ${duration(seconds / 60)}`;
  return when(iso);
}

function duration(minutes) {
  const m = Math.round(minutes);
  if (m < 60) return `${m} min`;
  const hours = Math.floor(m / 60);
  return m % 60 ? `${hours} h ${m % 60} min` : `${hours} h`;
}

function plural(n, one, many) {
  return `${fmtNumber.format(n)} ${n === 1 ? one : many}`;
}

function metres(m) {
  return m >= 1000 ? `${(m / 1000).toLocaleString("es-ES", { maximumFractionDigits: 1 })} km` : `${Math.round(m)} m`;
}

function utc(iso) {
  return `${new Date(iso).toISOString().slice(11, 16)} UTC`;
}

async function api(path) {
  const res = await fetch(API + path, { headers: { Accept: "application/json" } });
  let body = null;
  try {
    body = await res.json();
  } catch {
    // not JSON: fall through to the status
  }
  if (!res.ok) throw new Error(body?.error?.message ?? `HTTP ${res.status}`);
  return body.data;
}

function loadZonePref() {
  try {
    return localStorage.getItem("uatu.zone") ?? ALL;
  } catch {
    return ALL;
  }
}

function saveZonePref() {
  try {
    localStorage.setItem("uatu.zone", state.zone);
  } catch {
    // not remembered: fine
  }
}

/** The path with the zone the dashboard looks at. */
function inZone(path) {
  return `${path}${path.includes("?") ? "&" : "?"}zone=${encodeURIComponent(state.zone)}`;
}

function zoneName(key) {
  return state.zones.find((z) => z.key === key)?.name ?? key;
}

/** The box the dashboard looks at: the zone's, or the whole coast's. */
function scopeArea() {
  return state.zones.find((z) => z.key === state.zone) ?? state.wholeCoast;
}

function loadLayerPrefs() {
  const prefs = Object.fromEntries(Object.entries(LAYERS).map(([key, layer]) => [key, layer.on]));
  try {
    Object.assign(prefs, JSON.parse(localStorage.getItem("uatu.layers.v2") ?? "{}"));
  } catch {
    // private window or blocked storage: defaults
  }
  return prefs;
}

function saveLayerPrefs() {
  try {
    localStorage.setItem("uatu.layers.v2", JSON.stringify(state.layers));
  } catch {
    // not remembered: fine
  }
}

// --- map ------------------------------------------------------------------------------------

const map = new maplibregl.Map({
  container: "map",
  style: {
    version: 8,
    sources: {},
    layers: [{ id: "sea", type: "background", paint: { "background-color": C.page } }],
  },
  center: [-5.5, 36],
  zoom: 8,
  minZoom: 4,
  maxZoom: 15,
  attributionControl: false,
  dragRotate: false,
  pitchWithRotate: false,
});
map.touchZoomRotate.disableRotation();
map.keyboard.disableRotation();
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
map.addControl(new maplibregl.ScaleControl({ unit: "nautical" }), "bottom-left");
map.addControl(
  new maplibregl.AttributionControl({
    compact: true,
    customAttribution:
      "Contiene datos modificados de Copernicus Sentinel · ESA WorldCover · EMODnet · TeleGeography · aisstream.io · OpenSanctions",
  }),
  "bottom-left",
);

function icon(draw, size = 32) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const g = canvas.getContext("2d");
  draw(g, size);
  return g.getImageData(0, 0, size, size);
}

function arrowIcon(fill) {
  return icon((g, s) => {
    g.beginPath();
    g.moveTo(s / 2, 3);
    g.lineTo(s - 8, s - 4);
    g.lineTo(s / 2, s - 10);
    g.lineTo(8, s - 4);
    g.closePath();
    g.lineJoin = "round";
    g.lineWidth = 3;
    g.strokeStyle = C.page;
    g.stroke();
    g.fillStyle = fill;
    g.fill();
  });
}

/** Diagonal lines over a dark veil: the sea a pass did not image. A texture, so it reads without colour. */
function hatchPattern() {
  return icon((g, s) => {
    g.fillStyle = "rgba(13, 13, 13, 0.5)";
    g.fillRect(0, 0, s, s);
    g.strokeStyle = "rgba(195, 194, 183, 0.45)";
    g.lineWidth = 1.5;
    for (const offset of [-s, 0, s]) {
      g.beginPath();
      g.moveTo(offset, s);
      g.lineTo(offset + s, 0);
      g.stroke();
    }
  }, 16);
}

function diamondIcon(fill) {
  return icon((g, s) => {
    g.beginPath();
    g.moveTo(s / 2, 3);
    g.lineTo(s - 3, s / 2);
    g.lineTo(s / 2, s - 3);
    g.lineTo(3, s / 2);
    g.closePath();
    g.lineJoin = "round";
    g.lineWidth = 3;
    g.strokeStyle = C.page;
    g.stroke();
    g.fillStyle = fill;
    g.fill();
  }, 28);
}

const kindColor = ["match", ["get", "kind"], ...Object.entries(KIND).flatMap(([k, v]) => [k, v.color]), C.muted];
const kindRank = ["match", ["get", "kind"], ...Object.entries(KIND).flatMap(([k, v]) => [k, -v.rank]), -9];
// As listing() above, for map styles.
const vesselListing = [
  "case",
  ["any", ["==", ["get", "sanctioned"], true], ["==", ["get", "risk"], "sanctioned_owner"]], 2,
  ["any", ["==", ["get", "risk"], "watchlisted"], ["==", ["get", "watched"], true]], 1,
  0,
];

function addLayers() {
  for (const [name, image] of [
    ["arrow", arrowIcon(C.ink2)],
    ["arrow-sanctioned", arrowIcon(C.critical)],
    ["arrow-watch", arrowIcon(C.serious)],
    ["hatch", hatchPattern()],
    ["diamond-3", diamondIcon(C.critical)],
    ["diamond-2", diamondIcon(C.serious)],
    ["diamond-1", diamondIcon(C.muted)],
  ]) {
    map.addImage(name, image, { pixelRatio: 2 });
  }
  for (const id of ["land", "zones", "unimaged", "coverage", "density", "infra", "footprint", "detections", "pass-ais", "events", "time-trails", "vessels", "track", "link", "selected"]) {
    map.addSource(id, { type: "geojson", data: EMPTY });
  }

  // Land under the radar images (which show the land too where the satellite imaged it), the
  // coastline over them.
  map.addLayer({ id: "land", type: "fill", source: "land", paint: { "fill-color": C.land } });
  // The latest radar image of every place: the passes of the last days, newest on top.
  map.addSource("radar-recent", { type: "raster", tiles: [recentRadarUrl()], tileSize: 256, minzoom: 5, maxzoom: 14 });
  map.addLayer({
    id: "radar-recent",
    type: "raster",
    source: "radar-recent",
    layout: { visibility: "none" }, // until start() knows the latest pass, which versions its tiles
    paint: { "raster-opacity": 0.95, "raster-brightness-max": 0.9, "raster-fade-duration": 0 },
  });
  // Over the radar of other days: the sea the pass on the map did not image, where nothing was looked for.
  map.addLayer({ id: "unimaged", type: "fill", source: "unimaged", paint: { "fill-pattern": "hatch", "fill-antialias": false } });
  map.addLayer({
    id: "coverage",
    type: "fill",
    source: "coverage",
    paint: {
      "fill-color": ["match", ["get", "reception"], "good", C.coverage.good, "patchy", C.coverage.patchy, C.coverage.poor],
      "fill-opacity": 0.5,
      "fill-outline-color": C.page,
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
      "heatmap-color": ["interpolate", ["linear"], ["heatmap-density"], 0, "rgba(24,79,149,0)", 0.15, C.coverage.poor, 0.45, C.coverage.patchy, 0.75, C.coverage.good, 1, "#e6f0fc"],
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
    paint: { "circle-radius": 2.5, "circle-color": C.infra, "circle-stroke-width": 1, "circle-stroke-color": C.page },
  });
  // The edges of the zones: AIS is recorded and radar processed inside them only.
  map.addLayer({
    id: "zone-edges",
    type: "line",
    source: "zones",
    paint: {
      "line-color": ["case", ["get", "selected"], C.ink2, C.muted],
      "line-width": 1,
      "line-opacity": ["case", ["get", "selected"], 0.8, 0.5],
      "line-dasharray": [4, 3],
    },
  });
  map.addLayer({
    id: "footprint",
    type: "line",
    source: "footprint",
    paint: { "line-color": C.muted, "line-width": 1, "line-opacity": 0.45 },
  });
  map.addLayer({
    id: "track",
    type: "line",
    source: "track",
    layout: { "line-join": "round", "line-cap": "round" },
    paint: { "line-color": C.ink, "line-width": 1.5, "line-opacity": 0.75 },
  });
  map.addLayer({
    id: "pass-ais",
    type: "circle",
    source: "pass-ais",
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 8, 4, 12, 8],
      "circle-color": "rgba(0,0,0,0)",
      "circle-stroke-width": 1.2,
      "circle-stroke-color": C.ink2,
    },
  });
  map.addLayer({
    id: "detections",
    type: "circle",
    source: "detections",
    layout: { "circle-sort-key": kindRank },
    paint: {
      "circle-radius": [
        "interpolate", ["linear"], ["zoom"],
        8, ["interpolate", ["linear"], ["get", "length_m"], 0, 2.5, 300, 5],
        12, ["interpolate", ["linear"], ["get", "length_m"], 0, 7, 300, 12],
      ],
      // Close in, a ring around the ship's echo in the radar image rather than a dot hiding it.
      "circle-color": kindColor,
      "circle-opacity": ["interpolate", ["linear"], ["zoom"], 9.5, 1, 10.5, 0.12],
      "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 9.5, 1.5, 10.5, 2],
      "circle-stroke-color": [
        "step", ["zoom"],
        ["match", ["get", "kind"], "fixed", C.muted, C.page],
        10, ["match", ["get", "kind"], "fixed", C.muted, kindColor],
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
      "line-color": ["match", vesselListing, 2, C.critical, 1, C.serious, C.ink2],
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
      "circle-color": ["match", vesselListing, 2, C.critical, 1, C.serious, C.ink2],
      "circle-stroke-width": 1,
      "circle-stroke-color": C.page,
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
        "interpolate", ["linear"], ["zoom"],
        7, ["match", vesselListing, 0, 0.6, 0.9],
        12, ["match", vesselListing, 0, 1, 1.4],
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
  // A detection and where its vessel was by AIS at the same moment.
  map.addLayer({
    id: "link",
    type: "line",
    source: "link",
    filter: ["==", ["geometry-type"], "LineString"],
    paint: { "line-color": C.ink, "line-width": 1.5, "line-dasharray": [2, 2] },
  });
  map.addLayer({
    id: "link-end",
    type: "circle",
    source: "link",
    filter: ["==", ["geometry-type"], "Point"],
    paint: { "circle-radius": 6, "circle-color": "rgba(0,0,0,0)", "circle-stroke-width": 1.5, "circle-stroke-color": C.ink },
  });
  map.addLayer({
    id: "selected",
    type: "circle",
    source: "selected",
    paint: { "circle-radius": 13, "circle-color": "rgba(0,0,0,0)", "circle-stroke-width": 2, "circle-stroke-color": C.ink },
  });
}

const phone = matchMedia("(max-width: 760px)");

function fitPadding() {
  const sheet = phone.matches ? document.getElementById("panel").offsetHeight : 0;
  map.setPadding({ top: 0, right: 0, left: 0, bottom: sheet });
}

function setData(source, data) {
  map.getSource(source)?.setData(data ?? EMPTY);
}

function applyLayerVisibility() {
  for (const [key, layer] of Object.entries(LAYERS)) {
    for (const id of layer.ids) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", state.layers[key] ? "visible" : "none");
    }
  }
}

/** The pass's radar image: one per frame it came in. */
function showRadarImage(pass) {
  for (const id of LAYERS.radar.ids) {
    if (map.getLayer(id)) map.removeLayer(id);
    if (map.getSource(id)) map.removeSource(id);
  }
  (pass?.images ?? []).slice(0, LAYERS.radar.ids.length).forEach((image, i) => {
    const id = LAYERS.radar.ids[i];
    // At 20 m as map tiles when the pass has them (detail on zooming in); else the frame at 80 m as one image.
    map.addSource(
      id,
      image.tiles
        ? { type: "raster", tiles: [image.tiles], tileSize: 256, bounds: image.bounds, minzoom: 5, maxzoom: 14 }
        : { type: "image", url: image.url, coordinates: image.coordinates },
    );
    map.addLayer(
      {
        id,
        type: "raster",
        source: id,
        layout: { visibility: state.layers.radar ? "visible" : "none" },
        paint: { "raster-opacity": 0.95, "raster-brightness-max": 0.9, "raster-fade-duration": 0 },
      },
      "coverage",
    );
  });
}

function recentRadarUrl() {
  const latest = state.status?.latest_pass?.id ?? ""; // a new pass, new tiles
  return `${location.origin}${API}/radar/tiles/{z}/{x}/{y}.png?zone=${encodeURIComponent(state.zone)}&days=7&v=${latest}`;
}

let recentUrl = null;

/** New tiles for a new pass or zone; the same URL again would fetch them all again. */
function updateRecentRadar() {
  const url = recentRadarUrl();
  if (url === recentUrl) return;
  recentUrl = url;
  map.getSource("radar-recent")?.setTiles([url]);
}

/** Each zone's edge, the selected one brighter. */
function zoneEdges() {
  return {
    type: "FeatureCollection",
    features: state.zones.map((z) => ({
      type: "Feature",
      geometry: {
        type: "LineString",
        coordinates: [[z.west, z.south], [z.east, z.south], [z.east, z.north], [z.west, z.north], [z.west, z.south]],
      },
      properties: { selected: z.key === state.zone },
    })),
  };
}

function select(lngLat) {
  setData("selected", lngLat ? { type: "Point", coordinates: lngLat } : EMPTY);
  if (!lngLat) setData("link", EMPTY);
}

/** A dashed line from a detection to where its vessel was by AIS at the time of the pass. */
function link(from, to) {
  setData("link", {
    type: "FeatureCollection",
    features: [
      { type: "Feature", geometry: { type: "LineString", coordinates: [from, to] }, properties: {} },
      { type: "Feature", geometry: { type: "Point", coordinates: to }, properties: {} },
    ],
  });
}

/** Whether a lon/lat point falls inside a GeoJSON Polygon or MultiPolygon (outer rings). */
function contains(geometry, [x, y]) {
  const polygons = geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates;
  return polygons.some(([ring]) => {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i];
      const [xj, yj] = ring[j];
      if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  });
}

function bounds(coordinates) {
  const b = new maplibregl.LngLatBounds();
  const walk = (c) => (typeof c[0] === "number" ? b.extend(c) : c.forEach(walk));
  walk(coordinates);
  return b;
}

function fitToArea(b) {
  const a = scopeArea();
  if (a) {
    b = new maplibregl.LngLatBounds(
      [Math.max(b.getWest(), a.west), Math.max(b.getSouth(), a.south)],
      [Math.min(b.getEast(), a.east), Math.min(b.getNorth(), a.north)],
    );
  }
  map.fitBounds(b, { padding: 40, maxZoom: 11, duration: 600 });
}

function flyTo(lngLat, zoom = 11) {
  map.flyTo({ center: lngLat, zoom: Math.max(map.getZoom(), zoom), duration: 700 });
}

// Popups ----------------------------------------------------------------------------------------

let popup = null;

function openPopup(lngLat, content) {
  popup?.remove();
  // On a phone the sheet covers the bottom of the map: keep the popup above the point.
  popup = new maplibregl.Popup({ maxWidth: "300px", focusAfterOpen: false, anchor: phone.matches ? "bottom" : undefined })
    .setLngLat(lngLat)
    .setDOMContent(h("div", { class: "popup" }, content))
    .addTo(map);
  popup.on("close", () => select(null));
  select(lngLat);
}

function kv(pairs) {
  return h(
    "dl",
    { class: "kv" },
    pairs.filter(([, v]) => v != null && v !== "").flatMap(([k, v]) => [h("dt", null, k), h("dd", null, v)]),
  );
}

function externalLink(label, href) {
  return h("a", { href, target: "_blank", rel: "noopener noreferrer" }, `${label} ↗`);
}

function vesselButton(mmsi, label = "Ver barco") {
  return h("button", { onclick: () => navigate(`vessels/${mmsi}`) }, label);
}

function detectionPopup(p, lngLat) {
  const kind = KIND[p.kind] ?? KIND.unpaired;
  const rows = [
    ["Hora", `${fmtTime.format(new Date(p.seen_at))} (${utc(p.seen_at)})`],
    ["Eslora aprox.", p.length_m != null ? `${p.length_m} m` : null],
    ["Contraste", p.contrast_db != null ? `${p.contrast_db} dB` : null],
    ["VH", p.vh_db != null ? `${p.vh_db} dB` : null],
  ];
  const actions = [];
  const ais = p.mmsi ? byId.passAis.get(p.mmsi) : null;
  if (p.mmsi) {
    rows.push(["Barco AIS", p.vessel_name ?? `MMSI ${p.mmsi}`]);
    if (p.distance_m != null) rows.push(["Distancia", metres(p.distance_m)]);
    actions.push(vesselButton(p.mmsi));
  }
  if (p.suspected_mmsi) {
    rows.push(["Posible barco", p.suspect_name ?? `MMSI ${p.suspected_mmsi}`]);
    rows.push(["Sin emitir", duration(p.suspect_silent_minutes)]);
    actions.push(vesselButton(p.suspected_mmsi, "Ver posible barco"));
  }
  if (p.fixed_passes_seen) rows.push(["Visto en", plural(p.fixed_passes_seen, "pasada", "pasadas")]);
  const reception = receptionBox(p);
  const text = {
    went_silent: "Ningún AIS aquí, pero un barco que dejó de emitir hace rato pudo llegar hasta este punto.",
    unidentified: "Ningún AIS aquí, ningún barco callado que lo explique y buena recepción alrededor.",
    reception_gap: "Sin AIS aquí, pero un barco que lleva poco sin oírse pudo estar en este punto: probablemente recepción.",
    no_coverage: "Ningún AIS aquí, pero alrededor aisstream oye poco: puede que emitiera y no le llegara.",
    fixed: "Un punto que aparece pasada tras pasada: plataforma, boya o fondeo largo.",
  }[p.kind];
  openPopup(lngLat, [
    tag("circle", kind.color, kind.label),
    h("p", { class: "popup-title" }, "Detección radar"),
    text && h("p", { class: "text" }, text),
    ais && h("p", { class: "text" }, "La línea discontinua lleva a donde estaba ese barco según su AIS."),
    kv(rows),
    reception,
    actions.length && h("div", { class: "actions" }, actions),
    opticalBox(p.id, lngLat),
  ]);
  if (ais) link(lngLat, ais.geometry.coordinates);
}

/** A button that looks for a Sentinel-2 photo of the detection's place, and lays it on the map. */
function opticalBox(detectionId, lngLat) {
  const box = h("div", { class: "optical" });
  const look = async () => {
    fill(box, h("div", { class: "optical-caption busy" }, h("span", { class: "spinner small" }), "Buscando la foto más clara de ±3 días…"));
    try {
      const image = await api(`/detections/${detectionId}/optical`);
      if (!image) {
        fill(box, h("div", { class: "optical-caption" }, "No hay ninguna foto de Sentinel-2 sin nubes de este sitio en ±3 días."));
        return;
      }
      showOptical(image, lngLat);
    } catch (error) {
      fill(box, h("div", { class: "optical-caption" }, `No se pudo buscar: ${error.message}`));
    }
  };
  const shown = optical?.lngLat?.[0] === lngLat[0] && optical?.lngLat?.[1] === lngLat[1];
  fill(
    box,
    shown
      ? h("div", { class: "optical-caption" }, "Su foto óptica está en el mapa.")
      : [
          h("button", { class: "optical-button", onclick: look }, "Ver foto óptica en el mapa (Sentinel-2)"),
          h(
            "div",
            { class: "optical-caption" },
            "Sentinel-2 pasa hacia mediodía, horas antes o después del radar: un barco en marcha ya no estará ahí; uno fondeado, una plataforma o una granja, sí.",
          ),
        ],
  );
  return box;
}

// The Sentinel-2 photo of a detection's place, laid on the map where it belongs: zoom in to look.
let optical = null; // { image, lngLat }

function showOptical(image, lngLat) {
  const source = map.getSource("optical");
  if (source) source.updateImage({ url: image.url, coordinates: image.corners });
  else {
    map.addSource("optical", { type: "image", url: image.url, coordinates: image.corners });
    // Over the radar, under the coastline, the detections and the ring on this one.
    map.addLayer({ id: "optical", type: "raster", source: "optical", paint: { "raster-fade-duration": 0 } }, "coast");
  }
  optical = { image, lngLat };
  popup?.remove();
  select(lngLat);
  renderOpticalCard();
  // The whole photo in view, clear of the card above it (and of the layers card, beside it on a wide screen).
  const card = document.getElementById("optical-card").getBoundingClientRect();
  const top = Math.max(20, card.bottom - map.getContainer().getBoundingClientRect().top + 12);
  const lons = image.corners.map((c) => c[0]);
  const lats = image.corners.map((c) => c[1]);
  map.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]], {
    padding: { top, bottom: 20, left: 20, right: phone.matches ? 20 : 240 },
    duration: 700,
  });
}

function hideOptical() {
  if (!optical) return;
  if (map.getLayer("optical")) map.removeLayer("optical");
  if (map.getSource("optical")) map.removeSource("optical");
  optical = null;
  select(null);
  renderOpticalCard();
}

function renderOpticalCard() {
  const card = document.getElementById("optical-card");
  card.hidden = !optical;
  if (!optical) return fill(card);
  const opacity = h("input", { type: "range", min: 0, max: 100, value: 100, "aria-label": "Opacidad de la foto" });
  opacity.addEventListener("input", () => map.setPaintProperty("optical", "raster-opacity", opacity.value / 100));
  fill(
    card,
    h(
      "div",
      { class: "optical-card-top" },
      h("span", null, h("strong", null, "Foto óptica "), opticalCaption(optical.image)),
      h("button", { class: "close", onclick: hideOptical, "aria-label": "Quitar la foto", title: "Quitar la foto" }, "×"),
    ),
    h("label", { class: "optical-opacity" }, "Opacidad", opacity),
  );
}

function opticalCaption(image) {
  const minutes = Math.abs(image.minutes_from_radar);
  const gap = minutes < 48 * 60 ? duration(minutes) : `${Math.round(minutes / 1440)} días`;
  const side = image.minutes_from_radar >= 0 ? "después" : "antes";
  const km = (image.size_m / 1000).toLocaleString("es-ES");
  return [
    `Sentinel-${image.platform.replace("sentinel-", "").toUpperCase()}`,
    when(image.acquired_at),
    `${gap} ${side} del radar`,
    `nubes ${Math.round(image.cloud * 100)} %`,
    `${km} × ${km} km`,
  ].join(" · ");
}

/** For an unpaired detection: how much AIS was heard around it, against what counts as well received. */
function receptionBox(p) {
  const rule = state.pass?.reception_rule;
  if (p.ais_vessels_nearby == null || p.ais_reports_nearby == null || !rule) return null;
  const perVessel = p.ais_vessels_nearby ? p.ais_reports_nearby / p.ais_vessels_nearby : 0;
  const ok = p.ais_vessels_nearby >= rule.min_vessels && perVessel >= rule.min_reports_per_vessel;
  const fmt = (x) => x.toLocaleString("es-ES", { maximumFractionDigits: 1 });
  return h(
    "div",
    { class: "reception" },
    h("div", { class: "reception-title" }, `AIS alrededor (${metres(rule.radius_m)}, ±${fmt(rule.window_hours)} h)`),
    receptionRow(plural(p.ais_vessels_nearby, "barco oído", "barcos oídos"), rule.min_vessels, p.ais_vessels_nearby >= rule.min_vessels),
    receptionRow(`${fmt(perVessel)} mensajes por barco`, fmt(rule.min_reports_per_vessel), perVessel >= rule.min_reports_per_vessel),
    h("div", { class: "reception-verdict" }, ok ? "Recepción buena: su silencio cuenta." : "Recepción insuficiente: su silencio no dice nada."),
  );
}

function receptionRow(value, minimum, met) {
  return h(
    "div",
    { class: "reception-row num" },
    h("span", null, value),
    h("span", { class: "need" }, `mínimo ${minimum} `, h("span", { class: met ? "met" : "unmet" }, met ? "✓" : "✗")),
  );
}

function eventPopup(p, lngLat, pass) {
  const sev = SEVERITY[p.severity] ?? SEVERITY[1];
  openPopup(lngLat, [
    tag("diamond", sev.color, sev.label),
    h("p", { class: "popup-title" }, p.title),
    h("p", { class: "text" }, p.message),
    kv([
      ["Desde", when(p.start_at)],
      ["Hasta", p.end_at ? when(p.end_at) : "sigue"],
      ["Satélite", pass ? `pasó ${when(pass.acquired_at)}` : "no pasó por aquí durante el evento"],
    ]),
    h(
      "div",
      { class: "actions" },
      vesselButton(p.mmsi),
      pass && h("button", { onclick: () => navigate(`passes/${pass.id}`) }, "Ver pasada"),
      h("button", { onclick: () => replayEvent(p, lngLat) }, "Máquina del tiempo"),
    ),
  ]);
}

/** The pass that imaged an event's place while it was going on (give or take half an hour), if any. */
function passForEvent(p, lngLat) {
  const start = new Date(p.start_at).getTime() - 30 * 60_000;
  const end = (p.end_at ? new Date(p.end_at).getTime() : Date.now()) + 30 * 60_000;
  return (state.passes ?? []).find((pass) => {
    const t = new Date(pass.acquired_at).getTime();
    return t >= start && t <= end && contains(pass.footprint, lngLat);
  });
}

/** An event, with the map showing the pass of that moment, or no radar at all if none passed. */
async function showEvent(p, lngLat) {
  const pass = passForEvent(p, lngLat);
  if (pass) await loadPass(pass.id).catch(() => {});
  else clearPass("Sin imagen radar: ningún satélite pasó durante este evento");
  eventPopup(p, lngLat, pass);
}

function passAisPopup(p, lngLat) {
  const detection = [...byId.detections.values()].find((d) => d.mmsi === p.mmsi);
  openPopup(lngLat, [
    tag("ring", C.ink2, p.status === "detected" ? "Visto por el radar" : "No visto por el radar"),
    h("p", { class: "popup-title" }, p.name ?? `MMSI ${p.mmsi}`),
    h("p", { class: "text" }, "Posición AIS interpolada a la hora de la pasada."),
    kv([
      ["Eslora", p.length_m != null ? `${p.length_m} m` : null],
      ["Velocidad", p.sog != null ? `${p.sog} nudos` : null],
    ]),
    h("div", { class: "actions" }, vesselButton(p.mmsi)),
  ]);
  if (detection) link(lngLat, byId.detectionFeatures.get(detection.id).geometry.coordinates);
}

const hover = new maplibregl.Popup({ closeButton: false, closeOnClick: false, className: "hover-popup", offset: 10 });

function wireMapEvents() {
  const clickable = ["vessels-moving", "vessels-stopped", "events", "detections", "pass-ais"];
  for (const id of clickable) {
    map.on("mouseenter", id, () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", id, () => {
      map.getCanvas().style.cursor = "";
      hover.remove();
    });
  }
  const tooltip = (e, text) => hover.setLngLat(e.lngLat).setText(text).addTo(map);
  for (const id of ["vessels-moving", "vessels-stopped"]) {
    map.on("mousemove", id, (e) => {
      const p = e.features[0].properties;
      const speed = p.sog != null && p.sog !== "null" ? ` · ${p.sog} nudos` : "";
      const listed = listing(p).label ? ` · ${listing(p).label}` : "";
      const at = state.time ? ` · oído ${when(p.received_at)}` : " · ahora";
      tooltip(e, `${p.name && p.name !== "null" ? p.name : `MMSI ${p.mmsi}`}${speed}${at}${listed}`);
    });
  }
  map.on("mousemove", "detections", (e) => {
    const p = parseProperties(e.features[0].properties);
    const who = p.mmsi ? (p.vessel_name ?? `MMSI ${p.mmsi}`) : p.suspected_mmsi ? `¿${p.suspect_name ?? p.suspected_mmsi}?` : null;
    tooltip(e, [(KIND[p.kind] ?? KIND.unpaired).label, who, p.length_m && `${p.length_m} m`].filter(Boolean).join(" · "));
  });
  map.on("mousemove", "pass-ais", (e) => {
    const p = parseProperties(e.features[0].properties);
    tooltip(e, `${p.name ?? `MMSI ${p.mmsi}`} · AIS a la hora de la pasada`);
  });
  map.on("mousemove", "events", (e) => tooltip(e, parseProperties(e.features[0].properties).title));
  map.on("click", (e) => {
    const [feature] = map.queryRenderedFeatures(e.point, { layers: clickable.filter((id) => map.getLayer(id)) });
    if (!feature) return;
    hover.remove();
    const lngLat = feature.geometry.coordinates;
    const p = parseProperties(feature.properties);
    switch (feature.layer.id) {
      case "vessels-moving":
      case "vessels-stopped":
        navigate(`vessels/${p.mmsi}`);
        break;
      case "events":
        showEvent(p, lngLat);
        break;
      case "detections":
        detectionPopup(p, lngLat);
        break;
      case "pass-ais":
        passAisPopup(p, lngLat);
        break;
    }
  });
}

// MapLibre hands back feature properties flattened (nested objects as JSON strings); the
// originals are kept by id instead.
const byId = { detections: new Map(), detectionFeatures: new Map(), events: new Map(), passAis: new Map() };

function parseProperties(p) {
  if ("kind" in p && "seen_at" in p) return byId.detections.get(p.id) ?? p;
  if ("severity" in p) return byId.events.get(p.id) ?? p;
  if ("status" in p && byId.passAis.has(p.mmsi)) return byId.passAis.get(p.mmsi).properties;
  return p;
}

// --- layers card and legend -------------------------------------------------------------------

function renderLayers() {
  const body = document.getElementById("layers-body");
  fill(body, 
    ...Object.entries(LAYERS).map(([key, layer]) => {
      const input = h("input", { type: "checkbox", checked: state.layers[key] });
      input.addEventListener("change", () => {
        state.layers[key] = input.checked;
        saveLayerPrefs();
        applyLayerVisibility();
        if (key === "coverage" && input.checked) loadCoverage();
        if (key === "density" && input.checked) loadDensity();
        renderLegend();
      });
      return h("label", { class: "toggle" }, input, layer.label, layer.hint && h("span", { class: "hint" }, layer.hint));
    }),
  );
  const toggle = document.getElementById("layers-toggle");
  toggle.onclick = () => toggle.setAttribute("aria-expanded", toggle.getAttribute("aria-expanded") === "true" ? "false" : "true");
  if (phone.matches) toggle.setAttribute("aria-expanded", "false");
}

function legendItem(shape, color, label) {
  return h("div", { class: "legend-item" }, svg(shape, color), label);
}

function renderLegend() {
  const parts = [];
  if (state.layers.detections && state.pass) {
    const present = new Set(state.pass.detections_geojson.features.map((f) => f.properties.kind));
    parts.push(
      h("h3", null, "Detecciones radar"),
      Object.entries(KIND)
        .filter(([k]) => present.has(k))
        .map(([, v]) => legendItem("circle", v.color, v.label)),
    );
  }
  if (state.layers.passAis && state.pass) parts.push(legendItem("ring", C.ink2, "AIS a la hora de la pasada"));
  if (state.layers.events) {
    parts.push(h("h3", null, "Eventos AIS"), [3, 2, 1].filter((s) => s >= state.eventFilter.minSeverity).map((s) => legendItem("diamond", SEVERITY[s].color, SEVERITY[s].label)));
  }
  if (state.layers.vessels) {
    parts.push(h("h3", null, "Barcos en vivo"), legendItem("arrow", C.ink2, "Navegando (rumbo)"), legendItem("circle", C.ink2, "Parado"), legendItem("arrow", C.critical, "Sancionado o con gestor sancionado"), legendItem("arrow", C.serious, "En listas de vigilancia"));
  }
  if (state.layers.infrastructure) {
    parts.push(h("h3", null, "Infraestructura"), legendItem("dash", C.infra, "Cable submarino"), legendItem("line", C.infra, "Gasoducto u oleoducto"));
  }
  if (state.layers.coverage) {
    parts.push(h("h3", null, "Recepción AIS"), Object.values(RECEPTION).map((r) => legendItem("square", r.color, r.label)));
  }
  if (state.layers.density) {
    parts.push(h("h3", null, "Densidad de tráfico (7 días)"), legendItem("square", C.coverage.poor, "Poco tráfico"), legendItem("square", C.coverage.good, "Mucho tráfico"));
  }
  parts.push(h("h3", null, "Mapa"), legendItem("dash", C.muted, "Zonas vigiladas"));
  if (state.layers.unimaged) {
    parts.push(legendItem("hatch", C.ink2, state.pass ? "Fuera de esta pasada: sin analizar" : "Sin pasada de radar: sin analizar"));
  }
  if (state.layers.recent) parts.push(legendItem("square", "#8a8a85", "Radar: última pasada de cada sitio, 7 días"));
  fill(document.getElementById("legend"), parts);
}

// --- status -----------------------------------------------------------------------------------

function renderStatus(error) {
  const el = document.getElementById("status");
  if (error) {
    fill(el, h("div", { class: "live" }, h("span", { class: "dot critical" }), "Sin conexión con el servidor"));
    return;
  }
  const s = state.status;
  if (!s) return;
  const fresh = s.last_ais_at && Date.now() - new Date(s.last_ais_at).getTime() < 10 * 60_000;
  const live = s.recording && fresh;
  fill(el, 
    h(
      "div",
      { class: "live" },
      h("span", { class: `dot ${live ? "good" : "critical"}` }),
      live ? "Grabando AIS" : "AIS detenido",
      s.last_ais_at && h("span", { class: "facts" }, `· último mensaje ${ago(s.last_ais_at)}`),
    ),
    h(
      "div",
      { class: "facts num" },
      `${plural(s.vessels_last_hour, "barco", "barcos")} en la última hora · ${plural(s.events_24h, "evento", "eventos")} en 24 h`,
    ),
    s.latest_pass &&
      h(
        "div",
        { class: "facts" },
        `Última pasada ${when(s.latest_pass.acquired_at)} · Sentinel-${s.latest_pass.platform.slice(1)}`,
        state.zone === ALL ? ` · ${zoneName(s.latest_pass.zone)}` : "",
      ),
    state.reports?.length > 0 &&
      h(
        "div",
        { class: "facts" },
        h("a", { href: state.reports[0].url, target: "_blank", rel: "noopener" }, `Informe hasta el ${fmtDay.format(new Date(`${state.reports[0].last_day}T12:00:00Z`))}`),
        state.reports.length > 1 ? ` · ${state.reports.length} informes` : "",
      ),
  );
}

// --- views ------------------------------------------------------------------------------------

const view = document.getElementById("view");
let route = { tab: "passes", id: null };

function navigate(path) {
  if (location.hash === `#/${path}`) render();
  else location.hash = `#/${path}`;
}

function parseRoute() {
  const [tab, id] = location.hash.replace(/^#\/?/, "").split("/");
  if (tab === "companies" && id) return { tab, id: decodeURIComponent(id) }; // under the vessels tab
  return { tab: ["passes", "events", "vessels"].includes(tab) ? tab : "passes", id: id || null };
}

function message(cls, text) {
  return h("div", { class: cls }, text);
}

function backButton(tab, label) {
  return h("button", { class: "back", onclick: () => navigate(tab) }, "← ", label);
}

async function render() {
  route = parseRoute();
  for (const button of document.querySelectorAll(".tabs button")) {
    button.setAttribute("aria-selected", String(button.dataset.tab === (route.tab === "companies" ? "vessels" : route.tab)));
  }
  if (!(route.tab === "vessels" && route.id)) clearVessel();
  applyFocus();
  renderMapContext();
  view.scrollTop = 0;
  try {
    if (route.tab === "passes") await (route.id ? renderPass(route.id) : renderPasses());
    else if (route.tab === "events") renderEvents();
    else if (route.tab === "companies") await renderCompany(route.id);
    else await (route.id ? renderVessel(Number(route.id)) : renderVessels());
  } catch (error) {
    fill(view, message("error-note", `No se pudo cargar: ${error.message}`));
  }
}

// Passes ----------------------------------------------------------------------------------------

function countsLine(p) {
  return h(
    "div",
    { class: "counts num" },
    h("span", { class: "count" }, plural(p.detections, "detección", "detecciones")),
    h("span", { class: "count" }, svg("circle", C.blue), `${fmtNumber.format(p.matched)} con AIS`),
    p.candidates > 0 && h("span", { class: "count" }, svg("circle", C.critical), plural(p.candidates, "candidato", "candidatos")),
  );
}

function passRow(p) {
  return h(
    "li",
    null,
    h(
      "button",
      { class: "row", onclick: () => navigate(`passes/${p.id}`), "aria-current": state.pass?.id === p.id ? "true" : null },
      h(
        "div",
        { class: "row-top" },
        h("span", { class: "row-title" }, when(p.acquired_at)),
        h(
          "span",
          { class: "row-time" },
          [state.zone === ALL && zoneName(p.zone), p.platform, p.orbit_direction === "ASCENDING" ? "asc." : "desc.", `${Math.round(p.coverage * 100)} %`]
            .filter(Boolean)
            .join(" · "),
        ),
      ),
      countsLine(p),
    ),
  );
}

async function renderPasses() {
  if (!state.passes) fill(view, message("loading", "Cargando pasadas…"));
  state.passes = await api(inZone("/passes?limit=60"));
  if (route.tab !== "passes" || route.id) return;
  if (!state.passes.length) {
    fill(view, message("empty", "Todavía no hay pasadas procesadas desde que se graba AIS."));
    return;
  }
  fill(view, 
    h(
      "p",
      { class: "note" },
      `Pasadas de Sentinel-1 sobre ${state.zone === ALL ? "la costa" : zoneName(state.zone)} desde que Uatu graba AIS allí. Cada detección radar se cruza con las posiciones AIS de ese momento.`,
    ),
    h("ul", { class: "list" }, state.passes.map(passRow)),
  );
}

async function loadPass(id) {
  if (state.pass?.id === id) return state.pass;
  const pass = await api(`/passes/${id}`);
  hideOptical();
  state.pass = pass;
  state.passNote = null;
  byId.detections = new Map(pass.detections_geojson.features.map((f) => [f.properties.id, f.properties]));
  byId.detectionFeatures = new Map(pass.detections_geojson.features.map((f) => [f.properties.id, f]));
  byId.passAis = new Map(pass.ais_geojson.features.map((f) => [f.properties.mmsi, f]));
  setData("footprint", { type: "Feature", geometry: pass.footprint, properties: {} });
  setData("detections", pass.detections_geojson);
  setData("pass-ais", pass.ais_geojson);
  showRadarImage(pass);
  loadUnimaged();
  renderLegend();
  renderMapContext();
  return pass;
}

/** The sea outside the pass on the map (all of it without one), to hatch. */
async function loadUnimaged() {
  const passId = state.pass?.id;
  const data = await api(inZone(`/map/unimaged${passId ? `?pass=${encodeURIComponent(passId)}` : ""}`)).catch(() => EMPTY);
  if ((state.pass?.id ?? undefined) === passId) setData("unimaged", data);
}

/** Take the pass off the map, saying why. */
function clearPass(note) {
  hideOptical();
  state.pass = null;
  state.passNote = note;
  byId.detections = new Map();
  byId.detectionFeatures = new Map();
  byId.passAis = new Map();
  for (const source of ["footprint", "detections", "pass-ais", "link"]) setData(source, EMPTY);
  showRadarImage(null);
  loadUnimaged();
  renderLegend();
  renderMapContext();
}

/** The chip over the map saying which moment the radar layers show. */
function renderMapContext() {
  const el = document.getElementById("map-context");
  const pass = state.pass;
  el.hidden = !pass && !state.passNote;
  if (pass) {
    const onIt = route.tab === "passes" && route.id === pass.id;
    fill(
      el,
      svg("circle", C.blue),
      h("span", null, `Radar: pasada de ${when(pass.acquired_at)}${state.zone === ALL ? ` · ${zoneName(pass.zone)}` : ""}`),
      !onIt && h("button", { onclick: () => navigate(`passes/${pass.id}`) }, "Ver"),
    );
  } else if (state.passNote) {
    fill(el, h("span", null, state.passNote));
  }
}

/** On a pass, what is happening now (live vessels, recent events) steps back. */
function applyFocus() {
  if (!map.getLayer("events")) return;
  const opacity = route.tab === "passes" && route.id ? 0.2 : 1;
  map.setPaintProperty("vessels-moving", "icon-opacity", opacity);
  map.setPaintProperty("vessels-stopped", "circle-opacity", opacity);
  map.setPaintProperty("vessels-stopped", "circle-stroke-opacity", opacity);
  map.setPaintProperty("events", "icon-opacity", opacity);
}

function detectionRow(f) {
  const p = f.properties;
  const kind = KIND[p.kind];
  const lines = [`Eslora aprox. ${p.length_m} m`, p.contrast_db != null && `contraste ${p.contrast_db} dB`].filter(Boolean).join(" · ");
  let who = null;
  if (p.kind === "matched") who = `${p.vessel_name ?? `MMSI ${p.mmsi}`} · a ${metres(p.distance_m ?? 0)}`;
  else if (p.suspected_mmsi) who = `Posible: ${p.suspect_name ?? `MMSI ${p.suspected_mmsi}`}, ${duration(p.suspect_silent_minutes)} sin emitir`;
  return h(
    "li",
    null,
    h(
      "button",
      {
        class: "row",
        onclick: () => {
          flyTo(f.geometry.coordinates, 11);
          detectionPopup(p, f.geometry.coordinates);
        },
      },
      h("div", { class: "row-top" }, tag("circle", kind.color, kind.label), h("span", { class: "grow" }), h("span", { class: "row-time" }, fmtTime.format(new Date(p.seen_at)))),
      who && h("div", { class: "row-text" }, who),
      h("div", { class: "row-meta num" }, lines),
    ),
  );
}

function stat(value, label, marker) {
  return h("div", null, h("div", { class: "stat-value" }, marker, fmtNumber.format(value)), h("div", { class: "stat-label" }, label));
}

async function renderPass(id) {
  fill(view, backButton("passes", "Pasadas"), message("loading", "Cargando pasada…"));
  const pass = await loadPass(id);
  if (route.id !== id) return;
  fitToArea(bounds(pass.footprint.coordinates));
  const features = pass.detections_geojson.features;
  const review = features.filter((f) => TO_REVIEW.includes(f.properties.kind)).sort((a, b) => KIND[a.properties.kind].rank - KIND[b.properties.kind].rank);
  const matched = features.filter((f) => f.properties.kind === "matched").sort((a, b) => b.properties.length_m - a.properties.length_m);
  const undetected = pass.ais_geojson.features.filter((f) => f.properties.status === "undetected").length;
  fill(view, 
    backButton("passes", "Pasadas"),
    h(
      "div",
      { class: "detail-head" },
      h("h2", { class: "detail-title" }, fmtFull.format(new Date(pass.acquired_at))),
      h(
        "div",
        { class: "detail-sub" },
        `${zoneName(pass.zone)} · Sentinel-${pass.platform.slice(1)} · ${pass.orbit_direction === "ASCENDING" ? "ascendente" : "descendente"} · ${Math.round(pass.coverage * 100)} % de la zona`,
        pass.frames > 1 ? ` · ${pass.frames} tomas` : "",
      ),
    ),
    h(
      "div",
      { class: "stats" },
      stat(pass.detections, "detecciones"),
      stat(pass.matched, "con AIS", svg("circle", C.blue)),
      stat(pass.candidates, "candidatos", svg("circle", C.critical)),
      stat(pass.no_coverage, "sin AIS, mala recepción", svg("circle", C.muted)),
      stat(pass.fixed, "objetos fijos", svg("circle", C.fixed)),
      stat(undetected, "AIS no vistos", svg("ring", C.ink2)),
    ),
    !pass.images.length && h("p", { class: "note" }, "Esta pasada no tiene imagen radar guardada."),
    h("h3", { class: "section-title" }, `Para revisar (${review.length})`),
    review.length
      ? h("ul", { class: "list" }, review.map(detectionRow))
      : h("p", { class: "note" }, "Nada que revisar: cada detección tiene AIS, es un objeto fijo o está donde no se recibe AIS."),
    pass.no_coverage > 0 &&
      h("p", { class: "note" }, `${plural(pass.no_coverage, "detección no tiene", "detecciones no tienen")} AIS en sitios donde aisstream oye poco: puede que emitieran y no les llegara. Pulsa una para ver cuánto se oía alrededor.`),
    h("h3", { class: "section-title" }, `Con AIS (${matched.length})`),
    h("ul", { class: "list" }, matched.map(detectionRow)),
  );
}

// Events ----------------------------------------------------------------------------------------

async function loadEvents() {
  const { minSeverity, hours } = state.eventFilter;
  state.events = await api(inZone(`/events?hours=${hours}&min_severity=${minSeverity}&limit=500`));
  byId.events = new Map(state.events.features.map((f) => [f.properties.id, f.properties]));
  if (!state.time) setData("events", state.events);
  renderLegend();
}

function chip(label, pressed, onclick) {
  return h("button", { class: "chip", "aria-pressed": String(pressed), onclick }, label);
}

function eventRow(f) {
  const p = f.properties;
  const sev = SEVERITY[p.severity] ?? SEVERITY[1];
  return h(
    "li",
    null,
    h(
      "button",
      {
        class: "row",
        onclick: () => {
          flyTo(f.geometry.coordinates, 10);
          showEvent(p, f.geometry.coordinates);
        },
      },
      h("div", { class: "row-top" }, tag("diamond", sev.color, sev.label), h("span", { class: "grow" }), h("span", { class: "row-time" }, when(p.start_at))),
      h("div", { class: "row-title" }, p.title),
      h("div", { class: "row-text" }, p.message),
    ),
  );
}

function renderEvents() {
  const f = state.eventFilter;
  const setFilter = (change) => async () => {
    Object.assign(state.eventFilter, change);
    renderEvents();
    await loadEvents();
    renderEvents();
  };
  const features = [...state.events.features].sort((a, b) => b.properties.start_at.localeCompare(a.properties.start_at));
  fill(view, 
    h(
      "div",
      { class: "toolbar" },
      h(
        "div",
        { class: "segmented", role: "group", "aria-label": "Gravedad" },
        chip("Alertas", f.minSeverity === 3, setFilter({ minSeverity: 3 })),
        chip("Notables", f.minSeverity === 2, setFilter({ minSeverity: 2 })),
        chip("Todos", f.minSeverity === 1, setFilter({ minSeverity: 1 })),
      ),
      h("span", { class: "spacer" }),
      h(
        "div",
        { class: "segmented", role: "group", "aria-label": "Periodo" },
        chip("24 h", f.hours === 24, setFilter({ hours: 24 })),
        chip("48 h", f.hours === 48, setFilter({ hours: 48 })),
        chip("7 d", f.hours === 168, setFilter({ hours: 168 })),
      ),
    ),
    features.length
      ? h("ul", { class: "list" }, features.map(eventRow))
      : message("empty", "Ningún evento con este filtro."),
  );
}

// Vessels ---------------------------------------------------------------------------------------

function liveRow(f) {
  const p = f.properties;
  const facts = [p.flag, p.ship_type, p.length_m && `${p.length_m} m`].filter(Boolean).join(" · ");
  const moving = p.sog != null && p.sog >= 0.5;
  return h(
    "li",
    null,
    h(
      "button",
      { class: "row", onclick: () => navigate(`vessels/${p.mmsi}`) },
      h(
        "div",
        { class: "row-top" },
        h("span", { class: "row-title" }, p.name ?? `MMSI ${p.mmsi}`),
        listingTag(p),
        h("span", { class: "row-time num" }, moving ? `${p.sog} nudos` : "parado"),
      ),
      facts && h("div", { class: "row-meta" }, facts),
    ),
  );
}

function searchRow(v) {
  const facts = [v.flag, v.ship_type, v.last_seen_at && `visto ${ago(v.last_seen_at)}`].filter(Boolean).join(" · ");
  return h(
    "li",
    null,
    h(
      "button",
      { class: "row", onclick: () => navigate(`vessels/${v.mmsi}`) },
      h("div", { class: "row-top" }, h("span", { class: "row-title" }, v.name ?? `MMSI ${v.mmsi}`), h("span", { class: "row-time num" }, v.mmsi)),
      facts && h("div", { class: "row-meta" }, facts),
    ),
  );
}

let searchSeq = 0;

async function renderVesselResults(list) {
  const q = state.vesselQuery.trim();
  if (q.length < 2) {
    // Sanctioned first, then on watchlists, then by name; vessels with no name yet last.
    const live = [...state.live.features].sort(
      (a, b) =>
        listing(b.properties).level - listing(a.properties).level ||
        Number(a.properties.name == null) - Number(b.properties.name == null) ||
        (a.properties.name ?? String(a.properties.mmsi)).localeCompare(b.properties.name ?? String(b.properties.mmsi)),
    );
    fill(list, 
      h("h3", { class: "section-title" }, `En la zona en la última hora (${live.length})`),
      live.length ? h("ul", { class: "list" }, live.map(liveRow)) : message("empty", "Ningún barco oído en la última hora."),
    );
    return;
  }
  const seq = ++searchSeq;
  const results = await api(`/vessels?q=${encodeURIComponent(q)}&limit=30`);
  if (seq !== searchSeq) return;
  fill(list, results.length ? h("ul", { class: "list" }, results.map(searchRow)) : message("empty", "Ningún barco coincide."));
}

let vesselList = null;

async function renderVessels() {
  const list = (vesselList = h("div"));
  let timer = null;
  const input = h("input", {
    class: "search",
    type: "search",
    placeholder: "Buscar por nombre o MMSI",
    "aria-label": "Buscar barco",
    value: state.vesselQuery,
    maxlength: 40,
  });
  input.addEventListener("input", () => {
    state.vesselQuery = input.value;
    clearTimeout(timer);
    timer = setTimeout(() => renderVesselResults(list).catch((e) => fill(list, message("error-note", e.message))), 250);
  });
  fill(view, h("div", { class: "toolbar" }, input), list);
  await renderVesselResults(list);
}

function clearVessel() {
  if (!state.vessel) return;
  state.vessel = null;
  setData("track", EMPTY);
  select(null);
}

async function renderVessel(mmsi) {
  fill(view, backButton("vessels", "Barcos"), message("loading", "Cargando barco…"));
  const v = await api(`/vessels/${mmsi}?track_hours=24`);
  if (Number(route.id) !== mmsi) return;
  state.vessel = v;
  state.lastVessel = { mmsi: v.mmsi, name: v.name ?? `MMSI ${v.mmsi}` };
  setData("track", v.track ? { type: "Feature", geometry: v.track, properties: {} } : EMPTY);
  const last = v.last_position;
  if (v.track) fitToArea(bounds(v.track.coordinates));
  else if (last) flyTo(last.geometry.coordinates, 10);
  if (last) select(last.geometry.coordinates);
  const lp = last?.properties;
  const facts = [v.flag, v.ship_type, v.length_m && `${v.length_m} m`].filter(Boolean).join(" · ");
  fill(view, 
    backButton("vessels", "Barcos"),
    h("div", { class: "detail-head" }, h("h2", { class: "detail-title" }, v.name ?? `MMSI ${v.mmsi}`), facts && h("div", { class: "detail-sub" }, facts)),
    v.sanction &&
      h(
        "div",
        { class: "callout" },
        h("strong", null, svg("arrow", C.critical), "En listas de sanciones"),
        `Figura como ${v.sanction.listed_as}`,
        v.sanction.programs ? ` (${v.sanction.programs.replaceAll(";", ", ")})` : "",
        ". Fuente: OpenSanctions.",
      ),
    v.risk && riskCallout(v.risk),
    v.watched.length > 0 &&
      h(
        "div",
        { class: "callout watch" },
        h("strong", null, svg("arrow", C.serious), "En tu lista de vigilancia"),
        v.watched.length === 1
          ? `${capitalise(v.watched[0].note)} (desde ${month(v.watched[0].added_at)}).`
          : h("ul", { class: "reasons" }, v.watched.map((w) => h("li", null, `${capitalise(w.note)} (desde ${month(w.added_at)})`))),
      ),
    kv([
      ["MMSI", String(v.mmsi)],
      ["IMO", v.imo ? String(v.imo) : null],
      ["Indicativo", v.callsign],
      ["Destino", v.destination],
      ["Manga", v.beam_m ? `${v.beam_m} m` : null],
      ["Última posición", lp ? ago(lp.received_at) : "sin posiciones"],
      ["Velocidad", lp?.sog != null ? `${lp.sog} nudos` : null],
      ["Rumbo", lp?.cog != null ? `${lp.cog}°` : null],
    ]),
    // Photos, registry data and port calls live there; we link rather than copy (their terms forbid it).
    h(
      "div",
      { class: "links" },
      externalLink("VesselFinder", `https://www.vesselfinder.com/vessels/details/${v.imo ?? v.mmsi}`),
      externalLink("MarineTraffic", `https://www.marinetraffic.com/en/ais/details/ships/mmsi:${v.mmsi}`),
    ),
    !v.track && h("p", { class: "note" }, "Sin derrota en las últimas 24 h."),
    h("h3", { class: "section-title" }, `Propietario y gestores (${v.companies.length})`),
    v.companies.length
      ? h("ul", { class: "list" }, v.companies.map(companyRow))
      : h(
          "p",
          { class: "note" },
          v.imo
            ? "Las fuentes abiertas no dicen quién lo gestiona: solo cubren barcos sancionados, vigilados o detenidos."
            : "Sin IMO no se puede saber quién lo gestiona.",
        ),
    v.watchlists.length > 0 && [
      h("h3", { class: "section-title" }, "Lo que dicen las listas"),
      v.watchlists.map((w) =>
        h(
          "details",
          { class: "source-note" },
          h("summary", null, WATCHLIST[w.watchlist] ?? w.watchlist),
          w.note ? h("p", { lang: "en" }, w.note) : h("p", null, "Sin más detalle."),
        ),
      ),
    ],
    h("h3", { class: "section-title" }, `Escalas, fondeos y cruces del Estrecho (${v.stops.length + v.transits.length})`),
    v.stops.length + v.transits.length
      ? h("ul", { class: "list" }, voyageRows(v))
      : h("p", { class: "note" }, "Ninguna parada ni cruce del Estrecho en 30 días, en las zonas analizadas."),
    h("h3", { class: "section-title" }, `Eventos (${v.events.length})`),
    v.events.length
      ? h(
          "ul",
          { class: "list" },
          v.events.map((e) => {
            const sev = SEVERITY[e.severity] ?? SEVERITY[1];
            return h(
              "li",
              null,
              h(
                "div",
                { class: "row" },
                h("div", { class: "row-top" }, tag("diamond", sev.color, sev.label), h("span", { class: "grow" }), h("span", { class: "row-time" }, when(e.start_at))),
                h("div", { class: "row-text" }, e.message),
              ),
            );
          }),
        )
      : h("p", { class: "note" }, "Ningún evento registrado."),
    h("h3", { class: "section-title" }, `Visto por el radar (${v.radar_sightings.length})`),
    v.radar_sightings.length
      ? h(
          "ul",
          { class: "list" },
          v.radar_sightings.map((s) =>
            h(
              "li",
              null,
              h(
                "button",
                { class: "row", onclick: () => navigate(`passes/${s.pass_id}`) },
                h("div", { class: "row-top" }, h("span", { class: "row-title" }, when(s.seen_at)), s.as_suspect && tag("circle", C.critical, "Sin AIS")),
                h(
                  "div",
                  { class: "row-meta" },
                  s.as_suspect ? "Detección sin AIS donde pudo estar tras dejar de emitir" : "Detección emparejada con su AIS",
                  s.distance_m != null ? ` · a ${metres(s.distance_m)}` : "",
                ),
              ),
            ),
          ),
        )
      : h("p", { class: "note" }, "Ninguna pasada lo ha captado todavía."),
  );
}

// Where a vessel stopped and when it crossed the Strait: one timeline, newest first.
const STOP = {
  berth: { label: "Puerto", title: "En puerto", color: C.blue },
  anchorage: { label: "Fondeo", title: "Fondeado", color: C.ink2 },
  drifting: { label: "Parado", title: "Parado mar adentro", color: C.serious },
};
const COUNTRY = { Spain: "España", Morocco: "Marruecos", Algeria: "Argelia", France: "Francia", Italy: "Italia", Tunisia: "Túnez" };

function stopTitle(s) {
  const abroad = s.place_country && !["Spain", s.place].includes(s.place_country); // not "Gibraltar (Gibraltar)"
  const where = abroad ? `${s.place} (${COUNTRY[s.place_country] ?? s.place_country})` : s.place;
  if (!where) return STOP[s.kind].title;
  if (s.kind === "berth") return `Puerto de ${where}`;
  if (s.kind === "anchorage") return `Fondeado frente a ${where}`;
  return `Parado mar adentro, a ${Math.round(s.place_km)} km de ${where}`;
}

function voyageRows(v) {
  const items = [
    ...v.stops.map((s) => ({ at: s.start_at, stop: s })),
    ...v.transits.map((t) => ({ at: t.at, transit: t })),
  ].sort((a, b) => b.at.localeCompare(a.at));
  return items.map(({ stop, transit }) => {
    if (transit) {
      const east = transit.direction === "east";
      return h(
        "li",
        null,
        h(
          "div",
          { class: "row" },
          h("div", { class: "row-top" }, h("span", { class: "row-title" }, east ? "Cruza el Estrecho hacia el Mediterráneo" : "Cruza el Estrecho hacia el Atlántico"), h("span", { class: "grow" }), h("span", { class: "row-time" }, when(transit.at))),
        ),
      );
    }
    const kind = STOP[stop.kind];
    const minutes = (new Date(stop.end_at) - new Date(stop.start_at)) / 60_000;
    const sameDay = dayKey.format(new Date(stop.start_at)) === dayKey.format(new Date(stop.end_at));
    const span = `${when(stop.start_at)} – ${sameDay ? fmtTime.format(new Date(stop.end_at)) : when(stop.end_at)}`;
    const recent = Date.now() - new Date(stop.end_at) < 30 * 60_000;
    return h(
      "li",
      null,
      h(
        "button",
        { class: "row", onclick: () => { flyTo([stop.lon, stop.lat], 13); select([stop.lon, stop.lat]); } },
        h("div", { class: "row-top" }, h("span", { class: "row-title" }, stopTitle(stop)), tag("square", kind.color, kind.label)),
        h("div", { class: "row-meta" }, `${span} · ${duration(minutes)}${recent ? " · sigue ahí" : ""}`),
      ),
    );
  });
}

function riskCallout(risk) {
  const watch = risk.level === "watchlisted";
  return h(
    "div",
    { class: watch ? "callout watch" : "callout" },
    h("strong", null, svg("arrow", watch ? C.serious : C.critical), watch ? "En listas de vigilancia" : "Armador o gestor sancionado"),
    !watch && "No figura en listas de sanciones, pero:",
    h("ul", { class: "reasons" }, risk.reasons.map((r) => h("li", null, capitalise(r)))),
    "Fuente: OpenSanctions.",
  );
}

/** A company behind a vessel, its roles there and the share of its fleet under sanctions. */
function companyRow(c) {
  const when = c.since ? `desde ${month(c.since)}` : c.as_of ? `inspección de ${month(c.as_of)}` : null;
  const facts = [c.roles.map((r) => ROLE[r] ?? r).join(", "), when, c.sources.map((s) => SOURCE[s] ?? s).join(", ")];
  const fleet = c.sanctioned_vessels > 0 && c.vessels > 1 ? `${c.sanctioned_vessels} de sus ${c.vessels} barcos conocidos, sancionados` : null;
  return h(
    "li",
    null,
    h(
      "button",
      { class: "row", onclick: () => navigate(`companies/${encodeURIComponent(c.id)}`) },
      h(
        "div",
        { class: "row-top" },
        h("span", { class: "row-title" }, c.name),
        c.sanctioned && tag("square", C.critical, c.kind === "person" ? "Sancionado" : "Sancionada"),
      ),
      h("div", { class: "row-meta" }, facts.filter(Boolean).join(" · ")),
      fleet && h("div", { class: "row-meta" }, fleet),
    ),
  );
}

function fleetRow(v) {
  const renamed = v.ais_name && v.ais_name.toUpperCase() !== v.name.toUpperCase();
  const facts = [
    `IMO ${v.imo}`,
    renamed && `en las listas como ${v.name}`,
    v.roles.map((r) => ROLE[r] ?? r).join(", "),
    v.sources.map((s) => SOURCE[s] ?? s).join(", "),
  ];
  const top = h(
    "div",
    { class: "row-top" },
    h("span", { class: "row-title" }, v.ais_name ?? v.name),
    listingTag(v),
    h("span", { class: "grow" }),
    v.mmsi != null && h("span", { class: "row-time" }, v.last_seen_at ? `oído ${ago(v.last_seen_at)}` : "oído"),
  );
  if (v.mmsi != null) {
    const meta = h("div", { class: "row-meta" }, facts.filter(Boolean).join(" · "));
    return h("li", null, h("button", { class: "row", onclick: () => navigate(`vessels/${v.mmsi}`) }, top, meta));
  }
  const link = externalLink("VesselFinder", `https://www.vesselfinder.com/vessels/details/${v.imo}`);
  return h("li", null, h("div", { class: "row" }, top, h("div", { class: "row-meta" }, facts.filter(Boolean).join(" · "), " · ", link)));
}

async function renderCompany(id) {
  const back = state.lastVessel ? backButton(`vessels/${state.lastVessel.mmsi}`, state.lastVessel.name) : backButton("vessels", "Barcos");
  fill(view, back, message("loading", "Cargando empresa…"));
  const c = await api(`/companies/${encodeURIComponent(id)}`);
  if (route.id !== id) return;
  const heard = c.fleet.filter((v) => v.mmsi != null).length;
  const sub = [
    c.kind === "person" ? "Persona" : "Empresa",
    c.countries.map(regionName).join(", "),
    c.imos.length > 0 && `IMO de empresa ${c.imos.join(", ")}`,
  ];
  const sanctionedFleet = !c.sanctioned && c.sanctioned_vessels >= 2 && 2 * c.sanctioned_vessels >= c.vessels;
  fill(
    view,
    back,
    h("div", { class: "detail-head" }, h("h2", { class: "detail-title" }, c.name), h("div", { class: "detail-sub" }, sub.filter(Boolean).join(" · "))),
    c.sanctioned &&
      h(
        "div",
        { class: "callout" },
        h("strong", null, svg("square", C.critical), "En listas de sanciones"),
        c.programs ? `Programas: ${c.programs.replaceAll(";", ", ")}. ` : "",
        "Fuente: OpenSanctions.",
      ),
    sanctionedFleet &&
      h(
        "div",
        { class: "callout watch" },
        h("strong", null, svg("square", C.serious), "Gestiona una flota sancionada"),
        `${c.sanctioned_vessels} de sus ${c.vessels} barcos conocidos están en listas de sanciones.`,
      ),
    h(
      "h3",
      { class: "section-title" },
      `Flota conocida (${c.fleet.length}: ${c.sanctioned_vessels} ${c.sanctioned_vessels === 1 ? "sancionado" : "sancionados"},` +
        ` ${heard} ${heard === 1 ? "oído" : "oídos"} por Uatu)`,
    ),
    h("ul", { class: "list" }, c.fleet.map(fleetRow)),
    h(
      "p",
      { class: "note" },
      "Según OpenSanctions: listas de sanciones, inteligencia ucraniana (GUR), vetos del Paris MoU y detenciones " +
        "de los MoU de Tokio y del mar Negro de los últimos 3 años. Puede que ya no gestione alguno de estos barcos.",
    ),
  );
}

// --- data loading -----------------------------------------------------------------------------

async function loadDensity() {
  if (state.densityLoaded) return;
  state.densityLoaded = true;
  try {
    setData("density", await api(inZone("/history/density?days=7")));
  } catch {
    state.densityLoaded = false;
  }
}

async function loadCoverage() {
  if (state.coverageLoaded) return;
  state.coverageLoaded = true;
  try {
    setData("coverage", await api(inZone("/coverage?days=7")));
  } catch {
    state.coverageLoaded = false;
  }
}

async function loadLive() {
  state.live = await api(inZone("/vessels/live?minutes=60"));
  if (!state.time) setData("vessels", state.live); // the time machine draws its own
}

async function refresh() {
  if (document.hidden) return;
  try {
    const before = state.status?.latest_pass?.id;
    state.status = await api(inZone("/status"));
    renderStatus();
    if (state.status.latest_pass?.id !== before) updateRecentRadar();
  } catch {
    renderStatus(true);
    return;
  }
  await Promise.allSettled([loadLive(), loadEvents()]);
  if (route.id) return; // don't pull a detail out from under the reader
  if (route.tab === "events") renderEvents();
  else if (route.tab === "passes") await renderPasses().catch(() => {});
  else if (route.tab === "vessels" && vesselList?.isConnected && !state.vesselQuery.trim()) {
    await renderVesselResults(vesselList).catch(() => {});
  }
}

/** Optical imagery on land, under the radar (which shows the sea): MapTiler, or Sentinel-2 cloudless. */
function addSatellite(layer) {
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
      layout: { visibility: state.layers.satellite ? "visible" : "none" },
      paint: { "raster-fade-duration": 0, "raster-saturation": -0.15 },
    },
    "radar-recent",
  );
}

function renderZoneSelect() {
  const select = document.getElementById("zone");
  fill(
    select,
    h("option", { value: ALL, selected: state.zone === ALL }, "Toda la costa"),
    state.zones.map((z) => h("option", { value: z.key, selected: state.zone === z.key }, z.name)),
  );
  select.onchange = () => changeZone(select.value);
}

/** Draw what depends on the zone looked at (but not live data: see refresh). */
async function showZone({ fit }) {
  setData("zones", zoneEdges());
  loadUnimaged();
  updateRecentRadar();
  const area = scopeArea();
  if (fit) map.fitBounds([[area.west, area.south], [area.east, area.north]], { padding: 20, duration: fit === "animate" ? 600 : 0 });
  state.coverageLoaded = false;
  if (state.layers.coverage) loadCoverage();
  state.densityLoaded = false;
  if (state.layers.density) loadDensity();
  if (state.time) {
    state.time.chunks.clear();
    loadTimeEvents();
    renderTime();
  }
  setData("infra", await api(inZone("/map/infrastructure")).catch(() => EMPTY));
}

async function changeZone(key) {
  state.zone = key;
  saveZonePref();
  if (state.pass && key !== ALL && state.pass.zone !== key) clearPass(null);
  await showZone({ fit: "animate" });
  state.passes = await api(inZone("/passes?limit=60")).catch(() => null);
  if (route.id) navigate(route.tab);
  await refresh();
  if (!state.pass && state.status?.latest_pass) await loadPass(state.status.latest_pass.id).catch(() => {});
  if (!route.id) render();
}

async function start() {
  fitPadding();
  phone.addEventListener("change", fitPadding);
  addLayers();
  wireMapEvents();
  watchMapLoading();
  document.getElementById("time-toggle").onclick = () => enterTime(Date.now() - HOUR);
  renderLayers();
  const [zones, land, satellite] = await Promise.all([api("/zones"), api("/map/land"), api("/map/satellite")]);
  api("/reports").then((reports) => {
    state.reports = reports;
    renderStatus();
  }).catch(() => {});
  state.zones = zones.zones;
  state.wholeCoast = zones.whole_coast;
  if (state.zone !== ALL && !state.zones.some((z) => z.key === state.zone)) state.zone = ALL;
  state.status = await api(inZone("/status")).catch(() => null); // the latest pass versions the radar tiles
  const coast = zones.whole_coast;
  map.setMaxBounds([[coast.west - 3, coast.south - 2], [coast.east + 3, coast.north + 2]]);
  setData("land", land);
  renderZoneSelect();
  await showZone({ fit: !parseRoute().id });
  addSatellite(satellite); // once framed: tiles for the view it starts from would be thrown away
  applyLayerVisibility();
  if (phone.matches) {
    const attribution = document.querySelector(".maplibregl-ctrl-attrib");
    attribution?.removeAttribute("open");
    attribution?.classList.remove("maplibregl-compact-show");
  }

  // The passes, to find the one an event happened under from any view.
  splashText("Cargando barcos y eventos…");
  state.passes = await api(inZone("/passes?limit=60")).catch(() => null);
  await refresh();
  // Show the latest pass on the map from the start, without leaving the current view.
  if (state.status?.latest_pass && !(parseRoute().tab === "passes" && parseRoute().id)) {
    splashText("Cargando la última pasada del radar…");
    await loadPass(state.status.latest_pass.id).catch(() => {});
  }
  await render();
  renderLegend();
  splashText("Cargando el mapa…");
  await mapSettled(8000);
  hideSplash();
  setInterval(refresh, REFRESH_MS);
  document.addEventListener("visibilitychange", () => !document.hidden && refresh());
}

for (const button of document.querySelectorAll(".tabs button")) {
  button.addEventListener("click", () => navigate(button.dataset.tab));
}
window.addEventListener("hashchange", () => render());
document.getElementById("handle").addEventListener("click", () => {
  const panel = document.getElementById("panel");
  panel.classList.toggle("expanded");
  panel.addEventListener("transitionend", fitPadding, { once: true });
  document.getElementById("handle").setAttribute("aria-label", panel.classList.contains("expanded") ? "Reducir panel" : "Expandir panel");
});

// --- the time machine -----------------------------------------------------------------------
// The AIS record played back: where every vessel was at a moment, with its last hour behind it.
// Reports come an hour at a time (past hours stay in the browser's cache); between two reports a
// vessel is placed on the straight line joining them.

const HOUR = 3_600_000;
const STALE_S = 30 * 60; // unheard for longer than this, a vessel is not drawn
const TRAIL_S = 60 * 60;
const SPEEDS = [10, 60, 300, 1800]; // times real time: 1 s plays 10 s ... 30 min

function enterTime(t) {
  if (!state.time) {
    state.time = { t, playing: false, speed: 60, chunks: new Map(), from: null, outages: [], events: null, ui: null, passId: undefined };
    api("/history").then((range) => {
      if (!state.time) return;
      state.time.from = new Date(range.start).getTime();
      state.time.outages = range.outages.map((o) => [new Date(o.start).getTime(), new Date(o.end).getTime()]);
      renderTimebar();
    }).catch(() => {});
    loadTimeEvents();
    renderTimebar();
  }
  state.time.t = clampTime(t);
  renderTime();
}

function exitTime() {
  if (!state.time) return;
  state.time = null;
  setData("vessels", state.live);
  setData("time-trails", EMPTY);
  setData("events", state.events);
  renderTimebar();
  // Back to the present: its latest pass.
  if (state.status?.latest_pass) loadPass(state.status.latest_pass.id).catch(() => {});
}

/** The radar pass of the moment shown, if a satellite passed within 45 minutes of it; else none. */
function syncPassToTime() {
  const time = state.time;
  const distance = (p) => Math.abs(new Date(p.acquired_at).getTime() - time.t);
  const near = (state.passes ?? []).filter((p) => distance(p) <= 45 * 60_000).sort((a, b) => distance(a) - distance(b))[0];
  if ((near?.id ?? null) === time.passId) return;
  time.passId = near?.id ?? null;
  if (near) loadPass(near.id).catch(() => {});
  else clearPass("Máquina del tiempo: ningún satélite pasó a esa hora");
}

function clampTime(t) {
  return Math.min(Math.max(t, state.time?.from ?? 0), Date.now());
}

/** An event, played back from a little before it started. */
function replayEvent(p, lngLat) {
  popup?.remove();
  enterTime(new Date(p.start_at).getTime() - 20 * 60_000);
  flyTo(lngLat, 11);
  select(lngLat);
}

async function loadTimeEvents() {
  const time = state.time;
  const hours = Math.min(24 * 30, Math.ceil((Date.now() - (time.from ?? Date.now() - 7 * 24 * HOUR)) / HOUR) + 1);
  const events = await api(inZone(`/events?hours=${hours}&min_severity=${state.eventFilter.minSeverity}&limit=1000`)).catch(() => null);
  if (state.time === time) {
    time.events = events;
    renderTime();
  }
}

/** The hour of reports starting at `key`, fetched once. */
function timeChunk(key) {
  const time = state.time;
  let chunk = time.chunks.get(key);
  if (!chunk) {
    chunk = { data: null };
    time.chunks.set(key, chunk);
    api(inZone(`/history/tracks?start=${encodeURIComponent(new Date(key).toISOString())}&hours=1`))
      .then((data) => {
        chunk.data = data;
        if (state.time === time) renderTime();
      })
      .catch(() => time.chunks.delete(key));
  }
  return chunk;
}

/** Index of the first element of a sorted array greater than x (or, `orEqual`, not less than x). */
function bisect(a, x, orEqual = false) {
  let lo = 0;
  let hi = a.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (a[mid] < x || (!orEqual && a[mid] === x)) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

function renderTime() {
  const time = state.time;
  if (!time) return;
  const ts = time.t / 1000;
  const hour = Math.floor(time.t / HOUR) * HOUR;
  const keys = [hour - HOUR, hour, hour + HOUR].filter((k) => k <= Date.now()); // the next one, to play into
  for (const key of time.chunks.keys()) if (Math.abs(key - hour) > 3 * HOUR) time.chunks.delete(key);
  const parts = new Map();
  for (const key of keys) {
    for (const track of timeChunk(key).data?.tracks ?? []) {
      if (parts.has(track.mmsi)) parts.get(track.mmsi).push(track);
      else parts.set(track.mmsi, [track]);
    }
  }
  const vessels = [];
  const trails = [];
  for (const [mmsi, pieces] of parts) {
    const t = pieces.flatMap((p) => p.t);
    const lon = pieces.flatMap((p) => p.lon);
    const lat = pieces.flatMap((p) => p.lat);
    const i = bisect(t, ts) - 1; // its last report by then
    if (i < 0 || ts - t[i] > STALE_S) continue;
    let at = [lon[i], lat[i]];
    const j = i + 1;
    if (j < t.length && t[j] > t[i] && t[j] - t[i] <= STALE_S) {
      const f = (ts - t[i]) / (t[j] - t[i]);
      at = [lon[i] + f * (lon[j] - lon[i]), lat[i] + f * (lat[j] - lat[i])];
    }
    const meta = pieces[0];
    const properties = {
      mmsi,
      name: meta.name,
      ship_type: meta.ship_type,
      length_m: meta.length_m,
      flag: meta.flag,
      sog: pieces.flatMap((p) => p.sog)[i],
      cog: pieces.flatMap((p) => p.cog)[i],
      received_at: new Date(t[i] * 1000).toISOString(),
      sanctioned: meta.sanctioned,
      risk: meta.risk,
    };
    vessels.push({ type: "Feature", geometry: { type: "Point", coordinates: at }, properties });
    const trail = [];
    for (let m = bisect(t, ts - TRAIL_S, true); m <= i; m++) trail.push([lon[m], lat[m]]);
    trail.push(at);
    if (trail.length >= 2) {
      trails.push({ type: "Feature", geometry: { type: "LineString", coordinates: trail }, properties: { sanctioned: meta.sanctioned, risk: meta.risk } });
    }
  }
  setData("vessels", { type: "FeatureCollection", features: vessels });
  setData("time-trails", { type: "FeatureCollection", features: trails });
  if (time.events) {
    // Going on at that moment; an instant (an identity change) shows for half an hour.
    const going = time.events.features.filter((f) => {
      const start = new Date(f.properties.start_at).getTime();
      const end = f.properties.end_at ? new Date(f.properties.end_at).getTime() : Date.now();
      return start <= time.t && time.t <= Math.max(end, start + 30 * 60_000);
    });
    setData("events", { type: "FeatureCollection", features: going });
  }
  syncPassToTime();
  updateTimebar(vessels.length, keys.some((key) => !time.chunks.get(key)?.data));
}

function renderTimebar() {
  const bar = document.getElementById("timebar");
  const toggle = document.getElementById("time-toggle");
  const time = state.time;
  bar.hidden = !time;
  toggle.hidden = Boolean(time);
  if (!time) return fill(bar);
  const play = h("button", { class: "time-play", onclick: togglePlay });
  const slider = h("input", { type: "range", class: "time-slider", step: 60_000, "aria-label": "Momento" });
  slider.addEventListener("input", () => {
    time.t = clampTime(Number(slider.value));
    renderTime();
  });
  const speed = h(
    "select",
    { class: "time-speed", "aria-label": "Velocidad" },
    SPEEDS.map((x) => h("option", { value: x, selected: x === time.speed }, x < 60 ? `1 s = ${x} s` : `1 s = ${x / 60} min`)),
  );
  speed.addEventListener("change", () => (time.speed = Number(speed.value)));
  const label = h("span", { class: "time-label num" });
  const count = h("span", { class: "time-count" });
  const nudge = (minutes) => () => {
    time.t = clampTime(time.t + minutes * 60_000);
    renderTime();
  };
  time.ui = { play, slider, label, count };
  fill(
    bar,
    h(
      "div",
      { class: "time-top" },
      play,
      h("button", { class: "time-step", onclick: nudge(-10), title: "10 min antes" }, "−10 min"),
      label,
      h("button", { class: "time-step", onclick: nudge(10), title: "10 min después" }, "+10 min"),
      h("span", { class: "grow" }),
      speed,
      h("button", { class: "close", onclick: exitTime, "aria-label": "Volver al presente", title: "Volver al presente" }, "×"),
    ),
    slider,
    count,
  );
  updateTimebar();
}

function updateTimebar(vessels = null, loading = false) {
  const time = state.time;
  if (!time?.ui) return;
  const { play, slider, label, count } = time.ui;
  play.textContent = time.playing ? "❚❚" : "▶";
  play.setAttribute("aria-label", time.playing ? "Pausa" : "Reproducir");
  slider.min = String(Math.floor((time.from ?? time.t - 7 * 24 * HOUR) / 60_000) * 60_000); // whole minutes
  slider.max = String(Date.now());
  slider.value = String(time.t);
  label.textContent = when(new Date(time.t).toISOString());
  const outage = time.outages.find(([start, end]) => start <= time.t && time.t <= end);
  count.classList.toggle("warning", Boolean(outage));
  if (outage) {
    // Uatu itself was deaf: vessels still drawn were heard before, and a silence then says nothing.
    count.textContent = `⚠ Uatu no grababa AIS de ${when(new Date(outage[0]).toISOString())} a ${when(new Date(outage[1]).toISOString())} (sin conexión): lo que falta no es un barco callado`;
  } else if (vessels != null) {
    count.textContent = loading ? "Cargando AIS de ese momento…" : `${fmtNumber.format(vessels)} barcos oídos en la última media hora`;
  }
}

function togglePlay() {
  const time = state.time;
  time.playing = !time.playing;
  if (time.playing) {
    if (time.t >= Date.now() - 60_000) time.t = clampTime(Date.now() - 6 * HOUR); // at the end: from six hours ago
    time.lastFrame = performance.now();
    requestAnimationFrame(playFrame);
  }
  updateTimebar();
}

function playFrame(now) {
  const time = state.time;
  if (!time?.playing) return;
  time.t = clampTime(time.t + (now - time.lastFrame) * time.speed);
  time.lastFrame = now;
  if (now - (time.lastRender ?? 0) > 125) {
    time.lastRender = now;
    renderTime();
  }
  if (time.t >= Date.now() - 1000) {
    time.playing = false;
    renderTime();
    return;
  }
  requestAnimationFrame(playFrame);
}

// --- loading ----------------------------------------------------------------------------------

function splashText(text) {
  document.getElementById("splash-text").textContent = text;
}

function hideSplash() {
  const splash = document.getElementById("splash");
  if (!splash || splash.classList.contains("gone")) return;
  splash.classList.add("gone");
  setTimeout(() => splash.remove(), 400);
}

/** Once the map has drawn what it is loading, or after `ms` at most. */
function mapSettled(ms) {
  return new Promise((resolve) => {
    if (map.loaded()) return resolve();
    const timer = setTimeout(resolve, ms);
    map.once("idle", () => {
      clearTimeout(timer);
      resolve();
    });
  });
}

/** A small "loading" chip while the map fetches tiles or data for longer than a moment. */
function watchMapLoading() {
  const busy = document.getElementById("map-busy");
  let timer = null;
  // Tiles only: GeoJSON is updated all the time (live vessels, the time machine playing).
  map.on("dataloading", (e) => {
    if (!e.tile || timer || !busy.hidden) return;
    timer = setTimeout(() => {
      timer = null;
      if (!map.areTilesLoaded()) busy.hidden = false;
    }, 400);
  });
  const settled = () => {
    if (!map.areTilesLoaded()) return;
    clearTimeout(timer);
    timer = null;
    busy.hidden = true;
  };
  map.on("data", settled);
  map.on("idle", settled);
}

map.on("load", () =>
  start()
    .catch((error) => {
      renderStatus(true);
      fill(view, message("error-note", `No se pudo cargar: ${error.message}`));
    })
    .finally(hideSplash),
);
