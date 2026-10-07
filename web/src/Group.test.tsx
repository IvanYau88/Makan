import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";
import { POLL_MS } from "./GroupView";
import type { GroupOption, GroupResult, GroupSession, GroupYou } from "./types";

/**
 * A small in-memory stand-in for /api/groups that follows the real routes' rules: a reader sees who
 * is in and who has shared, never what; a token is needed to answer or close; only the host closes;
 * and the closed group's result names people for the host and nobody for everyone else.
 */
interface Person {
  token: string;
  name: string | null;
  host: boolean;
  inputs: Record<string, unknown> | null;
}

class FakeGroups {
  link = "11111111-2222-4333-8444-555555555555";
  exists = false;
  expired = false;
  closed = false;
  request = "";
  radius = 0;
  people: Person[] = [];
  calls: { method: string; path: string; auth: string | null; body: unknown }[] = [];
  resultCalls = 0;
  failResult = false;
  private next = 1;

  private label(person: Person) {
    const index = this.people.indexOf(person) + 1;
    return person.name ?? `Guest ${index}`;
  }

  private session(): GroupSession {
    return {
      request: this.request,
      latitude: 3.148,
      longitude: 101.695,
      radius_m: this.radius,
      created_at: "2026-10-07T10:00:00+00:00",
      expires_at: "2026-10-08T10:00:00+00:00",
      closed: this.closed,
      participants: this.people.map((p) => ({
        name: this.label(p),
        is_host: p.host,
        submitted: p.inputs !== null,
      })),
    };
  }

  private you(person: Person | undefined): GroupYou | null {
    if (!person) return null;
    const inputs = person.inputs as {
      constraints?: GroupYou["constraints"];
      preferences?: GroupYou["preferences"];
    } | null;
    return {
      name: this.label(person),
      is_host: person.host,
      submitted: inputs !== null,
      constraints: inputs?.constraints ?? {},
      preferences: inputs?.preferences ?? {},
    };
  }

  private fail(status: number, code: string, message: string) {
    return Response.json({ error: { code, message } }, { status });
  }

  private pick(host: boolean): GroupOption {
    return {
      id: "p1",
      name: "Mid Thai",
      category: "thai_restaurant",
      distance_m: 140,
      address: "1 Jalan Test",
      reasons: ["~460 ft from the search point", "liked by 1 of 2"],
      lowest_score: 0.62,
      average_score: 0.71,
      ...(host && { lowest_scorers: ["Alex"] }),
      warnings: [
        host ? "Cannot verify Sam's allergy to peanut." : "Cannot verify an allergy to peanut.",
      ],
    };
  }

  private result(host: boolean): GroupResult {
    return {
      audience: host ? "host" : "member",
      pick: this.pick(host),
      runners_up: [
        { ...this.pick(host), id: "p2", name: "Far Thai", distance_m: 760, lowest_score: 0.5 },
      ],
      excluded: [
        host
          ? {
              id: "p3",
              name: "Sea Palace",
              category: "seafood_restaurant",
              refusals: [{ person: "Sam", term: "seafood" }],
            }
          : {
              id: "p3",
              name: "Sea Palace",
              category: "seafood_restaurant",
              refused_terms: ["seafood"],
            },
      ],
      explanation: host
        ? "Try Mid Thai: liked by 1 of 2. Alex is least happy with it."
        : "Try Mid Thai: liked by 1 of 2. Nobody scored it below 0.62.",
      warnings: [
        "Opening hours, menus, prices, and public reviews are unavailable; verify before going.",
      ],
      participant_count: this.people.length,
      pending: [],
      data_source: "demo",
      attribution: null,
      partial: false,
      mode: "demo",
    };
  }

