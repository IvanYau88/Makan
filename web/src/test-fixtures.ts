import type { Place, Recommendation, Run, Stage, StageName } from "./types";

export function place(name: string, distance_m: number, extra: Partial<Place> = {}): Place {
  return {
    id: name.toLowerCase().replace(/ /g, "-"),
    name,
    category: "thai_restaurant",
    distance_m,
    address: `${distance_m} Test Street`,
    lat: 3.148 + distance_m / 1_000_000,
    lon: 101.695,
    rank: 1,
    matched: true,
    reasons: [
      `${(distance_m / 1609.344).toFixed(1)} mi from your approximate location`,
      "matches your request for thai",
    ],
    ...extra,
  };
}

const MID = place("Mid Thai", 140, { rank: 1 });
const FAR = place("Far Thai", 760, { rank: 2 });
const NEAR = place("Near Ramen", 90, {
  rank: 3,
  category: "ramen_restaurant",
  matched: false,
  reasons: [
    "0.1 mi from your approximate location",
    "nearby alternative; your request is not confirmed for this place",
  ],
});

const STAGES: Record<StageName, StageName[]> = {
  classify: [],
  intent: ["classify"],
  requested_places: ["intent"],
  nearby_places: ["intent"],
  memory: ["intent"],
  merge: ["intent", "requested_places", "nearby_places", "memory"],
  rank: ["merge"],
  explain: ["rank"],
};

export function stage(name: StageName, extra: Partial<Stage> = {}): Stage {
  return {
    name,
    after: STAGES[name],
    status: "ok",
    timeout_s: 30,
    started_ms: 5,
    duration_ms: 12,
    parallel_with: [],
    input: { note: `${name} input` },
    output: { note: `${name} output` },
    error: null,
    calls: [],
    ...extra,
  };
}

export function run(extra: Partial<Run> = {}, stages?: Stage[]): Run {
  return {
    run_id: "run-1",
    graph: "solo_recommendation",
    status: "ok",
    graph_ok: true,
    outcome: "complete",
    started_at: "2026-10-05T10:00:00+00:00",
    ended_at: "2026-10-05T10:00:01+00:00",
    duration_ms: 420,
    mode: "live",
    search_mode: "recommend",
    model: "test/model",
    request: "thai please",
    center: { latitude: 3.148, longitude: 101.695 },
    radius_m: 1609,
    data_source: "overture:2026-09-23.1",
    limits: { max_concurrency: 4, step_timeout_s: 30, max_iterations: 10, token_budget: 50000 },
    stages: stages ?? (Object.keys(STAGES) as StageName[]).map((name) => stage(name)),
    ...extra,
  };
}

export const RESULT: Recommendation = {
  query: {
    mode: "recommend",
    latitude: 3.148,
    longitude: 101.695,
    radius_m: 1609,
    request: "thai please",
  },
  intent: { cuisine: "thai", category: null },
  pick: MID,
  runners_up: [FAR, NEAR],
  places: [MID, FAR, NEAR],
  candidate_count: 3,
  truncated: false,
  explanation: "Try Mid Thai.",
  warnings: [
    "Opening hours, menus, prices, and public reviews are unavailable; verify before going.",
  ],
  stale_facts: [],
  data_source: "overture:2026-09-23.1",
  attribution: "Places data: Overture Maps Foundation (CDLA Permissive 2.0).",
  partial: false,
  mode: "live",
  run: run(),
};

const BROWSE_PLACES: Place[] = [
  place("Near Ramen", 90, {
    rank: 1,
    category: "ramen_restaurant",
    matched: false,
    reasons: ["0.1 mi from your approximate location"],
  }),
  place("Mid Thai", 140, {
    rank: 2,
    matched: false,
    reasons: ["0.1 mi from your approximate location"],
  }),
  place("Far Thai", 760, {
    rank: 3,
    matched: false,
    reasons: ["0.5 mi from your approximate location"],
  }),
];

/** A browse run: only the places search ran, and the other seven stages were skipped. */
export function browseRun(extra: Partial<Run> = {}): Run {
  return run(
    {
      graph: "browse_nearby",
      search_mode: "browse",
      request: null,
      ...extra,
    },
    (Object.keys(STAGES) as StageName[]).map((name) =>
      name === "nearby_places"
        ? stage(name, { after: [] })
        : stage(name, {
            status: "skipped",
            started_ms: null,
            duration_ms: null,
            input: null,
            output: null,
          }),
    ),
  );
}

/** What Browse nearby returns: no pick, no intent, and the places nearest first. */
export const BROWSE_RESULT: Recommendation = {
  ...RESULT,
  query: {
    mode: "browse",
    latitude: 3.148,
    longitude: 101.695,
    radius_m: 1609,
    request: null,
  },
  intent: null,
  pick: null,
  runners_up: [],
  places: BROWSE_PLACES,
  explanation: "The places nearest to you come first. Makan made no recommendation.",
  run: browseRun(),
};
