// The map itself. Its children draw on it once it has loaded (see MapSync, TimeSync).
import { AttributionControl, Map as MapLibre, NavigationControl, ScaleControl, setWorkerUrl } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// MapLibre 6 runs its tile work in a module worker of its own: bundled by Vite, served from here.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { type ReactNode, useEffect, useRef } from "react";
import { useUi } from "../app/store";
import { API, C } from "../lib/constants";
import { setMap, useMapReady } from "./instance";
import { wireInteractions } from "./interactions";
import s from "./map.module.css";

setWorkerUrl(workerUrl);

import { addLayers } from "./style";

/** The latest radar of every place: versioned by the latest pass, so a new one brings new tiles. */
export function recentRadarTiles(zone: string, latestPassId: string | null | undefined): string {
  return `${window.location.origin}${API}/radar/tiles/{z}/{x}/{y}.png?zone=${encodeURIComponent(zone)}&days=7&v=${latestPassId ?? ""}`;
}

export function MapView({ children }: { children: ReactNode }) {
  const container = useRef<HTMLElement>(null);
  const ready = useMapReady((m) => m.ready);

  useEffect(() => {
    const map = new MapLibre({
      container: container.current!,
      style: {
        version: 8,
        sources: {},
        layers: [{ id: "sea", type: "background", paint: { "background-color": C.bg } }],
      },
      center: [-4.5, 37.5],
      zoom: 5.5,
      minZoom: 4,
      maxZoom: 15,
      attributionControl: false,
      dragRotate: false,
      pitchWithRotate: false,
    });
    map.touchZoomRotate.disableRotation();
    map.keyboard.disableRotation();
    map.addControl(new NavigationControl({ showCompass: false }), "bottom-right");
    map.addControl(new ScaleControl({ unit: "nautical" }), "bottom-right");
    map.addControl(
      new AttributionControl({
        compact: true,
        customAttribution:
          "Contiene datos modificados de Copernicus Sentinel · ESA WorldCover · EMODnet · TeleGeography · aisstream.io · OpenSanctions",
      }),
      "bottom-right",
    );
    map.on("load", () => {
      addLayers(map, recentRadarTiles(useUi.getState().zone, null));
      wireInteractions(map);
      // The attribution as its "i" button: open, it would cover the bottom of the map.
      map.getContainer().querySelector(".maplibregl-ctrl-attrib")?.classList.remove("maplibregl-compact-show");
      setMap(map);
    });
    return () => {
      setMap(null);
      map.remove();
    };
  }, []);

  return (
    <div className={s.wrap}>
      <section ref={container} className={s.map} aria-label="Mapa" />
      {ready && children}
    </div>
  );
}
