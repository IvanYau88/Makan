/**
 * Words for OpenStreetMap facts. Hours arrive as minutes from midnight and are worded here, at
 * the display edge, on a US 12-hour clock.
 */
import type { OsmFacts, OsmHoursListed } from "./types";

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const DAY_MINUTES = 24 * 60;

/** 660 is "11 AM", 690 "11:30 AM", 720 "12 PM", and the end of a day "midnight". */
export function clockLabel(minutes: number, isEnd = false): string {
  if (isEnd && minutes % DAY_MINUTES === 0) return "midnight";
  const inDay = minutes % DAY_MINUTES;
  const hour = Math.floor(inDay / 60);
  const minute = inDay % 60;
  const suffix = hour < 12 ? "AM" : "PM";
  const twelve = hour % 12 === 0 ? 12 : hour % 12;
  return minute === 0
    ? `${twelve} ${suffix}`
    : `${twelve}:${String(minute).padStart(2, "0")} ${suffix}`;
}

/** One day's opening times, such as "11 AM–2 PM, 5 PM–10 PM", or "Closed". */
export function dayHoursLabel(ranges: [number, number][]): string {
  if (ranges.length === 0) return "Closed";
  const [only] = ranges;
  if (ranges.length === 1 && only && only[0] === 0 && only[1] >= DAY_MINUTES) {
    return "Open 24 hours";
  }
  return ranges.map(([start, end]) => `${clockLabel(start)}–${clockLabel(end, true)}`).join(", ");
}

export interface HoursRow {
  days: string;
  hours: string;
  /** Holds today, by this device's clock. */
  today: boolean;
}

/** The week as rows, with neighbouring days of the same hours joined: "Mon–Fri", "11 AM–10 PM". */
export function weekRows(hours: OsmHoursListed, today: number): HoursRow[] {
  const labels = hours.days.map(dayHoursLabel);
  if (labels.every((label) => label === labels[0])) {
    // One row for the whole week has nothing to single out for today.
    return [{ days: "Every day", hours: labels[0] ?? "Closed", today: false }];
  }
  const rows: HoursRow[] = [];
  let first = 0;
  for (let day = 1; day <= labels.length; day += 1) {
    if (day < labels.length && labels[day] === labels[first]) continue;
    const last = day - 1;
    rows.push({
      days: first === last ? dayName(first) : `${dayName(first)}–${dayName(last)}`,
      hours: labels[first] ?? "Closed",
      today: first <= today && today <= last,
    });
    first = day;
  }
  return rows;
}

function dayName(index: number): string {
  return DAY_NAMES[index] ?? "";
}

/** Monday is 0, the way the hours are listed. */
export function weekdayIndex(now: Date): number {
  return (now.getDay() + 6) % 7;
}

/**
 * "Today 11 AM–10 PM", "Open 24 hours today", "Closed today", or null when the hours are unknown
 * or unreadable.
 */
export function todayLabel(osm: OsmFacts | null | undefined, now: Date): string | null {
  const hours = osm?.hours;
  if (!hours || hours.status !== "listed") return null;
  const ranges = hours.days[weekdayIndex(now)] ?? [];
  if (ranges.length === 0) return "Closed today";
  const label = dayHoursLabel(ranges);
  return label === "Open 24 hours" ? "Open 24 hours today" : `Today ${label}`;
}

const SERVICE_WORDS: Record<string, Record<string, string>> = {
  takeaway: { yes: "Takeout", only: "Takeout only", no: "No takeout" },
  delivery: { yes: "Delivery", only: "Delivery only", no: "No delivery" },
  outdoor_seating: { yes: "Outdoor seating", no: "No outdoor seating" },
};

/** Takeout, delivery and outdoor seating in words. `positiveOnly` leaves out the "No" ones. */
export function serviceLabels(osm: OsmFacts, positiveOnly = false): string[] {
  const out: string[] = [];
  for (const key of ["takeaway", "delivery", "outdoor_seating"] as const) {
    const value = osm[key];
    if (!value || (positiveOnly && value === "no")) continue;
    const words = SERVICE_WORDS[key]?.[value];
    if (words) out.push(words);
  }
  return out;
}

/** "vegan only", "vegetarian options", "some gluten-free options", "no halal options". */
export function dietLabel(diet: OsmFacts["diets"][number]): string {
  const name = diet.label.toLowerCase();
  switch (diet.level) {
    case "only":
      return `${name} only`;
    case "yes":
      return `${name} options`;
    case "limited":
      return `some ${name} options`;
    case "no":
      return `no ${name} options`;
  }
}

const ACCESS_WORDS: Record<string, string> = {
  yes: "Wheelchair accessible",
  limited: "Limited wheelchair access",
  no: "Not wheelchair accessible",
};

export function wheelchairLabel(value: OsmFacts["wheelchair"]): string | null {
  return value ? (ACCESS_WORDS[value] ?? null) : null;
}

/** "2025-06" or "2025-06-14" as "June 2025" or "June 14, 2025". */
export function checkDateLabel(text: string | null): string | null {
  const found = text ? /^(\d{4})-(\d{2})(?:-(\d{2}))?$/.exec(text) : null;
  if (!found) return null;
  const [, year, month, day] = found;
  const date = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day ?? 1)));
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString("en-US", {
    timeZone: "UTC",
    year: "numeric",
    month: "long",
    ...(day ? { day: "numeric" } : {}),
  });
}

/** An ISO timestamp as "Nov 8, 2026". */
export function asOfLabel(timestamp: string): string | null {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString("en-US", {
    timeZone: "UTC",
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}
