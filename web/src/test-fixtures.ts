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
  query: { latitude: 3.148, longitude: 101.695, radius_m: 1609, request: "thai please" },
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
