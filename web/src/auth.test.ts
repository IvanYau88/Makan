import type { SupabaseClient } from "@supabase/supabase-js";
import { Auth, MESSAGES, failureFrom, linkProblem } from "./auth";

describe("failureFrom", () => {
  it.each([
    [{ code: "invalid_credentials", status: 400 }, "wrong_password"],
    [{ code: "email_not_confirmed", status: 400 }, "unconfirmed"],
    [{ code: "weak_password", status: 422 }, "weak_password"],
    [{ code: "email_address_invalid", status: 400 }, "invalid_email"],
    [{ code: "over_request_rate_limit", status: 429 }, "rate_limited"],
    [{ code: "over_email_send_rate_limit", status: 429 }, "rate_limited"],
    [{ status: 429 }, "rate_limited"],
    [{ name: "AuthRetryableFetchError", status: 0 }, "network"],
    [new TypeError("Failed to fetch"), "network"],
    [{ code: "something_new", status: 500 }, "other"],
    [null, "other"],
  ] as const)("maps %j to %s with a message for a person", (error, kind) => {
    const failure = failureFrom(error);
    expect(failure.kind).toBe(kind);
    expect(failure.message).toBe(MESSAGES[kind]);
  });

  it("never passes the server's own text through", () => {
    const failure = failureFrom({
      code: "invalid_credentials",
      message: "Invalid login credentials",
    });
    expect(failure.message).not.toMatch(/Invalid login/);
  });
});

describe("linkProblem", () => {
  const at = (search: string, hash = "") => ({ search, hash });

  it("is null for an ordinary address", () => {
    expect(linkProblem(at(""))).toBeNull();
    expect(linkProblem(at("?code=abc"))).toBeNull();
  });

  it("says a link has expired", () => {
    const hash = "#error=access_denied&error_code=otp_expired&error_description=Email+link+expired";
    expect(linkProblem(at("", hash))).toMatch(/expired/);
  });

  it("says any other bad link did not work, from the query or the fragment", () => {
    expect(linkProblem(at("?error=access_denied"))).toMatch(/did not work/);
    expect(linkProblem(at("", "#error_code=bad_jwt"))).toMatch(/did not work/);
  });
});

function fakeClient(overrides: Record<string, unknown> = {}) {
  const auth = {
    getSession: vi.fn().mockResolvedValue({ data: { session: null }, error: null }),
    signUp: vi.fn().mockResolvedValue({ data: { session: null, user: {} }, error: null }),
    signInWithPassword: vi.fn().mockResolvedValue({ data: {}, error: null }),
    resend: vi.fn().mockResolvedValue({ error: null }),
    signOut: vi.fn().mockResolvedValue({ error: null }),
    onAuthStateChange: vi.fn(),
    ...overrides,
  };
  return { auth, client: { auth } as unknown as SupabaseClient };
}

const SETTINGS = { url: "https://p.supabase.test", anon_key: "anon", redirect_url: null };

describe("Auth", () => {
  it("tells a sign-up with no session to check email, and one with a session it is signed in", async () => {
    const { auth, client } = fakeClient();
    const subject = new Auth(SETTINGS, client);
    await expect(subject.signUp("bob@example.com", "password1")).resolves.toBe("confirm_email");
    auth.signUp.mockResolvedValue({ data: { session: { access_token: "t" } }, error: null });
    await expect(subject.signUp("bob@example.com", "password1")).resolves.toBe("signed_in");
  });

  it("sends the configured redirect address, or this page's own origin", async () => {
    const own = fakeClient();
    await new Auth(SETTINGS, own.client).signUp("a@b.co", "password1");
    expect(own.auth.signUp.mock.calls[0]?.[0].options.emailRedirectTo).toBe(window.location.origin);
    const set = fakeClient();
    await new Auth({ ...SETTINGS, redirect_url: "https://makan.test/" }, set.client).signUp(
      "a@b.co",
      "password1",
    );
    expect(set.auth.signUp.mock.calls[0]?.[0].options.emailRedirectTo).toBe("https://makan.test/");
  });

  it("raises a failure the page can word for a wrong password and an unconfirmed email", async () => {
    const { auth, client } = fakeClient();
    const subject = new Auth(SETTINGS, client);
    auth.signInWithPassword.mockResolvedValue({ data: {}, error: { code: "invalid_credentials" } });
    await expect(subject.signIn("a@b.co", "x")).rejects.toMatchObject({ kind: "wrong_password" });
    auth.signInWithPassword.mockResolvedValue({ data: {}, error: { code: "email_not_confirmed" } });
    await expect(subject.signIn("a@b.co", "x")).rejects.toMatchObject({ kind: "unconfirmed" });
  });

  it("reports the session with a token, and null when signed out", async () => {
    const { auth, client } = fakeClient();
    const subject = new Auth(SETTINGS, client);
    await expect(subject.session()).resolves.toBeNull();
    auth.getSession.mockResolvedValue({
      data: { session: { access_token: "tok", user: { email: "bob@example.com" } } },
      error: null,
    });
    await expect(subject.session()).resolves.toEqual({ email: "bob@example.com", token: "tok" });
  });

  it("calls a sign-out the person did not ask for unexpected", async () => {
    let listener: (event: string, session: unknown) => void = () => undefined;
    const { auth, client } = fakeClient({
      onAuthStateChange: vi.fn((cb) => {
        listener = cb;
        return { data: { subscription: { unsubscribe: vi.fn() } } };
      }),
    });
    const subject = new Auth(SETTINGS, client);
    const seen: [unknown, boolean][] = [];
    subject.onChange((session, unexpected) => seen.push([session, unexpected]));

    listener("SIGNED_OUT", null); // Supabase gave up refreshing
    auth.signOut.mockImplementation(async () => listener("SIGNED_OUT", null));
    await subject.signOut(); // the person asked for this one
    listener("INITIAL_SESSION", null); // not an event the page acts on

    expect(seen).toEqual([
      [null, true],
      [null, false],
    ]);
  });
});
