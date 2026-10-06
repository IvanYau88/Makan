/** The pin's touch target, the project's 44px minimum. */
export const PIN_HIT_MAX_PX = 44;
/** Never shrink a target below the WCAG AA minimum, even for pins on top of each other. */
export const PIN_HIT_MIN_PX = 24;

export interface PinPoint {
  id: string;
  x: number;
  y: number;
}

/**
 * How wide each pin's round touch target is, in pixels. Every pin gets 44px unless a neighbour is
 * closer than that, and then it gets the distance to that neighbour, so two targets are tangent at
 * worst and never compete for the same touch. Pins closer than the minimum still overlap, and the
 * person can zoom in or use the list, which numbers the same places.
 */
export function pinHitSizes(points: PinPoint[]): Map<string, number> {
  const sizes = new Map<string, number>();
  for (const pin of points) {
    let nearest = Infinity;
    for (const other of points) {
      if (other.id !== pin.id)
        nearest = Math.min(nearest, Math.hypot(pin.x - other.x, pin.y - other.y));
    }
    sizes.set(pin.id, Math.max(PIN_HIT_MIN_PX, Math.min(PIN_HIT_MAX_PX, Math.floor(nearest))));
  }
  return sizes;
}
