/** "thai_restaurant" becomes "Thai restaurant". */
export function categoryLabel(category: string): string {
  const text = category.replace(/_/g, " ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Distance is great-circle, so it is always approximate and never a walking time. */
export function distanceLabel(meters: number): string {
  if (meters < 1000) return `~${meters} m`;
  return `~${(meters / 1000).toFixed(1)} km`;
}

export function radiusLabel(meters: number): string {
  return meters < 1000 ? `${meters} m` : `${meters / 1000} km`;
}

export function coordinateLabel(latitude: number, longitude: number): string {
  return `${latitude.toFixed(3)}, ${longitude.toFixed(3)}`;
}

export function durationLabel(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "-";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}
