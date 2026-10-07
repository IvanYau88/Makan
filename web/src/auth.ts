import { createClient } from "@supabase/supabase-js";
import type { SupabaseClient } from "@supabase/supabase-js";
import type { AuthSettings } from "./types";

/** At least this many characters for a new password. Sign-in accepts whatever was set. */
export const MIN_PASSWORD_CHARS = 8;

export type AuthFailureKind =
  "wrong_password" | "unconfirmed" | "weak_password" | "rate_limited" | "network" | "other";

/** A sign-up or sign-in failure, worded for the person. `kind` lets the page add the right help. */
export class AuthFailure extends Error {
  readonly kind: AuthFailureKind;

  constructor(kind: AuthFailureKind, message: string) {
    super(message);
    this.name = "AuthFailure";
    this.kind = kind;
  }
}

export const MESSAGES: Record<AuthFailureKind, string> = {
  wrong_password: "That email and password do not match. Check them and try again.",
  unconfirmed: "Confirm your email first. Open the link in the message we sent you, then sign in.",
  weak_password: `Choose a longer or less common password, at least ${MIN_PASSWORD_CHARS} characters.`,
  rate_limited: "Too many attempts. Wait a minute and try again.",
  network: "Can't reach the sign-in service. Check your connection and try again.",
  other: "Something went wrong signing you in. Try again.",
};

/** Turn whatever supabase-js threw or returned into a failure the page can show. */
export function failureFrom(error: unknown): AuthFailure {
  const { code, name, status } = (error ?? {}) as {
    code?: unknown;
    name?: unknown;
    status?: unknown;
  };
  let kind: AuthFailureKind = "other";
  if (code === "invalid_credentials") kind = "wrong_password";
  else if (code === "email_not_confirmed") kind = "unconfirmed";
  else if (code === "weak_password") kind = "weak_password";
  else if (
    code === "over_request_rate_limit" ||
    code === "over_email_send_rate_limit" ||
    status === 429
  )
    kind = "rate_limited";
  else if (name === "AuthRetryableFetchError" || name === "TypeError" || status === 0)
    kind = "network";
  return new AuthFailure(kind, MESSAGES[kind]);
}

/** A signed-in person, as far as the page needs: who, and the token for the API. */
export interface AuthSession {
  email: string | null;
  token: string;
}

export type SignUpResult = "signed_in" | "confirm_email";

/** The few things the page does with Supabase Auth, over a client that can be swapped in a test. */
export class Auth {
  private readonly client: SupabaseClient;
  private readonly redirect: string | null;
  private leaving = false;

  constructor(settings: AuthSettings, client?: SupabaseClient) {
    this.redirect = settings.redirect_url;
    this.client =
      client ??
      createClient(settings.url, settings.anon_key, {
        // PKCE keeps the confirmation link from carrying a usable token in the address bar.
        auth: { flowType: "pkce", persistSession: true, autoRefreshToken: true },
      });
  }

  /** The current session, refreshed first if its token ran out. Null when signed out. */
  async session(): Promise<AuthSession | null> {
    const { data, error } = await this.client.auth.getSession();
    if (error || !data.session) return null;
    return { email: data.session.user.email ?? null, token: data.session.access_token };
  }

  /**
   * Hear about every sign-in and sign-out. `unexpected` is true only for a sign-out the person did
   * not ask for, such as a session Supabase would no longer refresh.
   */
  onChange(listener: (session: AuthSession | null, unexpected: boolean) => void): () => void {
    const { data } = this.client.auth.onAuthStateChange((event, session) => {
      if (event === "INITIAL_SESSION") return;
      const next = session
        ? { email: session.user.email ?? null, token: session.access_token }
        : null;
      listener(next, event === "SIGNED_OUT" && !this.leaving);
    });
    return () => data.subscription.unsubscribe();
  }

  async signUp(email: string, password: string): Promise<SignUpResult> {
    const { data, error } = await this.client.auth.signUp({
      email,
      password,
      options: { emailRedirectTo: this.redirect ?? window.location.origin },
    });
    if (error) throw failureFrom(error);
    // With email confirmation on there is a user and no session until the link is opened. An
    // address that is already registered looks the same, so the page cannot be used to find out.
    return data.session ? "signed_in" : "confirm_email";
  }

  async signIn(email: string, password: string): Promise<void> {
    const { error } = await this.client.auth.signInWithPassword({ email, password });
    if (error) throw failureFrom(error);
  }

  async resendConfirmation(email: string): Promise<void> {
    const { error } = await this.client.auth.resend({
      type: "signup",
      email,
      options: { emailRedirectTo: this.redirect ?? window.location.origin },
    });
    if (error) throw failureFrom(error);
  }

  /** Sign out on this device. The server is told on a best effort basis: this device signs out anyway. */
  async signOut(): Promise<void> {
    this.leaving = true;
    try {
      await this.client.auth.signOut({ scope: "local" });
    } finally {
      this.leaving = false;
    }
  }
}

/**
 * What went wrong with a confirmation link the person followed, from the address it opened, or
 * null when there was no problem. Supabase puts the problem in the address after the redirect.
 */
export function linkProblem(location: Pick<Location, "search" | "hash">): string | null {
  const params = new URLSearchParams(location.hash.replace(/^#/, ""));
  for (const [key, value] of new URLSearchParams(location.search)) params.set(key, value);
  const code = params.get("error_code");
  if (!params.get("error") && !code) return null;
  if (code === "otp_expired") {
    return "That confirmation link has expired. Sign in with your email and password to get a new one.";
  }
  return "That link did not work. Sign in with your email and password, or create the account again.";
}
