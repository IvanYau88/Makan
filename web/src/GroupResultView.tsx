import { forwardRef } from "react";
import { categoryLabel, distanceLabel } from "./format";
import type { GroupExcluded, GroupOption, GroupResult } from "./types";

interface Props {
  result: GroupResult;
}

/** The explanation one sentence to a line, since it is a long paragraph on a phone. */
const sentences = (text: string): string[] => text.split(/(?<=\.)\s+(?=[A-Z])/);

const score = (value: number) => value.toFixed(2);

function Scores({ option }: { option: GroupOption }) {
  if (option.lowest_score === null || option.average_score === null) return null;
  return (
    <p className="meta">
      Lowest score {score(option.lowest_score)} · average {score(option.average_score)} (out of 1)
      {option.lowest_scorers && option.lowest_scorers.length > 0
        ? ` · least happy: ${option.lowest_scorers.join(", ")}`
        : ""}
    </p>
  );
}

function excludedWhy(place: GroupExcluded): string {
  if (place.refusals) {
    return place.refusals.map((r) => `${r.person} refuses ${r.term}`).join("; ");
  }
  return `refused by someone in the group: ${(place.refused_terms ?? []).join(", ")}`;
}

/** The group's pick with why, the runners-up, what was ruled out, and what could not be checked. */
export const GroupResultView = forwardRef<HTMLHeadingElement, Props>(function GroupResultView(
  { result },
  headingRef,
) {
  const { pick } = result;
  return (
    <section className="group-result" aria-labelledby="group-result-heading">
      <h2 id="group-result-heading" ref={headingRef} tabIndex={-1} className="section-title">
        {pick ? "The group's pick" : "No place to pick"}
      </h2>

      {result.partial && (
        <p className="notice notice-warn" role="status">
          Part of the search failed, so this may be missing places.
        </p>
      )}

      {pick && (
        <article className="group-pick" aria-label={`Top pick: ${pick.name}`}>
          <h3 className="detail-title">
            {pick.name} <span className="chip">Top pick</span>
          </h3>
          <p className="meta">{categoryLabel(pick.category)}</p>
          <p className="meta">
            {distanceLabel(pick.distance_m)} straight-line from the search point
            {pick.address ? ` · ${pick.address}` : ""}
          </p>
          <Scores option={pick} />
          <ul className="reasons">
            {pick.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </article>
      )}

      <div className="group-why">
        <h3 className="subtitle">Why</h3>
        {sentences(result.explanation).map((sentence, i) => (
          <p key={i}>{sentence}</p>
        ))}
      </div>

      {result.runners_up.length > 0 && (
        <section aria-labelledby="group-runners-heading">
          <h3 id="group-runners-heading" className="subtitle">
            Runners-up
          </h3>
          <ol className="group-list">
            {result.runners_up.map((option) => (
              <li key={option.id}>
                <strong>{option.name}</strong>
                <p className="meta">
                  {categoryLabel(option.category)} · {distanceLabel(option.distance_m)}
                </p>
                <Scores option={option} />
              </li>
            ))}
          </ol>
        </section>
      )}

      {result.excluded.length > 0 && (
        <section aria-labelledby="group-excluded-heading">
          <h3 id="group-excluded-heading" className="subtitle">
            Ruled out before scoring
          </h3>
          <ul className="group-list">
            {result.excluded.map((place) => (
              <li key={place.id}>
                <strong>{place.name}</strong>
                <p className="meta">{excludedWhy(place)}</p>
              </li>
            ))}
          </ul>
        </section>
      )}

      {(pick?.warnings.length ?? 0) + result.warnings.length > 0 && (
        <section aria-labelledby="group-notes-heading">
          <h3 id="group-notes-heading" className="subtitle">
            Good to know
          </h3>
          <ul className="group-list">
            {[...new Set([...(pick?.warnings ?? []), ...result.warnings])].map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </section>
      )}

      <p className="source">
        {result.mode === "demo" ? "Sample places for the demo, not real venues. " : null}
        {result.attribution}
      </p>
    </section>
  );
});
