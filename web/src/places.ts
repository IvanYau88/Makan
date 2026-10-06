import type { Place } from "./types";

export interface PlaceFilters {
  /** A category slug, or "all". */
  category: string;
  /** Only places that matched what the request asked for, not nearby alternatives. */
  matchedOnly: boolean;
  sort: "rank" | "distance" | "name";
}

export const NO_FILTERS: PlaceFilters = { category: "all", matchedOnly: false, sort: "rank" };

export function isFiltered(filters: PlaceFilters): boolean {
  return filters.category !== "all" || filters.matchedOnly;
}

/** The places that pass the filters, in the chosen order. Ranks never change, only the order. */
export function visiblePlaces(places: Place[], filters: PlaceFilters): Place[] {
  const shown = places.filter(
    (p) =>
      (filters.category === "all" || p.category === filters.category) &&
      (!filters.matchedOnly || p.matched),
  );
  if (filters.sort === "distance") {
    return [...shown].sort((a, b) => a.distance_m - b.distance_m || a.rank - b.rank);
  }
  if (filters.sort === "name") {
    return [...shown].sort((a, b) => a.name.localeCompare(b.name) || a.rank - b.rank);
  }
  return [...shown].sort((a, b) => a.rank - b.rank);
}

/** Each category present, most common first, with how many places have it. */
export function categoryCounts(places: Place[]): { category: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const p of places) counts.set(p.category, (counts.get(p.category) ?? 0) + 1);
  return [...counts.entries()]
    .map(([category, count]) => ({ category, count }))
    .sort((a, b) => b.count - a.count || a.category.localeCompare(b.category));
}

/** A place can be pinned only when the data gave it coordinates. */
export function hasCoordinates(place: Place): place is Place & { lat: number; lon: number } {
  return place.lat !== null && place.lon !== null;
}
