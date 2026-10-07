import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";
import { RESULT } from "./test-fixtures";
import type { Me, Recommendation, Taste } from "./types";

// The Supabase client is the only thing mocked here: it stands for the network calls to Supabase
// Auth. The page talks to the real `Auth` wrapper over it, and to a stubbed Makan API.
const supabase = vi.hoisted(() => {
  type Session = { access_token: string; user: { email: string } };
  const state = {
    session: null as Session | null,
    listeners: [] as ((event: string, session: Session | null) => void)[],
    signUp: vi.fn(),
    signInWithPassword: vi.fn(),
    resend: vi.fn(),
    signOut: vi.fn(),
  };
  const emit = (event: string, session: Session | null) => {
    state.session = session;
    for (const listener of state.listeners) listener(event, session);
  };
  const client = {
    auth: {
      getSession: async () => ({ data: { session: state.session }, error: null }),
      onAuthStateChange: (listener: (event: string, session: Session | null) => void) => {
        state.listeners.push(listener);
        return { data: { subscription: { unsubscribe: () => undefined } } };
      },
      signUp: (args: unknown) => state.signUp(args),
      signInWithPassword: (args: unknown) => state.signInWithPassword(args),
      resend: (args: unknown) => state.resend(args),
      signOut: async () => {
        await state.signOut();
        emit("SIGNED_OUT", null);
        return { error: null };
      },
    },
  };
  return { state, emit, client };
});

vi.mock("@supabase/supabase-js", () => ({ createClient: vi.fn(() => supabase.client) }));

const AUTH = { url: "https://p.supabase.test", anon_key: "anon-key", redirect_url: null };
const EMPTY_TASTE: Taste = { likes: [], dislikes: [], allergies: [], diets: [], never_places: [] };
const SESSION = { access_token: "tok-bob", user: { email: "bob@example.com" } };

interface Api {
  me: Me;
  calls: { method: string; url: string; headers: Record<string, string>; body: unknown }[];
  /** Replace the answer for a route, such as `PUT /api/me/profile`. */
  fail: Map<string, Response>;
  searched: Record<string, unknown>[];
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

function problem(code: string, message: string, status: number): Response {
  return json({ error: { code, message } }, status);
}

function stubApi(
  options: { auth?: typeof AUTH | null; me?: Partial<Me>; greeting?: string } = {},
): Api {
  const api: Api = {
    me: {
      user: { id: "u1", email: "bob@example.com" },
      profile: null,
      taste: EMPTY_TASTE,
      ...options.me,
    },
    calls: [],
    fail: new Map(),
    searched: [],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const headers = (init?.headers ?? {}) as Record<string, string>;
      const body = init?.body ? (JSON.parse(String(init.body)) as unknown) : undefined;
      if (url === "/api/config") {
        return json({
          mode: "live",
          map: {
            tile_url: "https://t.test/{z}/{x}/{y}.png",
            attribution: "© T",
            attribution_url: null,
          },
          auth: options.auth === undefined ? AUTH : options.auth,
        });
      }
      api.calls.push({ method, url, headers, body });
      const key = `${method} ${url}`;
      const forced = api.fail.get(key);
      if (forced) return forced.clone();
      if (url === "/api/recommendations/stream") {
        api.searched.push(body as Record<string, unknown>);
        const recommendation: Recommendation = {
          ...RESULT,
          greeting: options.greeting ?? null,
        };
        return new Response(JSON.stringify({ type: "result", recommendation }) + "\n", {
          headers: { "Content-Type": "application/x-ndjson" },
        });
      }
      if (key === "GET /api/me") return json(api.me);
      if (key === "PUT /api/me/profile") {
        api.me = { ...api.me, profile: body as Me["profile"] };
        return json({ profile: body });
      }
      if (key === "PUT /api/me/taste") {
        api.me = { ...api.me, taste: body as Taste };
        return json({ taste: body });
      }
      if (key === "GET /api/me/export") return new Response('{"user":{"id":"u1"}}');
      if (key === "DELETE /api/me") return new Response(null, { status: 204 });
      return problem("not_found", "Not found.", 404);
    }),
  );
  return api;
}

