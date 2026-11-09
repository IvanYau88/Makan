import { FEET_PER_METER, metersToMiles } from "./units";

/** "thai_restaurant" becomes "Thai restaurant". */
export function categoryLabel(category: string): string {
  const text = category.replace(/_/g, " ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Distance is great-circle, so it is always approximate and never a walking time. */
export function distanceLabel(meters: number): string {
  const miles = metersToMiles(meters);
  // Under a tenth of a mile, one decimal would say "0.0 mi", so use feet. Keep in step with
  // `distance_label` in makan/places/base.py, which words the same distances for the API.
  if (miles < 0.1) return `~${Math.round((meters * FEET_PER_METER) / 10) * 10} ft`;
  return `~${miles.toFixed(1)} mi`;
}

/** A search radius: up to two decimals, so a quarter mile reads "0.25 mi" and a mile "1 mi". */
export function radiusLabel(meters: number): string {
  return `${Number(metersToMiles(meters).toFixed(2))} mi`;
}

/**
 * A copy of a run's JSON for people to read: every meter value (a key ending `_m`, such as
 * `radius_m` or `distance_m`) becomes a miles and feet label under the key without `_m`.
 * The run itself keeps meters, so the Execution view's JSON is the only raw output that gets this copy.
 */
export function usUnitsForDisplay(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(usUnitsForDisplay);
  if (value === null || typeof value !== "object") return value;
  const out: Record<string, unknown> = {};
  for (const [key, child] of Object.entries(value)) {
    if (key.endsWith("_m") && typeof child === "number") {
      const name = key.slice(0, -"_m".length);
      out[name] = name === "radius" ? radiusLabel(child) : distanceLabel(child);
    } else {
      out[key] = usUnitsForDisplay(child);
    }
  }
  return out;
}

export function coordinateLabel(latitude: number, longitude: number): string {
  return `${latitude.toFixed(3)}, ${longitude.toFixed(3)}`;
}

export function durationLabel(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "-";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}
