import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";
import { radiusLabel } from "./format";
import {
  GROUP_CLOSED,
  GROUP_EXPIRED,
  GROUP_NOT_FOUND,
  NOT_A_PARTICIPANT,
  closeGroup,
  joinGroup,
  readGroup,
  readGroupResult,
  shareInputs,
} from "./groupApi";
import { forgetCredential, groupUrl, loadCredential, saveCredential } from "./groupLink";
import { GroupInputsForm } from "./GroupInputsForm";
import { GroupResultView } from "./GroupResultView";
import type { GroupInputs, GroupMember, GroupResult, GroupView as View } from "./types";

/** How often an open group asks the server who has answered and whether the host has closed it. */
export const POLL_MS = 5000;

interface Props {
  link: string;
  /** The group as the create call returned it, so a new group does not flash a loading state. */
  initial: View | null;
  onLeave: () => void;
  /** True while another page is showing, so a group nobody is looking at is not polled. */
  paused?: boolean;
  pollMs?: number;
}

type Phase =
  | { kind: "loading" }
  | { kind: "gone"; reason: "missing" | "expired" }
  | { kind: "failed"; message: string }
  | { kind: "ready" };

type ResultState =
  | { kind: "loading" }
  | { kind: "ready"; result: GroupResult }
  | { kind: "failed"; message: string };

