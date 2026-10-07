import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";
import {
  deleteAccount,
  exportData,
  fetchMe,
  saveProfile,
  saveTaste,
  sessionEnded,
} from "./accountApi";
import { DataRights } from "./DataRights";
import { ProfileForm } from "./ProfileForm";
import { SignInForm } from "./SignInForm";
import { TasteForm } from "./TasteForm";
import type { Me, Profile, Taste } from "./types";
import { SESSION_EXPIRED } from "./useAuth";
import type { UseAuth } from "./useAuth";

export const DELETED = "Your data and account were deleted. Makan works as a guest again.";

interface Props {
  auth: UseAuth;
}

type Load = { kind: "loading" } | { kind: "ready"; me: Me } | { kind: "failed"; message: string };

/** The Account page: sign in or create an account, then profile, taste, export, and delete. */
export function AccountView({ auth }: Props) {
  const { state } = auth;
  return (
    <div className="group-page">
      <h2 className="section-title">Account</h2>
      {state.status === "loading" && <p role="status">Checking your sign-in…</p>}
      {state.status === "out" && (
        <>
          <p className="hint">
            Sign in so Makan remembers your taste and calls you by name. Your searches work the same
            without one.
          </p>
          <SignInForm auth={auth} notice={state.notice} />
        </>
      )}
      {state.status === "in" && <SignedIn auth={auth} email={state.email} />}
    </div>
  );
}

function SignedIn({ auth, email }: { auth: UseAuth; email: string | null }) {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [justCreated, setJustCreated] = useState(false);
  const tasteHeading = useRef<HTMLHeadingElement>(null);
  const { token, endSession } = auth;

  /** Run an API call with a fresh token. A token the server refuses ends the session. */
  const withToken = useCallback(
    async <T,>(run: (token: string) => Promise<T>): Promise<T> => {
      const current = await token();
      if (!current) {
        await endSession(SESSION_EXPIRED);
        throw new ApiError("server", SESSION_EXPIRED);
      }
      try {
        return await run(current);
      } catch (error) {
        if (sessionEnded(error)) {
          await endSession(SESSION_EXPIRED);
          throw new ApiError("server", SESSION_EXPIRED);
        }
        throw error;
      }
    },
    [token, endSession],
  );

  const fetchProfile = useCallback(
    (signal?: AbortSignal) => {
      withToken((t) => fetchMe(t, signal)).then(
        (me) => setLoad({ kind: "ready", me }),
        (error: unknown) => {
          if (signal?.aborted) return;
          setLoad({
            kind: "failed",
            message: error instanceof Error ? error.message : "Something went wrong. Try again.",
          });
        },
      );
    },
    [withToken],
  );

  useEffect(() => {
    const controller = new AbortController();
    fetchProfile(controller.signal);
    return () => controller.abort();
  }, [fetchProfile]);

  const onProfile = async (profile: Profile) => {
    const saved = await withToken((t) => saveProfile(t, profile));
    const wasNew = load.kind === "ready" && load.me.profile === null;
    setLoad((current) =>
      current.kind === "ready" ? { kind: "ready", me: { ...current.me, profile: saved } } : current,
    );
    if (wasNew) {
      setJustCreated(true);
      setTimeout(() => tasteHeading.current?.focus(), 0);
    }
  };

  const onTaste = async (taste: Taste) => {
    const saved = await withToken((t) => saveTaste(t, taste));
    setLoad((current) =>
      current.kind === "ready" ? { kind: "ready", me: { ...current.me, taste: saved } } : current,
    );
    return saved;
  };

  const onDelete = async () => {
    await withToken((t) => deleteAccount(t));
    await endSession(DELETED);
  };

  return (
    <>
      <div className="account-who">
        <p>
          Signed in as <strong>{email ?? "your account"}</strong>
        </p>
        <button type="button" className="button button-small" onClick={() => void auth.signOut()}>
          Sign out
        </button>
      </div>

      {load.kind === "loading" && <p role="status">Loading your profile…</p>}
      {load.kind === "failed" && (
        <div className="notice notice-error" role="alert">
          <p>{load.message}</p>
          <button
            type="button"
            className="button button-link"
            onClick={() => {
              setLoad({ kind: "loading" });
              fetchProfile();
            }}
          >
            Try again
          </button>
        </div>
      )}
      {load.kind === "ready" && (
        <>
          <section className="account-section" aria-labelledby="profile-title">
            <h3 id="profile-title" className="section-title">
              {load.me.profile ? "Your profile" : "Create your profile"}
            </h3>
            {!load.me.profile && (
              <p className="hint">
                Tell Makan what to call you. After that, you can say what you like and what to
                avoid.
              </p>
            )}
            <ProfileForm profile={load.me.profile} onSave={onProfile} />
          </section>

          {load.me.profile && (
            <section className="account-section" aria-labelledby="taste-title">
              <h3 id="taste-title" className="section-title" tabIndex={-1} ref={tasteHeading}>
                {justCreated ? "Next: your taste" : "Your taste"}
              </h3>
              <p className="hint">
                {justCreated
                  ? "A minute here makes your first picks better. You can skip it and come back."
                  : "What Makan remembers about your food. Each entry has a date and how sure Makan is, and your never-lists do not fade."}
              </p>
              <TasteForm taste={load.me.taste} onSave={onTaste} />
            </section>
          )}

          <section className="account-section" aria-labelledby="data-title">
            <h3 id="data-title" className="section-title">
              Your data
            </h3>
            <DataRights onExport={() => withToken((t) => exportData(t))} onDelete={onDelete} />
          </section>
        </>
      )}
    </>
  );
}
