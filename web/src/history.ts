import type { Run } from "./types";

/** How many runs one tab remembers. Older runs drop off, and a reload forgets all of them. */
export const MAX_HISTORY = 10;

export interface HistoryEntry {
  /** Local to this tab: one per search, there before the server names the run. */
  key: number;
  run: Run;
  /** The page stopped following this run (a newer search began), so it may have finished unseen. */
  abandoned: boolean;
}

/** Add or update the entry for a search, newest first, keeping at most `MAX_HISTORY`. */
export function recordRun(history: HistoryEntry[], key: number, run: Run): HistoryEntry[] {
  const existing = history.find((e) => e.key === key);
  const next = existing
    ? history.map((e) => (e.key === key ? { ...e, run } : e))
    : [{ key, run, abandoned: false }, ...history];
  return next.slice(0, MAX_HISTORY);
}

/** Mark a run the page gave up following. A run that already ended is left as it is. */
export function abandonRun(history: HistoryEntry[], key: number): HistoryEntry[] {
  return history.map((e) =>
    e.key === key && e.run.status === "running" ? { ...e, abandoned: true } : e,
  );
}
