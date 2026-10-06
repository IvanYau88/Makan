import { categoryLabel, distanceLabel } from "./format";
import { hasCoordinates } from "./places";
import type { Mode, Place } from "./types";

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
          {place.address && <p className="meta">{place.address}</p>}
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
            hasCoordinates(place) && (
              <p className="detail-links">
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
              </p>
            )
          )}
        </div>
      )}
    </section>
  );
}
