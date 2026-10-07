import type { GroupConstraints, GroupInputs, GroupPreferences, GroupYou } from "./types";

/** The most the backend takes: keep in step with the limits in makan/consensus.py. */
export const MAX_ITEMS = 20;
export const MAX_TERM_CHARS = 40;
export const MAX_NOTE_CHARS = 80;
export const MAX_NAME_CHARS = 40;

/** One text box per list, with the items separated by commas. */
export interface InputValues {
  name: string;
  refuses: string;
  allergies: string;
  diets: string;
  budget: string;
  likes: string;
  dislikes: string;
}

export const EMPTY_INPUTS: InputValues = {
  name: "",
  refuses: "",
  allergies: "",
  diets: "",
  budget: "",
  likes: "",
  dislikes: "",
};

export type InputErrors = Partial<Record<keyof InputValues, string>>;

/** The items of a comma or line separated list, trimmed, with blanks and repeats dropped. */
export function splitList(text: string): string[] {
  const seen = new Set<string>();
  const items: string[] = [];
  for (const part of text.split(/[,\n]/)) {
    const item = part.trim().replace(/\s+/g, " ");
    if (item && !seen.has(item.toLowerCase())) {
      seen.add(item.toLowerCase());
      items.push(item);
    }
  }
  return items;
}

const joinList = (items: readonly string[] | undefined) => (items ?? []).join(", ");

/**
 * What a person already shared, as form values, so changing an answer starts from it. The name
 * stays empty: the server shows a stand-in such as "Guest 2" when none was given, and sending that
 * back would make it the person's chosen name. An empty name leaves the current one alone.
 */
export function valuesFrom(you: GroupYou | null): InputValues {
  if (!you) return EMPTY_INPUTS;
  return {
    name: "",
    refuses: joinList(you.constraints.refuses),
    allergies: joinList(you.constraints.allergies),
    diets: joinList(you.constraints.diets),
    budget: you.constraints.budget ?? "",
    likes: joinList(you.preferences.likes),
    dislikes: joinList(you.preferences.dislikes),
  };
}

const LISTS: { key: keyof InputValues; label: string; max: number }[] = [
  { key: "refuses", label: "Places you won't go to", max: MAX_TERM_CHARS },
  { key: "allergies", label: "Allergies", max: MAX_NOTE_CHARS },
  { key: "diets", label: "Dietary needs", max: MAX_NOTE_CHARS },
  { key: "likes", label: "Foods you like", max: MAX_TERM_CHARS },
  { key: "dislikes", label: "Foods you'd rather skip", max: MAX_TERM_CHARS },
];

/** What is wrong with the form, before it is sent. The backend checks the same limits. */
export function validateInputs(values: InputValues): InputErrors {
  const errors: InputErrors = {};
  if (values.name.trim().length > MAX_NAME_CHARS) {
    errors.name = `Use ${MAX_NAME_CHARS} characters or fewer.`;
  }
  for (const { key, label, max } of LISTS) {
    const items = splitList(values[key]);
    if (items.length > MAX_ITEMS) errors[key] = `${label}: list at most ${MAX_ITEMS}.`;
    else if (items.some((item) => item.length > max)) {
      errors[key] = `${label}: keep each one to ${max} characters or fewer.`;
    }
  }
  if (values.budget.trim().length > MAX_NOTE_CHARS) {
    errors.budget = `Use ${MAX_NOTE_CHARS} characters or fewer.`;
  }
  return errors;
}

/** The request body for the form. Every key is sent, so "nothing to add" still counts as shared. */
export function toInputs(values: InputValues): GroupInputs {
  const constraints: GroupConstraints = {
    refuses: splitList(values.refuses),
    allergies: splitList(values.allergies),
    diets: splitList(values.diets),
    budget: values.budget.trim() || null,
  };
  const preferences: GroupPreferences = {
    likes: splitList(values.likes),
    dislikes: splitList(values.dislikes),
  };
  const name = values.name.trim();
  return { ...(name && { display_name: name }), constraints, preferences };
}
