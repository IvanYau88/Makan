import type { TasteEstimate } from "./types";

/** Only a well-formed estimate is shown, whatever the server sent. */
export function usableTaste(taste: TasteEstimate | null | undefined): TasteEstimate | null {
  if (!taste) return null;
  const { score, scale, ratings, why } = taste;
  if (
    typeof score !== "number" ||
    typeof scale !== "number" ||
    !Number.isFinite(score) ||
    !Number.isFinite(scale) ||
    scale <= 0 ||
    score < 0 ||
    score > scale ||
    typeof ratings !== "number" ||
    typeof why !== "string"
  ) {
    return null;
  }
  return taste;
}

function name(taste: TasteEstimate): string {
  return taste.estimate ? "Your estimate" : "For you";
}

/** The accessible wording, for a row's label. */
export function tasteLabel(raw: TasteEstimate | null | undefined): string | null {
  const taste = usableTaste(raw);
  if (!taste) return null;
  return `${name(taste)} ${taste.score.toFixed(1)} out of ${taste.scale}. ${taste.why}`;
}

/**
 * The diner's own score for a place, apart from the cuisine fit plate. The detail adds how it
 * was made and that it does not change the order.
 */
export function TasteLine({
  taste: raw,
  detail = false,
}: {
  taste?: TasteEstimate | null;
  detail?: boolean;
}) {
  const taste = usableTaste(raw);
  if (!taste) return null;
  const source = taste.why.startsWith("From your") ? "" : ` From your ${taste.ratings} ratings.`;
  const settling = taste.estimate ? " It stays an estimate until you rate more places." : "";
  return (
    <span className={`taste${detail ? " taste-detail" : ""}`}>
      <span className="taste-head">
        <span className="taste-score">{taste.score.toFixed(1)}</span>
        <span className="taste-scale">/{taste.scale}</span>
        <span className="taste-name">{name(taste)}</span>
      </span>
      <span className="taste-why">
        {taste.why}
        {detail && `${source}${settling} It does not change the order.`}
      </span>
    </span>
  );
}