  handle = async (url: string, init?: RequestInit): Promise<Response> => {
    if (url === "/api/config") {
      return Response.json({
        mode: "demo",
        map: { tile_url: "https://tiles.test/{z}/{x}/{y}.png", attribution: "© Test Maps" },
      });
    }
    const method = init?.method ?? "GET";
    const headers = (init?.headers ?? {}) as Record<string, string>;
    const auth = headers.Authorization ?? null;
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    this.calls.push({ method, path: url, auth, body });
    const path = url.replace("/api/groups", "");
    const token = auth?.replace("Bearer ", "");
    const me = this.people.find((p) => p.token === token);

    if (path === "" && method === "POST") {
      this.exists = true;
      this.request = body.request;
      this.radius = body.radius_m;
      const host: Person = {
        token: "host-token",
        name: body.display_name ?? null,
        host: true,
        inputs: null,
      };
      this.people = [host];
      return Response.json(
        {
          link_token: this.link,
          participant_token: host.token,
          session: this.session(),
          you: this.you(host),
        },
        { status: 201 },
      );
    }
    const [, link, rest] = path.split("/");
    if (!this.exists || link !== this.link)
      return this.fail(404, "session_not_found", "This session does not exist. Check the link.");
    if (this.expired) return this.fail(410, "session_expired", "This session has expired.");
    if (token && !me)
      return this.fail(403, "not_a_participant", "This is not a participant of the session.");

    if (rest === undefined && method === "GET") {
      return Response.json({ session: this.session(), you: this.you(me) });
    }
    if (rest === "participants") {
      if (this.closed) return this.fail(409, "session_closed", "The host has closed this session.");
      const person: Person = {
        token: `guest-${this.next++}`,
        name: body.display_name ?? null,
        host: false,
        inputs: null,
      };
      this.people.push(person);
      return Response.json(
        { participant_token: person.token, session: this.session(), you: this.you(person) },
        { status: 201 },
      );
    }
    if (!me) return this.fail(401, "participant_required", "This needs your participant token.");
    if (rest === "me") {
      if (this.closed) return this.fail(409, "session_closed", "The host has closed this session.");
      me.inputs = { constraints: body.constraints, preferences: body.preferences };
      if (body.display_name) me.name = body.display_name;
      return Response.json({ session: this.session(), you: this.you(me) });
    }
    if (rest === "close") {
      if (!me.host) return this.fail(403, "host_only", "Only the host can do this.");
      this.closed = true;
      return Response.json({ session: this.session(), you: this.you(me) });
    }
    if (rest === "result") {
      this.resultCalls += 1;
      if (!this.closed)
        return this.fail(409, "session_open", "The host has not closed this group yet.");
      if (this.failResult)
        return this.fail(
          502,
          "places_error",
          "Nearby places data is unavailable right now. Try again in a moment.",
        );
      return Response.json(this.result(me.host));
    }
    return this.fail(404, "http_error", "Not Found");
  };
}

function stubWorld(groups: FakeGroups, geolocation: "ok" | "denied" = "ok") {
  vi.stubGlobal("fetch", vi.fn(groups.handle));
  vi.stubGlobal("navigator", {
    geolocation: {
      getCurrentPosition: (ok: (p: unknown) => void, fail: (e: unknown) => void) =>
        geolocation === "ok"
          ? ok({ coords: { latitude: 3.14812, longitude: 101.69534 } })
          : fail({ code: 1, PERMISSION_DENIED: 1, POSITION_UNAVAILABLE: 2, TIMEOUT: 3 }),
    },
  });
}

/** Open the page the way a shared link does: at its address. */
function openLink(link: string) {
  window.history.replaceState(null, "", `/g/${link}`);
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
  window.history.replaceState(null, "", "/");
});

const user = () => userEvent.setup();

async function startGroup(groups: FakeGroups, name = "Alex") {
  const person = user();
  render(<App />);
  await person.click(screen.getByRole("button", { name: "Group" }));
  await person.type(screen.getByLabelText("What is the group in the mood for?"), "dinner for four");
  if (name) await person.type(screen.getByLabelText("Your name (optional)"), name);
  await person.click(screen.getByRole("button", { name: "Start the group" }));
  await screen.findByRole("heading", { name: "Where should we eat?" });
  expect(groups.exists).toBe(true);
  return person;
}

