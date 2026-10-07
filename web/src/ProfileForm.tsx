import { useId, useRef, useState } from "react";
import type { FormEvent } from "react";
import { MAX_NAME_CHARS } from "./tasteForm";
import type { Profile } from "./types";

interface Props {
  /** The saved profile, or null while the person is making one. */
  profile: Profile | null;
  /** Save it. Rejects with a message that is safe to show. */
  onSave: (profile: Profile) => Promise<void>;
}

/** The profile: the name Makan greets the person with, and whether to keep location history. */
export function ProfileForm({ profile, onSave }: Props) {
  const [name, setName] = useState(profile?.display_name ?? "");
  const [optIn, setOptIn] = useState(profile?.location_history_opt_in ?? false);
  const [error, setError] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [working, setWorking] = useState(false);
  const nameInput = useRef<HTMLInputElement>(null);
  const id = useId();
  const creating = profile === null;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (working) return;
    setFailure(null);
    setSaved(false);
    if (name.trim() === "") {
      setError("Enter the name Makan should call you.");
      nameInput.current?.focus();
      return;
    }
    setError(null);
    setWorking(true);
    try {
      await onSave({ display_name: name, location_history_opt_in: optIn });
      setSaved(true);
    } catch (e) {
      setFailure(e instanceof Error ? e.message : "Something went wrong. Try again.");
    } finally {
      setWorking(false);
    }
  };

  return (
    <form className="account-form" onSubmit={(e) => void submit(e)} noValidate>
      <div className="field">
        <label htmlFor={`${id}-name`}>Your name</label>
        <input
          ref={nameInput}
          id={`${id}-name`}
          type="text"
          value={name}
          maxLength={MAX_NAME_CHARS}
          autoComplete="given-name"
          aria-invalid={error ? true : undefined}
          aria-describedby={`${id}-name-hint${error ? ` ${id}-name-error` : ""}`}
          onChange={(e) => {
            setName(e.target.value);
            setSaved(false);
          }}
        />
        <p className="hint" id={`${id}-name-hint`}>
          Makan greets you with it, as in “Hey {name.trim() || "Bob"}!”.
        </p>
        {error && (
          <p className="field-error" id={`${id}-name-error`}>
            {error}
          </p>
        )}
      </div>

      <label className="check-row" htmlFor={`${id}-history`}>
        <input
          id={`${id}-history`}
          type="checkbox"
          checked={optIn}
          onChange={(e) => {
            setOptIn(e.target.checked);
            setSaved(false);
          }}
        />
        <span>
          Keep a history of where I search
          <span className="hint check-hint">
            Off by default. Your location is only used for the search you run and is not saved
            unless you turn this on.
          </span>
        </span>
      </label>

      {failure && (
        <p className="notice notice-error" role="alert">
          {failure}
        </p>
      )}
      <div className="group-actions">
        <button type="submit" className="button button-primary button-stable" disabled={working}>
          <span className="button-state" aria-hidden={working || undefined}>
            {creating ? "Create profile" : "Save profile"}
          </span>
          <span className="button-state button-state-busy" aria-hidden={!working || undefined}>
            <span className="spinner" aria-hidden="true" />
            Saving…
          </span>
        </button>
        <span className="hint" role="status">
          {saved ? (creating ? "Profile created." : "Profile saved.") : ""}
        </span>
      </div>
    </form>
  );
}
