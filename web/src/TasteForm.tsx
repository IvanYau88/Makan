import { useId, useState } from "react";
import type { FormEvent } from "react";
import { toTaste, validateTaste, valuesFrom } from "./tasteForm";
import type { TasteErrors, TasteValues } from "./tasteForm";
import type { Taste } from "./types";

interface Props {
  taste: Taste;
  /** Save the lists. Resolves with what the server stored. Rejects with a message to show. */
  onSave: (taste: Taste) => Promise<Taste>;
}

interface Row {
  key: keyof TasteValues;
  label: string;
  hint: string;
  placeholder: string;
}

const SOFT: Row[] = [
  {
    key: "likes",
    label: "Cuisines you like",
    hint: "Separated by commas. Makan ranks these higher.",
    placeholder: "e.g. thai, ramen",
  },
  {
    key: "dislikes",
    label: "Cuisines you'd rather skip",
    hint: "Makan ranks these lower, but may still suggest one when nothing else fits.",
    placeholder: "e.g. burgers",
  },
];

const HARD: Row[] = [
  {
    key: "never_places",
    label: "Places you won't go to",
    hint: "Makan never suggests a place with one of these names.",
    placeholder: "e.g. pizza hut",
  },
  {
    key: "allergies",
    label: "Allergies",
    hint: "Makan cannot check menus, so it reminds you to check each time instead of ruling places out.",
    placeholder: "e.g. peanut, shellfish",
  },
  {
    key: "diets",
    label: "Diets",
    hint: "A reminder every time too, since Makan cannot check menus.",
    placeholder: "e.g. halal, vegetarian",
  },
];

/** What Makan should remember about the person's food. Everything here is optional. */
export function TasteForm({ taste, onSave }: Props) {
  const [values, setValues] = useState<TasteValues>(() => valuesFrom(taste));
  const [errors, setErrors] = useState<TasteErrors>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [working, setWorking] = useState(false);
  const id = useId();

  const set = (patch: Partial<TasteValues>) => {
    setValues((current) => ({ ...current, ...patch }));
    setSaved(false);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (working) return;
    setFailure(null);
    setSaved(false);
    const found = validateTaste(values);
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    setWorking(true);
    try {
      const stored = await onSave(toTaste(values));
      setValues(valuesFrom(stored));
      setSaved(true);
    } catch (e) {
      setFailure(e instanceof Error ? e.message : "Something went wrong. Try again.");
    } finally {
      setWorking(false);
    }
  };

  const field = (row: Row) => {
    const inputId = `${id}-${row.key}`;
    const error = errors[row.key];
    return (
      <div className="field" key={row.key}>
        <label htmlFor={inputId}>{row.label}</label>
        <input
          id={inputId}
          type="text"
          value={values[row.key]}
          placeholder={row.placeholder}
          autoComplete="off"
          aria-invalid={error ? true : undefined}
          aria-describedby={`${inputId}-hint${error ? ` ${inputId}-error` : ""}`}
          onChange={(e) => set({ [row.key]: e.target.value })}
        />
        <p className="hint" id={`${inputId}-hint`}>
          {row.hint}
        </p>
        {error && (
          <p className="field-error" id={`${inputId}-error`}>
            {error}
          </p>
        )}
      </div>
    );
  };

  return (
    <form className="account-form" onSubmit={(e) => void submit(e)} noValidate>
      <fieldset className="group-fieldset">
        <legend>What you feel like</legend>
        {SOFT.map(field)}
      </fieldset>
      <fieldset className="group-fieldset">
        <legend>What you never want</legend>
        {HARD.map(field)}
        <p className="hint">
          These never fade with time. Remove one here when it stops being true.
        </p>
      </fieldset>

      {failure && (
        <p className="notice notice-error" role="alert">
          {failure}
        </p>
      )}
      <div className="group-actions">
        <button type="submit" className="button button-primary button-stable" disabled={working}>
          <span className="button-state" aria-hidden={working || undefined}>
            Save my taste
          </span>
          <span className="button-state button-state-busy" aria-hidden={!working || undefined}>
            <span className="spinner" aria-hidden="true" />
            Saving…
          </span>
        </button>
        <span className="hint" role="status">
          {saved ? "Saved. Makan will use this in your next search." : ""}
        </span>
      </div>
    </form>
  );
}
