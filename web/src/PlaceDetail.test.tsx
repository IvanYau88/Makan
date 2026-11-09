import { render, screen, within } from "@testing-library/react";
import { OsmDetail } from "./OsmDetail";
import { osmLine } from "./PlaceList";
import { PlaceDetail, webLink } from "./PlaceDetail";
import { osmFacts, place } from "./test-fixtures";
import type { OsmHoursListed } from "./types";

const noop = () => {};

describe("PlaceDetail", () => {
  it("links out to the venue's website and shows listed cuisines and soft diets", () => {
    const vegan = place("Green Bowl", 120, {
      category: "vegan_restaurant",
      cuisines: [],
      diets: [
        { id: "vegan", label: "Vegan" },
        { id: "gluten_free", label: "Gluten-free" },
      ],
      website: "https://www.greenbowl.example/menu",
    });
    render(<PlaceDetail place={vegan} isPick={false} mode="places" onClose={noop} />);
    const link = screen.getByRole("link", {
      name: "Website for Green Bowl, greenbowl.example (opens a new tab)",
    });
    expect(link).toHaveAttribute("href", "https://www.greenbowl.example/menu");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByText("Listed as vegan and gluten-free")).toBeInTheDocument();
    expect(screen.getByText(/That label is not verified/)).toBeInTheDocument();
    expect(screen.queryByText(/Listed cuisine/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^Directions to Green Bowl/ })).toBeInTheDocument();
  });

  it("names every listed cuisine, and shows no website or diet note when there is none", () => {
    const pizza = place("Corner Pizza", 300, {
      category: "pizza_restaurant",
      cuisines: [
        { id: "pizza", label: "Pizza" },
        { id: "italian", label: "Italian" },
      ],
    });
    render(<PlaceDetail place={pizza} isPick={false} mode="places" onClose={noop} />);
    expect(screen.getByText("Listed cuisines: Pizza, Italian")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^Website/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/Listed as/)).not.toBeInTheDocument();
  });

  it("keeps a sample venue free of links, even with a website", () => {
    const sample = place("Sample Thai", 140, { website: "https://sample.example" });
    render(<PlaceDetail place={sample} isPick={false} mode="demo" onClose={noop} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("drops a website without coordinates only when it is unsafe", () => {
    const unsafe = place("Odd Link", 140, { website: "javascript:alert(1)", lat: null, lon: null });
    const { unmount } = render(
      <PlaceDetail place={unsafe} isPick={false} mode="places" onClose={noop} />,
    );
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    unmount();
    const safe = place("Plain Link", 140, {
      website: "http://plain.example",
      lat: null,
      lon: null,
    });
    render(<PlaceDetail place={safe} isPick={false} mode="places" onClose={noop} />);
    expect(screen.getAllByRole("link")).toHaveLength(1);
  });
});

const HOURS: OsmHoursListed = {
  status: "listed",
  days: [
    [[660, 1320]],
    [[660, 1320]],
    [[660, 1320]],
    [[660, 1320]],
    [[660, 1320]],
    [[720, 1380]],
    [],
  ],
  always_open: false,
  holidays_differ: true,
};
const MAPPED = osmFacts({
  cuisines: [
    { id: "thai", label: "Thai" },
    { id: "noodle", label: "Noodle" },
  ],
  diets: [{ id: "vegan", label: "Vegan", level: "only" }],
  takeaway: "yes",
  delivery: "no",
  outdoor_seating: "yes",
  wheelchair: "limited",
  hours: HOURS,
  menu_url: "https://venue.example/menu",
  check_date: "2025-06",
});
const SATURDAY = new Date(2026, 10, 14, 12);

describe("OpenStreetMap facts", () => {
  it("shows the mapped week, services, cuisines and the credit", () => {
    render(<OsmDetail osm={MAPPED} placeName="Mid Thai" now={SATURDAY} />);
    const week = screen.getByRole("table", { name: "Opening hours for Mid Thai" });
    const rows = within(week).getAllByRole("row");
    expect(rows.map((row) => row.textContent)).toEqual([
      "Mon–Fri11 AM–10 PM",
      "Sat (today)12 PM–11 PM",
      "Sun" + "Closed",
    ]);
    expect(rows[1]).toHaveClass("osm-today");
    expect(screen.getByText("Holiday hours may differ.")).toBeInTheDocument();
    expect(screen.getByText("Takeout · No delivery · Outdoor seating")).toBeInTheDocument();
    expect(screen.getByText("Cuisines: Thai, Noodle")).toBeInTheDocument();
    expect(screen.getByText("Last checked by a mapper: June 2025.")).toBeInTheDocument();
    const credit = screen.getByRole("link", { name: /^© OpenStreetMap contributors/ });
    expect(credit).toHaveAttribute("href", "https://www.openstreetmap.org/copyright");
    expect(credit.closest("p")).toHaveTextContent(
      "© OpenStreetMap contributors, ODbL 1.0, data as of Nov 8, 2026.",
    );
  });

  it("keeps diet and access tags soft, with the not-verified note", () => {
    render(<OsmDetail osm={MAPPED} placeName="Mid Thai" now={SATURDAY} />);
    const note = screen.getByText(/not verified/).closest("p");
    expect(note).toHaveClass("diet-note");
    expect(note).toHaveTextContent("Mapped as vegan only.");
    expect(note).toHaveTextContent("Limited wheelchair access.");
    expect(note).toHaveTextContent("before relying on it for a diet, an allergy or access");
  });

  it("shows unreadable hours as the mapped text, never as closed", () => {
    const odd = osmFacts({
      hours: { status: "unreadable", text: "sunrise-sunset" },
      takeaway: "no",
    });
    render(<OsmDetail osm={odd} placeName="Odd" now={SATURDAY} />);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByText("sunrise-sunset")).toBeInTheDocument();
    expect(screen.getByText(/cannot read this format/)).toBeInTheDocument();
    expect(screen.queryByText(/Closed/)).not.toBeInTheDocument();
    expect(screen.queryByText(/not verified/)).not.toBeInTheDocument(); // no diet or access tag
  });

  it("says when hours are not mapped", () => {
    render(<OsmDetail osm={osmFacts({ takeaway: "yes" })} placeName="X" now={SATURDAY} />);
    expect(screen.getByText("Hours are not mapped.")).toBeInTheDocument();
  });

  it("links the menu and the element, and changes the caveat for mapped hours", () => {
    const mapped = place("Mid Thai", 140, { osm: MAPPED });
    render(<PlaceDetail place={mapped} isPick={false} mode="places" onClose={noop} />);
    expect(
      screen.getByRole("link", { name: "Menu for Mid Thai, venue.example (opens a new tab)" }),
    ).toHaveAttribute("href", "https://venue.example/menu");
    expect(screen.getByRole("link", { name: /^View Mid Thai on OpenStreetMap/ })).toHaveAttribute(
      "href",
      "https://www.openstreetmap.org/node/42",
    );
    expect(screen.getByText(/mapped hours can be out of date/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "From OpenStreetMap" })).toBeInTheDocument();
  });

  it("shows nothing from OpenStreetMap, and no credit, for a place without a match", () => {
    render(<PlaceDetail place={place("Plain", 140)} isPick={false} mode="places" onClose={noop} />);
    expect(screen.queryByText(/OpenStreetMap contributors/)).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "From OpenStreetMap" })).not.toBeInTheDocument();
    expect(screen.getByText(/opening hours, price, menus/)).toBeInTheDocument();
  });

  it("drops an unsafe menu link", () => {
    const unsafe = place("Odd", 140, { osm: osmFacts({ menu_url: "javascript:alert(1)" }) });
    render(<PlaceDetail place={unsafe} isPick={false} mode="places" onClose={noop} />);
    expect(screen.queryByRole("link", { name: /^Menu/ })).not.toBeInTheDocument();
  });

  it("gives a list row today's hours and what the venue offers", () => {
    expect(osmLine(place("A", 1, { osm: MAPPED }), SATURDAY)).toBe(
      "Today 12 PM–11 PM · Takeout · Outdoor seating",
    );
    const sunday = new Date(2026, 10, 15, 12);
    expect(osmLine(place("A", 1, { osm: MAPPED }), sunday)).toBe(
      "Closed today · Takeout · Outdoor seating",
    );
    expect(osmLine(place("B", 1, { osm: osmFacts({ delivery: "no" }) }), SATURDAY)).toBeNull();
    expect(osmLine(place("C", 1), SATURDAY)).toBeNull();
  });
});

describe("webLink", () => {
  it.each([
    ["https://venue.example/", "venue.example"],
    ["http://www.venue.example/a?b=1", "venue.example"],
  ])("keeps %s", (raw, host) => {
    expect(webLink(raw)?.host).toBe(host);
  });

  it.each([
    "javascript:alert(1)",
    "data:text/html,hi",
    "ftp://venue.example",
    "venue.example",
    "https://user:pass@venue.example",
    "",
    null,
    undefined,
  ])("rejects %s", (raw) => {
    expect(webLink(raw)).toBeNull();
  });
});
