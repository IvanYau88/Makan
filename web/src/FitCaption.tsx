import type { FitAssessment, FitSummary } from "./types";

/** Same bounded assessment accompanies every plate, including null and numeric zero. */
export function FitCaption({ fit }: { fit?: FitAssessment | null }) {
  if (!fit || fit.reason === "disabled") return null;
  return (
    <span className="fit-caption">
      {fit.basis === "profile_model" && fit.score !== null
        ? "Cloudflare taste estimate"
        : fit.explanation}
      {fit.cached ? " · Cached" : ""}
    </span>
  );
}

export function FitNotice({ summary }: { summary?: FitSummary }) {
  if (!summary || summary.strategy !== "clef") return null;
  const reset = new Date();
  reset.setUTCHours(24, 0, 0, 0);
  const resetLabel = reset.toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });
  return (
    <div className="fit-summary" role="note" aria-label="Taste scoring summary">
      <p className="hint">
        Cloudflare taste scoring: {summary.rated} rated, {summary.unrated} Unrated
        {summary.cached > 0 ? `, ${summary.cached} cached` : ""}. Scores do not change the order.
      </p>
      {!!summary.reasons.quota_exhausted && (
        <p className="notice notice-warn">
          Taste scoring's daily allowance is used up. New scores return at {resetLabel}. Nearby
          results are still available.
        </p>
      )}
    </div>
  );
}