describe("starting a group", () => {
  it("asks what the group wants, then starts it at the map center and gives a link to share", async () => {
    const groups = new FakeGroups();
    stubWorld(groups);
    await startGroup(groups);

    expect(groups.calls[0]).toMatchObject({
      method: "POST",
      path: "/api/groups",
      auth: null,
      body: {
        latitude: 3.148,
        longitude: 101.695,
        request: "dinner for four",
        radius_m: 1609,
        display_name: "Alex",
      },
    });
    const url = `${window.location.origin}/g/${groups.link}`;
    expect(screen.getByRole("textbox", { name: "Link to share" })).toHaveValue(url);
    expect(window.location.pathname).toBe(`/g/${groups.link}`);
    expect(screen.getByText(/Alex is looking for/)).toBeInTheDocument();
    expect(screen.getByText("dinner for four")).toBeInTheDocument();
    expect(screen.getByText(/within 1 mi\./)).toBeInTheDocument();
    expect(screen.getByText("0 of 1 person has shared their answers.")).toBeInTheDocument();
    expect(screen.getByRole("listitem")).toHaveTextContent("Alex (host) (you)Waiting");
    // The host has to share their own answers too, so nobody is treated differently.
    expect(screen.getByRole("heading", { name: "Your answers" })).toBeInTheDocument();
    expect(window.localStorage.getItem(`makan.group.${groups.link}`)).toBe("host-token");
  });

  it("will not start without a request, and says why", async () => {
    const groups = new FakeGroups();
    stubWorld(groups);
    const person = user();
    render(<App />);
    await person.click(screen.getByRole("button", { name: "Group" }));
    await person.click(screen.getByRole("button", { name: "Start the group" }));
    const box = screen.getByLabelText("What is the group in the mood for?");
    expect(box).toBeInvalid();
    expect(box).toHaveFocus();
    expect(screen.getByText(/Say what the group feels like eating/)).toBeInTheDocument();
    expect(groups.exists).toBe(false);
  });

  it("falls back to typed coordinates when the browser will not say where the host is", async () => {
    const groups = new FakeGroups();
    // The Discover map only has a center once something places it, so with no location it has none.
    stubWorld(groups, "denied");
    const person = user();
    render(<App />);
    await person.click(screen.getByRole("button", { name: "Group" }));
    await person.type(screen.getByLabelText("What is the group in the mood for?"), "pizza");
    await person.click(screen.getByRole("button", { name: "Start the group" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/Location access was denied/);
    expect(screen.getByLabelText("Latitude")).toHaveFocus();
    await person.type(screen.getByLabelText("Latitude"), "3.1481");
    await person.type(screen.getByLabelText("Longitude"), "101.6953");
    await person.click(screen.getByRole("button", { name: "Start the group" }));
    await screen.findByRole("heading", { name: "Where should we eat?" });
    expect(groups.calls[0]?.body).toMatchObject({ latitude: 3.148, longitude: 101.695 });
  });

  it("shows the server's message when a group cannot be started", async () => {
    const groups = new FakeGroups();
    stubWorld(groups);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) =>
        url === "/api/groups"
          ? Response.json(
              {
                error: {
                  code: "server_error",
                  message: "Something went wrong on our side. Try again.",
                },
              },
              { status: 500 },
            )
          : groups.handle(url, init),
      ),
    );
    const person = user();
    render(<App />);
    await person.click(screen.getByRole("button", { name: "Group" }));
    await person.type(screen.getByLabelText("What is the group in the mood for?"), "pizza");
    await person.click(screen.getByRole("button", { name: "Start the group" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong on our side.");
    expect(screen.getByRole("button", { name: "Start the group" })).toBeEnabled();
  });
});

describe("opening a shared link", () => {
  async function host(groups: FakeGroups) {
    stubWorld(groups);
    const person = await startGroup(groups);
    return person;
  }

  it("lets a friend join and share their answers with no account", async () => {
    const groups = new FakeGroups();
    await host(groups);
    // The friend is on another device: nothing is stored there.
    cleanup();
    window.localStorage.clear();
    openLink(groups.link);
    const friend = user();
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Join this group" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Group" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByText(/No account needed/)).toBeInTheDocument();
    const people = screen.getByRole("list");
    expect(within(people).getAllByRole("listitem")).toHaveLength(1);
    expect(screen.queryByRole("textbox", { name: "Link to share" })).not.toBeInTheDocument();

    await friend.type(screen.getByLabelText("Your name"), "Sam");
    await friend.type(screen.getByLabelText("Places you won't go to"), "seafood");
    await friend.type(screen.getByLabelText("Allergies"), "peanut, shellfish");
    await friend.type(screen.getByLabelText("Foods you like"), "ramen");
    await friend.type(screen.getByLabelText("Foods you'd rather skip"), "thai");
    await friend.click(screen.getByRole("button", { name: "Share my answers" }));

    await screen.findByText("Shared with the group.");
    expect(screen.getByRole("heading", { name: "Your answers" })).toBeInTheDocument();
    expect(screen.getByText("1 of 2 people have shared their answers.")).toBeInTheDocument();
    const rows = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(rows).toEqual(["Alex (host)Waiting", "Sam (you)Shared"]);

    const [join, share] = groups.calls.slice(-2);
    expect(join).toMatchObject({
      method: "POST",
      path: `/api/groups/${groups.link}/participants`,
      auth: null,
      body: { display_name: "Sam" },
    });
    expect(share).toMatchObject({
      method: "PUT",
      auth: "Bearer guest-1",
      body: {
        display_name: "Sam",
        constraints: {
          refuses: ["seafood"],
          allergies: ["peanut", "shellfish"],
          diets: [],
          budget: null,
        },
        preferences: { likes: ["ramen"], dislikes: ["thai"] },
      },
    });
    expect(window.localStorage.getItem(`makan.group.${groups.link}`)).toBe("guest-1");
  });

  it("remembers a friend who comes back to the link, and lets them change their answers", async () => {
    const groups = new FakeGroups();
    await host(groups);
    cleanup();
    window.localStorage.clear();
    openLink(groups.link);
    const first = render(<App />);
    const friend = user();
    await friend.type(await screen.findByLabelText("Your name"), "Sam");
    await friend.type(screen.getByLabelText("Foods you like"), "ramen");
    await friend.click(screen.getByRole("button", { name: "Share my answers" }));
    await screen.findByText("Shared with the group.");
    first.unmount();

    openLink(groups.link);
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Your answers" })).toBeInTheDocument();
    expect(screen.getByLabelText("Foods you like")).toHaveValue("ramen");
    // The name box starts empty: "Shown as Sam" is only what the group sees now.
    expect(screen.getByLabelText("Your name")).toHaveValue("");
    expect(screen.getByLabelText("Your name")).toHaveAttribute("placeholder", "Shown as Sam");
    await friend.clear(screen.getByLabelText("Foods you like"));
    await friend.type(screen.getByLabelText("Foods you like"), "pizza");
    await friend.click(screen.getByRole("button", { name: "Update my answers" }));
    await screen.findByText("Shared with the group.");
    expect(groups.calls.at(-1)?.body).toMatchObject({ preferences: { likes: ["pizza"] } });
    expect(groups.calls.at(-1)?.body).not.toHaveProperty("display_name");
    expect(groups.people).toHaveLength(2); // coming back did not add a second person
  });

  it("forgets a token the server no longer knows and lets the person join again", async () => {
    const groups = new FakeGroups();
    await host(groups);
    window.localStorage.setItem(`makan.group.${groups.link}`, "stale-token");
    openLink(groups.link);
    cleanup();
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Join this group" })).toBeInTheDocument();
    expect(window.localStorage.getItem(`makan.group.${groups.link}`)).toBeNull();
  });

  it("checks the form before sending, and the answer goes nowhere until it is fixed", async () => {
    const groups = new FakeGroups();
    await host(groups);
    const sent = groups.calls.length;
    await user().type(screen.getByLabelText("Budget"), "x".repeat(81));
    await user().click(screen.getByRole("button", { name: "Share my answers" }));
    expect(screen.getByLabelText("Budget")).toBeInvalid();
    expect(screen.getByText("Use 80 characters or fewer.")).toBeInTheDocument();
    expect(groups.calls).toHaveLength(sent);
  });
});

describe("what a link can come to", () => {
  it("says so when no group has that link", async () => {
    const groups = new FakeGroups();
    stubWorld(groups);
    openLink("00000000-0000-4000-8000-000000000000");
    render(<App />);
    const heading = await screen.findByRole("heading", { name: "We can't find this group" });
    expect(heading).toHaveFocus();
    expect(screen.getByText(/The link may be mistyped/)).toBeInTheDocument();
    await user().click(screen.getByRole("button", { name: "Start a new group" }));
    expect(
      screen.getByRole("heading", { name: "Decide where to eat as a group" }),
    ).toBeInTheDocument();
    expect(window.location.pathname).toBe("/");
  });

  it("says so when the group has expired", async () => {
    const groups = new FakeGroups();
    stubWorld(groups);
    await startGroup(groups);
    groups.expired = true;
    openLink(groups.link);
    cleanup();
    render(<App />);
    expect(await screen.findByRole("heading", { name: "This group has expired" })).toHaveFocus();
    expect(screen.getByText(/are deleted when they expire/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Foods you like")).not.toBeInTheDocument();
  });

  it("notices on its own that a group expired while it was open", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const groups = new FakeGroups();
    stubWorld(groups);
    await startGroup(groups);
    groups.expired = true;
    await act(() => vi.advanceTimersByTimeAsync(POLL_MS));
    expect(
      await screen.findByRole("heading", { name: "This group has expired" }),
    ).toBeInTheDocument();
  });

  it("tells a latecomer the group is closed, since they can neither join nor see a result", async () => {
    const groups = new FakeGroups();
    stubWorld(groups);
    const host = await startGroup(groups);
    await host.click(screen.getByRole("button", { name: "Close the group and pick" }));
    await host.click(screen.getByRole("button", { name: "Yes, close it and pick" }));
    await screen.findByRole("heading", { name: "The group's pick" });

    window.localStorage.clear();
    cleanup();
    openLink(groups.link);
    render(<App />);
    expect(await screen.findByText(/You did not join before it closed/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Join this group" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "The group's pick" })).not.toBeInTheDocument();
    expect(groups.resultCalls).toBe(1); // only the host's own call
  });

  it("offers a retry when the group cannot be reached", async () => {
    const groups = new FakeGroups();
    stubWorld(groups);
    let down = true;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url !== "/api/config" && down) throw new TypeError("offline");
        return groups.handle(url, init);
      }),
    );
    groups.exists = true;
    groups.request = "pizza";
    groups.radius = 1609;
    groups.people = [{ token: "host-token", name: "Alex", host: true, inputs: null }];
    openLink(groups.link);
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Can't reach Makan");
    down = false;
    await user().click(screen.getByRole("button", { name: "Try again" }));
    expect(
      await screen.findByRole("heading", { name: "Where should we eat?" }),
    ).toBeInTheDocument();
  });
});

