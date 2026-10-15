// The time machine's arithmetic: where every vessel was at a moment, from hourly chunks of reports.
// Between two reports a vessel is placed on the straight line joining them.
import type { HistoryTrack } from "../api/types";

export const HOUR = 3_600_000;
export const STALE_S = 30 * 60; // unheard for longer, a vessel is not drawn (as the radar pairing: sar/ais.py)
export const TRAIL_S = 60 * 60;
export const SPEEDS = [10, 60, 300, 1800]; // times real time: 1 s plays 10 s ... 30 min

type Point = [number, number];

export interface Frame {
  vessels: { type: "Feature"; geometry: { type: "Point"; coordinates: Point }; properties: Record<string, unknown> }[];
  trails: {
    type: "Feature";
    geometry: { type: "LineString"; coordinates: Point[] };
    properties: Record<string, unknown>;
  }[];
}

/** The hours whose reports a moment needs: the one before (trails), its own and the next (to play into). */
export function hoursAround(t: number, now = Date.now()): number[] {
  const hour = Math.floor(t / HOUR) * HOUR;
  return [hour - HOUR, hour, hour + HOUR].filter((k) => k <= now);
}

/** Index of the first element of a sorted array greater than x (or, `orEqual`, not less than x). */
export function bisect(a: number[], x: number, orEqual = false): number {
  let lo = 0;
  let hi = a.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    const v = a[mid] as number;
    if (v < x || (!orEqual && v === x)) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

export function frameAt(chunks: HistoryTrack[][], tMs: number): Frame {
  const ts = tMs / 1000;
  const pieces = new Map<number, HistoryTrack[]>();
  for (const tracks of chunks) {
    for (const track of tracks) {
      const known = pieces.get(track.mmsi);
      if (known) known.push(track);
      else pieces.set(track.mmsi, [track]);
    }
  }
  const frame: Frame = { vessels: [], trails: [] };
  for (const [mmsi, parts] of pieces) {
    parts.sort((a, b) => (a.t[0] ?? 0) - (b.t[0] ?? 0));
    const t = parts.flatMap((p) => p.t);
    const lon = parts.flatMap((p) => p.lon);
    const lat = parts.flatMap((p) => p.lat);
    const i = bisect(t, ts) - 1; // its last report by then
    if (i < 0 || ts - (t[i] as number) > STALE_S) continue;
    let at: Point = [lon[i] as number, lat[i] as number];
    const j = i + 1;
    if (j < t.length && (t[j] as number) > (t[i] as number) && (t[j] as number) - (t[i] as number) <= STALE_S) {
      const f = (ts - (t[i] as number)) / ((t[j] as number) - (t[i] as number));
      at = [
        (lon[i] as number) + f * ((lon[j] as number) - (lon[i] as number)),
        (lat[i] as number) + f * ((lat[j] as number) - (lat[i] as number)),
      ];
    }
    const meta = parts[0] as HistoryTrack;
    frame.vessels.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: at },
      properties: {
        mmsi,
        name: meta.name,
        ship_type: meta.ship_type,
        length_m: meta.length_m,
        flag: meta.flag,
        sog: parts.flatMap((p) => p.sog)[i] ?? null,
        cog: parts.flatMap((p) => p.cog)[i] ?? null,
        received_at: new Date((t[i] as number) * 1000).toISOString(),
        sanctioned: meta.sanctioned,
        risk: meta.risk,
      },
    });
    const trail: Point[] = [];
    for (let m = bisect(t, ts - TRAIL_S, true); m <= i; m++) trail.push([lon[m] as number, lat[m] as number]);
    trail.push(at);
    if (trail.length >= 2) {
      frame.trails.push({
        type: "Feature",
        geometry: { type: "LineString", coordinates: trail },
        properties: { sanctioned: meta.sanctioned, risk: meta.risk },
      });
    }
  }
  return frame;
}
