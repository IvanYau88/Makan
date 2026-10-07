/** The JSON the backend returns from /api/config and /api/recommendations. */

export interface Place {
  id: string;
  name: string;
  category: string;
  distance_m: number;
  address: string | null;
  /** The venue's own coordinates, null when the data source gave none. */
  lat: number | null;
  lon: number | null;
  /** Stable position in the server's ranking, from 1. Pins and rows share it. */
  rank: number;
  /** True when the place matched what the request asked for, not just being nearby. */
  matched: boolean;
  reasons: string[];
}

export interface StaleFact {
  id: string;
  kind: string;
  content: unknown;
  reason: string | null;
}

/** What a search is for: a suggestion (`recommend`), or just the places nearby (`browse`). */
export type SearchMode = "recommend" | "browse";

export interface SearchQuery extends Coordinates {
  mode: SearchMode;
  radius_m: number;
  /** What the person asked for. Null when browsing, which takes no request. */
  request: string | null;
}

export interface Recommendation {
  /** The search as it ran: the rounded center and the radius, not what was typed. */
  query: SearchQuery;
  intent: { cuisine: string | null; category: string | null } | null;
  pick: Place | null;
  runners_up: Place[];
  /** Every ranked candidate, pick included. Bounded: see `truncated`. */
  places: Place[];
  candidate_count: number;
  /** A search hit its limit, so more places may lie within the radius. */
  truncated: boolean;
  explanation: string;
  /** "Hey Bob!" for a signed-in person who has a profile, null for a guest. */
  greeting: string | null;
  warnings: string[];
  stale_facts: StaleFact[];
  data_source: string;
  attribution: string | null;
  partial: boolean;
  mode: Mode;
  run: Run | null;
}

export type Mode = "demo" | "live";

export interface MapConfig {
  tile_url: string;
  attribution: string;
  attribution_url: string | null;
}

/** What the page needs to talk to Supabase Auth. The anon key is public by design. */
export interface AuthSettings {
  url: string;
  anon_key: string;
  /** Where the confirmation email sends the person, or null for this page's own address. */
  redirect_url: string | null;
}

export interface AppConfig {
  mode: Mode;
  map: MapConfig;
  /** Null when the server has no accounts, so everyone is a guest and there is no Account page. */
  auth: AuthSettings | null;
}

export interface Profile {
  display_name: string;
  location_history_opt_in: boolean;
}

/** The lists on the taste form. The soft "skip" is `dislikes`, and the hard "never" is the rest. */
export interface Taste {
  likes: string[];
  dislikes: string[];
  allergies: string[];
  diets: string[];
  never_places: string[];
}

export interface Me {
  user: { id: string; email: string | null };
  /** Null until the person has made one: a name is required to create it. */
  profile: Profile | null;
  taste: Taste;
}

export interface Coordinates {
  latitude: number;
  longitude: number;
}

/** The request body. A recommendation needs `request`, and browsing nearby sends none. */
export interface RecommendRequest extends Coordinates {
  mode: SearchMode;
  request?: string;
  radius_m: number;
}

export type StageName =
  | "classify"
  | "intent"
  | "requested_places"
  | "nearby_places"
  | "memory"
  /** Only in a graph built with a scorer backend: it starts the run beside the classification. */
  | "signals"
  | "merge"
  | "rank"
  | "explain";

export type StageStatus =
  | "waiting"
  | "queued"
  | "running"
  | "ok"
  | "error"
  | "timeout"
  /** This search does not need the stage, so it never ran. */
  | "skipped";

export type Outcome = "complete" | "partial" | "no_result" | "failed";

export interface StageCall {
  kind: "model" | "tool";
  status?: string;
  name?: string;
  arguments?: Record<string, unknown>;
  ok?: boolean | null;
  duration_ms?: number | null;
  model_calls?: { iteration: number; duration_ms: number; tokens: number }[];
  iterations?: number;
}

export interface Stage {
  name: StageName;
  after: StageName[];
  status: StageStatus;
  timeout_s: number;
  /** Milliseconds from the start of the run to the start of this stage. */
  started_ms: number | null;
  duration_ms: number | null;
  parallel_with: StageName[];
  input: Record<string, unknown> | null;
  output: Record<string, unknown> | null;
  error: { type: string; message: string } | null;
  calls: StageCall[];
}

/** A public, bounded record of one run of the solo recommendation graph. */
export interface Run {
  run_id: string | null;
  graph: string | null;
  /** The graph's own status: `ok` or `failed` once it ended, `running` before. */
  status: "running" | "ok" | "failed";
  graph_ok?: boolean | null;
  /** What the person got. A failed graph can still carry a usable recommendation. */
  outcome: Outcome | null;
  started_at?: string;
  ended_at?: string | null;
  duration_ms?: number | null;
  mode: Mode;
  search_mode: SearchMode;
  model: string;
  /** Null for a browse run, which has no request. */
  request: string | null;
  center: Coordinates;
  radius_m: number;
  data_source: string;
  limits: {
    max_concurrency: number;
    step_timeout_s: number;
    max_iterations: number;
    token_budget: number;
  };
  stages: Stage[];
}

/** The JSON of /api/groups. A reader sees who is in and who has shared, never what they shared. */

export interface GroupMember {
  name: string;
  is_host: boolean;
  submitted: boolean;
}

export interface GroupSession {
  request: string;
  latitude: number;
  longitude: number;
  radius_m: number;
  created_at: string;
  /** Null when the session never expires. */
  expires_at: string | null;
  closed: boolean;
  participants: GroupMember[];
}

export interface GroupConstraints {
  refuses: string[];
  allergies: string[];
  diets: string[];
  budget: string | null;
}

export interface GroupPreferences {
  likes: string[];
  dislikes: string[];
}

/** The caller's own row, sent only when they present their participant token. */
export interface GroupYou {
  name: string;
  is_host: boolean;
  submitted: boolean;
  constraints: Partial<GroupConstraints>;
  preferences: Partial<GroupPreferences>;
}

export interface GroupView {
  session: GroupSession;
  you: GroupYou | null;
}

/** What creating or joining adds: the credential for this person, which only they receive. */
export interface GroupCredential extends GroupView {
  participant_token: string;
}

export interface GroupCreated extends GroupCredential {
  link_token: string;
}

export interface GroupInputs {
  display_name?: string;
  constraints: GroupConstraints;
  preferences: GroupPreferences;
}

export interface GroupCreateRequest extends Coordinates {
  request: string;
  radius_m: number;
  display_name?: string;
}

export interface GroupOption {
  id: string;
  name: string;
  category: string;
  distance_m: number;
  address: string | null;
  reasons: string[];
  /** Null when nobody shared a taste, so nothing was scored. */
  lowest_score: number | null;
  average_score: number | null;
  /** Who scored it lowest: the host's result only. */
  lowest_scorers?: string[];
  warnings: string[];
}

export interface GroupExcluded {
  id: string;
  name: string;
  category: string;
  /** Who refuses what: the host's result only. */
  refusals?: { person: string; term: string }[];
  /** What was refused, with no name: everyone else's result. */
  refused_terms?: string[];
}

/** The closed group's result. The host's names a person next to what they shared, the rest do not. */
export interface GroupResult {
  audience: "host" | "member";
  pick: GroupOption | null;
  runners_up: GroupOption[];
  excluded: GroupExcluded[];
  explanation: string;
  warnings: string[];
  participant_count: number;
  pending: string[];
  data_source: string;
  attribution: string | null;
  partial: boolean;
  mode: Mode;
}
