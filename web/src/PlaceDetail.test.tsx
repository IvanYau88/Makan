import { render, screen } from "@testing-library/react";
import { PlaceDetail, webLink } from "./PlaceDetail";
import { place } from "./test-fixtures";

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
