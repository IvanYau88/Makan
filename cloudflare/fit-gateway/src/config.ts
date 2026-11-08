// Changes require review of pricing, qualification and cache compatibility.
export const POLICY = {
  version: "fit-gateway-v1",
  reasonsVersion: 2,
  // Frozen state contracts: version 1 (guest cuisines) and version 2 (owner history).
  // Each manifest selects its own state schema and cache namespace; nothing else is served.
  manifests: [
    "dce0bdf6b62813c26d8447dcd0c31eac0e5633a094aebc126b0fa3c6bf1f0641",
    "d1d589b75c1b38f30b9b3cab97db8dfdb4dfb1f025d29799aba038fef155409a",
  ],
  pricingVersion: "clef-3455-per-million-full-24576-v1",
  attemptsPerMinute: 240,
  inFlight: 4,
  dailyMilliNeurons: 9_000_000,
  reservationMilliNeurons: 85_000,
  roundingSlackMilliNeurons: 10,
  upstreamDeadlineMs: 3000,
  cacheTtlMs: 86_400_000,
  cooldownMs: 5000,
  metricsDays: 7,
} as const;

export type Reason =
  | "configuration"
  | "rate_limited"
  | "quota_exhausted"
  | "timeout"
  | "service_unavailable"
  | "malformed_response"
  | "refused"
  | "invalid_input";

export class Failure extends Error {
  constructor(public reason: Reason) {
    super(reason);
  }
}
export const dayOf = (now: number) => Math.floor(now / 86_400_000);
export const resetAt = (now: number) => (dayOf(now) + 1) * 86_400_000;
