import {
  asOfLabel,
  checkDateLabel,
  clockLabel,
  dayHoursLabel,
  dietLabel,
  serviceLabels,
  todayLabel,
  weekRows,
  weekdayIndex,
  wheelchairLabel,
} from "./osm";
import { osmFacts } from "./test-fixtures";
import type { OsmHoursListed } from "./types";

const WEEK: OsmHoursListed = {
  status: "listed",
  days: [
    [[660, 1320]],
    [[660, 1320]],
    [[660, 1320]],
    [[660, 1320]],
    [[660, 1320]],
    [
      [690, 840],
      [1020, 1560],
    ],
    [],
  ],
  always_open: false,
  holidays_differ: false,
};
// November 2026: the 9th is a Monday and the 15th a Sunday.
const MONDAY = new Date(2026, 10, 9, 12);
const SATURDAY = new Date(2026, 10, 14, 12);
const SUNDAY = new Date(2026, 10, 15, 12);

describe("OpenStreetMap wording", () => {
  it("words times on a 12-hour clock", () => {
    expect(clockLabel(0)).toBe("12 AM");
    expect(clockLabel(660)).toBe("11 AM");
    expect(clockLabel(690)).toBe("11:30 AM");
    expect(clockLabel(720)).toBe("12 PM");
    expect(clockLabel(1325)).toBe("10:05 PM");
    expect(clockLabel(1440, true)).toBe("midnight");
    expect(clockLabel(1560, true)).toBe("2 AM"); // past midnight
  });

  it("words a day", () => {
    expect(dayHoursLabel([])).toBe("Closed");
    expect(dayHoursLabel([[0, 1440]])).toBe("Open 24 hours");
    expect(
      dayHoursLabel([
        [690, 840],
        [1020, 1560],
      ]),
    ).toBe("11:30 AM–2 PM, 5 PM–2 AM");
    expect(dayHoursLabel([[1080, 1440]])).toBe("6 PM–midnight");
  });

  it("joins neighbouring days with the same hours and marks today", () => {
    expect(weekRows(WEEK, weekdayIndex(SATURDAY))).toEqual([
      { days: "Mon–Fri", hours: "11 AM–10 PM", today: false },
      { days: "Sat", hours: "11:30 AM–2 PM, 5 PM–2 AM", today: true },
      { days: "Sun", hours: "Closed", today: false },
    ]);
    const daily: OsmHoursListed = { ...WEEK, days: Array(7).fill([[0, 1440]]) };
    expect(weekRows(daily, 0)).toEqual([
      { days: "Every day", hours: "Open 24 hours", today: false },
    ]);
  });

  it("counts the week from Monday", () => {
    expect(weekdayIndex(MONDAY)).toBe(0);
    expect(weekdayIndex(SUNDAY)).toBe(6);
  });

  it("says today's hours only when they were read", () => {
    expect(todayLabel(osmFacts({ hours: WEEK }), MONDAY)).toBe("Today 11 AM–10 PM");
    expect(todayLabel(osmFacts({ hours: WEEK }), SUNDAY)).toBe("Closed today");
    const always: OsmHoursListed = { ...WEEK, days: Array(7).fill([[0, 1440]]), always_open: true };
    expect(todayLabel(osmFacts({ hours: always }), MONDAY)).toBe("Open 24 hours today");
    expect(
      todayLabel(osmFacts({ hours: { status: "unreadable", text: "sunrise-sunset" } }), MONDAY),
    ).toBeNull();
    expect(todayLabel(osmFacts(), MONDAY)).toBeNull();
    expect(todayLabel(null, MONDAY)).toBeNull();
  });

  it("words services, diets and access", () => {
    const osm = osmFacts({ takeaway: "only", delivery: "no", outdoor_seating: "yes" });
    expect(serviceLabels(osm)).toEqual(["Takeout only", "No delivery", "Outdoor seating"]);
    expect(serviceLabels(osm, true)).toEqual(["Takeout only", "Outdoor seating"]);
    expect(dietLabel({ id: "vegan", label: "Vegan", level: "only" })).toBe("vegan only");
    expect(dietLabel({ id: "vegan", label: "Vegan", level: "yes" })).toBe("vegan options");
    expect(dietLabel({ id: "gluten_free", label: "Gluten-free", level: "limited" })).toBe(
      "some gluten-free options",
    );
    expect(dietLabel({ id: "halal", label: "Halal", level: "no" })).toBe("no halal options");
    expect(wheelchairLabel("limited")).toBe("Limited wheelchair access");
    expect(wheelchairLabel(null)).toBeNull();
  });

  it("words dates", () => {
    expect(checkDateLabel("2025-06")).toBe("June 2025");
    expect(checkDateLabel("2025-06-14")).toBe("June 14, 2025");
    expect(checkDateLabel("June")).toBeNull();
    expect(checkDateLabel(null)).toBeNull();
    expect(asOfLabel("2026-11-08T09:30:00Z")).toBe("Nov 8, 2026");
    expect(asOfLabel("soon")).toBeNull();
  });
});
