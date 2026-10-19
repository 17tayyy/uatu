// Keeps the map in step with the data and the viewer's choices. Draws nothing of its own.
import { useEffect, useRef } from "react";
import {
  useCoverage,
  useDensity,
  useEvents,
  useInfrastructure,
  useLand,
  useLive,
  usePass,
  useSatellite,
  useStatus,
  useUnimaged,
  useVessel,
  useWaters,
  useZones,
} from "../api/queries";
import type { PassDetail, ZoneInfo } from "../api/types";
import { useRoute } from "../app/router";
import { useUi } from "../app/store";
import { ALL } from "../lib/constants";
import { type LngLat, offset } from "../lib/geo";
import { EMPTY, getMap, setData } from "./instance";
import { LAYERS, type LayerKey, RADAR_FRAMES } from "./layerConfig";
import { recentRadarTiles } from "./MapView";
import { zoneOfPass } from "./passes";
import { addSatellite } from "./style";

export const PHONE = "(max-width: 760px)";

function zoneEdges(zones: ZoneInfo[], selected: string) {
  return {
    type: "FeatureCollection",
    features: zones.map((z) => ({
      type: "Feature",
      geometry: {
        type: "LineString",
        coordinates: [
          [z.west, z.south],
          [z.east, z.south],
          [z.east, z.north],
          [z.west, z.north],
          [z.west, z.south],
        ],
      },
      properties: { selected: z.key === selected },
    })),
  };
}

function passLinks(pass: PassDetail) {
  return {
    type: "FeatureCollection",
    features: pass.detections_geojson.features
      .filter((f) => f.properties.ais_lon != null && f.properties.ais_lat != null)
      .map((f) => ({
        type: "Feature",
        geometry: {
          type: "LineString",
          coordinates: [f.geometry.coordinates, [f.properties.ais_lon, f.properties.ais_lat]],
        },
        properties: {},
      })),
  };
}

/** Each clear wake: the line behind where the vessel really was, an arrow there, and a dashed line
 * from its echo (shifted by the Doppler effect) to it. */
function passWakes(pass: PassDetail) {
  return {
    type: "FeatureCollection",
    features: pass.detections_geojson.features.flatMap((f) => {
      const w = f.properties.wake;
      if (!w) return [];
      const ship: LngLat = [w.lon, w.lat];
      return [
        {
          type: "Feature",
          geometry: { type: "LineString", coordinates: [ship, offset(ship, w.course_deg + 180, w.length_m)] },
          properties: { part: "wake" },
        },
        {
          type: "Feature",
          geometry: { type: "LineString", coordinates: [f.geometry.coordinates, ship] },
          properties: { part: "shift" },
        },
        { type: "Feature", geometry: { type: "Point", coordinates: ship }, properties: { course: w.course_deg } },
      ];
    }),
  };
}

/** The pass's radar image: one per frame it came in, at 20 m as tiles where it has them. */
function showRadarImages(pass: PassDetail | null, visible: boolean): void {
  const map = getMap();
  if (!map) return;
  for (const id of RADAR_FRAMES) {
    if (map.getLayer(id)) map.removeLayer(id);
    if (map.getSource(id)) map.removeSource(id);
  }
  (pass?.images ?? []).slice(0, RADAR_FRAMES.length).forEach((image, i) => {
    const id = RADAR_FRAMES[i] as string;
    map.addSource(
      id,
      image.tiles && image.bounds
        ? {
            type: "raster",
            tiles: [image.tiles],
            tileSize: 256,
            bounds: image.bounds as [number, number, number, number],
            minzoom: 5,
            maxzoom: 14,
          }
        : {
            type: "image",
            url: image.url,
            coordinates: image.coordinates as [[number, number], [number, number], [number, number], [number, number]],
          },
    );
    map.addLayer(
      {
        id,
        type: "raster",
        source: id,
        layout: { visibility: visible ? "visible" : "none" },
        paint: { "raster-opacity": 0.95, "raster-brightness-max": 0.9, "raster-fade-duration": 0 },
      },
      "coverage",
    );
  });
}

