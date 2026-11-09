import { choiceOf, choose, groupedMatches, searchKey } from "./cuisineVocabulary";
import { VOCABULARY } from "./test-fixtures";

describe("groupedMatches", () => {
  it("lists every term in the server's group order, alphabetical within a group", () => {
    const groups = groupedMatches(VOCABULARY, "");
    expect(groups.map((g) => g.name)).toEqual(VOCABULARY.groups);
    expect(groups[0]?.terms.map((t) => t.label)).toEqual(["Chinese", "Japanese", "Ramen"]);
    expect(groups.flatMap((g) => g.terms)).toHaveLength(VOCABULARY.cuisines.length);
  });

  it("matches the start of any word of a label, id or alias, ignoring case and accents", () => {
    const labels = (query: string) =>
      groupedMatches(VOCABULARY, query).flatMap((g) => g.terms.map((t) => t.label));
    expect(labels("BBQ")).toEqual(["Barbecue"]);
    expect(labels("pizzer")).toEqual(["Pizza"]);
    expect(labels("  ThÁi ")).toEqual(["Thai"]);
    expect(labels("burg")).toEqual(["Burgers"]);
    expect(labels("men")).toEqual([]);
    expect(labels("noodles")).toEqual([]);
    expect(groupedMatches(VOCABULARY, "bbq").map((g) => g.name)).toEqual(["Dishes and more"]);
  });

  it("still shows a term filed under a group the server did not list", () => {
    const extra = {
      ...VOCABULARY,
      cuisines: [
        ...VOCABULARY.cuisines,
        { id: "poke", label: "Poke", group: "Pacific", aliases: [] },
      ],
    };
    expect(groupedMatches(extra, "poke")).toEqual([
      { name: "Pacific", terms: [{ id: "poke", label: "Poke", group: "Pacific", aliases: [] }] },
    ]);
  });
});

describe("choose", () => {
  it("moves a cuisine between like and skip and clears it on a second press", () => {
    let tastes = { likes: ["thai"], dislikes: [] as string[] };
    tastes = choose(tastes, "pizza", "skip");
    expect(tastes).toEqual({ likes: ["thai"], dislikes: ["pizza"] });
    tastes = choose(tastes, "pizza", "like");
    expect(tastes).toEqual({ likes: ["thai", "pizza"], dislikes: [] });
    expect(choiceOf(tastes, "pizza")).toBe("like");
    tastes = choose(tastes, "pizza", "like");
    expect(tastes).toEqual({ likes: ["thai"], dislikes: [] });
    expect(choiceOf(tastes, "pizza")).toBe("");
  });
});

it("reduces text to plain lowercase words for search", () => {
  expect(searchKey("Tex-Mex")).toBe("tex mex");
  expect(searchKey("Döner  Kebab!")).toBe("doner kebab");
  expect(searchKey("Hong Kong-style")).toBe("hong kong style");
});
