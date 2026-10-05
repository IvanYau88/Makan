/** "thai_restaurant" becomes "Thai restaurant". */
export function categoryLabel(category: string): string {
  const text = category.replace(/_/g, " ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function distanceLabel(meters: number): string {
  if (meters < 1000) return `${meters} m away`;
  return `${(meters / 1000).toFixed(1)} km away`;
}

export function radiusLabel(meters: number): string {
  return meters < 1000 ? `${meters} m` : `${meters / 1000} km`;
}
