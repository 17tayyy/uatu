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
  no_coverage: { label: "Sin cobertura AIS", color: C.muted, rank: 4 },
  unpaired: { label: "Antes de grabar AIS", color: C.muted, rank: 5 },
  fixed: { label: "Objeto fijo", color: C.fixed, rank: 6 },
};
const TO_REVIEW = ["went_silent", "unidentified", "reception_gap"];

const SEVERITY = {
  3: { label: "Alerta", color: C.critical },
  2: { label: "Notable", color: C.serious },
  1: { label: "Info", color: C.muted },
};

const RECEPTION = {
  good: { label: "Buena (≥ 3 mensajes por barco)", color: C.coverage.good },
  patchy: { label: "Irregular (1,5–3)", color: C.coverage.patchy },
  poor: { label: "Pobre (< 1,5)", color: C.coverage.poor },
};

const LAYERS = {
  radar: { label: "Imagen radar", on: true, ids: ["radar"] },
  detections: { label: "Detecciones radar", on: true, ids: ["footprint", "detections"] },
  passAis: { label: "AIS en la pasada", on: false, ids: ["pass-ais"] },
  vessels: { label: "Barcos en vivo", on: true, ids: ["vessels-moving", "vessels-stopped"] },
  events: { label: "Eventos AIS", on: true, ids: ["events"] },
  infrastructure: { label: "Infraestructura", on: false, ids: ["infra-cables", "infra-pipes", "infra-points"] },
  coverage: { label: "Cobertura AIS", hint: "7 días", on: false, ids: ["coverage"] },
};

const EMPTY = { type: "FeatureCollection", features: [] };

