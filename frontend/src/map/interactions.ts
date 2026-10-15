// Pointing at the map: a tooltip on hover, and on click the thing opens (in the inspector, or its page).
import { type MapGeoJSONFeature, type Map as MapLibre, Popup } from "maplibre-gl";
import type { EventProps, FeatureCollection, PassDetail } from "../api/types";
import { queryClient } from "../app/queryClient";
import { navigate } from "../app/router";
import { useUi } from "../app/store";
import { type DetectionKind, KIND } from "../lib/constants";
import { when } from "../lib/format";
import type { LngLat } from "../lib/geo";
import { listing } from "../lib/listing";
import { passForEvent } from "./passes";

const CLICKABLE = ["vessels-moving", "vessels-stopped", "events", "detections", "pass-ais"];

const text = (v: unknown): string | null => (v == null || v === "null" ? null : String(v));

function hoverText(feature: MapGeoJSONFeature): string {
  const p = feature.properties;
  switch (feature.layer.id) {
    case "vessels-moving":
    case "vessels-stopped": {
      const name = text(p.name) ?? `MMSI ${p.mmsi}`;
      const speed = text(p.sog) ? ` · ${p.sog} nudos` : "";
      const at = useUi.getState().time ? ` · oído ${when(String(p.received_at))}` : "";
      const listed = listing({ sanctioned: p.sanctioned === true, risk: text(p.risk), watched: p.watched === true });
      return `${name}${speed}${at}${listed.label ? ` · ${listed.label}` : ""}`;
    }
    case "detections": {
      const who = text(p.mmsi)
        ? (text(p.vessel_name) ?? `MMSI ${p.mmsi}`)
        : text(p.suspected_mmsi)
          ? `¿${text(p.suspect_name) ?? p.suspected_mmsi}?`
          : null;
      const kind = KIND[p.kind as DetectionKind]?.label ?? "Detección";
      return [kind, who, p.length_m && `${Math.round(Number(p.length_m))} m`].filter(Boolean).join(" · ");
    }
    case "pass-ais":
      return `${text(p.name) ?? `MMSI ${p.mmsi}`} · AIS a la hora de la pasada`;
    case "events":
      return String(p.title);
    default:
      return "";
  }
}

/** An event's own properties (nested ones included): MapLibre hands them back flattened. */
function eventById(id: number): EventProps | undefined {
  for (const [, data] of queryClient.getQueriesData<FeatureCollection<EventProps>>({ queryKey: ["events"] })) {
    const found = data?.features.find((f) => f.properties.id === id);
    if (found) return found.properties;
  }
  return undefined;
}

export function selectEvent(event: EventProps, lngLat: LngLat): void {
  const ui = useUi.getState();
  ui.select({ kind: "event", event, lngLat });
  if (ui.time) return; // the time machine shows the pass of its moment
  const pass = passForEvent(event, lngLat);
  ui.showPass(pass?.id ?? null, pass ? null : "Sin imagen radar: ningún satélite pasó durante este evento");
}

export function wireInteractions(map: MapLibre): void {
  const hover = new Popup({ closeButton: false, closeOnClick: false, className: "hover-tip", offset: 12 });
  for (const id of CLICKABLE) {
    map.on("mouseenter", id, () => {
      map.getCanvas().style.cursor = "pointer";
    });
    map.on("mouseleave", id, () => {
      map.getCanvas().style.cursor = "";
      hover.remove();
    });
    map.on("mousemove", id, (e) => {
      const feature = e.features?.[0];
      if (feature) hover.setLngLat(e.lngLat).setText(hoverText(feature)).addTo(map);
    });
  }

  map.on("click", (e) => {
    const [feature] = map.queryRenderedFeatures(e.point, { layers: CLICKABLE.filter((id) => map.getLayer(id)) });
    const ui = useUi.getState();
    if (!feature) {
      ui.select(null);
      return;
    }
    hover.remove();
    const p = feature.properties;
    const lngLat = (feature.geometry as unknown as { coordinates: LngLat }).coordinates;
    switch (feature.layer.id) {
      case "vessels-moving":
      case "vessels-stopped":
        navigate(`vessels/${p.mmsi}`);
        break;
      case "events": {
        const event = eventById(Number(p.id));
        if (event) selectEvent(event, lngLat);
        break;
      }
      case "detections":
        if (ui.passId) ui.select({ kind: "detection", id: Number(p.id), passId: ui.passId, lngLat });
        break;
      case "pass-ais": {
        const pass = ui.passId ? queryClient.getQueryData<PassDetail>(["pass", ui.passId]) : undefined;
        const ais = pass?.ais_geojson.features.find((f) => f.properties.mmsi === Number(p.mmsi))?.properties;
        if (ais && ui.passId) ui.select({ kind: "passAis", ais, passId: ui.passId, lngLat });
        break;
      }
    }
  });
}
