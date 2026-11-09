import {
  categoryLabel,
  coordinateLabel,
  distanceLabel,
  durationLabel,
  radiusLabel,
  usUnitsForDisplay,
} from "./format";

describe("format", () => {
  it("turns category slugs into labels", () => {
    expect(categoryLabel("thai_restaurant")).toBe("Thai restaurant");
    expect(categoryLabel("cafe")).toBe("Cafe");
  });

  it("shows distances as approximate, in feet under a tenth of a mile and miles above", () => {
    expect(distanceLabel(0)).toBe("~0 ft");
    expect(distanceLabel(140)).toBe("~460 ft");
    expect(distanceLabel(160)).toBe("~520 ft");
    expect(distanceLabel(161)).toBe("~0.1 mi");
    expect(distanceLabel(482)).toBe("~0.3 mi");
    expect(distanceLabel(1609)).toBe("~1.0 mi");
    expect(distanceLabel(5000)).toBe("~3.1 mi");
  });

  it("rewrites every meter value in run JSON as miles and feet, and leaves the rest alone", () => {
    const run = {
      searched: { latitude: 3.148, radius_m: 1609 },
      places: [{ name: "Ny - Times Square", distance_m: 34, rank: 1 }],
      count: 1,
    };
    expect(usUnitsForDisplay(run)).toEqual({
      searched: { latitude: 3.148, radius: "1 mi" },
      places: [{ name: "Ny - Times Square", distance: "~110 ft", rank: 1 }],
      count: 1,
    });
    expect(usUnitsForDisplay(null)).toBeNull();
    expect(usUnitsForDisplay("radius_m")).toBe("radius_m");
  });

  it("labels radii, coordinates and durations", () => {
    expect(radiusLabel(402)).toBe("0.25 mi");
    expect(radiusLabel(805)).toBe("0.5 mi");
    expect(radiusLabel(1609)).toBe("1 mi");
    expect(radiusLabel(4828)).toBe("3 mi");
    expect(radiusLabel(100)).toBe("0.06 mi");
    expect(coordinateLabel(3.1480001, 101.695)).toBe("3.148, 101.695");
    expect(durationLabel(null)).toBe("-");
    expect(durationLabel(12)).toBe("12 ms");
    expect(durationLabel(1500)).toBe("1.5 s");
  });
});
