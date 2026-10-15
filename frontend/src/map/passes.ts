// Which radar pass goes with a moment or an event.

import type { EventProps, PassDetail, PassSummary } from "../api/types";
import { queryClient } from "../app/queryClient";
import { useUi } from "../app/store";
import { contains, type LngLat } from "../lib/geo";

function passes(): PassSummary[] {
  return queryClient.getQueryData<PassSummary[]>(["passes", useUi.getState().zone]) ?? [];
}

/** The pass that imaged an event's place while it went on (give or take half an hour), if any. */
export function passForEvent(event: EventProps, lngLat: LngLat): PassSummary | undefined {
  const start = new Date(event.start_at).getTime() - 30 * 60_000;
  const end = (event.end_at ? new Date(event.end_at).getTime() : Date.now()) + 30 * 60_000;
  return passes().find((p) => {
    const t = new Date(p.acquired_at).getTime();
    return t >= start && t <= end && contains(p.footprint, lngLat);
  });
}

/** The pass nearest a moment, if a satellite passed within 45 minutes of it. */
export function passNear(t: number, list: PassSummary[]): PassSummary | undefined {
  const distance = (p: PassSummary) => Math.abs(new Date(p.acquired_at).getTime() - t);
  return list.filter((p) => distance(p) <= 45 * 60_000).sort((a, b) => distance(a) - distance(b))[0];
}

/** When the radar looked at the pass's sea: the middle of its detections' times (a frame takes ~25 s). */
export function passMoment(pass: PassDetail): number {
  const times = pass.detections_geojson.features
    .map((f) => new Date(f.properties.seen_at).getTime())
    .sort((a, b) => a - b);
  return times.length ? (times[Math.floor(times.length / 2)] as number) : new Date(pass.acquired_at).getTime();
}

/** The zone a pass belongs to, from its id (S1D-0097DF-estrecho). */
export function zoneOfPass(passId: string): string {
  return passId.split("-").at(-1) ?? "";
}
