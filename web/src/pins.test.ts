import { PIN_HIT_MAX_PX, PIN_HIT_MIN_PX, pinHitSizes } from "./pins";

describe("pinHitSizes", () => {
  it("gives a lone pin and well spaced pins the full 44px target", () => {
    expect([...pinHitSizes([{ id: "a", x: 0, y: 0 }]).values()]).toEqual([PIN_HIT_MAX_PX]);
    const far = pinHitSizes([
      { id: "a", x: 0, y: 0 },
      { id: "b", x: 60, y: 0 },
    ]);
    expect(far.get("a")).toBe(44);
    expect(far.get("b")).toBe(44);
  });

  it("shrinks close pins so their targets never overlap", () => {
    const sizes = pinHitSizes([
      { id: "a", x: 0, y: 0 },
      { id: "b", x: 30, y: 0 },
      { id: "c", x: 200, y: 200 },
    ]);
    expect(sizes.get("a")).toBe(30);
    expect(sizes.get("b")).toBe(30);
    expect(sizes.get("c")).toBe(44);
  });

  it("uses the nearest neighbour, in any direction", () => {
    const sizes = pinHitSizes([
      { id: "a", x: 0, y: 0 },
      { id: "b", x: 30, y: 40 },
      { id: "c", x: -100, y: 0 },
    ]);
    expect(sizes.get("a")).toBe(44);
    expect(sizes.get("b")).toBe(44);
  });

  it("never goes below the minimum, even for pins on the same spot", () => {
    const sizes = pinHitSizes([
      { id: "a", x: 5, y: 5 },
      { id: "b", x: 5, y: 5 },
    ]);
    expect(sizes.get("a")).toBe(PIN_HIT_MIN_PX);
  });
});
