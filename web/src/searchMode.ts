import type { SearchMode } from "./types";

const KEY = "makan.search-mode";

export const DEFAULT_MODE: SearchMode = "recommend";

export const MODE_COPY: Record<SearchMode, { label: string; hint: string }> = {
  recommend: {
    label: "Pick for me",
    hint: "Say what you feel like and get one suggestion.",
  },
  browse: {
    label: "Browse nearby",
    hint: "Look around at the places nearest you.",
  },
};

/** The mode this device chose last. Storage can be missing or blocked, so any failure is the default. */
export function loadMode(): SearchMode {
  try {
    const stored = window.localStorage.getItem(KEY);
    return stored === "recommend" || stored === "browse" ? stored : DEFAULT_MODE;
  } catch {
    return DEFAULT_MODE;
  }
}

export function saveMode(mode: SearchMode): void {
  try {
    window.localStorage.setItem(KEY, mode);
  } catch {
    // The choice then lasts only for this visit.
  }
}
