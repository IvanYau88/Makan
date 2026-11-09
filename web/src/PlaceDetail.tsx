import { FitCaption } from "./FitCaption";
import { Plate } from "./Plate";
import { categoryLabel, distanceLabel } from "./format";
import { hasCoordinates } from "./places";
import type { Mode, Place } from "./types";

/** A link out only for an absolute http or https address, whatever the server sent. */
export function webLink(raw: string | null | undefined): { href: string; host: string } | null {
  if (!raw) return null;
  try {
    const url = new URL(raw);
    if ((url.protocol !== "https:" && url.protocol !== "http:") || url.username || url.password) {
      return null;
    }
    return { href: url.href, host: url.hostname.replace(/^www\./, "") };
  } catch {
    return null;
  }
}

function listOf(words: string[]): string {
  if (words.length < 2) return words.join("");
  return `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
}

interface Props {
  place: Place;
  isPick: boolean;
  mode: Mode;
  /** Set for the map-edge panel, which can collapse. The inline detail has no toggle. */
  expanded?: boolean;
  onToggle?: () => void;
  onClose: () => void;
}

export function PlaceDetail({ place, isPick, mode, expanded = true, onToggle, onClose }: Props) {
  const titleId = `detail-title-${place.id}`;
  const open = expanded || !onToggle;
  const website = webLink(place.website);
  return (
    <section className="detail" aria-labelledby={titleId}>
      <div className="detail-head">
        <div>
          <div className="detail-titlebar">
            <h3 id={titleId} className="detail-title">
              {place.name}
            </h3>
            {isPick && <span className="chip">Top pick</span>}
          </div>
          <p className="meta">{categoryLabel(place.category)}</p>
          <Plate score={place.fit?.score} />
          <FitCaption fit={place.fit} />
          <p className="meta">
            {distanceLabel(place.distance_m)} straight-line from the search center
          </p>
        </div>
        <div className="detail-buttons">
          {onToggle && (
            <button
              type="button"
              className="button button-small"
              aria-expanded={expanded}
              onClick={onToggle}
            >
              {expanded ? "Show less" : "Show more"}
            </button>
          )}
          <button type="button" className="button button-small" onClick={onClose}>
            Close
          </button>
        </div>
      </div>

      {open && (
        <div className="detail-body">
          {place.fit && place.fit.score !== null && (
            <p className="hint">
              {/* A local explanation is already the caption under the plate above. */}
              {place.fit.basis === "profile_model" ? `${place.fit.explanation} ` : ""}The score is
              an estimate, not a probability.
            </p>
          )}
          {place.address && <p className="meta">{place.address}</p>}
          {!!place.cuisines?.length && (
            <p className="meta">
              {place.cuisines.length > 1 ? "Listed cuisines" : "Listed cuisine"}:{" "}
              {place.cuisines.map((c) => c.label).join(", ")}
            </p>
          )}
          {!!place.diets?.length && (
            <p className="diet-note">
              <strong>Listed as {listOf(place.diets.map((d) => d.label.toLowerCase()))}</strong> by
              the data source. That label is not verified: check with the venue before relying on it
              for a diet or allergy.
            </p>
          )}
          <h4 className="subtitle">Why this option</h4>
          <ul className="reasons">
            {place.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
          <p className="caveat">
            <strong>Check before going:</strong> opening hours, price, menus, reviews and dietary
            suitability are not in this data, so none of them is verified.
          </p>
          {mode === "demo" ? (
            <p className="hint">Sample venue for the demo, not a real place.</p>
          ) : (
            (hasCoordinates(place) || website) && (
              <p className="detail-links">
                {hasCoordinates(place) && (
                  <>
                    <a
                      href={`https://www.google.com/maps/dir/?api=1&destination=${place.lat},${place.lon}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      aria-label={`Directions to ${place.name} (opens a new tab)`}
                    >
                      Directions
                    </a>
                    <a
                      href={`https://www.openstreetmap.org/?mlat=${place.lat}&mlon=${place.lon}#map=18/${place.lat}/${place.lon}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      aria-label={`View ${place.name} on OpenStreetMap (opens a new tab)`}
                    >
                      View on OpenStreetMap
                    </a>
                  </>
                )}
                {website && (
                  <a
                    href={website.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    aria-label={`Website for ${place.name}, ${website.host} (opens a new tab)`}
                  >
                    Website
                  </a>
                )}
              </p>
            )
          )}
        </div>
      )}
    </section>
  );
}