const state = {
  area: null,
  status: null,
  passes: null,
  pass: null, // the pass drawn on the map
  live: EMPTY,
  events: EMPTY,
  eventFilter: { minSeverity: 2, hours: 48 },
  vessel: null, // the vessel whose track is drawn
  vesselQuery: "",
  layers: loadLayerPrefs(),
  coverageLoaded: false,
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

function loadLayerPrefs() {
  const prefs = Object.fromEntries(Object.entries(LAYERS).map(([key, layer]) => [key, layer.on]));
  try {
    Object.assign(prefs, JSON.parse(localStorage.getItem("uatu.layers") ?? "{}"));
  } catch {
    // private window or blocked storage: defaults
  }
  return prefs;
}

function saveLayerPrefs() {
  try {
    localStorage.setItem("uatu.layers", JSON.stringify(state.layers));
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
  minZoom: 6,
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

function addLayers() {
  for (const [name, image] of [
    ["arrow", arrowIcon(C.ink2)],
    ["arrow-sanctioned", arrowIcon(C.critical)],
    ["diamond-3", diamondIcon(C.critical)],
    ["diamond-2", diamondIcon(C.serious)],
    ["diamond-1", diamondIcon(C.muted)],
  ]) {
    map.addImage(name, image, { pixelRatio: 2 });
  }
  for (const id of ["land", "coverage", "infra", "footprint", "detections", "pass-ais", "events", "vessels", "track", "selected"]) {
    map.addSource(id, { type: "geojson", data: EMPTY });
  }

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
  map.addLayer({ id: "land", type: "fill", source: "land", paint: { "fill-color": C.land } });
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
        12, ["interpolate", ["linear"], ["get", "length_m"], 0, 4, 300, 9],
      ],
      "circle-color": kindColor,
      "circle-stroke-width": ["match", ["get", "kind"], "fixed", 1, 1.5],
      "circle-stroke-color": ["match", ["get", "kind"], "fixed", C.muted, C.page],
    },
  });
  map.addLayer({
    id: "vessels-stopped",
    type: "circle",
    source: "vessels",
    filter: ["any", ["==", ["get", "cog"], null], ["<", ["coalesce", ["get", "sog"], 0], 0.5]],
    paint: {
      "circle-radius": ["case", ["get", "sanctioned"], 4.5, 2.5],
      "circle-color": ["case", ["get", "sanctioned"], C.critical, C.ink2],
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
      "icon-image": ["case", ["get", "sanctioned"], "arrow-sanctioned", "arrow"],
      "icon-size": [
        "interpolate", ["linear"], ["zoom"],
        7, ["case", ["get", "sanctioned"], 0.9, 0.6],
        12, ["case", ["get", "sanctioned"], 1.4, 1],
      ],
      "icon-rotate": ["get", "cog"],
      "icon-rotation-alignment": "map",
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
      "symbol-sort-key": ["case", ["get", "sanctioned"], 1, 0],
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

function showRadarImage(pass) {
  if (map.getLayer("radar")) map.removeLayer("radar");
  if (map.getSource("radar")) map.removeSource("radar");
  if (!pass?.image) return;
  map.addSource("radar", { type: "image", url: pass.image.url, coordinates: pass.image.coordinates });
  map.addLayer(
    {
      id: "radar",
      type: "raster",
      source: "radar",
      layout: { visibility: state.layers.radar ? "visible" : "none" },
      paint: { "raster-opacity": 0.9, "raster-brightness-max": 0.7, "raster-fade-duration": 0 },
    },
    "coverage",
  );
}

function select(lngLat) {
  setData("selected", lngLat ? { type: "Point", coordinates: lngLat } : EMPTY);
}

function bounds(coordinates) {
  const b = new maplibregl.LngLatBounds();
  const walk = (c) => (typeof c[0] === "number" ? b.extend(c) : c.forEach(walk));
  walk(coordinates);
  return b;
}

function fitToArea(b) {
  const a = state.area;
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
  const text = {
    went_silent: "Ningún AIS aquí, pero un barco que dejó de emitir hace rato pudo llegar hasta este punto.",
    unidentified: "Ningún AIS aquí, ningún barco callado que lo explique y buena recepción alrededor.",
    reception_gap: "Sin AIS aquí, pero un barco que lleva poco sin oírse pudo estar en este punto: probablemente recepción.",
    no_coverage: "Aquí aisstream apenas recibe AIS: sin AIS no se puede saber si emitía.",
    fixed: "Un punto que aparece pasada tras pasada: plataforma, boya o fondeo largo.",
  }[p.kind];
  openPopup(lngLat, [
    tag("circle", kind.color, kind.label),
    h("p", { class: "popup-title" }, "Detección radar"),
    text && h("p", { class: "text" }, text),
    kv(rows),
    actions.length && h("div", { class: "actions" }, actions),
  ]);
}

function eventPopup(p, lngLat) {
  const sev = SEVERITY[p.severity] ?? SEVERITY[1];
  openPopup(lngLat, [
    tag("diamond", sev.color, sev.label),
    h("p", { class: "popup-title" }, p.title),
    h("p", { class: "text" }, p.message),
    kv([
      ["Desde", when(p.start_at)],
      ["Hasta", p.end_at ? when(p.end_at) : "sigue"],
    ]),
    h("div", { class: "actions" }, vesselButton(p.mmsi)),
  ]);
}

function passAisPopup(p, lngLat) {
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
  for (const id of ["vessels-moving", "vessels-stopped"]) {
    map.on("mousemove", id, (e) => {
      const p = e.features[0].properties;
      const speed = p.sog != null && p.sog !== "null" ? ` · ${p.sog} nudos` : "";
      hover.setLngLat(e.lngLat).setText(`${p.name && p.name !== "null" ? p.name : `MMSI ${p.mmsi}`}${speed}`).addTo(map);
    });
  }
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
        eventPopup(p, lngLat);
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
const byId = { detections: new Map(), events: new Map(), passAis: new Map() };

function parseProperties(p) {
  if ("kind" in p && "seen_at" in p) return byId.detections.get(p.id) ?? p;
  if ("severity" in p) return byId.events.get(p.id) ?? p;
  if ("status" in p && byId.passAis.has(p.mmsi)) return byId.passAis.get(p.mmsi);
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
    parts.push(h("h3", null, "Barcos en vivo"), legendItem("arrow", C.ink2, "Navegando (rumbo)"), legendItem("circle", C.ink2, "Parado"), legendItem("arrow", C.critical, "Sancionado"));
  }
  if (state.layers.infrastructure) {
    parts.push(h("h3", null, "Infraestructura"), legendItem("dash", C.infra, "Cable submarino"), legendItem("line", C.infra, "Gasoducto u oleoducto"));
  }
  if (state.layers.coverage) {
    parts.push(h("h3", null, "Recepción AIS"), Object.values(RECEPTION).map((r) => legendItem("square", r.color, r.label)));
  }
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
    s.latest_pass && h("div", { class: "facts" }, `Última pasada ${when(s.latest_pass.acquired_at)} · Sentinel-${s.latest_pass.platform.slice(1)}`),
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
    button.setAttribute("aria-selected", String(button.dataset.tab === route.tab));
  }
  if (!(route.tab === "vessels" && route.id)) clearVessel();
  view.scrollTop = 0;
  try {
    if (route.tab === "passes") await (route.id ? renderPass(route.id) : renderPasses());
    else if (route.tab === "events") renderEvents();
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
        h("span", { class: "row-time" }, `${p.platform} · ${p.orbit_direction === "ASCENDING" ? "asc." : "desc."} · ${Math.round(p.coverage * 100)} % zona`),
      ),
      countsLine(p),
    ),
  );
}

async function renderPasses() {
  if (!state.passes) fill(view, message("loading", "Cargando pasadas…"));
  state.passes = await api("/passes?limit=60");
  if (route.tab !== "passes" || route.id) return;
  if (!state.passes.length) {
    fill(view, message("empty", "Todavía no hay pasadas procesadas desde que se graba AIS."));
    return;
  }
  fill(view, 
    h("p", { class: "note" }, "Pasadas de Sentinel-1 sobre el Estrecho desde que Uatu graba AIS. Cada detección radar se cruza con las posiciones AIS de ese momento."),
    h("ul", { class: "list" }, state.passes.map(passRow)),
  );
}

