import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PlaceDetail } from "./PlaceDetail";
import { emptyMessage, forgetMenus, readMenu } from "./menus";
import { place } from "./test-fixtures";
import type { MenuDish, MenuFacts, Place } from "./types";

const noop = () => {};
const REF = "v1.aHR0cHM6Ly9sZW1vbmdyYXNzLmV4YW1wbGUvbWVudQ.0123456789abcdef0123456789abcdef";

function facts(extra: Partial<MenuFacts> = {}): MenuFacts {
  return {
    status: "found",
    reason: null,
    dishes: [
      { name: "Pad Thai", price: { amount: "14.95", currency: "USD", label: "$14.95" } },
      { name: "Tom Kha Soup", price: null },
    ],
    truncated: false,
    source: {
      url: "https://lemongrass.example/menu",
      host: "lemongrass.example",
      method: "json_ld",
      retrieved_at: "2026-11-08T12:00:00Z",
      expires_at: "2999-01-01T00:00:00Z",
    },
    ...extra,
  };
}

function answering(...bodies: (MenuFacts | Error)[]) {
  const fetchMock = vi.fn();
  for (const body of bodies) {
    if (body instanceof Error) fetchMock.mockRejectedValueOnce(body);
    else fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(body), { status: 200 }));
  }
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const THAI: Place = place("Lemongrass Corner", 140, {
  website: "https://lemongrass.example/",
  menu_ref: REF,
});

function open(target: Place = THAI, extra: Partial<Parameters<typeof PlaceDetail>[0]> = {}) {
  return render(
    <PlaceDetail place={target} isPick={false} mode="places" onClose={noop} {...extra} />,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  forgetMenus();
});

describe("Menu facts on the place detail", () => {
  it("asks for nothing and shows nothing when the server gave no ref", () => {
    const fetchMock = answering();
    open(place("Plain Noodle", 200, { website: "https://plain.example/" }));
    open(place("Old Server", 200, { menu_ref: null }));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByText("From the restaurant's website")).not.toBeInTheDocument();
  });

  it("reads the menu once the detail opens, sending only the ref", async () => {
    const fetchMock = answering(facts());
    open();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Reading the menu on the restaurant's website…",
    );
    const list = await screen.findByRole("list", { name: "Dishes at Lemongrass Corner" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows.map((row) => row.textContent)).toEqual(["Pad Thai$14.95", "Tom Kha Soup"]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/places/menu");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ ref: REF });
    expect(init.headers).toEqual({ "Content-Type": "application/json" });
  });

  it("credits the page it read, with the date, and says dishes are not verified", async () => {
    answering(facts());
    open();
    const link = await screen.findByRole("link", {
      name: "lemongrass.example, the page Makan read (opens a new tab)",
    });
    expect(link).toHaveAttribute("href", "https://lemongrass.example/menu");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    const source = link.closest("p");
    expect(source).toHaveTextContent(/^Read from lemongrass\.example on Nov 8, 2026\./);
    expect(source).toHaveTextContent("none is checked for a diet or an allergy");
    expect(screen.getByText(/the website's dishes and prices can be out of date/)).toBeVisible();
  });

  it("waits while a map panel is collapsed, and reads when it is expanded", async () => {
    const fetchMock = answering(facts());
    const { rerender } = open(THAI, { expanded: false, onToggle: noop });
    expect(fetchMock).not.toHaveBeenCalled();
    rerender(
      <PlaceDetail
        place={THAI}
        isPick={false}
        mode="places"
        onClose={noop}
        expanded
        onToggle={noop}
      />,
    );
    expect(await screen.findByText("Pad Thai")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("opens the same place again from memory", async () => {
    const fetchMock = answering(facts());
    const first = open();
    await screen.findByText("Pad Thai");
    first.unmount();
    open();
    expect(screen.getByText("Pad Thai")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([
    [
      { status: "none", reason: "no_dishes" },
      "Makan read the restaurant's website and found no dish list",
    ],
    [{ status: "refused", reason: "robots" }, "asks automated readers like Makan not to read it"],
    [{ status: "refused", reason: "forbidden" }, "turned Makan's reader away"],
    [{ status: "unreadable", reason: "pdf" }, "The menu is a PDF, which Makan cannot read"],
    [{ status: "unavailable", reason: "timeout" }, "did not answer"],
    [{ status: "platform", reason: "third_party" }, "ordering, delivery or social platform"],
  ] as [Partial<MenuFacts>, string][])(
    "says honestly why there are no dishes: %o",
    async (extra, text) => {
      answering(facts({ ...extra, dishes: [], source: { ...facts().source, method: null } }));
      open();
      expect(await screen.findByText(new RegExp(text))).toBeInTheDocument();
      expect(screen.queryByRole("list", { name: /^Dishes/ })).not.toBeInTheDocument();
      expect(screen.getByText(/^Tried/)).toHaveTextContent(
        "Tried lemongrass.example on Nov 8, 2026.",
      );
      expect(screen.getByText(/price, menus, reviews/)).toBeInTheDocument(); // the usual caveat
    },
  );

  it("offers to try again when the server cannot be reached", async () => {
    const fetchMock = answering(new TypeError("Failed to fetch"), facts());
    open();
    await userEvent.click(await screen.findByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Pad Thai")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("shows eight dishes first and the rest on request", async () => {
    const dishes: MenuDish[] = Array.from({ length: 12 }, (_, i) => ({
      name: `Dish ${i + 1}`,
      price: null,
    }));
    answering(facts({ dishes, truncated: true }));
    open();
    const list = await screen.findByRole("list", { name: /^Dishes/ });
    expect(within(list).getAllByRole("listitem")).toHaveLength(8);
    const more = screen.getByRole("button", { name: "Show all 12 dishes" });
    await userEvent.click(more);
    expect(within(list).getAllByRole("listitem")).toHaveLength(12);
    expect(more).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("The page lists more dishes than Makan shows here.")).toBeVisible();
  });

  it("shows a list of ten or fewer in full, with no button", async () => {
    const dishes: MenuDish[] = Array.from({ length: 10 }, (_, i) => ({
      name: `Dish ${i + 1}`,
      price: null,
    }));
    answering(facts({ dishes }));
    open();
    const list = await screen.findByRole("list", { name: /^Dishes/ });
    expect(within(list).getAllByRole("listitem")).toHaveLength(10);
    expect(screen.queryByRole("button", { name: /^Show/ })).not.toBeInTheDocument();
  });

  it("renders a dish name as text, never as markup", async () => {
    answering(facts({ dishes: [{ name: '<img src=x onerror="alert(1)">', price: null }] }));
    const { container } = open();
    expect(await screen.findByText('<img src=x onerror="alert(1)">')).toBeInTheDocument();
    expect(container.querySelector(".menu img")).toBeNull();
  });
});

describe("readMenu", () => {
  it("keeps a well formed answer and drops malformed dishes", () => {
    const raw = { ...facts(), dishes: [...facts().dishes, { name: 3 }, null] };
    expect(readMenu(raw)?.dishes).toEqual(facts().dishes);
  });

  it("refuses an answer that is not the server's shape", () => {
    expect(readMenu(null)).toBeNull();
    expect(readMenu({ ...facts(), status: "maybe" })).toBeNull();
    expect(readMenu({ ...facts(), source: { host: "x" } })).toBeNull();
  });

  it("words an unknown reason with its status", () => {
    expect(emptyMessage(facts({ status: "unreadable", reason: null }))).toBe(
      "The menu page is not a kind Makan can read.",
    );
  });
});
