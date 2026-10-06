import {
  categoryLabel,
  coordinateLabel,
  distanceLabel,
  durationLabel,
  radiusLabel,
} from "./format";

describe("format", () => {
  it("turns category slugs into labels", () => {
    expect(categoryLabel("thai_restaurant")).toBe("Thai restaurant");
    expect(categoryLabel("cafe")).toBe("Cafe");
  });

  it("shows distances as approximate, in meters under a kilometer", () => {
    expect(distanceLabel(140)).toBe("~140 m");
    expect(distanceLabel(999)).toBe("~999 m");
    expect(distanceLabel(1250)).toBe("~1.3 km");
  });

  it("labels radii, coordinates and durations", () => {
    expect(radiusLabel(500)).toBe("500 m");
    expect(radiusLabel(2000)).toBe("2 km");
    expect(coordinateLabel(3.1480001, 101.695)).toBe("3.148, 101.695");
    expect(durationLabel(null)).toBe("-");
    expect(durationLabel(12)).toBe("12 ms");
    expect(durationLabel(1500)).toBe("1.5 s");
  });
});
