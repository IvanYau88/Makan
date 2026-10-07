import {
  EMPTY_INPUTS,
  MAX_ITEMS,
  MAX_NAME_CHARS,
  MAX_NOTE_CHARS,
  MAX_TERM_CHARS,
  splitList,
  toInputs,
  validateInputs,
  valuesFrom,
} from "./groupForm";

describe("splitList", () => {
  it("splits on commas and new lines, trims, and drops blanks and repeats", () => {
    expect(splitList(" Thai ,, ramen\nthai,  fried   rice ")).toEqual([
      "Thai",
      "ramen",
      "fried rice",
    ]);
    expect(splitList("")).toEqual([]);
  });
});

describe("toInputs", () => {
  it("sends every key even when the person has nothing to add, so it still counts as shared", () => {
    expect(toInputs(EMPTY_INPUTS)).toEqual({
      constraints: { refuses: [], allergies: [], diets: [], budget: null },
      preferences: { likes: [], dislikes: [] },
    });
  });

  it("carries a name only when one was typed, so an empty box keeps the current name", () => {
    expect(toInputs({ ...EMPTY_INPUTS, name: "  Sam " }).display_name).toBe("Sam");
    expect("display_name" in toInputs(EMPTY_INPUTS)).toBe(false);
  });

  it("puts each box in its own list", () => {
    expect(
      toInputs({
        name: "",
        refuses: "seafood",
        allergies: "peanut, shellfish",
        diets: "halal",
        budget: " under $15 ",
        likes: "thai",
        dislikes: "burgers",
      }),
    ).toEqual({
      constraints: {
        refuses: ["seafood"],
        allergies: ["peanut", "shellfish"],
        diets: ["halal"],
        budget: "under $15",
      },
      preferences: { likes: ["thai"], dislikes: ["burgers"] },
    });
  });
});

describe("valuesFrom", () => {
  it("fills the boxes from what was shared and leaves the name empty", () => {
    const values = valuesFrom({
      name: "Guest 2",
      is_host: false,
      submitted: true,
      constraints: {
        refuses: ["seafood", "pork"],
        allergies: [],
        diets: ["halal"],
        budget: "RM20",
      },
      preferences: { likes: ["thai"], dislikes: [] },
    });
    expect(values).toEqual({
      name: "",
      refuses: "seafood, pork",
      allergies: "",
      diets: "halal",
      budget: "RM20",
      likes: "thai",
      dislikes: "",
    });
  });

  it("starts empty for someone who has not joined, or shared nothing yet", () => {
    expect(valuesFrom(null)).toEqual(EMPTY_INPUTS);
    expect(
      valuesFrom({
        name: "Alex",
        is_host: true,
        submitted: false,
        constraints: {},
        preferences: {},
      }),
    ).toEqual(EMPTY_INPUTS);
  });
});

describe("validateInputs", () => {
  it("accepts a form at the backend's limits", () => {
    const items = Array.from({ length: MAX_ITEMS }, (_, i) => `t${i}`).join(",");
    expect(
      validateInputs({
        ...EMPTY_INPUTS,
        name: "n".repeat(MAX_NAME_CHARS),
        refuses: items,
        allergies: "a".repeat(MAX_NOTE_CHARS),
        budget: "b".repeat(MAX_NOTE_CHARS),
        likes: "l".repeat(MAX_TERM_CHARS),
      }),
    ).toEqual({});
  });

  it("names the box that is over a limit", () => {
    const errors = validateInputs({
      ...EMPTY_INPUTS,
      name: "n".repeat(MAX_NAME_CHARS + 1),
      refuses: Array.from({ length: MAX_ITEMS + 1 }, (_, i) => `t${i}`).join(","),
      likes: "l".repeat(MAX_TERM_CHARS + 1),
      allergies: "a".repeat(MAX_NOTE_CHARS + 1),
      budget: "b".repeat(MAX_NOTE_CHARS + 1),
    });
    expect(Object.keys(errors).sort()).toEqual(["allergies", "budget", "likes", "name", "refuses"]);
    expect(errors.refuses).toMatch(/at most 20/);
  });
});
