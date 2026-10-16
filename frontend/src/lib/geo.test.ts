import { describe, expect, it } from "vitest";
import { offset } from "./geo";

describe("offset", () => {
  it("moves north, east and back along a bearing", () => {
    const [lon, lat] = offset([-5.5, 36], 0, 1113.2);
    expect(lon).toBeCloseTo(-5.5, 9);
    expect(lat).toBeCloseTo(36.01, 6);
    const east = offset([-5.5, 36], 90, 1000);
    expect(east[1]).toBeCloseTo(36, 9);
    expect((east[0] + 5.5) * 111_320 * Math.cos((36 * Math.PI) / 180)).toBeCloseTo(1000, 6);
    const back = offset(offset([-5.5, 36], 75, 1500), 255, 1500);
    expect(back[0]).toBeCloseTo(-5.5, 5);
    expect(back[1]).toBeCloseTo(36, 5);
  });
});