beforeEach(() => {
  supabase.state.session = null;
  supabase.state.listeners.length = 0;
  supabase.state.signUp.mockReset();
  supabase.state.signInWithPassword.mockReset();
  supabase.state.resend.mockReset().mockResolvedValue({ error: null });
  supabase.state.signOut.mockReset().mockResolvedValue(undefined);
  vi.stubGlobal("navigator", {
    geolocation: {
      getCurrentPosition: (ok: (p: unknown) => void) =>
        ok({ coords: { latitude: 3.14812, longitude: 101.69534 } }),
    },
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const openAccount = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(await screen.findByRole("button", { name: "Account" }));
};

async function signInForm(user: ReturnType<typeof userEvent.setup>, password = "hunter2hunter2") {
  await user.type(screen.getByLabelText("Email"), "bob@example.com");
  await user.type(screen.getByLabelText("Password"), password);
}

describe("without accounts", () => {
  it("has no Account page and sends no credentials", async () => {
    const api = stubApi({ auth: null });
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Group" });
    expect(screen.queryByRole("button", { name: "Account" })).not.toBeInTheDocument();

    await user.type(screen.getByLabelText("What are you in the mood for?"), "thai");
    await user.click(screen.getByRole("button", { name: "Find food here" }));
    await screen.findByRole("heading", { name: "Nearby options" });
    expect(api.calls.every((c) => !("Authorization" in c.headers))).toBe(true);
    expect(screen.queryByText(/^Hey /)).not.toBeInTheDocument();
  });
});

describe("signing in", () => {
  it("shows the wrong password state and keeps the email", async () => {
    stubApi();
    supabase.state.signInWithPassword.mockResolvedValue({
      data: {},
      error: { code: "invalid_credentials", status: 400, message: "Invalid login credentials" },
    });
    const user = userEvent.setup();
    render(<App />);
    await openAccount(user);
    await signInForm(user);
    await user.click(
      screen.getByRole("button", { name: "Sign in", pressed: undefined, description: "" }),
    );

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("That email and password do not match.");
    expect(screen.getByLabelText("Email")).toHaveValue("bob@example.com");
    expect(screen.getByLabelText("Password")).toHaveFocus();
    expect(screen.getByLabelText("Password")).toBeInvalid();
  });

  it("shows the unconfirmed email state and can send the link again", async () => {
    stubApi();
    supabase.state.signInWithPassword.mockResolvedValue({
      data: {},
      error: { code: "email_not_confirmed", status: 400 },
    });
    const user = userEvent.setup();
    render(<App />);
    await openAccount(user);
    await signInForm(user);
    await user.click(submitButton());

    expect(await screen.findByRole("alert")).toHaveTextContent("Confirm your email first");
    await user.click(screen.getByRole("button", { name: "Send the confirmation email again" }));
    expect(supabase.state.resend).toHaveBeenCalledWith(
      expect.objectContaining({ type: "signup", email: "bob@example.com" }),
    );
    expect(screen.getByText(/We sent another confirmation link to bob@example.com/)).toBeVisible();
  });

  it("asks for the missing fields before calling Supabase", async () => {
    stubApi();
    const user = userEvent.setup();
    render(<App />);
    await openAccount(user);
    await user.click(submitButton());
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter the email address");
    expect(screen.getByLabelText("Email")).toHaveFocus();
    expect(supabase.state.signInWithPassword).not.toHaveBeenCalled();
  });

  it("signs in and loads the profile with the token", async () => {
    const api = stubApi();
    supabase.state.signInWithPassword.mockImplementation(async () => {
      supabase.emit("SIGNED_IN", SESSION);
      return { data: {}, error: null };
    });
    const user = userEvent.setup();
    render(<App />);
    await openAccount(user);
    await signInForm(user);
    await user.click(submitButton());

    expect(await screen.findByText("bob@example.com")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Create your profile" })).toBeInTheDocument();
    const me = api.calls.find((c) => c.url === "/api/me");
    expect(me?.headers.Authorization).toBe("Bearer tok-bob");
  });
});

describe("creating an account", () => {
  it("tells the person to check their email when there is no session yet", async () => {
    stubApi();
    supabase.state.signUp.mockResolvedValue({ data: { session: null, user: {} }, error: null });
    const user = userEvent.setup();
    render(<App />);
    await openAccount(user);
    await user.click(screen.getByRole("button", { name: "I'm new here" }));
    await signInForm(user);
    await user.click(submitButton());

    expect(
      await screen.findByText(/Check your email\. We sent a confirmation link to bob@example.com/),
    ).toBeVisible();
    expect(supabase.state.signUp).toHaveBeenCalledWith(
      expect.objectContaining({
        email: "bob@example.com",
        options: { emailRedirectTo: window.location.origin },
      }),
    );
    // Back on the sign in form, with the password cleared.
    expect(screen.getByLabelText("Password")).toHaveValue("");
  });

  it("refuses a short password without calling Supabase", async () => {
    stubApi();
    const user = userEvent.setup();
    render(<App />);
    await openAccount(user);
    await user.click(screen.getByRole("button", { name: "I'm new here" }));
    await signInForm(user, "short");
    await user.click(submitButton());
    expect(await screen.findByRole("alert")).toHaveTextContent("at least 8 characters");
    expect(supabase.state.signUp).not.toHaveBeenCalled();
  });
});

describe("a signed-in person", () => {
  const signedIn = async (api: Api = stubApi()) => {
    supabase.state.session = SESSION;
    const user = userEvent.setup();
    render(<App />);
    await openAccount(user);
    return { api, user };
  };

  it("makes a profile with a required name, then offers the taste form", async () => {
    const { api, user } = await signedIn();
    await screen.findByRole("heading", { name: "Create your profile" });

    await user.click(screen.getByRole("button", { name: "Create profile" }));
    expect(screen.getByText("Enter the name Makan should call you.")).toBeInTheDocument();
    expect(screen.getByLabelText("Your name")).toHaveFocus();
    expect(api.calls.some((c) => c.url === "/api/me/profile")).toBe(false);

    await user.type(screen.getByLabelText("Your name"), "Bob");
    expect(screen.getByText(/Hey Bob!/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create profile" }));

    expect(await screen.findByRole("heading", { name: "Next: your taste" })).toHaveFocus();
    const put = api.calls.find((c) => c.url === "/api/me/profile");
    expect(put?.body).toEqual({ display_name: "Bob", location_history_opt_in: false });
    expect(put?.headers.Authorization).toBe("Bearer tok-bob");
  });

  it("stores the opt-in only when it is ticked", async () => {
    const { api, user } = await signedIn();
    await screen.findByRole("heading", { name: "Create your profile" });
    expect(screen.getByLabelText(/Keep a history of where I search/)).not.toBeChecked();
    await user.type(screen.getByLabelText("Your name"), "Bob");
    await user.click(screen.getByLabelText(/Keep a history of where I search/));
    await user.click(screen.getByRole("button", { name: "Create profile" }));
    await screen.findByRole("heading", { name: "Next: your taste" });
    expect(api.calls.find((c) => c.url === "/api/me/profile")?.body).toMatchObject({
      location_history_opt_in: true,
    });
  });

  it("shows the server's message when the name is refused", async () => {
    const { api, user } = await signedIn();
    api.fail.set(
      "PUT /api/me/profile",
      problem("invalid_request", "Use 40 characters or fewer for your name.", 422),
    );
    await screen.findByRole("heading", { name: "Create your profile" });
    await user.type(screen.getByLabelText("Your name"), "Bob");
    await user.click(screen.getByRole("button", { name: "Create profile" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Use 40 characters or fewer");
  });

  it("saves taste with the soft skip apart from the hard nevers", async () => {
    const api = stubApi({
      me: { profile: { display_name: "Bob", location_history_opt_in: false } },
    });
    const { user } = await signedIn(api);
    await screen.findByRole("heading", { name: "Your taste" });

    await user.type(screen.getByLabelText("Cuisines you like"), "thai, ramen");
    await user.type(screen.getByLabelText("Cuisines you'd rather skip"), "burgers");
    await user.type(screen.getByLabelText("Allergies"), "peanut");
    await user.type(screen.getByLabelText("Places you won't go to"), "pizza hut");
    await user.click(screen.getByRole("button", { name: "Save my taste" }));

    expect(await screen.findByText(/Saved\. Makan will use this/)).toBeInTheDocument();
    expect(api.calls.find((c) => c.url === "/api/me/taste")?.body).toEqual({
      likes: ["thai", "ramen"],
      dislikes: ["burgers"],
      allergies: ["peanut"],
      diets: [],
      never_places: ["pizza hut"],
    });
  });

  it("refuses a cuisine that is both liked and skipped before sending", async () => {
    const api = stubApi({
      me: { profile: { display_name: "Bob", location_history_opt_in: false } },
    });
    const { user } = await signedIn(api);
    await screen.findByRole("heading", { name: "Your taste" });
    await user.type(screen.getByLabelText("Cuisines you like"), "thai");
    await user.type(screen.getByLabelText("Cuisines you'd rather skip"), "Thai");
    await user.click(screen.getByRole("button", { name: "Save my taste" }));
    expect(screen.getByText(/Thai cannot be both liked and skipped/)).toBeInTheDocument();
    expect(api.calls.some((c) => c.url === "/api/me/taste")).toBe(false);
  });

  it("starts the taste form from what is stored", async () => {
    const api = stubApi({
      me: {
        profile: { display_name: "Bob", location_history_opt_in: true },
        taste: { ...EMPTY_TASTE, likes: ["thai"], allergies: ["peanut"] },
      },
    });
    await signedIn(api);
    expect(await screen.findByLabelText("Cuisines you like")).toHaveValue("thai");
    expect(screen.getByLabelText("Allergies")).toHaveValue("peanut");
    expect(screen.getByLabelText(/Keep a history/)).toBeChecked();
  });

  it("sends the token with a search, and shows the greeting the server wrote", async () => {
    const api = stubApi({ greeting: "Hey Bob!" });
    supabase.state.session = SESSION;
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Account" });
    await user.type(screen.getByLabelText("What are you in the mood for?"), "thai please");
    await user.click(screen.getByRole("button", { name: "Find food here" }));
    await screen.findByRole("heading", { name: "Nearby options" });
    const search = api.calls.find((c) => c.url === "/api/recommendations/stream");
    expect(search?.headers.Authorization).toBe("Bearer tok-bob");
    expect(screen.getByText("Hey Bob! Your top pick is Mid Thai.")).toBeInTheDocument();
  });

  it("exports the data as a file", async () => {
    const api = stubApi({
      me: { profile: { display_name: "Bob", location_history_opt_in: false } },
    });
    const create = vi.fn(() => "blob:export");
    const revoke = vi.fn();
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke }));
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);
    const { user } = await signedIn(api);
    await user.click(await screen.findByRole("button", { name: "Download" }));
    expect(await screen.findByText("Saved makan-data.json.")).toBeInTheDocument();
    expect(create).toHaveBeenCalledOnce();
    expect(click).toHaveBeenCalledOnce();
    expect(api.calls.find((c) => c.url === "/api/me/export")?.headers.Authorization).toBe(
      "Bearer tok-bob",
    );
  });

  it("asks before deleting, can cancel, and signs out with a notice after deleting", async () => {
    const api = stubApi({
      me: { profile: { display_name: "Bob", location_history_opt_in: false } },
    });
    const { user } = await signedIn(api);
    await user.click(await screen.findByRole("button", { name: "Delete…" }));
    const group = screen.getByRole("group", { name: "Delete everything?" });
    expect(within(group).getByText(/gone for good/)).toBeInTheDocument();
    expect(api.calls.some((c) => c.method === "DELETE")).toBe(false);

    await user.click(within(group).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("group", { name: "Delete everything?" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Delete…" }));
    await user.click(screen.getByRole("button", { name: "Yes, delete everything" }));

    expect(await screen.findByText(/Your data and account were deleted/)).toBeInTheDocument();
    expect(api.calls.filter((c) => c.method === "DELETE")).toHaveLength(1);
    expect(
      screen.getByRole("button", { name: "I have an account", pressed: true }),
    ).toBeInTheDocument();
  });

  it("keeps the account when deleting fails, and says so", async () => {
    const api = stubApi({
      me: { profile: { display_name: "Bob", location_history_opt_in: false } },
    });
    api.fail.set(
      "DELETE /api/me",
      problem("auth_unavailable", "Sign-in is unavailable right now. Try again in a minute.", 503),
    );
    const { user } = await signedIn(api);
    await user.click(await screen.findByRole("button", { name: "Delete…" }));
    await user.click(screen.getByRole("button", { name: "Yes, delete everything" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("unavailable right now");
    expect(screen.getByText("bob@example.com")).toBeInTheDocument();
  });

  it("signs out", async () => {
    await signedIn();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(supabase.state.signOut).toHaveBeenCalled();
    expect(await screen.findByLabelText("Email")).toBeInTheDocument();
  });
});

describe("an expired session", () => {
  it("signs the person out with a notice when the server refuses the token", async () => {
    const api = stubApi();
    api.fail.set(
      "GET /api/me",
      problem("token_expired", "Your session ended. Sign in again to continue.", 401),
    );
    supabase.state.session = SESSION;
    const user = userEvent.setup();
    render(<App />);
    await openAccount(user);
    expect(
      await screen.findByText("Your session expired. Sign in again to continue."),
    ).toBeVisible();
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.queryByText("bob@example.com")).not.toBeInTheDocument();
  });

  it("signs out and says why when a search is refused, then searches as a guest", async () => {
    const api = stubApi();
    api.fail.set(
      "POST /api/recommendations/stream",
      problem("token_expired", "Your session ended. Sign in again to continue.", 401),
    );
    supabase.state.session = SESSION;
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Account" });
    await user.type(screen.getByLabelText("What are you in the mood for?"), "thai");
    await user.click(screen.getByRole("button", { name: "Find food here" }));

    expect(
      await screen.findByText(/Your session expired and you are signed out/),
    ).toBeInTheDocument();
    // The note about it follows the person to the other pages.
    expect(
      await screen.findByText("Your session expired. Sign in again to continue."),
    ).toBeVisible();

    api.fail.clear();
    await user.click(screen.getByRole("button", { name: "Find food here" }));
    await screen.findByRole("heading", { name: "Nearby options" });
    const guest = api.calls.filter((c) => c.url === "/api/recommendations/stream").at(-1);
    expect(guest?.headers).not.toHaveProperty("Authorization");
  });

  it("notices a sign-out Supabase made on its own", async () => {
    stubApi();
    supabase.state.session = SESSION;
    const user = userEvent.setup();
    render(<App />);
    await openAccount(user);
    await screen.findByText("bob@example.com");
    act(() => supabase.emit("SIGNED_OUT", null));
    expect(
      await screen.findByText("Your session expired. Sign in again to continue."),
    ).toBeVisible();
  });
});

describe("a confirmation link that failed", () => {
  it("says the link expired on the sign in form", async () => {
    stubApi();
    window.history.replaceState(null, "", "/#error=access_denied&error_code=otp_expired");
    const user = userEvent.setup();
    render(<App />);
    await openAccount(user);
    await waitFor(() =>
      expect(screen.getByText(/That confirmation link has expired/)).toBeVisible(),
    );
    window.history.replaceState(null, "", "/");
  });
});

function submitButton() {
  const form = screen.getByLabelText("Email").closest("form") as HTMLFormElement;
  return within(form).getByRole("button", {
    name: /^(Sign in|Create account)$/,
    pressed: undefined,
  });
}