/** The page behind a shared link: who is in, your answers, closing it, and the result. */
export function GroupView({ link, initial, onLeave, paused = false, pollMs = POLL_MS }: Props) {
  const [phase, setPhase] = useState<Phase>(initial ? { kind: "ready" } : { kind: "loading" });
  const [view, setView] = useState<View | null>(initial);
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<ResultState>({ kind: "loading" });
  const [resultAttempt, setResultAttempt] = useState(0);
  const token = useRef<string | null>(loadCredential(link));
  const headingRef = useRef<HTMLHeadingElement>(null);
  const resultHeadingRef = useRef<HTMLHeadingElement>(null);

  /** Read the group with this person's token. A token the server no longer knows is dropped. */
  const fetchView = useCallback(
    async (signal?: AbortSignal): Promise<View> => {
      try {
        return await readGroup(link, token.current, signal);
      } catch (error) {
        if (error instanceof ApiError && error.code === NOT_A_PARTICIPANT && token.current) {
          forgetCredential(link);
          token.current = null;
          return readGroup(link, null, signal);
        }
        throw error;
      }
    },
    [link],
  );

  const settle = useCallback((error: unknown): boolean => {
    if (error instanceof ApiError && error.code === GROUP_NOT_FOUND) {
      setPhase({ kind: "gone", reason: "missing" });
      return true;
    }
    if (error instanceof ApiError && error.code === GROUP_EXPIRED) {
      setPhase({ kind: "gone", reason: "expired" });
      return true;
    }
    return false;
  }, []);

  useEffect(() => {
    if (initial && attempt === 0) return;
    const controller = new AbortController();
    fetchView(controller.signal).then(
      (next) => {
        setView(next);
        setPhase({ kind: "ready" });
      },
      (error: unknown) => {
        if (controller.signal.aborted || settle(error)) return;
        setPhase({
          kind: "failed",
          message: error instanceof ApiError ? error.message : "Something went wrong. Try again.",
        });
      },
    );
    return () => controller.abort();
  }, [fetchView, settle, initial, attempt]);

  const closed = view?.session.closed ?? false;
  const joined = view?.you != null;
  const open = phase.kind === "ready" && !closed && !paused;

  // While the group is open, keep who has answered fresh and notice when the host closes it. A
  // failed poll is not worth showing: the next one tries again, and only a gone group is news.
  useEffect(() => {
    if (!open) return;
    const timer = window.setInterval(() => {
      if (document.hidden) return;
      fetchView().then(setView, (error: unknown) => void settle(error));
    }, pollMs);
    return () => window.clearInterval(timer);
  }, [open, fetchView, settle, pollMs]);

  // Once it is closed, everyone in it reads the same result, which the server works out once.
  useEffect(() => {
    if (!closed || !joined || !token.current) return;
    const controller = new AbortController();
    readGroupResult(link, token.current, controller.signal).then(
      (answer) => setResult({ kind: "ready", result: answer }),
      (error: unknown) => {
        if (controller.signal.aborted || settle(error)) return;
        setResult({
          kind: "failed",
          message: error instanceof ApiError ? error.message : "Something went wrong. Try again.",
        });
      },
    );
    return () => controller.abort();
  }, [closed, joined, link, resultAttempt, settle]);

  const resultKind = result.kind;
  useEffect(() => {
    if (resultKind === "ready") resultHeadingRef.current?.focus();
  }, [resultKind]);
  const phaseKind = phase.kind;
  useEffect(() => {
    if (phaseKind === "gone" || phaseKind === "failed") headingRef.current?.focus();
  }, [phaseKind]);

  const share = async (inputs: GroupInputs) => {
    try {
      if (!token.current) {
        const joinedAs = await joinGroup(link, inputs.display_name);
        token.current = joinedAs.participant_token;
        saveCredential(link, joinedAs.participant_token);
        setView({ session: joinedAs.session, you: joinedAs.you });
      }
      setView(await shareInputs(link, token.current, inputs));
    } catch (error) {
      if (
        error instanceof ApiError &&
        (error.code === GROUP_CLOSED || error.code === GROUP_EXPIRED)
      ) {
        // The host closed it, or it ran out, while this form was open. Show what it is now.
        setAttempt((n) => n + 1);
      }
      throw error;
    }
  };

  const close = async () => {
    if (!token.current) return;
    setView(await closeGroup(link, token.current));
  };

  if (phase.kind === "loading") {
    return (
      <section className="group-page" aria-busy="true">
        <p className="progress" role="status">
          <span className="spinner" aria-hidden="true" />
          Opening the group…
        </p>
      </section>
    );
  }

  if (phase.kind === "gone") {
    const expired = phase.reason === "expired";
    return (
      <section className="group-page" aria-labelledby="group-gone-heading">
        <h2 id="group-gone-heading" ref={headingRef} tabIndex={-1} className="page-title">
          {expired ? "This group has expired" : "We can't find this group"}
        </h2>
        <p className="lead">
          {expired
            ? "Groups, and everything people shared in them, are deleted when they expire. Ask the host to start a new one."
            : "The link may be mistyped, or the group may have been deleted. Check the link, or ask the host to send it again."}
        </p>
        <div>
          <button type="button" className="button button-primary" onClick={onLeave}>
            Start a new group
          </button>
        </div>
      </section>
    );
  }

  if (phase.kind === "failed" || !view) {
    return (
      <section className="group-page" aria-labelledby="group-failed-heading">
        <h2 id="group-failed-heading" ref={headingRef} tabIndex={-1} className="page-title">
          Could not open the group
        </h2>
        <p className="notice notice-error" role="alert">
          {phase.kind === "failed" ? phase.message : "Something went wrong. Try again."}
        </p>
        <div>
          <button
            type="button"
            className="button button-primary"
            onClick={() => {
              setPhase({ kind: "loading" });
              setAttempt((n) => n + 1);
            }}
          >
            Try again
          </button>
        </div>
      </section>
    );
  }

  const { session, you } = view;
  const isHost = you?.is_host ?? false;
  const shared = session.participants.filter((p) => p.submitted).length;
  const total = session.participants.length;
  const host = session.participants.find((p) => p.is_host);

  const people = (
    <section aria-labelledby="group-people-heading" className="group-people">
      <h3 id="group-people-heading" className="section-title">
        Who is in
      </h3>
      <p className="hint" role="status">
        {shared} of {total} {total === 1 ? "person has" : "people have"} shared their answers.
      </p>
      <ul className="group-list">
        {session.participants.map((member) => (
          <Person key={`${member.name}-${member.is_host}`} member={member} you={you?.name} />
        ))}
      </ul>
    </section>
  );

  return (
    <section className="group-page" aria-labelledby="group-heading">
      <div>
        <p className="eyebrow">Eating together</p>
        <h2 id="group-heading" className="page-title">
          Where should we eat?
        </h2>
        <p className="lead">
          {host ? `${host.name} is looking for` : "Looking for"} <strong>{session.request}</strong>{" "}
          within {radiusLabel(session.radius_m)}.
          {session.expires_at &&
            ` Everything shared here is deleted ${expiryLabel(session.expires_at)}.`}
        </p>
      </div>

      {closed && (
        <p className={`notice ${you ? "notice-info" : "notice-warn"}`} role="status">
          The host has closed this group, so nobody can join or change their answers now.
          {!you &&
            " You did not join before it closed, so there is no result to show you. Ask the host."}
        </p>
      )}

      {!closed && you && <InviteLink link={link} />}

      {!closed && people}

      {!closed && (
        <section aria-labelledby="group-answers-heading" className="group-answers">
          <h3 id="group-answers-heading" className="section-title">
            {you ? "Your answers" : "Join this group"}
          </h3>
          {!you && (
            <p>
              No account needed. Tell the group what you need and what you feel like, and Makan will
              find a place everyone can live with.
            </p>
          )}
          <GroupInputsForm you={you} onSubmit={share} />
        </section>
      )}

      {!closed && you && isHost && <CloseGroup shared={shared} total={total} onClose={close} />}
      {!closed && you && !isHost && (
        <p className="notice notice-info" role="status">
          The host closes the group when everyone has answered. This page updates by itself, and the
          result appears here.
        </p>
      )}

      {closed && you && result.kind === "loading" && (
        <p className="progress" role="status">
          <span className="spinner" aria-hidden="true" />
          Makan is picking a place for the group…
        </p>
      )}
      {closed && you && result.kind === "failed" && (
        <section className="failure" role="alert" aria-labelledby="group-result-failed">
          <h3 id="group-result-failed" className="section-title">
            The result did not load
          </h3>
          <p>{result.message}</p>
          <button
            type="button"
            className="button button-secondary"
            onClick={() => {
              setResult({ kind: "loading" });
              setResultAttempt((n) => n + 1);
            }}
          >
            Try again
          </button>
        </section>
      )}
      {closed && you && result.kind === "ready" && (
        <GroupResultView ref={resultHeadingRef} result={result.result} />
      )}

      {closed && people}

      <div>
        <button type="button" className="button button-link" onClick={onLeave}>
          Start a different group
        </button>
      </div>
    </section>
  );
}

