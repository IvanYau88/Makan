import { useId, useRef, useState } from "react";
import type { FormEvent } from "react";
import { AuthFailure, MIN_PASSWORD_CHARS } from "./auth";
import type { AuthFailureKind } from "./auth";
import type { UseAuth } from "./useAuth";

type Mode = "sign-in" | "sign-up";

interface Props {
  auth: UseAuth;
  /** Why the person is looking at this form when they did not ask to, such as an expired session. */
  notice: string | null;
}

/** Sign in, or create an account, with an email and a password. */
export function SignInForm({ auth, notice }: Props) {
  const [mode, setMode] = useState<Mode>("sign-in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [failure, setFailure] = useState<{
    kind: AuthFailureKind | "form";
    message: string;
  } | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const emailInput = useRef<HTMLInputElement>(null);
  const passwordInput = useRef<HTMLInputElement>(null);
  const id = useId();
  const signUp = mode === "sign-up";

  const choose = (next: Mode) => {
    setMode(next);
    setFailure(null);
    setInfo(null);
    auth.setNotice(null);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (working) return;
    setInfo(null);
    auth.setNotice(null);
    const address = email.trim();
    if (!address || !address.includes("@")) {
      setFailure({ kind: "form", message: "Enter the email address you sign in with." });
      emailInput.current?.focus();
      return;
    }
    if (signUp && password.length < MIN_PASSWORD_CHARS) {
      setFailure({
        kind: "form",
        message: `Use at least ${MIN_PASSWORD_CHARS} characters for your password.`,
      });
      passwordInput.current?.focus();
      return;
    }
    if (!password) {
      setFailure({ kind: "form", message: "Enter your password." });
      passwordInput.current?.focus();
      return;
    }
    setFailure(null);
    setWorking(true);
    try {
      if (signUp) {
        const result = await auth.signUp(address, password);
        if (result === "confirm_email") {
          setInfo(
            `Check your email. We sent a confirmation link to ${address}. Open it, then come back and sign in.`,
          );
          setMode("sign-in");
          setPassword("");
        }
      } else {
        await auth.signIn(address, password);
      }
    } catch (error) {
      const found =
        error instanceof AuthFailure
          ? { kind: error.kind, message: error.message }
          : { kind: "other" as const, message: "Something went wrong. Try again." };
      setFailure(found);
      if (found.kind === "wrong_password") passwordInput.current?.focus();
    } finally {
      setWorking(false);
    }
  };

  const resend = async () => {
    setWorking(true);
    try {
      await auth.resend(email.trim());
      setFailure(null);
      setInfo(`We sent another confirmation link to ${email.trim()}. Check your email.`);
    } catch (error) {
      setFailure({
        kind: "other",
        message: error instanceof Error ? error.message : "Something went wrong. Try again.",
      });
    } finally {
      setWorking(false);
    }
  };

  return (
    <form className="account-form" onSubmit={(e) => void submit(e)} noValidate>
      <div className="mode-switch" role="group" aria-label="Sign in or create an account">
        <button
          type="button"
          className="button"
          aria-pressed={!signUp}
          onClick={() => choose("sign-in")}
        >
          I have an account
        </button>
        <button
          type="button"
          className="button"
          aria-pressed={signUp}
          onClick={() => choose("sign-up")}
        >
          I'm new here
        </button>
      </div>

      {notice && (
        <p className="notice notice-warn" role="status">
          {notice}
        </p>
      )}
      {info && (
        <p className="notice notice-info" role="status">
          {info}
        </p>
      )}

      <div className="field">
        <label htmlFor={`${id}-email`}>Email</label>
        <input
          ref={emailInput}
          id={`${id}-email`}
          type="email"
          value={email}
          autoComplete="email"
          inputMode="email"
          aria-invalid={failure?.kind === "form" && !email.includes("@") ? true : undefined}
          onChange={(e) => setEmail(e.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor={`${id}-password`}>Password</label>
        <input
          ref={passwordInput}
          id={`${id}-password`}
          type="password"
          value={password}
          autoComplete={signUp ? "new-password" : "current-password"}
          aria-invalid={failure?.kind === "wrong_password" ? true : undefined}
          aria-describedby={signUp ? `${id}-password-hint` : undefined}
          onChange={(e) => setPassword(e.target.value)}
        />
        {signUp && (
          <p className="hint" id={`${id}-password-hint`}>
            At least {MIN_PASSWORD_CHARS} characters.
          </p>
        )}
      </div>

      {failure && (
        <div className="notice notice-error" role="alert">
          <p>{failure.message}</p>
          {failure.kind === "unconfirmed" && (
            <button
              type="button"
              className="button button-link"
              onClick={() => void resend()}
              disabled={working}
            >
              Send the confirmation email again
            </button>
          )}
        </div>
      )}

      <div className="group-actions">
        <button type="submit" className="button button-primary button-stable" disabled={working}>
          <span className="button-state" aria-hidden={working || undefined}>
            {signUp ? "Create account" : "Sign in"}
          </span>
          <span className="button-state button-state-busy" aria-hidden={!working || undefined}>
            <span className="spinner" aria-hidden="true" />
            {signUp ? "Creating…" : "Signing in…"}
          </span>
        </button>
      </div>
      <p className="hint">
        You never need an account. Without one Makan works as a guest and remembers nothing.
      </p>
    </form>
  );
}
