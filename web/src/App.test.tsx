import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { onTestFinished } from "vitest";
import L from "leaflet";
import { App } from "./App";
import { RESULT, place, run, stage } from "./test-fixtures";
import type { Recommendation, Run } from "./types";

type Handler = (body: Record<string, unknown>) => Response | Promise<Response>;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

function lines(items: unknown[]): Response {
  return new Response(items.map((item) => JSON.stringify(item) + "\n").join(""), {
    status: 200,
    headers: { "Content-Type": "application/x-ndjson" },
  });
}

/** A streamed answer: the run snapshots as progress, then the result. */
function answer(recommendation: Recommendation, runs: Run[] = []): Response {
  return lines([...runs.map((r) => ({ type: "run", run: r })), { type: "result", recommendation }]);
}

function errorLine(code: string, message: string, status: number, failedRun?: Run): Response {
  return lines([
    { type: "error", status, error: { code, message }, ...(failedRun && { run: failedRun }) },
  ]);
}

/** Stub fetch: the config route answers with `mode`, and the stream route with `handler`. */
function stubApi(handler: Handler, mode: "demo" | "live" = "live") {
  const calls: Record<string, unknown>[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/config") {
        return json({
          mode,
          map: {
            tile_url: "https://tiles.test/{z}/{x}/{y}.png",
            attribution: "© Test Maps",
            attribution_url: "https://tiles.test/copyright",
          },
        });
      }
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      calls.push(body);
      return handler(body);
    }),
  );
  return calls;
}

