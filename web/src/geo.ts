import type { Coordinates } from "./types";

const EARTH_RADIUS_M = 6_371_000;

/** Great-circle distance in meters, the same measure the backend uses. */
export function distanceBetween(a: Coordinates, b: Coordinates): number {
  const p1 = (a.latitude * Math.PI) / 180;
  const p2 = (b.latitude * Math.PI) / 180;
  const h =
    Math.sin((p2 - p1) / 2) ** 2 +
    Math.cos(p1) * Math.cos(p2) * Math.sin(((b.longitude - a.longitude) * Math.PI) / 360) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(h));
}

/** The center Makan sends and keeps: about 100 m, like the backend's rounding. */
export function roundCenter(c: Coordinates): Coordinates {
  const round = (degrees: number) => Math.round(degrees * 1000) / 1000;
  return { latitude: round(c.latitude), longitude: round(c.longitude) };
}

/** Panning less than this is the map settling, not a new place to search. */
export const PENDING_MOVE_M = 120;

export function hasMoved(from: Coordinates, to: Coordinates): boolean {
  return distanceBetween(from, to) > PENDING_MOVE_M;
}

/**
 * Where the map starts when it does not know where the person is: the middle of the contiguous
 * United States, zoomed out to show all of it. It is a view, not a place to search.
 */
export const US_CENTER: Coordinates = { latitude: 39.5, longitude: -98.35 };

/** At this zoom or closer, the map shows a town, so its center is somewhere worth searching. */
export const SEARCH_ZOOM = 10;

/** The zoom that fits the contiguous US across a map this wide, in the map's half steps. */
export function usZoom(widthPx: number): number {
  const US_SPAN_DEGREES = 62; // 58 degrees of longitude, plus margin
  const zoom = Math.log2((widthPx * 360) / (US_SPAN_DEGREES * 256));
  return widthPx > 0 ? Math.max(2, Math.floor(zoom * 2) / 2) : 3;
}
