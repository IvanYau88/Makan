import { useCallback, useEffect, useMemo, useState } from "react";
import { Auth, linkProblem } from "./auth";
import type { AuthSession, SignUpResult } from "./auth";
import type { AuthSettings } from "./types";

export const SESSION_EXPIRED = "Your session expired. Sign in again to continue.";

/**
 * Where sign-in stands. `off` means the server has no accounts (or has not said yet), so there is
 * no Account page and everyone is a guest. `out` carries a `notice` when the person did not choose
 * to be there, such as an expired session or a confirmation link that did not work.
 */
export type AuthState =
  | { status: "off" }
  | { status: "loading" }
  | { status: "out"; notice: string | null }
  | { status: "in"; email: string | null };

export interface UseAuth {
  state: AuthState;
  /** A token for the API, refreshed if it ran out, or null for a guest. */
  token: () => Promise<string | null>;
  signUp: (email: string, password: string) => Promise<SignUpResult>;
  signIn: (email: string, password: string) => Promise<void>;
  resend: (email: string) => Promise<void>;
  signOut: () => Promise<void>;
  /** The API refused the token, so end the session here too and say why. */
  endSession: (notice?: string) => Promise<void>;
  /** Show a message on the sign-in form, or clear it. */
  setNotice: (notice: string | null) => void;
}

const OFF: AuthState = { status: "off" };

/** Sign-in state for the page. Pass the server's settings, or null for a server without accounts. */
export function useAuth(settings: AuthSettings | null): UseAuth {
  const auth = useMemo(() => (settings ? new Auth(settings) : null), [settings]);
  // The server's settings arrive once, so the client is made once and starts out checking.
  const [state, setState] = useState<AuthState>({ status: "loading" });

  useEffect(() => {
    if (!auth) return;
    let ignore = false;
    const problem = linkProblem(window.location);
    const apply = (session: AuthSession | null, notice: string | null) => {
      setState(session ? { status: "in", email: session.email } : { status: "out", notice });
    };
    void auth.session().then((session) => {
      if (ignore) return;
      apply(session, session ? null : problem);
      if (problem) window.history.replaceState(null, "", window.location.pathname);
    });
    const stop = auth.onChange((session, unexpected) => {
      if (ignore) return;
      apply(session, unexpected ? SESSION_EXPIRED : null);
    });
    return () => {
      ignore = true;
      stop();
    };
  }, [auth]);

  const token = useCallback(async () => {
    if (!auth) return null;
    const session = await auth.session();
    return session?.token ?? null;
  }, [auth]);

  const signOut = useCallback(async () => {
    await auth?.signOut();
    setState({ status: "out", notice: null });
  }, [auth]);

  const endSession = useCallback(
    async (notice: string = SESSION_EXPIRED) => {
      await auth?.signOut().catch(() => undefined);
      setState({ status: "out", notice });
    },
    [auth],
  );

  const setNotice = useCallback((notice: string | null) => {
    setState((s) => (s.status === "out" ? { status: "out", notice } : s));
  }, []);

  return {
    state: auth ? state : OFF,
    token,
    signUp: async (email, password) => {
      if (!auth) throw new Error("accounts are off");
      return auth.signUp(email, password);
    },
    signIn: async (email, password) => {
      if (!auth) throw new Error("accounts are off");
      await auth.signIn(email, password);
    },
    resend: async (email) => {
      if (!auth) throw new Error("accounts are off");
      await auth.resendConfirmation(email);
    },
    signOut,
    endSession,
    setNotice,
  };
}
