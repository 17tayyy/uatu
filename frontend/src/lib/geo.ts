import type { Geometry } from "../api/types";

export type LngLat = [number, number];
export type Bounds = [[number, number], [number, number]]; // [[west, south], [east, north]]

/** Whether a point falls inside a GeoJSON Polygon or MultiPolygon (outer rings). */
export function contains(geometry: Geometry, [x, y]: LngLat): boolean {
  const polygons = (geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates) as number[][][][];
  return polygons.some(([ring]) => {
    if (!ring) return false;
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i] as [number, number];
      const [xj, yj] = ring[j] as [number, number];
      if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  });
}

/** The box around any GeoJSON coordinates. */
export function boundsOf(coordinates: unknown): Bounds {
  let w = Infinity;
  let s = Infinity;
  let e = -Infinity;
  let n = -Infinity;
  const walk = (c: unknown): void => {
    if (Array.isArray(c) && typeof c[0] === "number") {
      const [x, y] = c as [number, number];
      w = Math.min(w, x);
      e = Math.max(e, x);
      s = Math.min(s, y);
      n = Math.max(n, y);
    } else if (Array.isArray(c)) c.forEach(walk);
  };
  walk(coordinates);
  return [
    [w, s],
    [e, n],
  ];
}