describe("the host's view and closing the group", () => {
  it("follows who has answered as friends join, without a reload", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const groups = new FakeGroups();
    stubWorld(groups);
    await startGroup(groups);
    expect(screen.getByText("0 of 1 person has shared their answers.")).toBeInTheDocument();

    groups.people.push({
      token: "g1",
      name: "Sam",
      host: false,
      inputs: { constraints: {}, preferences: {} },
    });
    groups.people.push({ token: "g2", name: null, host: false, inputs: null });
    await act(() => vi.advanceTimersByTimeAsync(POLL_MS));

    expect(await screen.findByText("1 of 3 people have shared their answers.")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Alex (host) (you)Waiting",
      "SamShared",
      "Guest 3Waiting",
    ]);
  });

  it("does not poll while another page is showing", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const groups = new FakeGroups();
    stubWorld(groups);
    const host = await startGroup(groups);
    await host.click(screen.getByRole("button", { name: "Discover" }));
    expect(window.location.pathname).toBe("/");
    const before = groups.calls.length;
    await act(() => vi.advanceTimersByTimeAsync(POLL_MS * 3));
    expect(groups.calls).toHaveLength(before);
    await host.click(screen.getByRole("button", { name: "Group" }));
    expect(window.location.pathname).toBe(`/g/${groups.link}`);
  });

  it("asks before closing, says who is still missing, and keeps the group open on Keep it open", async () => {
    const groups = new FakeGroups();
    stubWorld(groups);
    const host = await startGroup(groups);
    groups.people.push({ token: "g1", name: "Sam", host: false, inputs: null });
    await host.click(screen.getByRole("button", { name: "Close the group and pick" }));
    expect(screen.getByRole("button", { name: "Yes, close it and pick" })).toHaveFocus();
    await host.click(screen.getByRole("button", { name: "Keep it open" }));
    expect(screen.getByRole("button", { name: "Close the group and pick" })).toBeInTheDocument();
    expect(groups.closed).toBe(false);
  });

  it("shows the host the whole result: who is least happy, who refuses what, and distances in miles and feet", async () => {
    const groups = new FakeGroups();
    stubWorld(groups);
    const host = await startGroup(groups);
    await host.click(screen.getByRole("button", { name: "Close the group and pick" }));
    await host.click(screen.getByRole("button", { name: "Yes, close it and pick" }));

    const heading = await screen.findByRole("heading", { name: "The group's pick" });
    expect(heading).toHaveFocus();
    expect(groups.calls.at(-2)).toMatchObject({
      method: "POST",
      path: `/api/groups/${groups.link}/close`,
      auth: "Bearer host-token",
    });
    expect(screen.getByText(/closed this group/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Foods you like")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Close the group and pick" }),
    ).not.toBeInTheDocument();

    const pick = screen.getByRole("article", { name: "Top pick: Mid Thai" });
    expect(
      within(pick).getByText(/~460 ft straight-line from the search point · 1 Jalan Test/),
    ).toBeInTheDocument();
    expect(
      within(pick).getByText(/Lowest score 0.62 · average 0.71 \(out of 1\) · least happy: Alex/),
    ).toBeInTheDocument();
    expect(within(pick).getByText("liked by 1 of 2")).toBeInTheDocument();

    expect(screen.getByText("Try Mid Thai: liked by 1 of 2.")).toBeInTheDocument();
    expect(screen.getByText("Alex is least happy with it.")).toBeInTheDocument();
    const runners = screen.getByRole("region", { name: "Runners-up" });
    expect(within(runners).getByText("Far Thai")).toBeInTheDocument();
    expect(within(runners).getByText(/Thai restaurant · ~0.5 mi/)).toBeInTheDocument();
    const ruledOut = screen.getByRole("region", { name: "Ruled out before scoring" });
    expect(within(ruledOut).getByText("Sam refuses seafood")).toBeInTheDocument();
    const notes = screen.getByRole("region", { name: "Good to know" });
    expect(within(notes).getByText("Cannot verify Sam's allergy to peanut.")).toBeInTheDocument();
    expect(screen.getByText(/Sample places for the demo/)).toBeInTheDocument();
  });

  it("shows no meters or kilometers anywhere in a result", async () => {
    const groups = new FakeGroups();
    stubWorld(groups);
    const host = await startGroup(groups);
    await host.click(screen.getByRole("button", { name: "Close the group and pick" }));
    await host.click(screen.getByRole("button", { name: "Yes, close it and pick" }));
    await screen.findByRole("heading", { name: "The group's pick" });
    expect(document.body.textContent).not.toMatch(/\d\s?(m|km|meters?|kilomet\w+)\b/);
  });

  it("offers a retry when the result does not load, and the host can ask again", async () => {
    const groups = new FakeGroups();
    stubWorld(groups);
    const host = await startGroup(groups);
    groups.failResult = true;
    await host.click(screen.getByRole("button", { name: "Close the group and pick" }));
    await host.click(screen.getByRole("button", { name: "Yes, close it and pick" }));
    const failure = await screen.findByRole("alert");
    expect(failure).toHaveTextContent("Nearby places data is unavailable right now.");
    groups.failResult = false;
    await host.click(within(failure).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "The group's pick" })).toBeInTheDocument();
  });

  it("brings a host who reloads the closed group straight back to the result", async () => {
    const groups = new FakeGroups();
    stubWorld(groups);
    const host = await startGroup(groups);
    await host.click(screen.getByRole("button", { name: "Close the group and pick" }));
    await host.click(screen.getByRole("button", { name: "Yes, close it and pick" }));
    await screen.findByRole("heading", { name: "The group's pick" });
    cleanup();
    openLink(groups.link);
    render(<App />);
    expect(await screen.findByRole("heading", { name: "The group's pick" })).toBeInTheDocument();
    expect(screen.getByText("Alex is least happy with it.")).toBeInTheDocument();
  });
});

