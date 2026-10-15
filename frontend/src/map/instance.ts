// The one MapLibre map, for the components that draw on it or move it.
import type { GeoJSONSource, GeoJSONSourceSpecification, LngLatBoundsLike, Map as MapLibre } from "maplibre-gl";
import { create } from "zustand";
import type { LngLat } from "../lib/geo";

export const EMPTY = { type: "FeatureCollection" as const, features: [] };

let instance: MapLibre | null = null;

/** Whether the map has loaded its style and our layers: before that, nothing can be drawn. */
export const useMapReady = create<{ ready: boolean }>(() => ({ ready: false }));

export function setMap(map: MapLibre | null): void {
  instance = map;
  useMapReady.setState({ ready: map != null });
}

export function getMap(): MapLibre | null {
  return instance;
}

export function setData(source: string, data: unknown): void {
  (instance?.getSource(source) as GeoJSONSource | undefined)?.setData(
    (data ?? EMPTY) as GeoJSONSourceSpecification["data"] & object,
  );
}

/** Once the page has laid out what changed with it (the inspector opening narrows the map, and a resize
 * stops a camera animation half way): two frames on, with the map's size measured again. */
function afterLayout(move: (map: MapLibre) => void): void {
  requestAnimationFrame(() =>
    requestAnimationFrame(() => {
      if (!instance) return;
      instance.resize();
      move(instance);
    }),
  );
}

export function flyTo(lngLat: LngLat, zoom = 11): void {
  afterLayout((map) => map.flyTo({ center: lngLat, zoom: Math.max(map.getZoom(), zoom), duration: 700 }));
}

export function fitBounds(bounds: LngLatBoundsLike, maxZoom = 11): void {
  afterLayout((map) => map.fitBounds(bounds, { padding: 48, maxZoom, duration: 600 }));
}
