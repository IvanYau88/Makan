import { useState } from "react";
import type { FormEvent } from "react";
import { EMPTY_INPUTS, MAX_NAME_CHARS, toInputs, validateInputs, valuesFrom } from "./groupForm";
import type { InputErrors, InputValues } from "./groupForm";
import type { GroupInputs, GroupYou } from "./types";

interface Props {
  /** What this person already shared, when they have joined. */
  you: GroupYou | null;
  /** Send the answers. Rejects with the message to show when it fails. */
  onSubmit: (inputs: GroupInputs) => Promise<void>;
}

interface Row {
  key: Exclude<keyof InputValues, "name">;
  label: string;
  hint: string;
  placeholder: string;
}

const HARD: Row[] = [
  {
    key: "refuses",
    label: "Places you won't go to",
    hint: "Kinds of place or cuisine, separated by commas. Makan leaves these out completely.",
    placeholder: "e.g. seafood, steakhouse",
  },
  {
    key: "allergies",
    label: "Allergies",
    hint: "Makan cannot check menus, so it reminds the group to check instead of ruling places out.",
    placeholder: "e.g. peanut, shellfish",
  },
  {
    key: "diets",
    label: "Dietary needs",
    hint: "A reminder for the group, since Makan cannot check menus.",
    placeholder: "e.g. halal, vegetarian",
  },
  {
    key: "budget",
    label: "Budget",
    hint: "A reminder for the group, since Makan does not know prices.",
    placeholder: "e.g. under $15 each",
  },
];

const TASTES: Row[] = [
  {
    key: "likes",
    label: "Foods you like",
    hint: "Cuisines or kinds of place, separated by commas. These count toward the pick.",
    placeholder: "e.g. thai, ramen",
  },
  {
    key: "dislikes",
    label: "Foods you'd rather skip",
    hint: "These count against a place, but do not rule it out.",
    placeholder: "e.g. burgers",
  },
];

/** What one person tells the group: a name, what they cannot have, and what they like. */
export function GroupInputsForm({ you, onSubmit }: Props) {
  const [values, setValues] = useState<InputValues>(() => (you ? valuesFrom(you) : EMPTY_INPUTS));
  const [errors, setErrors] = useState<InputErrors>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [saved, setSaved] = useState(false);
  const set = (patch: Partial<InputValues>) => {
    setValues((current) => ({ ...current, ...patch }));
    setSaved(false);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (working) return;
    const found = validateInputs(values);
    setErrors(found);
    setFailure(null);
    if (Object.keys(found).length > 0) return;
    setWorking(true);
    try {
      await onSubmit(toInputs(values));
      setSaved(true);
    } catch (error) {
      setFailure(error instanceof Error ? error.message : "Something went wrong. Try again.");
    } finally {
      setWorking(false);
    }
  };

  const field = (row: Row) => {
    const id = `group-${row.key}`;
    const describedBy = [`${id}-hint`, errors[row.key] ? `${id}-error` : null]
      .filter(Boolean)
      .join(" ");
    return (
      <div className="field" key={row.key}>
        <label htmlFor={id}>{row.label}</label>
        <input
          id={id}
          type="text"
          value={values[row.key]}
          placeholder={row.placeholder}
          autoComplete="off"
          aria-invalid={errors[row.key] ? true : undefined}
          aria-describedby={describedBy}
          onChange={(e) => set({ [row.key]: e.target.value })}
        />
        <p className="hint" id={`${id}-hint`}>
          {row.hint}
        </p>
        {errors[row.key] && (
          <p className="field-error" id={`${id}-error`}>
            {errors[row.key]}
          </p>
        )}
      </div>
    );
  };

  return (
    <form className="group-form" onSubmit={(e) => void submit(e)} noValidate>
      <div className="field">
        <label htmlFor="group-name">Your name</label>
        <input
          id="group-name"
          type="text"
          value={values.name}
          maxLength={MAX_NAME_CHARS}
          placeholder={you ? `Shown as ${you.name}` : "So the group can see you have answered"}
          autoComplete="given-name"
          aria-invalid={errors.name ? true : undefined}
          aria-describedby={errors.name ? "group-name-error" : undefined}
          onChange={(e) => set({ name: e.target.value })}
        />
        {errors.name && (
          <p className="field-error" id="group-name-error">
            {errors.name}
          </p>
        )}
      </div>

      <fieldset className="group-fieldset">
        <legend>What you need</legend>
        {HARD.map(field)}
      </fieldset>

      <fieldset className="group-fieldset">
        <legend>What you feel like</legend>
        {TASTES.map(field)}
      </fieldset>

      <p className="hint">
        Only the group's host sees who said what. Everyone else sees the pick, without names beside
        anyone's needs. Leave a box empty if it does not apply to you.
      </p>

      {failure && (
        <p className="notice notice-error" role="alert">
          {failure}
        </p>
      )}
      <div className="group-actions">
        <button type="submit" className="button button-primary button-stable" disabled={working}>
          <span className="button-state" aria-hidden={working || undefined}>
            {you?.submitted ? "Update my answers" : "Share my answers"}
          </span>
          <span className="button-state button-state-busy" aria-hidden={!working || undefined}>
            <span className="spinner" aria-hidden="true" />
            Sending…
          </span>
        </button>
        <span className="hint" role="status">
          {saved ? "Shared with the group." : ""}
        </span>
      </div>
    </form>
  );
}