describe("what a friend sees when the host closes the group", () => {
  it("is told the group closed, and then reads the same pick with nobody named next to what they shared", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const groups = new FakeGroups();
    stubWorld(groups);
    await startGroup(groups);
    // Sam joins from another device.
    groups.people.push({
      token: "guest-9",
      name: "Sam",
      host: false,
      inputs: { constraints: { refuses: ["seafood"], allergies: ["peanut"] }, preferences: {} },
    });
    cleanup();
    window.localStorage.clear();
    window.localStorage.setItem(`makan.group.${groups.link}`, "guest-9");
    openLink(groups.link);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Your answers" })).toBeInTheDocument();
    expect(
      screen.getByText(/The host closes the group when everyone has answered/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Close the group/ })).not.toBeInTheDocument();

    groups.closed = true; // the host pressed close elsewhere
    await act(() => vi.advanceTimersByTimeAsync(POLL_MS));
    expect(await screen.findByRole("heading", { name: "The group's pick" })).toBeInTheDocument();
    expect(screen.getByText(/closed this group/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Foods you like")).not.toBeInTheDocument();

    const text = document.body.textContent ?? "";
    expect(text).toContain("Mid Thai");
    expect(text).toContain("Far Thai");
    expect(text).toContain("Nobody scored it below 0.62.");
    expect(text).toContain("refused by someone in the group: seafood");
    expect(text).toContain("Cannot verify an allergy to peanut.");
    expect(text).not.toContain("least happy");
    expect(text).not.toContain("Sam refuses");
    expect(text).not.toContain("Sam's allergy");
    expect(groups.calls.at(-1)).toMatchObject({
      method: "GET",
      path: `/api/groups/${groups.link}/result`,
      auth: "Bearer guest-9",
    });
  });
});
