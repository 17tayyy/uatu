import { describe, expect, it } from "vitest";
import type { HistoryTrack } from "../api/types";
import { bisect, frameAt, HOUR, hoursAround } from "./time";

const T0 = Date.UTC(2026, 9, 15, 6, 0) / 1000; // seconds

function track(mmsi: number, t: number[], lon: number[], lat: number[]): HistoryTrack {
  return {
    mmsi,
    name: `TEST ${mmsi}`,
    ship_type: null,
    length_m: null,
    flag: null,
    sanctioned: false,
    risk: null,
    t: t.map((x) => T0 + x),
    lon,
    lat,
    sog: t.map(() => 10),
    cog: t.map(() => 90),
  };
}

describe("the time machine", () => {
  it("places a vessel on the line between its reports", () => {
    const frame = frameAt([[track(1, [0, 600], [-5.6, -5.5], [36, 36.1])]], (T0 + 300) * 1000);
    expect(frame.vessels).toHaveLength(1);
    const [lon, lat] = frame.vessels[0]!.geometry.coordinates;
    expect(lon).toBeCloseTo(-5.55);
    expect(lat).toBeCloseTo(36.05);
    expect(frame.trails[0]!.geometry.coordinates).toHaveLength(2);
  });

  it("holds the last report for half an hour, then drops it", () => {
    const heard = [[track(2, [0], [-5.6], [36])]];
    expect(frameAt(heard, (T0 + 29 * 60) * 1000).vessels[0]!.geometry.coordinates).toEqual([-5.6, 36]);
    expect(frameAt(heard, (T0 + 31 * 60) * 1000).vessels).toHaveLength(0);
    expect(frameAt(heard, (T0 - 1) * 1000).vessels).toHaveLength(0); // not heard yet
  });

  it("joins a vessel's pieces from consecutive hours", () => {
    const before = track(3, [-60], [-5.6], [36]);
    const after = track(3, [60], [-5.4], [36]);
    const frame = frameAt([[after], [before]], T0 * 1000);
    expect(frame.vessels[0]!.geometry.coordinates[0]).toBeCloseTo(-5.5);
  });

  it("finds the hours around a moment, never the future", () => {
    const t = Date.UTC(2026, 9, 15, 6, 30);
    const hour = Date.UTC(2026, 9, 15, 6);
    expect(hoursAround(t, t + 2 * HOUR)).toEqual([hour - HOUR, hour, hour + HOUR]);
    expect(hoursAround(t, t)).toEqual([hour - HOUR, hour]);
  });

  it("bisects like the standard library", () => {
    expect(bisect([1, 2, 2, 3], 2)).toBe(3);
    expect(bisect([1, 2, 2, 3], 2, true)).toBe(1);
  });
});