async function loadPass(id) {
  if (state.pass?.id === id) return state.pass;
  const pass = await api(`/passes/${id}`);
  state.pass = pass;
  byId.detections = new Map(pass.detections_geojson.features.map((f) => [f.properties.id, f.properties]));
  byId.passAis = new Map(pass.ais_geojson.features.map((f) => [f.properties.mmsi, f.properties]));
  setData("footprint", { type: "Feature", geometry: pass.footprint, properties: {} });
  setData("detections", pass.detections_geojson);
  setData("pass-ais", pass.ais_geojson);
  showRadarImage(pass);
  renderLegend();
  return pass;
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
        `Sentinel-${pass.platform.slice(1)} · ${pass.orbit_direction === "ASCENDING" ? "ascendente" : "descendente"} · ${Math.round(pass.coverage * 100)} % de la zona`,
      ),
    ),
    h(
      "div",
      { class: "stats" },
      stat(pass.detections, "detecciones"),
      stat(pass.matched, "con AIS", svg("circle", C.blue)),
      stat(pass.candidates, "candidatos", svg("circle", C.critical)),
      stat(pass.no_coverage, "sin cobertura AIS", svg("circle", C.muted)),
      stat(pass.fixed, "objetos fijos", svg("circle", C.fixed)),
      stat(undetected, "AIS no vistos", svg("ring", C.ink2)),
    ),
    !pass.image && h("p", { class: "note" }, "Esta pasada no tiene imagen radar guardada."),
    h("h3", { class: "section-title" }, `Para revisar (${review.length})`),
    review.length
      ? h("ul", { class: "list" }, review.map(detectionRow))
      : h("p", { class: "note" }, "Nada que revisar: cada detección tiene AIS, es un objeto fijo o está donde no se recibe AIS."),
    pass.no_coverage > 0 &&
      h("p", { class: "note" }, `${plural(pass.no_coverage, "detección está", "detecciones están")} donde aisstream apenas recibe AIS: sin AIS ahí no se puede saber si emitían.`),
    h("h3", { class: "section-title" }, `Con AIS (${matched.length})`),
    h("ul", { class: "list" }, matched.map(detectionRow)),
  );
}

// Events ----------------------------------------------------------------------------------------

async function loadEvents() {
  const { minSeverity, hours } = state.eventFilter;
  state.events = await api(`/events?hours=${hours}&min_severity=${minSeverity}&limit=500`);
  byId.events = new Map(state.events.features.map((f) => [f.properties.id, f.properties]));
  setData("events", state.events);
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
          eventPopup(p, f.geometry.coordinates);
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
        p.sanctioned && tag("arrow", C.critical, "Sancionado"),
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
    // Sanctioned first, then by name; vessels with no name yet last.
    const live = [...state.live.features].sort(
      (a, b) =>
        Number(b.properties.sanctioned) - Number(a.properties.sanctioned) ||
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
    !v.track && h("p", { class: "note" }, "Sin derrota en las últimas 24 h."),
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

// --- data loading -----------------------------------------------------------------------------

async function loadCoverage() {
  if (state.coverageLoaded) return;
  state.coverageLoaded = true;
  try {
    setData("coverage", await api("/coverage?days=7"));
  } catch {
    state.coverageLoaded = false;
  }
}

async function loadLive() {
  state.live = await api("/vessels/live?minutes=60");
  setData("vessels", state.live);
}

async function refresh() {
  if (document.hidden) return;
  try {
    state.status = await api("/status");
    renderStatus();
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

async function start() {
  fitPadding();
  phone.addEventListener("change", fitPadding);
  addLayers();
  wireMapEvents();
  renderLayers();
  const [area, land, infra] = await Promise.all([api("/map/area"), api("/map/land"), api("/map/infrastructure")]);
  state.area = area;
  map.setMaxBounds([[area.west - 1.5, area.south - 1], [area.east + 1.5, area.north + 1]]);
  setData("land", land);
  setData("infra", infra);
  if (!parseRoute().id) map.fitBounds([[area.west, area.south], [area.east, area.north]], { padding: 20, duration: 0 });
  if (state.layers.coverage) loadCoverage();
  applyLayerVisibility();
  if (phone.matches) {
    const attribution = document.querySelector(".maplibregl-ctrl-attrib");
    attribution?.removeAttribute("open");
    attribution?.classList.remove("maplibregl-compact-show");
  }

  await refresh();
  // Show the latest pass on the map from the start, without leaving the current view.
  if (state.status?.latest_pass && !(parseRoute().tab === "passes" && parseRoute().id)) {
    await loadPass(state.status.latest_pass.id).catch(() => {});
  }
  await render();
  renderLegend();
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

map.on("load", () =>
  start().catch((error) => {
    renderStatus(true);
    fill(view, message("error-note", `No se pudo cargar: ${error.message}`));
  }),
);