function stubLocation(outcome: "ok" | "denied") {
  vi.stubGlobal("navigator", {
    geolocation: {
      getCurrentPosition: (ok: (p: unknown) => void, fail: (e: unknown) => void) =>
        outcome === "ok"
          ? ok({ coords: { latitude: 3.14812, longitude: 101.69534 } })
          : fail({ code: 1, PERMISSION_DENIED: 1, POSITION_UNAVAILABLE: 2, TIMEOUT: 3 }),
    },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const findFood = () => screen.getByRole("button", { name: "Find food here" });
const row = (name: RegExp | string) =>
  within(screen.getByRole("list", { name: "Nearby places" })).getByRole("button", { name });
const pin = (rank: number, name: string) =>
  screen.getByRole("button", { name: new RegExp(`^${rank}\\. ${name}, .*map pin$`) });

async function searched(handler: Handler = () => answer(RESULT), mode?: "demo" | "live") {
  const calls = stubApi(handler, mode);
  const user = userEvent.setup();
  render(<App />);
  await user.click(findFood());
  await screen.findByRole("heading", { name: "Nearby options" });
  return { calls, user };
}

describe("Discover", () => {
  it("searches around the map center and lists every option with its rank and match", async () => {
    const calls = stubApi(() => answer(RESULT));
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("What are you in the mood for?"), "thai please");
    await user.selectOptions(screen.getByLabelText("How far will you go?"), "2000");
    await user.click(findFood());

    expect(await screen.findByRole("heading", { name: "Nearby options" })).toHaveFocus();
    expect(calls).toEqual([
      { latitude: 3.148, longitude: 101.695, request: "thai please", radius_m: 2000 },
    ]);
    expect(
      screen.getByText(/3 options · within 1 km · straight-line distance/),
    ).toBeInTheDocument();

    const mid = row(/^1\. Mid Thai/);
    expect(within(mid).getByText("Top pick")).toBeInTheDocument();
    expect(within(mid).getByText("Matches your request")).toBeInTheDocument();
    expect(within(mid).getByText(/Thai restaurant · ~140 m/)).toBeInTheDocument();
    expect(within(row(/^3\. Near Ramen/)).getByText("Nearby alternative")).toBeInTheDocument();
    expect(screen.getByText(/Overture Maps Foundation/)).toBeInTheDocument();
    expect(screen.getByText(/Opening hours, menus, prices/)).toBeInTheDocument();
  });

  it("pins the same places with the same numbers, and says where the tiles come from", async () => {
    await searched();
    for (const [rank, name] of [
      [1, "Mid Thai"],
      [2, "Far Thai"],
      [3, "Near Ramen"],
    ] as const) {
      expect(pin(rank, name)).toHaveTextContent(String(rank));
    }
    const credit = screen.getByRole("link", { name: "© Test Maps" });
    expect(credit).toHaveAttribute("href", "https://tiles.test/copyright");
    expect(document.querySelectorAll(".map-radius").length).toBe(1);
  });

  it("selects from the list or a pin and opens the same detail either way", async () => {
    const { user } = await searched();
    expect(screen.queryByRole("heading", { name: "Mid Thai" })).not.toBeInTheDocument();

    await user.click(row(/^1\. Mid Thai/));
    const detail = screen.getByRole("heading", { name: "Mid Thai", level: 3 }).closest("section")!;
    expect(row(/^1\. Mid Thai/)).toHaveAttribute("aria-current", "true");
    expect(pin(1, "Mid Thai")).toHaveAttribute("aria-pressed", "true");
    expect(pin(1, "Mid Thai").className).toContain("pin-selected");
    expect(within(detail).getByText("matches your request for thai")).toBeInTheDocument();
    expect(
      within(detail).getByText(/not in this data, so none of them is verified/),
    ).toBeInTheDocument();
    expect(within(detail).getByText(/~140 m straight-line/)).toBeInTheDocument();

    await user.click(pin(2, "Far Thai"));
    expect(row(/^2\. Far Thai/)).toHaveAttribute("aria-current", "true");
    expect(row(/^1\. Mid Thai/)).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("heading", { name: "Far Thai", level: 3 })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Mid Thai", level: 3 })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("heading", { name: "Far Thai", level: 3 })).not.toBeInTheDocument();
    expect(row(/^2\. Far Thai/)).not.toHaveAttribute("aria-current");
  });

  it("closes the detail with Escape", async () => {
    const { user } = await searched();
    await user.click(row(/^1\. Mid Thai/));
    expect(screen.getByRole("heading", { name: "Mid Thai", level: 3 })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("heading", { name: "Mid Thai", level: 3 })).not.toBeInTheDocument();
  });

  it("returns focus to the invoking row when the detail is closed with Escape or Close", async () => {
    const { user } = await searched();
    const first = row(/^1\. Mid Thai/);
    await user.click(first);
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(first).toHaveFocus();

    await user.click(first);
    screen.getByRole("button", { name: "Close" }).focus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("heading", { name: "Mid Thai", level: 3 })).not.toBeInTheDocument();
    expect(first).toHaveFocus();
  });

  it("returns focus to the row of a place opened from its pin", async () => {
    const { user } = await searched();
    await user.click(pin(2, "Far Thai"));
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(row(/^2\. Far Thai/)).toHaveFocus();
  });

  it("previews a hovered or focused row on its pin without selecting it", async () => {
    const { user } = await searched();
    await user.hover(row(/^2\. Far Thai/));
    expect(pin(2, "Far Thai").className).toContain("pin-hover");
    expect(row(/^2\. Far Thai/)).not.toHaveAttribute("aria-current");
    await user.unhover(row(/^2\. Far Thai/));
    expect(pin(2, "Far Thai").className).not.toContain("pin-hover");
    await user.tab();
    await act(async () => row(/^1\. Mid Thai/).focus());
    expect(pin(1, "Mid Thai").className).toContain("pin-hover");
  });

  it("filters by category and by match, sorts by distance, and clears the filters", async () => {
    const { user } = await searched();
    await user.selectOptions(screen.getByLabelText("Category"), "ramen_restaurant");
    expect(screen.getByText(/1 of 3 shown/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^1\. Mid Thai/ })).not.toBeInTheDocument();
    expect(pin(3, "Near Ramen")).toBeInTheDocument();

    await user.click(screen.getByLabelText("Only places that match my request"));
    expect(screen.getByText("No options match these filters.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(
      within(screen.getByRole("list", { name: "Nearby places" })).getAllByRole("listitem"),
    ).toHaveLength(3);

    await user.selectOptions(screen.getByLabelText("Sort by"), "distance");
    const rows = within(screen.getByRole("list", { name: "Nearby places" })).getAllByRole("button");
    expect(rows[0]).toHaveAccessibleName(/^3\. Near Ramen/);
    // Ranks stay put when only the order changes.
    expect(pin(1, "Mid Thai")).toHaveTextContent("1");
  });

  it("drops the selection when a filter hides the selected place", async () => {
    const { user } = await searched();
    await user.click(row(/^1\. Mid Thai/));
    await user.selectOptions(screen.getByLabelText("Category"), "ramen_restaurant");
    expect(screen.queryByRole("heading", { name: "Mid Thai", level: 3 })).not.toBeInTheDocument();
  });

  it("has no request filter when the request named no cuisine or venue type", async () => {
    await searched(() => answer({ ...RESULT, intent: { cuisine: null, category: null } }));
    expect(screen.queryByLabelText("Only places that match my request")).not.toBeInTheDocument();
  });

  it("marks a changed radius as not yet applied and applies it on the next search", async () => {
    const { calls, user } = await searched((body) =>
      answer({ ...RESULT, query: { ...RESULT.query, radius_m: Number(body.radius_m) } }),
    );
    await user.selectOptions(screen.getByLabelText("How far will you go?"), "500");
    expect(screen.getByText(/The radius changed/)).toBeInTheDocument();
    expect(document.querySelectorAll(".map-radius-pending").length).toBe(1);
    await user.click(findFood());
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[1]?.radius_m).toBe(500);
    await waitFor(() => expect(screen.queryByText(/The radius changed/)).not.toBeInTheDocument());
  });

  it("offers a search of the new area after the map is moved, using the rounded center", async () => {
    const { calls, user } = await searched();
    expect(screen.queryByRole("button", { name: "Search this area" })).not.toBeInTheDocument();
    const map = capturedMap();
    await act(async () => map.setView([3.2, 101.8], 15, { animate: false }));
    await user.click(await screen.findByRole("button", { name: "Search this area" }));
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[1]).toMatchObject({ latitude: 3.2, longitude: 101.8, radius_m: 1000 });
  });

  it("keeps the previous results visible and marked while a new search runs", async () => {
    let finish: (r: Response) => void = () => undefined;
    let second = false;
    const { user } = await searched(() => {
      if (second) return new Promise<Response>((resolve) => (finish = resolve));
      second = true;
      return answer(RESULT);
    });
    second = true;
    await user.click(findFood());

    expect(await screen.findByRole("button", { name: /Finding food/ })).toBeDisabled();
    expect(row(/^1\. Mid Thai/)).toBeInTheDocument();
    expect(document.querySelector('[data-stale="true"]')).not.toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent("Starting the search…");

    finish(answer({ ...RESULT, pick: null, places: [place("Only Place", 50, { rank: 1 })] }));
    expect(await screen.findByRole("button", { name: /^1\. Only Place/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^1\. Mid Thai/ })).not.toBeInTheDocument();
    expect(document.querySelector('[data-stale="true"]')).toBeNull();
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("announces the stage the run is on, from the real stage statuses", async () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({ start: (c) => (controller = c) });
    stubApi(() => new Response(body, { status: 200 }));
    const user = userEvent.setup();
    render(<App />);
    await user.click(findFood());

    const running = run({ status: "running", outcome: null, duration_ms: null, ended_at: null }, [
      stage("classify"),
      stage("intent"),
      stage("requested_places", { status: "running", duration_ms: null }),
      stage("nearby_places", { status: "running", duration_ms: null }),
      stage("memory", { status: "running", duration_ms: null }),
      stage("merge", { status: "waiting", duration_ms: null, started_ms: null }),
      stage("rank", { status: "waiting", duration_ms: null, started_ms: null }),
      stage("explain", { status: "waiting", duration_ms: null, started_ms: null }),
    ]);
    await act(async () => {
      controller.enqueue(encoder.encode(JSON.stringify({ type: "run", run: running }) + "\n"));
    });
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        "Searching nearby places (2 of 8 stages done)",
      ),
    );
    await act(async () => {
      controller.enqueue(
        encoder.encode(JSON.stringify({ type: "result", recommendation: RESULT }) + "\n"),
      );
      controller.close();
    });
    await screen.findByRole("heading", { name: "Nearby options" });
  });

  it("ignores a slow answer that a newer search replaced, and marks that run as no longer watched", async () => {
    let n = 0;
    let slow!: ReadableStreamDefaultController<Uint8Array>;
    const encoder = new TextEncoder();
    const calls = stubApi(() => {
      n += 1;
      if (n === 1) return answer(RESULT);
      if (n === 2) {
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            slow = controller;
            const started = run({ status: "running", outcome: null, run_id: "run-2" }, [
              stage("classify"),
            ]);
            controller.enqueue(
              encoder.encode(JSON.stringify({ type: "run", run: started }) + "\n"),
            );
          },
        });
        return new Response(body, { status: 200 });
      }
      return answer({
        ...RESULT,
        query: { ...RESULT.query, latitude: 3.3, longitude: 101.9 },
        places: [place("New Answer", 20, { rank: 1 })],
        run: run({ run_id: "run-3", request: "third" }),
      });
    });
    const user = userEvent.setup();
    render(<App />);
    await user.click(findFood());
    await screen.findByRole("heading", { name: "Nearby options" });

    const map = capturedMap();
    await act(async () => map.setView([3.2, 101.8], 15, { animate: false }));
    await user.click(await screen.findByRole("button", { name: "Search this area" }));
    await waitFor(() => expect(calls).toHaveLength(2));
    await act(async () => map.setView([3.3, 101.9], 15, { animate: false }));
    await user.click(await screen.findByRole("button", { name: "Search this area" }));
    await waitFor(() => expect(row(/^1\. New Answer/)).toBeInTheDocument());

    // The replaced search now answers late, and must not overwrite the newer results.
    const late = answer({ ...RESULT, places: [place("Old Answer", 10, { rank: 1 })] });
    await act(async () => {
      slow.enqueue(encoder.encode(await late.text()));
      slow.close();
    });
    expect(screen.queryByRole("button", { name: /Old Answer/ })).not.toBeInTheDocument();
    expect(row(/^1\. New Answer/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Execution" }));
    expect(screen.getByText(/Stopped watching/)).toBeInTheDocument();
  });

  it("explains a denied location and falls back to typed coordinates", async () => {
    const calls = stubApi(() => answer(RESULT));
    stubLocation("denied");
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Use my location" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Location access was denied");
    expect(calls).toHaveLength(0);
    const latitude = screen.getByLabelText("Latitude");
    expect(latitude).toHaveFocus();

    await user.type(latitude, "3.148");
    await user.type(screen.getByLabelText("Longitude"), "101.695");
    await user.click(findFood());

    await screen.findByRole("heading", { name: "Nearby options" });
    expect(calls).toEqual([
      { latitude: 3.148, longitude: 101.695, request: "something good to eat", radius_m: 1000 },
    ]);
  });

  it("uses the browser location, rounded to about 100 m, and searches there", async () => {
    const calls = stubApi(() => answer(RESULT));
    stubLocation("ok");
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Use my location" }));
    await screen.findByRole("heading", { name: "Nearby options" });
    expect(calls).toEqual([
      { latitude: 3.148, longitude: 101.695, request: "something good to eat", radius_m: 1000 },
    ]);
  });

  it("opens the coordinate fields on request and goes back to the map center", async () => {
    stubApi(() => answer(RESULT));
    const user = userEvent.setup();
    render(<App />);
    const toggle = screen.getByRole("button", { name: "Enter coordinates" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);
    expect(screen.getByLabelText("Latitude")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Use the map center instead" }));
    expect(screen.queryByLabelText("Latitude")).not.toBeInTheDocument();
  });

  it("flags bad typed coordinates without calling the server", async () => {
    const calls = stubApi(() => answer(RESULT));
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Enter coordinates" }));
    await user.type(screen.getByLabelText("Latitude"), "95");
    await user.click(findFood());

    const latitude = screen.getByLabelText("Latitude");
    expect(latitude).toBeInvalid();
    expect(latitude).toHaveAccessibleDescription("Must be between -90 and 90.");
    expect(screen.getByLabelText("Longitude")).toHaveAccessibleDescription("Enter a number.");
    expect(calls).toHaveLength(0);
  });

  it("says nothing was found and offers a wider search", async () => {
    const calls = stubApi(() => answer({ ...RESULT, pick: null, runners_up: [], places: [] }));
    const user = userEvent.setup();
    render(<App />);
    await user.click(findFood());

    expect(await screen.findByRole("heading", { name: "No places found" })).toBeInTheDocument();
    expect(screen.getByText(/nothing to eat within 1 km/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Search a wider area" }));
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[1]?.radius_m).toBe(2000);
    expect(screen.getByLabelText("How far will you go?")).toHaveValue("2000");
  });

  it("offers no wider search at the largest radius", async () => {
    stubApi(() =>
      answer({
        ...RESULT,
        pick: null,
        runners_up: [],
        places: [],
        query: { ...RESULT.query, radius_m: 5000 },
      }),
    );
    const user = userEvent.setup();
    render(<App />);
    await user.selectOptions(screen.getByLabelText("How far will you go?"), "5000");
    await user.click(findFood());
    await screen.findByRole("heading", { name: "No places found" });
    expect(screen.queryByRole("button", { name: "Search a wider area" })).not.toBeInTheDocument();
  });

  it("says when the list is capped, and calls it bounded options, not every restaurant", async () => {
    await searched(() => answer({ ...RESULT, truncated: true }));
    expect(screen.getByText(/capped at 20 places, so more may be nearby/)).toBeInTheDocument();
    expect(screen.queryByText(/all restaurants/i)).not.toBeInTheDocument();
  });

  it("shows partial-result and stale-memory notes", async () => {
    await searched(() =>
      answer({
        ...RESULT,
        partial: true,
        warnings: ["Part of the search failed, so these results may be incomplete."],
        stale_facts: [
          { id: "f1", kind: "cuisine_like", content: "thai", reason: "expired" },
          { id: "f2", kind: "place_rating", content: { place_id: "x", rating: 5 }, reason: null },
        ],
      }),
    );
    expect(screen.getByText(/list may be missing places/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Check your saved tastes" })).toBeInTheDocument();
    expect(screen.getByText("cuisine like: thai")).toBeInTheDocument();
    expect(screen.getByText(/place rating: \{"place_id":"x","rating":5\}/)).toBeInTheDocument();
  });

  it("offers directions only for real venues with coordinates", async () => {
    const { user } = await searched();
    await user.click(row(/^1\. Mid Thai/));
    const directions = screen.getByRole("link", { name: /Directions to Mid Thai/ });
    expect(directions).toHaveAttribute(
      "href",
      expect.stringContaining("destination=3.14814,101.695"),
    );
    expect(directions).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByRole("link", { name: /on OpenStreetMap/ })).toHaveAttribute(
      "href",
      expect.stringContaining("mlat=3.14814"),
    );
  });

  it("says demo venues are samples and offers no directions to them", async () => {
    const { user } = await searched(() => answer({ ...RESULT, mode: "demo" }), "demo");
    expect(await screen.findByRole("note")).toHaveTextContent("Demo mode");
    await user.click(row(/^1\. Mid Thai/));
    expect(screen.getByText("Sample venue for the demo, not a real place.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Directions/ })).not.toBeInTheDocument();
  });

  it("lists places that have no coordinates but pins none of them", async () => {
    await searched(() =>
      answer({ ...RESULT, places: [place("No Coords", 100, { lat: null, lon: null })] }),
    );
    expect(row(/^1\. No Coords/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /map pin$/ })).not.toBeInTheDocument();
  });
});

describe("Discover on a phone", () => {
  function stubNarrow() {
    vi.stubGlobal("matchMedia", () => ({
      matches: false,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    }));
  }

  it("switches between the map and the list, and keeps both in step", async () => {
    stubNarrow();
    const { user } = await searched();
    const toggle = screen.getByRole("group", { name: "Show the map or the list" });
    expect(within(toggle).getByRole("button", { name: "Map" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await user.click(within(toggle).getByRole("button", { name: "List" }));
    expect(within(toggle).getByRole("button", { name: "List" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(document.querySelector(".discover")).toHaveAttribute("data-view", "list");

    // In the list view the detail opens inside the selected row, with no map beside it.
    await user.click(row(/^1\. Mid Thai/));
    const inside = row(/^1\. Mid Thai/).closest("li")!;
    expect(within(inside).getByRole("heading", { name: "Mid Thai", level: 3 })).toBeInTheDocument();
    expect(document.querySelector(".map-detail")).toBeNull();
    expect(
      within(inside).queryByRole("button", { name: /Show (more|less)/ }),
    ).not.toBeInTheDocument();
    await user.click(within(inside).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("heading", { name: "Mid Thai", level: 3 })).not.toBeInTheDocument();
  });

  it("opens a collapsed place sheet over the map that expands and closes with buttons", async () => {
    stubNarrow();
    const { user } = await searched();
    await user.click(pin(2, "Far Thai"));
    const sheet = screen.getByRole("heading", { name: "Far Thai", level: 3 }).closest("section")!;
    expect(sheet.closest(".map-detail")).not.toBeNull();
    expect(within(sheet).queryByText("Why this option")).not.toBeInTheDocument();
    const more = within(sheet).getByRole("button", { name: "Show more" });
    expect(more).toHaveAttribute("aria-expanded", "false");
    await user.click(more);
    expect(within(sheet).getByText("Why this option")).toBeInTheDocument();
    await user.click(within(sheet).getByRole("button", { name: "Show less" }));
    expect(within(sheet).queryByText("Why this option")).not.toBeInTheDocument();
    await user.click(within(sheet).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("heading", { name: "Far Thai", level: 3 })).not.toBeInTheDocument();
  });

  it("waits to fit a hidden map until it has a size, then fits the searched circle", async () => {
    let resized: () => void = () => undefined;
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(callback: () => void) {
          resized = callback;
        }
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
    let side = 0; // the list is showing, so the map's element has no size
    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(() => side);
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockImplementation(() => side);
    stubNarrow();
    await searched();
    const map = capturedMap();
    expect(map.getZoom()).toBe(15);

    side = 400;
    await act(async () => resized());
    expect(map.getZoom()).toBeLessThan(15); // a 1 km radius no longer fits at the starting zoom
    expect(map.getBounds().contains(L.latLng(3.148, 101.695).toBounds(2000))).toBe(true);
  });

  it("brings a place sheet opened from a pin into view", async () => {
    stubNarrow();
    const scrolled = vi.fn();
    // jsdom has no scrollIntoView, so put one on the prototype for this test only.
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      value: function (this: Element) {
        if (this.classList.contains("map-detail")) scrolled();
      },
    });
    onTestFinished(() => {
      Reflect.deleteProperty(Element.prototype, "scrollIntoView");
    });
    const { user } = await searched();
    expect(scrolled).not.toHaveBeenCalled();
    await user.click(pin(2, "Far Thai"));
    expect(scrolled).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "Show more" }));
    expect(scrolled).toHaveBeenCalledTimes(2);
  });

  describe("search outcomes stay out of the switched panes", () => {
    const inResultsPane = (el: HTMLElement) => el.closest(".results-pane") !== null;

    it("shows a failure and Try again while the map is selected, and focuses the failure", async () => {
      stubNarrow();
      stubApi(() => errorLine("model_busy", "The language model is busy.", 503));
      const user = userEvent.setup();
      render(<App />);
      await user.click(findFood());
      const alert = await screen.findByRole("alert");
      expect(document.querySelector(".discover")).toHaveAttribute("data-view", "map");
      expect(inResultsPane(alert)).toBe(false);
      expect(within(alert).getByRole("heading", { name: "The model is busy" })).toHaveFocus();
      expect(within(alert).getByRole("button", { name: "Try again" })).toBeInTheDocument();
    });

    it("focuses the failure, not the old result heading, when a search fails after a result", async () => {
      stubNarrow();
      let n = 0;
      const { user } = await searched(() => {
        n += 1;
        return n === 1 ? answer(RESULT) : errorLine("server_error", "It broke.", 500);
      });
      await user.click(findFood());
      const alert = await screen.findByRole("alert");
      expect(within(alert).getByRole("heading", { name: "That did not work" })).toHaveFocus();
    });

    it("shows the empty result and its wider search while the map is selected", async () => {
      stubNarrow();
      const calls = stubApi(() => answer({ ...RESULT, places: [], pick: null }));
      const user = userEvent.setup();
      render(<App />);
      await user.click(findFood());
      const heading = await screen.findByRole("heading", { name: "No places found" });
      expect(heading).toHaveFocus();
      expect(inResultsPane(heading)).toBe(false);
      const wider = screen.getByRole("button", { name: "Search a wider area" });
      expect(inResultsPane(wider)).toBe(false);
      await user.click(wider);
      await waitFor(() => expect(calls).toHaveLength(2));
    });

    it("shows the result heading, the partial warning and progress while the map is selected", async () => {
      stubNarrow();
      let release: () => void = () => undefined;
      const gate = new Promise<void>((resolve) => (release = resolve));
      stubApi(async () => {
        await gate;
        return answer({ ...RESULT, partial: true });
      });
      const user = userEvent.setup();
      render(<App />);
      await user.click(findFood());
      const progress = await waitFor(() => {
        const el = document.querySelector<HTMLElement>(".progress");
        expect(el).not.toBeNull();
        return el!;
      });
      expect(inResultsPane(progress)).toBe(false);
      expect(screen.getByRole("status")).toHaveTextContent(/Starting the search/);
      release();
      const heading = await screen.findByRole("heading", { name: "Nearby options" });
      expect(heading).toHaveFocus();
      expect(inResultsPane(heading)).toBe(false);
      expect(inResultsPane(screen.getByText(/list may be missing places/))).toBe(false);
    });
  });

  it("does not crash when a search finishes while the map is hidden by the list view", async () => {
    stubNarrow();
    const { user } = await searched();
    await user.click(screen.getByRole("button", { name: "List" }));
    await user.click(findFood());
    expect(await screen.findByRole("heading", { name: "Nearby options" })).toBeInTheDocument();
  });
});

describe("Pins follow the latest search", () => {
  it("renumbers a pin for the same venue when a new search ranks it differently", async () => {
    let n = 0;
    const swapped = [
      place("Far Thai", 760, { rank: 1 }),
      place("Mid Thai", 140, { rank: 2 }),
      place("Near Ramen", 90, { rank: 3, category: "ramen_restaurant", matched: false }),
    ];
    const { user } = await searched(() => {
      n += 1;
      return n === 1 ? answer({ ...RESULT, places: swapped }) : answer(RESULT);
    });
    expect(pin(1, "Far Thai")).toBeInTheDocument();
    await user.click(findFood());
    await waitFor(() => expect(pin(1, "Mid Thai")).toBeInTheDocument());
    expect(pin(2, "Far Thai")).toBeInTheDocument();
  });

  it("does not style pins by match when the request named no cuisine or venue type", async () => {
    await searched(() => answer({ ...RESULT, intent: { cuisine: null, category: null } }));
    expect(pin(3, "Near Ramen").className).toContain("pin-matched");
    expect(
      within(row(/^3\. Near Ramen/)).queryByText("Nearby alternative"),
    ).not.toBeInTheDocument();
  });
});

describe("Discover failures", () => {
  it("shows the server's message on an error and retries the same place", async () => {
    let attempts = 0;
    const calls = stubApi(() => {
      attempts += 1;
      return attempts === 1
        ? errorLine("places_error", "Nearby places data is unavailable.", 502)
        : answer(RESULT);
    });
    const user = userEvent.setup();
    render(<App />);
    await user.click(findFood());

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Nearby places data is unavailable.");
    expect(within(alert).getByRole("heading", { name: "That did not work" })).toHaveFocus();
    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "Nearby options" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(calls).toHaveLength(2);
  });

  it("says the model is busy when the backend reports a rate limit", async () => {
    stubApi(() =>
      errorLine("model_busy", "The language model is busy right now. Try again in a minute.", 503),
    );
    render(<App />);
    await userEvent.click(findFood());
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByRole("heading", { name: "The model is busy" })).toBeInTheDocument();
    expect(alert).toHaveTextContent("The language model is busy right now. Try again in a minute.");
  });

  it("keeps the generic heading for other failures, even a 503 with no model_busy code", async () => {
    stubApi(() => errorLine("provider_error", "The language model failed.", 503));
    render(<App />);
    await userEvent.click(findFood());
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByRole("heading", { name: "That did not work" })).toBeInTheDocument();
  });

  it("keeps the old results on screen and says they are old when a new search fails", async () => {
    let second = false;
    const { user } = await searched(() => {
      if (!second) {
        second = true;
        return answer(RESULT);
      }
      return errorLine("provider_error", "The language model failed.", 502);
    });
    await user.click(findFood());
    expect(await screen.findByRole("alert")).toHaveTextContent("previous search");
    expect(row(/^1\. Mid Thai/)).toBeInTheDocument();
  });

  it("rejects an invalid request with the server's words", async () => {
    stubApi(() =>
      json({ error: { code: "invalid_request", message: "Check these fields: radius_m." } }, 422),
    );
    render(<App />);
    await userEvent.click(findFood());
    expect(await screen.findByRole("alert")).toHaveTextContent("Check these fields: radius_m.");
  });

  it("falls back to a generic message when the error is not JSON", async () => {
    stubApi(() => new Response("<html>Bad gateway</html>", { status: 502 }));
    render(<App />);
    await userEvent.click(findFood());
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Something went wrong on our side. Try again.",
    );
  });

  it("says so when the server cannot be reached", async () => {
    stubApi(() => {
      throw new TypeError("Failed to fetch");
    });
    render(<App />);
    await userEvent.click(findFood());
    expect(await screen.findByRole("alert")).toHaveTextContent("Can't reach Makan");
  });

  it("says so when the stream ends without an answer", async () => {
    stubApi(() => lines([{ type: "run", run: run({ status: "running", outcome: null }) }]));
    render(<App />);
    await userEvent.click(findFood());
    expect(await screen.findByRole("alert")).toHaveTextContent("Can't reach Makan");
  });

  it("flags demo mode, and only demo mode", async () => {
    stubApi(() => answer(RESULT), "demo");
    const { unmount } = render(<App />);
    expect(await screen.findByRole("note")).toHaveTextContent("Demo mode");
    unmount();

    stubApi(() => answer(RESULT), "live");
    render(<App />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });

  it("works without configuration: the list and search still run, with no tile credit", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/api/config") throw new TypeError("offline");
        return answer(RESULT);
      }),
    );
    render(<App />);
    await userEvent.click(findFood());
    expect(await screen.findByRole("heading", { name: "Nearby options" })).toBeInTheDocument();
    expect(screen.getByText(/Map tiles:/)).toHaveTextContent("not loaded");
  });

  it("keeps the list working and offers a retry when the map cannot start", async () => {
    stubApi(() => answer(RESULT));
    const spy = vi.spyOn(L, "map").mockImplementationOnce(() => {
      throw new Error("no canvas");
    });
    const user = userEvent.setup();
    render(<App />);
    const alert = await screen.findByText("The map could not start.");
    expect(alert.closest('[role="alert"]')).toHaveTextContent("The list of places still works.");
    await user.click(findFood());
    expect(await screen.findByRole("button", { name: /^1\. Mid Thai/ })).toBeInTheDocument();

    spy.mockImplementation(trackedMap);
    await user.click(screen.getByRole("button", { name: "Retry the map" }));
    await waitFor(() =>
      expect(screen.queryByText("The map could not start.")).not.toBeInTheDocument(),
    );
    expect(document.querySelector(".leaflet-container")).not.toBeNull();
    expect(pin(1, "Mid Thai")).toBeInTheDocument();
  });

  it("gives the map notice its own space, so an open place never covers Retry", async () => {
    stubApi(() => answer(RESULT));
    vi.spyOn(L, "map").mockImplementationOnce(() => {
      throw new Error("no canvas");
    });
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("The map could not start.");
    await user.click(findFood());
    await user.click(await screen.findByRole("button", { name: /^1\. Mid Thai/ }));
    const notice = document.querySelector(".map-fallback")!;
    const detail = document.querySelector(".map-detail")!;
    expect(detail).not.toBeNull();
    // The sheet is positioned inside the stage under the notice, never over it.
    expect(notice.closest(".map-stage")).toBeNull();
    expect(detail.closest(".map-stage")).not.toBeNull();
    expect(
      within(notice as HTMLElement).getByRole("button", { name: "Retry the map" }),
    ).toBeEnabled();
  });
});

