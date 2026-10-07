import { MAX_ITEMS, MAX_TERM_CHARS, splitList } from "./groupForm";
import type { Taste } from "./types";

export const MAX_NAME_CHARS = 40;

/** One text box per list, with the items separated by commas. */
export interface TasteValues {
  likes: string;
  dislikes: string;
  allergies: string;
  diets: string;
  never_places: string;
}

export type TasteErrors = Partial<Record<keyof TasteValues, string>>;

const LABELS: Record<keyof TasteValues, string> = {
  likes: "cuisines you like",
  dislikes: "cuisines you would rather skip",
  allergies: "allergies",
  diets: "diets",
  never_places: "places you will not go to",
};

export function valuesFrom(taste: Taste): TasteValues {
  return {
    likes: taste.likes.join(", "),
    dislikes: taste.dislikes.join(", "),
    allergies: taste.allergies.join(", "),
    diets: taste.diets.join(", "),
    never_places: taste.never_places.join(", "),
  };
}

export function toTaste(values: TasteValues): Taste {
  return {
    likes: splitList(values.likes),
    dislikes: splitList(values.dislikes),
    allergies: splitList(values.allergies),
    diets: splitList(values.diets),
    never_places: splitList(values.never_places),
  };
}

/** The same rules the server applies, so the person hears about a mistake before sending. */
export function validateTaste(values: TasteValues): TasteErrors {
  const taste = toTaste(values);
  const errors: TasteErrors = {};
  for (const key of Object.keys(LABELS) as (keyof TasteValues)[]) {
    const items = taste[key];
    if (items.length > MAX_ITEMS) {
      errors[key] = `Use at most ${MAX_ITEMS} entries in ${LABELS[key]}.`;
    } else if (items.some((item) => item.length > MAX_TERM_CHARS)) {
      errors[key] = `Keep each entry in ${LABELS[key]} to ${MAX_TERM_CHARS} characters.`;
    } else if (items.some((item) => !/[\p{L}\p{N}]/u.test(item))) {
      errors[key] = `Each entry in ${LABELS[key]} needs a letter or a number.`;
    }
  }
  const liked = new Set(taste.likes.map((item) => item.toLowerCase()));
  const both = taste.dislikes.filter((item) => liked.has(item.toLowerCase()));
  if (both.length > 0 && !errors.dislikes) {
    errors.dislikes = `${both.join(", ")} cannot be both liked and skipped.`;
  }
  return errors;
}
