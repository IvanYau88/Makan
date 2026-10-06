import { DEFAULT_RADIUS_M, RADII } from "./SearchBar";
import { radiusLabel } from "./format";
import { METERS_PER_MILE, metersToMiles, milesToMeters } from "./units";

describe("radius choices", () => {
  it("are whole fractions of a mile, inside the API's 100 to 5000 m limits", () => {
    expect(RADII.map(radiusLabel)).toEqual(["0.25 mi", "0.5 mi", "1 mi", "2 mi", "3 mi"]);
    for (const meters of RADII) {
      expect(meters).toBeGreaterThanOrEqual(100);
      expect(meters).toBeLessThanOrEqual(5000);
      expect(Number.isInteger(meters)).toBe(true);
    }
    expect(RADII).toContain(DEFAULT_RADIUS_M);
  });

  it("convert between meters and miles", () => {
    expect(milesToMeters(1)).toBe(1609);
    expect(metersToMiles(METERS_PER_MILE)).toBe(1);
  });
});