describe("Execution", () => {
  const openExecution = (user: ReturnType<typeof userEvent.setup>) =>
    user.click(screen.getByRole("button", { name: "Execution" }));

  it("shows the real graph before any run, with no invented results", async () => {
    stubApi(() => answer(RESULT));
    const user = userEvent.setup();
    render(<App />);
    await openExecution(user);
    expect(
      screen.getByRole("heading", { name: "The graph Makan actually runs" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/No run to show yet/)).toBeInTheDocument();
    expect(screen.getByText(/No runs yet/)).toBeInTheDocument();
    const stages = within(screen.getByRole("list", { name: "Stages" })).getAllByRole("button");
    expect(stages.map((b) => b.querySelector(".stage-name")?.textContent)).toEqual([
      "Classify",
      "Intent",
      "Requested places",
      "Nearby places",
      "Memory",
      "Merge",
      "Rank",
      "Explain",
    ]);
  });

  it("lists a finished run and inspects a stage's input and output", async () => {
    const user = userEvent.setup();
    stubApi(() => answer(RESULT, [run({ status: "running", outcome: null })]));
    render(<App />);
    await user.click(findFood());
    await screen.findByRole("heading", { name: "Nearby options" });
    await openExecution(user);

    const summary = screen.getByRole("region", { name: "Run summary" });
    expect(within(summary).getByText("thai please · 1 km")).toBeInTheDocument();
    expect(within(summary).getByText("3.148, 101.695")).toBeInTheDocument();
    expect(within(summary).getByText("Complete recommendation")).toBeInTheDocument();
    expect(within(summary).getByText("Live")).toBeInTheDocument();
    expect(within(summary).getByText("overture:2026-09-23.1")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^Merge/ }));
    const detail = screen.getByRole("region", { name: "Merge" });
    expect(within(detail).getByText(/Starts after/)).toBeInTheDocument();
    expect(detail).toHaveTextContent("Intent, Requested places, Nearby places, Memory");
    expect(detail).toHaveTextContent('"note": "merge input"');
    expect(detail).toHaveTextContent('"note": "merge output"');
  });

  it("keeps a failed graph and a usable recommendation as separate facts", async () => {
    const partial = run({ status: "failed", graph_ok: false, outcome: "partial" }, [
      stage("classify"),
      stage("intent"),
      stage("requested_places", {
        status: "error",
        output: null,
        error: { type: "PlacesError", message: "Nearby places data was unavailable." },
      }),
      stage("nearby_places"),
      stage("memory"),
      stage("merge"),
      stage("rank"),
      stage("explain"),
    ]);
    const user = userEvent.setup();
    stubApi(() => answer({ ...RESULT, partial: true, run: partial }));
    render(<App />);
    await user.click(findFood());
    await screen.findByRole("heading", { name: "Nearby options" });
    await openExecution(user);

    const summary = screen.getByRole("region", { name: "Run summary" });
    expect(within(summary).getByText("Failed")).toBeInTheDocument();
    expect(within(summary).getByText("Partial recommendation")).toBeInTheDocument();
    expect(summary).toHaveTextContent("Requested places did not finish");
    expect(summary).toHaveTextContent("still usable");

    await user.click(screen.getByRole("button", { name: /^Requested places/ }));
    expect(screen.getByRole("region", { name: "Requested places" })).toHaveTextContent(
      "PlacesError: Nearby places data was unavailable.",
    );
    expect(screen.getByRole("button", { name: /^Merge/ })).toHaveTextContent("ok");
  });

  it("says waiting and queued stages are not running, rather than drawing a percentage", async () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({ start: (c) => (controller = c) });
    stubApi(() => new Response(body, { status: 200 }));
    const user = userEvent.setup();
    render(<App />);
    await user.click(findFood());
    const live = run({ status: "running", outcome: null, duration_ms: null, ended_at: null }, [
      stage("classify", { status: "running", duration_ms: null }),
      stage("intent", { status: "waiting", duration_ms: null, started_ms: null }),
      stage("requested_places", { status: "waiting", duration_ms: null, started_ms: null }),
      stage("nearby_places", { status: "waiting", duration_ms: null, started_ms: null }),
      stage("memory", { status: "queued", duration_ms: null, started_ms: null }),
      stage("merge", { status: "waiting", duration_ms: null, started_ms: null }),
      stage("rank", { status: "waiting", duration_ms: null, started_ms: null }),
      stage("explain", { status: "waiting", duration_ms: null, started_ms: null }),
    ]);
    await act(async () => {
      controller.enqueue(
        new TextEncoder().encode(JSON.stringify({ type: "run", run: live }) + "\n"),
      );
    });
    await openExecution(user);
    expect(
      (await screen.findAllByText(/Reading your request \(0 of 8 stages done\)/)).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^Intent/ }));
    expect(
      screen.getByText(/cannot start until every stage before it has ended/),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^Memory/ }));
    expect(screen.getByText(/waiting for a free concurrency slot/)).toBeInTheDocument();
    await act(async () => controller.close());
  });

  it("records a failed run, marks a run it stopped watching, and keeps the last ten", async () => {
    const user = userEvent.setup();
    let n = 0;
    stubApi(() => {
      n += 1;
      return n === 1
        ? errorLine(
            "model_busy",
            "The language model is busy right now.",
            503,
            run({ status: "failed", graph_ok: false, outcome: "failed", request: "first" }),
          )
        : answer({ ...RESULT, run: run({ request: `search ${n}`, run_id: `r${n}` }) });
    });
    render(<App />);
    for (let i = 0; i < 12; i += 1) {
      await user.click(findFood());
      await waitFor(() => expect(findFood()).toBeEnabled());
    }
    await openExecution(user);
    const items = within(screen.getByRole("list", { name: "Runs" })).getAllByRole("button");
    expect(items).toHaveLength(10);
    expect(items[0]).toHaveTextContent("search 12");
    expect(screen.queryByText("first")).not.toBeInTheDocument();
  });

  it("marks a run as lost when the connection drops before it ends", async () => {
    const user = userEvent.setup();
    stubApi(() =>
      lines([{ type: "run", run: run({ status: "running", outcome: null, request: "dropped" }) }]),
    );
    render(<App />);
    await user.click(findFood());
    expect(await screen.findByRole("alert")).toHaveTextContent("Can't reach Makan");
    await openExecution(user);
    const item = within(screen.getByRole("list", { name: "Runs" })).getByRole("button");
    expect(item).toHaveTextContent("dropped");
    expect(item).toHaveTextContent("Stopped watching");
  });

  it("clears the history on request", async () => {
    const user = userEvent.setup();
    stubApi(() => answer(RESULT));
    render(<App />);
    await user.click(findFood());
    await screen.findByRole("heading", { name: "Nearby options" });
    await openExecution(user);
    expect(screen.getByText("thai please")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear history" }));
    expect(screen.getByText(/No runs yet/)).toBeInTheDocument();
  });
});

/** The Leaflet map the page made, found through the container it controls. */
function capturedMap(): L.Map {
  const container = document.querySelector(".leaflet-container") as HTMLElement & {
    _leaflet_id?: number;
  };
  const found = mapInstances.find((m) => m.getContainer() === container);
  if (!found) throw new Error("map not found");
  return found;
}

const realMap = L.map.bind(L);
const mapInstances: L.Map[] = [];

function trackedMap(...args: Parameters<typeof L.map>): L.Map {
  const instance = realMap(...args);
  mapInstances.push(instance);
  return instance;
}

beforeEach(() => {
  mapInstances.length = 0;
  vi.spyOn(L, "map").mockImplementation(trackedMap);
});
