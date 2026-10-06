import { NO_FILTERS, categoryCounts, hasCoordinates, isFiltered, visiblePlaces } from "./places";
import { place } from "./test-fixtures";

const PLACES = [
  place("Alpha Thai", 300, { rank: 1 }),
  place("Bravo Ramen", 100, { rank: 2, category: "ramen_restaurant", matched: false }),
  place("Charlie Thai", 200, { rank: 3 }),
  place("Delta Cafe", 100, { rank: 4, category: "cafe", matched: false }),
];

const names = (list: { name: string }[]) => list.map((p) => p.name);

describe("visiblePlaces", () => {
  it("keeps rank order by default, whatever order the places arrive in", () => {
    expect(names(visiblePlaces([...PLACES].reverse(), NO_FILTERS))).toEqual([
      "Alpha Thai",
      "Bravo Ramen",
      "Charlie Thai",
      "Delta Cafe",
    ]);
  });

  it("sorts by distance and breaks ties by rank, leaving the ranks alone", () => {
    const shown = visiblePlaces(PLACES, { ...NO_FILTERS, sort: "distance" });
    expect(names(shown)).toEqual(["Bravo Ramen", "Delta Cafe", "Charlie Thai", "Alpha Thai"]);
    expect(shown.map((p) => p.rank)).toEqual([2, 4, 3, 1]);
  });

  it("sorts by name for browsing, breaking ties by rank", () => {
    const shown = visiblePlaces([...PLACES, place("Alpha Thai", 50, { rank: 5 })], {
      ...NO_FILTERS,
      sort: "name",
    });
    expect(names(shown)).toEqual([
      "Alpha Thai",
      "Alpha Thai",
      "Bravo Ramen",
      "Charlie Thai",
      "Delta Cafe",
    ]);
    expect(shown.map((p) => p.rank).slice(0, 2)).toEqual([1, 5]);
  });

  it("filters by category and by whether the request matched", () => {
    expect(names(visiblePlaces(PLACES, { ...NO_FILTERS, category: "cafe" }))).toEqual([
      "Delta Cafe",
    ]);
    expect(names(visiblePlaces(PLACES, { ...NO_FILTERS, matchedOnly: true }))).toEqual([
      "Alpha Thai",
      "Charlie Thai",
    ]);
    expect(visiblePlaces(PLACES, { ...NO_FILTERS, category: "cafe", matchedOnly: true })).toEqual(
      [],
    );
  });

  it("does not change the list it is given", () => {
    const copy = [...PLACES];
    visiblePlaces(PLACES, { ...NO_FILTERS, sort: "distance" });
    expect(PLACES).toEqual(copy);
  });
});

describe("filters", () => {
  it("know when they narrow the list", () => {
    expect(isFiltered(NO_FILTERS)).toBe(false);
    expect(isFiltered({ ...NO_FILTERS, sort: "distance" })).toBe(false);
    expect(isFiltered({ ...NO_FILTERS, category: "cafe" })).toBe(true);
    expect(isFiltered({ ...NO_FILTERS, matchedOnly: true })).toBe(true);
  });

  it("count each category, most common first", () => {
    expect(categoryCounts(PLACES)).toEqual([
      { category: "thai_restaurant", count: 2 },
      { category: "cafe", count: 1 },
      { category: "ramen_restaurant", count: 1 },
    ]);
  });

  it("only pin places that have coordinates", () => {
    expect(hasCoordinates(place("Pinned", 1))).toBe(true);
    expect(hasCoordinates(place("Lost", 1, { lat: null, lon: 1 }))).toBe(false);
    expect(hasCoordinates(place("Lost", 1, { lat: 1, lon: null }))).toBe(false);
  });
});
