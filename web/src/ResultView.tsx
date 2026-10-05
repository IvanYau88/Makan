import { forwardRef } from "react";
import { categoryLabel, distanceLabel, radiusLabel } from "./format";
import type { Place, Recommendation } from "./types";

interface Props {
  result: Recommendation;
  radiusM: number;
  /** Set when a wider search exists to offer. */
  onWiden: (() => void) | null;
}

export const ResultView = forwardRef<HTMLHeadingElement, Props>(function ResultView(
  { result, radiusM, onWiden },
  headingRef,
) {
  const { pick } = result;
  return (
    <section className="results" aria-labelledby="result-heading">
      {pick ? (
        <>
          <h2 id="result-heading" ref={headingRef} tabIndex={-1} className="section-title">
            Your pick
          </h2>
          <article className="card pick">
            <h3 className="place-name">{pick.name}</h3>
            <PlaceMeta place={pick} />
            <h4 className="subtitle">Why this pick</h4>
            <ul className="reasons">
              {pick.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          </article>
        </>
      ) : (
        <>
          <h2 id="result-heading" ref={headingRef} tabIndex={-1} className="section-title">
            No places found
          </h2>
          <div className="card empty">
            <p>Makan found nothing to eat within {radiusLabel(radiusM)} of that spot.</p>
            <p className="hint">Try a wider search, or check that the location is right.</p>
            {onWiden && (
              <button type="button" className="button button-secondary" onClick={onWiden}>
                Search a wider area
              </button>
            )}
          </div>
        </>
      )}

      {result.partial && (
        <p className="notice notice-warn" role="status">
          Part of the search failed, so this list may be missing places.
        </p>
      )}

      {result.runners_up.length > 0 && (
        <>
          <h2 className="section-title">Runners-up</h2>
          <ol className="runners">
            {result.runners_up.map((place) => (
              <li key={place.id} className="card runner">
                <h3 className="place-name">{place.name}</h3>
                <PlaceMeta place={place} />
                <ul className="reasons">
                  {place.reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              </li>
            ))}
          </ol>
        </>
      )}

      {result.stale_facts.length > 0 && (
        <>
          <h2 className="section-title">Check your saved tastes</h2>
          <div className="card notes">
            <p className="hint">
              These saved preferences are out of date, so Makan did not use them. Are they still
              true?
            </p>
            <ul>
              {result.stale_facts.map((fact) => (
                <li key={fact.id}>
                  {fact.kind.replace(/_/g, " ")}: {describe(fact.content)}
                </li>
              ))}
            </ul>
          </div>
        </>
      )}

      {result.warnings.length > 0 && (
        <>
          <h2 className="section-title">Good to know</h2>
          <ul className="card notes warnings">
            {result.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </>
      )}

      <p className="source">
        {result.mode === "demo" ? "Sample places for the demo, not real venues. " : null}
        {result.attribution}
      </p>
    </section>
  );
});

function PlaceMeta({ place }: { place: Place }) {
  return (
    <>
      <p className="meta">
        <span>{categoryLabel(place.category)}</span>
        <span aria-hidden="true">·</span>
        <span>{distanceLabel(place.distance_m)}</span>
      </p>
      {place.address && <p className="meta">{place.address}</p>}
    </>
  );
}

function describe(content: unknown): string {
  return typeof content === "string" ? content : JSON.stringify(content);
}