export function MapSync() {
  const route = useRoute();
  const zone = useUi((s) => s.zone);
  const layers = useUi((s) => s.layers);
  const passId = useUi((s) => s.passId);
  const passNote = useUi((s) => s.passNote);
  const time = useUi((s) => s.time != null);
  const selection = useUi((s) => s.selection);
  const filter = useUi((s) => s.eventFilter);
  const showPass = useUi((s) => s.showPass);

  const zones = useZones();
  const land = useLand();
  const satellite = useSatellite();
  const status = useStatus(zone);
  const pass = usePass(passId);
  const livePositions = useLive(zone);
  const events = useEvents(zone, filter.hours, filter.minSeverity);
  const infra = useInfrastructure(zone, layers.infrastructure);
  const waters = useWaters(zone, layers.waters);
  const coverage = useCoverage(zone, layers.coverage);
  const density = useDensity(zone, layers.density);
  const unimaged = useUnimaged(zone, passId);
  const vesselMmsi = route.section === "vessels" ? route.mmsi : null;
  const vessel = useVessel(vesselMmsi);
  const latest = status.data?.latest_pass?.id ?? null;

  // The coast, its zones and the box the map may move in; framed on the zone when it changes.
  useEffect(() => setData("land", land.data), [land.data]);
  const framed = useRef<string | null>(null);
  useEffect(() => {
    const map = getMap();
    if (!map || !zones.data) return;
    setData("zones", zoneEdges(zones.data.zones, zone));
    const coast = zones.data.whole_coast;
    map.setMaxBounds([
      [coast.west - 3, coast.south - 2],
      [coast.east + 3, coast.north + 2],
    ]);
    if (framed.current === zone) return;
    const first = framed.current == null;
    framed.current = zone;
    if (first && window.location.hash.split("/").length > 2) return; // a link to something: it frames itself
    const area = zones.data.zones.find((z) => z.key === zone) ?? coast;
    map.fitBounds(
      [
        [area.west, area.south],
        [area.east, area.north],
      ],
      { padding: 24, duration: first ? 0 : 600 },
    );
  }, [zones.data, zone]);

  useEffect(() => {
    const map = getMap();
    if (map && satellite.data) addSatellite(map, satellite.data, useUi.getState().layers.satellite);
  }, [satellite.data]);

  useEffect(() => {
    const source = getMap()?.getSource("radar-recent") as { setTiles?: (t: string[]) => void } | undefined;
    source?.setTiles?.([recentRadarTiles(zone, latest)]);
  }, [zone, latest]);

  // Which pass is on the map: the route's, else the latest (the time machine picks its own).
  useEffect(() => {
    if (route.section === "passes" && route.passId) showPass(route.passId);
  }, [route, showPass]);
  useEffect(() => {
    if (time || !latest) return;
    const outside = passId != null && zone !== ALL && zoneOfPass(passId) !== zone;
    if ((passId == null && passNote == null) || outside) showPass(latest);
  }, [time, latest, passId, passNote, zone, showPass]);
  const wasTime = useRef(time);
  useEffect(() => {
    if (wasTime.current && !time && latest) showPass(latest); // back to the present: its latest pass
    wasTime.current = time;
  }, [time, latest, showPass]);

  // The pass: its footprint, detections, their links to AIS, the AIS of the moment, the radar image.
  const shown = passId ? (pass.data ?? null) : null;
  useEffect(() => {
    setData("footprint", shown ? { type: "Feature", geometry: shown.footprint, properties: {} } : EMPTY);
    setData("detections", shown?.detections_geojson);
    setData("pass-links", shown ? passLinks(shown) : EMPTY);
    setData("pass-wakes", shown ? passWakes(shown) : EMPTY);
    setData("pass-ais", shown?.ais_geojson);
    showRadarImages(shown, useUi.getState().layers.radar);
  }, [shown]);

  useEffect(() => {
    setData("unimaged", layers.unimaged ? unimaged.data : EMPTY);
  }, [unimaged.data, layers.unimaged]);

  // What is happening now; the time machine draws its own moment instead.
  useEffect(() => {
    if (!time) setData("vessels", livePositions.data);
  }, [livePositions.data, time]);
  useEffect(() => {
    if (!time) setData("events", events.data);
  }, [events.data, time]);

  useEffect(() => setData("infra", infra.data), [infra.data]);
  useEffect(() => setData("waters", waters.data), [waters.data]);
  useEffect(() => setData("coverage", coverage.data), [coverage.data]);
  useEffect(() => setData("density", density.data), [density.data]);

  // A vessel's page draws its track of the last 24 hours.
  useEffect(() => {
    const track = vesselMmsi != null ? vessel.data?.track : null;
    setData("track", track ? { type: "Feature", geometry: track, properties: {} } : EMPTY);
  }, [vessel.data, vesselMmsi]);

  // Layers on and off; again when layers are added (a pass's radar frames, the satellite).
  // biome-ignore lint/correctness/useExhaustiveDependencies: shown and satellite.data add layers to switch
  useEffect(() => {
    const map = getMap();
    if (!map) return;
    for (const [key, layer] of Object.entries(LAYERS) as [LayerKey, (typeof LAYERS)[LayerKey]][]) {
      for (const id of layer.ids) {
        if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", layers[key] ? "visible" : "none");
      }
    }
  }, [layers, shown, satellite.data]);

  // The selection: a ring on it, and a line to what it pairs with.
  useEffect(() => {
    setData("selected", selection ? { type: "Point", coordinates: selection.lngLat } : EMPTY);
    let to: LngLat | null = null;
    if (selection?.kind === "detection") {
      const d = shown?.detections_geojson.features.find((f) => f.properties.id === selection.id)?.properties;
      if (d?.ais_lon != null && d.ais_lat != null) to = [d.ais_lon, d.ais_lat];
    } else if (selection?.kind === "passAis") {
      const d = shown?.detections_geojson.features.find((f) => f.properties.mmsi === selection.ais.mmsi);
      if (d) to = d.geometry.coordinates as LngLat;
    }
    setData(
      "link",
      selection && to
        ? {
            type: "FeatureCollection",
            features: [
              {
                type: "Feature",
                geometry: { type: "LineString", coordinates: [selection.lngLat, to] },
                properties: {},
              },
              { type: "Feature", geometry: { type: "Point", coordinates: to }, properties: {} },
            ],
          }
        : EMPTY,
    );
  }, [selection, shown]);

  // On a pass or the review, what is happening now steps back.
  const reviewing = (route.section === "passes" && route.passId != null) || route.section === "review";
  useEffect(() => {
    const map = getMap();
    if (!map?.getLayer("events")) return;
    const opacity = reviewing && !time ? 0.2 : 1;
    map.setPaintProperty("vessels-moving", "icon-opacity", opacity);
    map.setPaintProperty("vessels-stopped", "circle-opacity", opacity);
    map.setPaintProperty("vessels-stopped", "circle-stroke-opacity", opacity);
    map.setPaintProperty("events", "icon-opacity", opacity);
  }, [reviewing, time]);

  // On a phone the inspector is a sheet over the map: keep what the map centres on clear of it.
  // (On a wider screen it sits beside the map, which simply gets narrower.)
  useEffect(() => {
    const map = getMap();
    if (!map) return;
    const phone = matchMedia(PHONE).matches;
    map.setPadding({
      top: 0,
      left: 0,
      right: 0,
      bottom: phone && selection ? map.getContainer().clientHeight * 0.62 : 0,
    });
  }, [selection]);

  return null;
}
