import { describe, expect, it } from "vitest";
import { duration, metres, plural, utc } from "./format";

describe("the Spanish way", () => {
  it("writes durations, distances and counts", () => {
    expect(duration(45)).toBe("45 min");
    expect(duration(90)).toBe("1 h 30 min");
    expect(duration(120)).toBe("2 h");
    expect(metres(925)).toBe("925 m");
    expect(metres(1500)).toBe("1,5 km");
    expect(plural(1, "barco", "barcos")).toBe("1 barco");
    expect(plural(1234, "barco", "barcos")).toBe("1234 barcos".replace("1234", (1234).toLocaleString("es-ES")));
    expect(utc("2026-10-15T06:18:52Z")).toBe("06:18 UTC");
  });
});