function expiryLabel(expiresAt: string): string {
  const when = new Date(expiresAt);
  if (Number.isNaN(when.getTime())) return "after a day";
  return `after ${when.toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}`;
}

function Person({ member, you }: { member: GroupMember; you: string | undefined }) {
  return (
    <li className="group-person">
      <span className="group-person-name">
        {member.name}
        {member.is_host && " (host)"}
        {you === member.name && " (you)"}
      </span>
      <span className="group-status" data-done={member.submitted}>
        {member.submitted ? "Shared" : "Waiting"}
      </span>
    </li>
  );
}

function InviteLink({ link }: { link: string }) {
  const url = groupUrl(link);
  const [note, setNote] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setNote("Link copied.");
    } catch {
      input.current?.select();
      setNote("Press Ctrl+C or long-press to copy the link.");
    }
  };
  const canShare = typeof navigator.share === "function";
  return (
    <section aria-labelledby="group-invite-heading" className="group-invite">
      <h3 id="group-invite-heading" className="section-title">
        Invite friends
      </h3>
      <p className="hint">
        Send this link. They open it in any browser, with no account, and add their own answers.
      </p>
      <div className="group-link-row">
        <input
          ref={input}
          type="text"
          readOnly
          value={url}
          aria-label="Link to share"
          onFocus={(e) => e.currentTarget.select()}
        />
        <button type="button" className="button button-secondary" onClick={() => void copy()}>
          Copy link
        </button>
        {canShare && (
          <button
            type="button"
            className="button button-secondary"
            onClick={() =>
              void navigator.share({ title: "Where should we eat?", url }).catch(() => undefined)
            }
          >
            Share…
          </button>
        )}
      </div>
      <p className="hint" role="status">
        {note}
      </p>
    </section>
  );
}

function CloseGroup({
  shared,
  total,
  onClose,
}: {
  shared: number;
  total: number;
  onClose: () => Promise<void>;
}) {
  const [confirming, setConfirming] = useState(false);
  const [working, setWorking] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const confirm = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (confirming) confirm.current?.focus();
  }, [confirming]);
  const run = async () => {
    setWorking(true);
    setFailure(null);
    try {
      await onClose();
    } catch (error) {
      setFailure(error instanceof Error ? error.message : "Something went wrong. Try again.");
      setWorking(false);
    }
  };
  const waiting = total - shared;
  return (
    <section aria-labelledby="group-close-heading" className="group-close">
      <h3 id="group-close-heading" className="section-title">
        Close the group
      </h3>
      <p>
        Closing stops anyone from joining or changing their answers, then Makan picks the place.
        {waiting > 0 &&
          ` ${waiting} ${waiting === 1 ? "person has" : "people have"} not answered yet, and ${waiting === 1 ? "they" : "those"} will count with no preferences.`}
      </p>
      {confirming ? (
        <div className="group-actions" role="group" aria-label="Confirm closing the group">
          <button
            ref={confirm}
            type="button"
            className="button button-primary"
            disabled={working}
            onClick={() => void run()}
          >
            {working ? "Closing…" : "Yes, close it and pick"}
          </button>
          <button
            type="button"
            className="button"
            disabled={working}
            onClick={() => setConfirming(false)}
          >
            Keep it open
          </button>
        </div>
      ) : (
        <div>
          <button
            type="button"
            className="button button-secondary"
            onClick={() => setConfirming(true)}
          >
            Close the group and pick
          </button>
        </div>
      )}
      {failure && (
        <p className="notice notice-error" role="alert">
          {failure}
        </p>
      )}
    </section>
  );
}
