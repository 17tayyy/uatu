// What the viewer is looking at: zone, layers, the pass on the map, the selection, the time machine.
// Server data lives in TanStack Query (api/queries.ts); this is the rest.
import { create } from "zustand";
import type { AisCheck, EventProps, OpticalImage } from "../api/types";
import { ALL } from "../lib/constants";
import type { LngLat } from "../lib/geo";
import { load, save } from "../lib/storage";
import { flyTo } from "../map/instance";
import { LAYERS, type LayerKey } from "../map/layerConfig";

export type Selection =
  | { kind: "detection"; id: number; passId: string; lngLat: LngLat }
  | { kind: "event"; event: EventProps; lngLat: LngLat }
  | { kind: "passAis"; ais: AisCheck; passId: string; lngLat: LngLat }
  | null;

export interface TimeMachine {
  t: number; // ms
  playing: boolean;
  speed: number; // times real time
}

/** A detection in a list being reviewed, in the list's order: the keyboard moves along it. */
export interface ReviewItem {
  id: number;
  passId: string;
  lngLat: LngLat;
}

export interface EventFilter {
  minSeverity: 1 | 2 | 3;
  hours: 24 | 48 | 168;
}

interface Ui {
  zone: string;
  layers: Record<LayerKey, boolean>;
  passId: string | null; // the pass drawn on the map
  passNote: string | null; // why none is, when that needs saying
  selection: Selection;
  time: TimeMachine | null;
  optical: { image: OpticalImage; lngLat: LngLat } | null;
  loginOpen: boolean;
  eventFilter: EventFilter;
  vesselQuery: string;
  lastVessel: { mmsi: number; name: string } | null;
  layersOpen: boolean;
  paletteOpen: boolean;
  reviewList: ReviewItem[];

  setZone(zone: string): void;
  toggleLayer(key: LayerKey, on?: boolean): void;
  showPass(passId: string | null, note?: string | null): void;
  select(selection: Selection): void;
  enterTime(t: number): void;
  setTime(t: number): void;
  setPlaying(playing: boolean): void;
  setSpeed(speed: number): void;
  exitTime(): void;
  showOptical(optical: Ui["optical"]): void;
  setLoginOpen(open: boolean): void;
  setEventFilter(filter: Partial<EventFilter>): void;
  setVesselQuery(q: string): void;
  setLastVessel(v: Ui["lastVessel"]): void;
  setLayersOpen(open: boolean): void;
  setPaletteOpen(open: boolean): void;
  setReviewList(list: ReviewItem[]): void;
  /** Open a detection: its pass on the map, the detection in the inspector, the map on it. */
  openDetection(item: ReviewItem): void;
}

const defaultLayers = Object.fromEntries(Object.entries(LAYERS).map(([k, l]) => [k, l.on])) as Record<
  LayerKey,
  boolean
>;

export const useUi = create<Ui>((set, get) => ({
  zone: load("uatu.zone", ALL),
  layers: { ...defaultLayers, ...load<Partial<Record<LayerKey, boolean>>>("uatu.layers.v3", {}) },
  passId: null,
  passNote: null,
  selection: null,
  time: null,
  optical: null,
  loginOpen: false,
  eventFilter: { minSeverity: 2, hours: 48 },
  vesselQuery: "",
  lastVessel: null,
  layersOpen: false,
  paletteOpen: false,
  reviewList: [],

  setZone: (zone) => {
    save("uatu.zone", zone);
    set({ zone, selection: null });
  },
  toggleLayer: (key, on) => {
    const layers = { ...get().layers, [key]: on ?? !get().layers[key] };
    save("uatu.layers.v3", layers);
    set({ layers });
  },
  showPass: (passId, note = null) => {
    if (passId === get().passId && note === get().passNote) return;
    set({ passId, passNote: passId ? null : note, optical: null });
  },
  select: (selection) => set({ selection }),
  enterTime: (t) => set({ time: { t: clampTime(t), playing: false, speed: get().time?.speed ?? 60 } }),
  setTime: (t) => {
    const time = get().time;
    if (time) set({ time: { ...time, t: clampTime(t) } });
  },
  setPlaying: (playing) => {
    const time = get().time;
    if (time) set({ time: { ...time, playing } });
  },
  setSpeed: (speed) => {
    const time = get().time;
    if (time) set({ time: { ...time, speed } });
  },
  exitTime: () => set({ time: null }),
  showOptical: (optical) => set({ optical }),
  setLoginOpen: (loginOpen) => set({ loginOpen }),
  setEventFilter: (filter) => set({ eventFilter: { ...get().eventFilter, ...filter } }),
  setVesselQuery: (vesselQuery) => set({ vesselQuery }),
  setLastVessel: (lastVessel) => set({ lastVessel }),
  setLayersOpen: (layersOpen) => set({ layersOpen }),
  setPaletteOpen: (paletteOpen) => set({ paletteOpen }),
  setReviewList: (reviewList) => set({ reviewList }),
  openDetection: (item) => {
    get().showPass(item.passId);
    set({ selection: { kind: "detection", ...item } });
    flyTo(item.lngLat, 11);
  },
}));

let recordingFrom = 0;
/** The earliest moment the time machine can show: when recording began (once the API says). */
export function setRecordingFrom(t: number): void {
  recordingFrom = t;
}

export function clampTime(t: number): number {
  return Math.min(Math.max(t, recordingFrom), Date.now());
}
