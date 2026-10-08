import { toTaste, validateTaste, valuesFrom } from "./tasteFormModel";

const EMPTY = { likes: "", dislikes: "", allergies: "", diets: "", never_places: "" };

describe("tasteFormModel", () => {
  it("round trips the lists through comma separated boxes", () => {
    const taste = {
      likes: ["thai", "ramen"],
      dislikes: ["burgers"],
      allergies: ["peanut"],
      diets: ["halal"],
      never_places: ["pizza hut"],
    };
    expect(toTaste(valuesFrom(taste))).toEqual(taste);
  });

  it("trims, drops blanks and repeats", () => {
    expect(toTaste({ ...EMPTY, likes: " Thai , thai,, ramen " }).likes).toEqual(["Thai", "ramen"]);
  });

  it("accepts an empty form", () => {
    expect(validateTaste(EMPTY)).toEqual({});
  });

  it("refuses a cuisine that is both liked and skipped, naming it", () => {
    const errors = validateTaste({ ...EMPTY, likes: "thai, ramen", dislikes: "Thai" });
    expect(errors.dislikes).toMatch(/Thai cannot be both liked and skipped/);
  });

  it("refuses too many, too long, and entries with no letter or number", () => {
    const many = Array.from({ length: 21 }, (_, i) => `d${i}`).join(",");
    expect(validateTaste({ ...EMPTY, diets: many }).diets).toMatch(/at most 20/);
    expect(validateTaste({ ...EMPTY, allergies: "a".repeat(41) }).allergies).toMatch(
      /40 characters/,
    );
    expect(validateTaste({ ...EMPTY, likes: "!!!" }).likes).toMatch(/letter or a number/);
  });

  it("accepts entries in any script", () => {
    expect(validateTaste({ ...EMPTY, likes: "吃饭, ñoquis" })).toEqual({});
  });
});
