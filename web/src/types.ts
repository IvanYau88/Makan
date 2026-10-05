/** The JSON the backend returns from POST /api/recommendations. */

export interface Place {
  id: string;
  name: string;
  category: string;
  distance_m: number;
  address: string | null;
  reasons: string[];
}

export interface StaleFact {
  id: string;
  kind: string;
  content: unknown;
  reason: string | null;
}

export interface Recommendation {
  pick: Place | null;
  runners_up: Place[];
  explanation: string;
  warnings: string[];
  stale_facts: StaleFact[];
  data_source: string;
  attribution: string | null;
  partial: boolean;
  mode: Mode;
}

export type Mode = "demo" | "live";

export interface Coordinates {
  latitude: number;
  longitude: number;
}

export interface RecommendRequest extends Coordinates {
  request: string;
  radius_m: number;
}
