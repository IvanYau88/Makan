import type { Outcome, Run, Stage, StageName, StageStatus } from "./types";

/** What each real stage of the solo graph does, in words for the inspector. */
export const STAGE_INFO: Record<StageName, { title: string; summary: string }> = {
  classify: {
    title: "Classify",
    summary:
      "One model call reads the request and answers with a cuisine, a venue category, and any requirements places data cannot check.",
  },
  intent: {
    title: "Intent",
    summary:
      "Checks that the classification has the required shape. Nothing searches places until this passes.",
  },
  requested_places: {
    title: "Requested places",
    summary:
      "A direct call to the places tool, with no model in between, using the cuisine and category filters.",
  },
  nearby_places: {
    title: "Nearby places",
    summary:
      "The same tool call without filters, in parallel, so nearby alternatives are available too.",
  },
  memory: {
    title: "Memory",
    summary:
      "Looks up stored tastes for signed-in users. Guests have none, so nothing is looked up.",
  },
  merge: {
    title: "Merge",
    summary:
      "Joins both searches into one deduplicated list and records what could not be verified. One failed search still leaves candidates.",
  },
  rank: {
    title: "Rank",
    summary:
      "Deterministic order: request fit, stored taste, distance, then name. No model writes a score.",
  },
  explain: {
    title: "Explain",
    summary: "Deterministic wording for the pick, runners-up, and the data caveats.",
  },
};

export const STAGE_ORDER: StageName[] = [
  "classify",
  "intent",
  "requested_places",
  "nearby_places",
  "memory",
  "merge",
  "rank",
  "explain",
];

/** The graph as it is wired, for the page before any run exists. */
export const STAGE_AFTER: Record<StageName, StageName[]> = {
  classify: [],
  intent: ["classify"],
  requested_places: ["intent"],
  nearby_places: ["intent"],
  memory: ["intent"],
  merge: ["intent", "requested_places", "nearby_places", "memory"],
  rank: ["merge"],
  explain: ["rank"],
};

/** A column and row for each stage in the dependency picture. */
export const STAGE_POSITION: Record<StageName, { col: number; row: number }> = {
  classify: { col: 0, row: 1 },
  intent: { col: 1, row: 1 },
  requested_places: { col: 2, row: 0 },
  nearby_places: { col: 2, row: 1 },
  memory: { col: 2, row: 2 },
  merge: { col: 3, row: 1 },
  rank: { col: 4, row: 1 },
  explain: { col: 5, row: 1 },
};

export const STATUS_LABEL: Record<StageStatus, string> = {
  waiting: "waiting for stages before it",
  queued: "ready, waiting for a free slot",
  running: "running",
  ok: "ok",
  error: "error",
  timeout: "timed out",
  skipped: "skipped",
};

export const OUTCOME_LABEL: Record<Outcome, string> = {
  complete: "Complete recommendation",
  partial: "Partial recommendation",
  no_result: "No places found",
  failed: "No recommendation",
};

/** What the person got, in words that fit the search: a list is not a recommendation. */
export function outcomeLabel(run: Run): string {
  if (!run.outcome) return "In progress";
  if (run.search_mode === "browse") {
    return run.outcome === "complete"
      ? "Places listed"
      : run.outcome === "no_result"
        ? "No places found"
        : "No places listed";
  }
  return OUTCOME_LABEL[run.outcome];
}

/** A blueprint run with every stage not started, for the empty inspector. */
export function blueprintStages(): Stage[] {
  return STAGE_ORDER.map((name) => ({
    name,
    after: STAGE_AFTER[name],
    status: "waiting",
    timeout_s: 0,
    started_ms: null,
    duration_ms: null,
    parallel_with: [],
    input: null,
    output: null,
    error: null,
    calls: [],
  }));
}

const PHASE: Record<StageName, string> = {
  classify: "Reading your request",
  intent: "Checking the classification",
  requested_places: "Searching nearby places",
  nearby_places: "Searching nearby places",
  memory: "Checking memory",
  merge: "Combining results",
  rank: "Ranking places",
  explain: "Writing the explanation",
};

/** What the run is doing now, from its stages: the real names, and a count, not a percentage. */
export function phaseText(run: Run | null): string {
  if (!run || run.stages.length === 0) return "Starting the search…";
  const active = run.stages.filter((s) => s.status === "running");
  const done = run.stages.filter(
    (s) => s.status === "ok" || s.status === "error" || s.status === "timeout",
  );
  // A skipped stage is no work to wait for, so it is not part of the count.
  const total = run.stages.filter((s) => s.status !== "skipped").length;
  const first = active[0];
  const label = first ? PHASE[first.name] : "Working";
  return `${label} (${done.length} of ${total} ${total === 1 ? "stage" : "stages"} done)`;
}

/** Plain words for why the run ended the way it did, keeping graph status and outcome apart. */
export function runSummary(run: Run): string {
  const failed = run.stages.filter((s) => s.status === "error" || s.status === "timeout");
  const browsing = run.search_mode === "browse";
  if (run.status === "running") return phaseText(run);
  if (run.outcome === "partial") {
    return `${failed.map((s) => STAGE_INFO[s.name].title).join(" and ")} did not finish, so the graph is marked failed. The results that remained are still usable.`;
  }
  if (run.outcome === "failed") {
    return `${failed.map((s) => STAGE_INFO[s.name].title).join(", ")} did not finish, so there was ${browsing ? "nothing to show" : "nothing to recommend"}.`;
  }
  if (browsing) {
    const skipped = run.stages.filter((s) => s.status === "skipped").length;
    const ran = `Browse nearby ran only the places search. The other ${skipped} stages were skipped, and no model was called.`;
    return run.outcome === "no_result" ? `${ran} No place was within the radius.` : ran;
  }
  if (run.outcome === "no_result")
    return "Every stage finished, and no place was within the radius.";
  return "Every stage finished.";
}
