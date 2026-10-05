import { categoryLabel, distanceLabel, radiusLabel } from "./format";

describe("format", () => {
  it("turns category slugs into labels", () => {
    expect(categoryLabel("thai_restaurant")).toBe("Thai restaurant");
    expect(categoryLabel("cafe")).toBe("Cafe");
  });

  it("shows short distances in meters and long ones in kilometers", () => {
    expect(distanceLabel(140)).toBe("140 m away");
    expect(distanceLabel(999)).toBe("999 m away");
    expect(distanceLabel(1250)).toBe("1.3 km away");
  });

  it("labels radii", () => {
    expect(radiusLabel(500)).toBe("500 m");
    expect(radiusLabel(2000)).toBe("2 km");
  });
});
