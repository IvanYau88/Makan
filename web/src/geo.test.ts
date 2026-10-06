import { PENDING_MOVE_M, US_CENTER, distanceBetween, hasMoved, roundCenter, usZoom } from "./geo";

const KLCC = { latitude: 3.148, longitude: 101.695 };

describe("geo", () => {
  it("measures great-circle distance like the backend", () => {
    expect(distanceBetween(KLCC, KLCC)).toBe(0);
    // One thousandth of a degree of latitude is about 111 m.
    expect(distanceBetween(KLCC, { latitude: 3.149, longitude: 101.695 })).toBeCloseTo(111.2, 0);
  });

  it("rounds a center to about 100 m", () => {
    expect(roundCenter({ latitude: 3.14849, longitude: 101.69551 })).toEqual({
      latitude: 3.148,
      longitude: 101.696,
    });
  });

  it("treats rounding and settling as no move, and a real pan as a new place", () => {
    expect(hasMoved(KLCC, { latitude: 3.1485, longitude: 101.6955 })).toBe(false);
    expect(hasMoved(KLCC, { latitude: 3.152, longitude: 101.695 })).toBe(true);
    expect(PENDING_MOVE_M).toBeGreaterThan(79); // above the worst rounding error
  });

  it("fits the United States across a phone or a desktop map, in the map's half steps", () => {
    expect(usZoom(390)).toBe(3);
    expect(usZoom(1000)).toBe(4.5);
    expect(usZoom(0)).toBe(3); // a hidden map has no width yet
    expect(US_CENTER.longitude).toBeLessThan(-90); // the middle of the US, not anywhere in Asia
  });
});
