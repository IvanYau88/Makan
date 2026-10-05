import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";
import type { Place, Recommendation } from "./types";

function place(name: string, distance_m: number, extra: Partial<Place> = {}): Place {
  return {
    id: name.toLowerCase().replace(/ /g, "-"),
    name,
    category: "thai_restaurant",
    distance_m,
    address: `${distance_m} Test Street`,
    reasons: [`${distance_m} m from your approximate location`],
    ...extra,
  };
}

const RESULT: Recommendation = {
  pick: place("Mid Thai", 140, {
    reasons: ["140 m from your approximate location", "matches 1 requested category filter(s)"],
  }),
  runners_up: [place("Far Thai", 760), place("Near Ramen", 90, { category: "ramen_restaurant" })],
  explanation: "Try Mid Thai.",
  warnings: [
    "Opening hours, menus, prices, and public reviews are unavailable; verify before going.",
  ],
  stale_facts: [],
  data_source: "overture:2026-09-23.1",
  attribution: "Places data: Overture Maps Foundation (CDLA Permissive 2.0).",
  partial: false,
  mode: "live",
};

type Handler = (body: Record<string, unknown>) => Response | Promise<Response>;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

/** Stub fetch: the health route answers with `mode`, and the recommendation route with `handler`. */
function stubApi(handler: Handler, mode: "demo" | "live" = "live") {
  const calls: Record<string, unknown>[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/health") return json({ status: "ok", mode });
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

afterEach(() => vi.unstubAllGlobals());

const locateButton = () => screen.getByRole("button", { name: "Locate me and find food" });

describe("App", () => {
  it("locates the user, sends the request, and shows the pick, why, runners-up and warnings", async () => {
    const calls = stubApi(() => json(RESULT));
    stubLocation("ok");
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("What are you in the mood for?"), "thai please");
    await user.selectOptions(screen.getByLabelText("How far will you go?"), "2000");
    await user.click(locateButton());

    expect(await screen.findByRole("heading", { name: "Your pick" })).toBeInTheDocument();
    expect(calls).toEqual([
      { latitude: 3.148, longitude: 101.695, request: "thai please", radius_m: 2000 },
    ]);

    const pick = screen.getByRole("heading", { name: "Mid Thai" }).closest("article")!;
    expect(within(pick).getByText("Thai restaurant")).toBeInTheDocument();
    expect(within(pick).getByText("140 m away")).toBeInTheDocument();
    expect(within(pick).getByText("matches 1 requested category filter(s)")).toBeInTheDocument();

    const runners = screen.getByRole("heading", { name: "Runners-up" });
    expect(runners).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Far Thai" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Near Ramen" })).toBeInTheDocument();
    expect(screen.getByText("Ramen restaurant")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Good to know" })).toBeInTheDocument();
    expect(screen.getByText(/Opening hours, menus, prices/)).toBeInTheDocument();
    expect(screen.getByText(/Overture Maps Foundation/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Your pick" })).toHaveFocus();
  });

  it("uses a default request when the box is empty", async () => {
    const calls = stubApi(() => json(RESULT));
    stubLocation("ok");
    render(<App />);
    await userEvent.click(locateButton());
    await screen.findByRole("heading", { name: "Your pick" });
    expect(calls[0]?.request).toBe("something good to eat");
  });

  it("shows a loading state and blocks a second submit while working", async () => {
    let finish: (r: Response) => void = () => undefined;
    const calls = stubApi(() => new Promise<Response>((resolve) => (finish = resolve)));
    stubLocation("ok");
    const user = userEvent.setup();
    render(<App />);
    await user.click(locateButton());

    const busy = await screen.findByRole("button", { name: /Finding food/ });
    expect(busy).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("Researching places nearby…");
    await user.click(busy);
    expect(calls).toHaveLength(1);

    finish(json(RESULT));
    await screen.findByRole("heading", { name: "Your pick" });
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("explains a denied location and falls back to typed coordinates", async () => {
    const calls = stubApi(() => json(RESULT));
    stubLocation("denied");
    const user = userEvent.setup();
    render(<App />);
    await user.click(locateButton());

    expect(await screen.findByRole("alert")).toHaveTextContent("Location access was denied");
    expect(calls).toHaveLength(0);
    const latitude = screen.getByLabelText("Latitude");
    expect(latitude).toHaveFocus();

    await user.type(latitude, "3.148");
    await user.type(screen.getByLabelText("Longitude"), "101.695");
    await user.click(screen.getByRole("button", { name: "Find food here" }));

    await screen.findByRole("heading", { name: "Your pick" });
    expect(calls).toEqual([
      { latitude: 3.148, longitude: 101.695, request: "something good to eat", radius_m: 1000 },
    ]);
  });

  it("opens the manual fields on request and goes back to the browser location", async () => {
    stubApi(() => json(RESULT));
    const user = userEvent.setup();
    render(<App />);
    const toggle = screen.getByRole("button", { name: "Enter coordinates instead" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);
    expect(screen.getByLabelText("Latitude")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Use my location instead" }));
    expect(screen.queryByLabelText("Latitude")).not.toBeInTheDocument();
  });

  it("flags bad typed coordinates without calling the server", async () => {
    const calls = stubApi(() => json(RESULT));
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Enter coordinates instead" }));
    await user.type(screen.getByLabelText("Latitude"), "95");
    await user.click(screen.getByRole("button", { name: "Find food here" }));

    const latitude = screen.getByLabelText("Latitude");
    expect(latitude).toBeInvalid();
    expect(latitude).toHaveAccessibleDescription("Must be between -90 and 90.");
    expect(screen.getByLabelText("Longitude")).toHaveAccessibleDescription("Enter a number.");
    expect(calls).toHaveLength(0);
  });

  it("says nothing was found and offers a wider search", async () => {
    const calls = stubApi(() => json({ ...RESULT, pick: null, runners_up: [] }));
    stubLocation("ok");
    const user = userEvent.setup();
    render(<App />);
    await user.click(locateButton());

    expect(await screen.findByRole("heading", { name: "No places found" })).toBeInTheDocument();
    expect(screen.getByText(/nothing to eat within 1 km/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Search a wider area" }));
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[1]?.radius_m).toBe(2000);
    expect(screen.getByLabelText("How far will you go?")).toHaveValue("2000");
  });

  it("offers no wider search at the largest radius", async () => {
    stubApi(() => json({ ...RESULT, pick: null, runners_up: [] }));
    stubLocation("ok");
    const user = userEvent.setup();
    render(<App />);
    await user.selectOptions(screen.getByLabelText("How far will you go?"), "5000");
    await user.click(locateButton());
    await screen.findByRole("heading", { name: "No places found" });
    expect(screen.queryByRole("button", { name: "Search a wider area" })).not.toBeInTheDocument();
  });

  it("shows the server's message on an error and retries", async () => {
    let attempts = 0;
    stubApi(() => {
      attempts += 1;
      return attempts === 1
        ? json(
            { error: { code: "places_error", message: "Nearby places data is unavailable." } },
            502,
          )
        : json(RESULT);
    });
    stubLocation("ok");
    const user = userEvent.setup();
    render(<App />);
    await user.click(locateButton());

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Nearby places data is unavailable.");
    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "Your pick" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("says the model is busy when the backend reports a rate limit", async () => {
    stubApi(() =>
      json(
        {
          error: {
            code: "model_busy",
            message: "The language model is busy right now. Try again in a minute.",
          },
        },
        503,
      ),
    );
    stubLocation("ok");
    render(<App />);
    await userEvent.click(locateButton());

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByRole("heading", { name: "The model is busy" })).toBeInTheDocument();
    expect(alert).toHaveTextContent("The language model is busy right now. Try again in a minute.");
    expect(within(alert).getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("keeps the generic heading for other failures, even a 503 with no model_busy code", async () => {
    stubApi(() =>
      json({ error: { code: "provider_error", message: "The language model failed." } }, 503),
    );
    stubLocation("ok");
    render(<App />);
    await userEvent.click(locateButton());

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByRole("heading", { name: "That did not work" })).toBeInTheDocument();
    expect(within(alert).queryByText("The model is busy")).not.toBeInTheDocument();
  });

  it("falls back to a generic message when the error is not JSON", async () => {
    stubApi(() => new Response("<html>Bad gateway</html>", { status: 502 }));
    stubLocation("ok");
    render(<App />);
    await userEvent.click(locateButton());
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Something went wrong on our side. Try again.",
    );
  });

  it("says so when the server cannot be reached", async () => {
    stubApi(() => {
      throw new TypeError("Failed to fetch");
    });
    stubLocation("ok");
    render(<App />);
    await userEvent.click(locateButton());
    expect(await screen.findByRole("alert")).toHaveTextContent("Can't reach Makan");
  });

  it("shows partial-result and stale-memory notes", async () => {
    stubApi(() =>
      json({
        ...RESULT,
        partial: true,
        warnings: ["Part of the search failed, so these results may be incomplete."],
        stale_facts: [
          { id: "f1", kind: "cuisine_like", content: "thai", reason: "expired" },
          { id: "f2", kind: "place_rating", content: { place_id: "x", rating: 5 }, reason: null },
        ],
      }),
    );
    stubLocation("ok");
    render(<App />);
    await userEvent.click(locateButton());

    await screen.findByRole("heading", { name: "Your pick" });
    expect(screen.getByText(/list may be missing places/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Check your saved tastes" })).toBeInTheDocument();
    expect(screen.getByText("cuisine like: thai")).toBeInTheDocument();
    expect(screen.getByText(/place rating: \{"place_id":"x","rating":5\}/)).toBeInTheDocument();
  });

  it("flags demo mode, and only demo mode", async () => {
    stubApi(() => json(RESULT), "demo");
    const { unmount } = render(<App />);
    expect(await screen.findByRole("note")).toHaveTextContent("Demo mode");
    unmount();

    stubApi(() => json(RESULT), "live");
    render(<App />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });

  it("ignores a failed health check", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    render(<App />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(locateButton()).toBeEnabled();
  });
});
