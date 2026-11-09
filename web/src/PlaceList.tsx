import { FitCaption } from "./FitCaption";
import { Plate } from "./Plate";
import { PlatePlaceholder } from "./PlatePlaceholder";
import { TasteLine, tasteLabel } from "./TasteLine";
import { useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { categoryLabel, distanceLabel } from "./format";
import { serviceLabels, todayLabel } from "./osm";
import type { Place } from "./types";

/** Today's mapped hours and what the venue offers, for one short row line. */
export function osmParts(place: Place, now: Date): string[] {
  if (!place.osm) return [];
  const parts = [todayLabel(place.osm, now), ...serviceLabels(place.osm, true)];
  return parts.filter((part): part is string => !!part);
}

export function osmLine(place: Place, now: Date): string | null {
  const parts = osmParts(place, now);
  return parts.length ? parts.join(" · ") : null;
}

interface Props {
  places: Place[];
  selectedId: string | null;
  hoveredId: string | null;
  pickId: string | null;
  /** Whether the request named a cuisine or venue type, so a row can say if it matched. */
  hasTerms: boolean;
  onSelect: (id: string) => void;
  onHover: (id: string | null) => void;
  /** The detail shown inside the selected row, for narrow screens with no map beside the list. */
  inlineDetail: ReactNode;
}

export function PlaceList({
  places,
  selectedId,
  hoveredId,
  pickId,
  hasTerms,
  onSelect,
  onHover,
  inlineDetail,
}: Props) {
  const selected = useRef<HTMLLIElement>(null);
  const now = new Date();
  // A pin tapped on the map may be far down the list, so bring its row into view.
  useEffect(() => {
    selected.current?.scrollIntoView?.({ block: "nearest" });
  }, [selectedId]);

  return (
    <ol className="rows" aria-label="Nearby places">
      {places.map((place) => {
        const isSelected = place.id === selectedId;
        const mapped = osmParts(place, now);
        return (
          <li
            key={place.id}
            ref={isSelected ? selected : undefined}
            className={`row${place.id === pickId ? " row-pick" : ""}${isSelected ? " row-selected" : ""}${place.id === hoveredId ? " row-hover" : ""}`}
          >
            <button
              type="button"
              className="row-button"
              data-place-id={place.id}
              aria-current={isSelected ? "true" : undefined}
              aria-label={rowLabel(place, place.id === pickId, hasTerms, mapped)}
              onClick={() => onSelect(place.id)}
              onMouseEnter={() => onHover(place.id)}
              onMouseLeave={() => onHover(null)}
              onFocus={() => onHover(place.id)}
              onBlur={() => onHover(null)}
            >
              <PlatePlaceholder />
              <span className="row-text">
                <span className="row-name">
                  <span className="row-position">{place.rank}. </span>
                  {place.name}
                  {place.id === pickId && <span className="chip">Top pick</span>}
                </span>
                <span className="row-meta">
                  {categoryLabel(place.category)} · {distanceLabel(place.distance_m)}
                </span>
                {mapped.length > 0 && <span className="row-osm">{mapped.join(" · ")}</span>}
                <TasteLine taste={place.taste} />
                {hasTerms && (
                  <span className={`row-fit${place.matched ? " row-fit-matched" : ""}`}>
                    {place.matched ? "Matches your request" : "Nearby alternative"}
                  </span>
                )}
                {place.id === pickId && place.reasons[0] && (
                  <span className="row-reason">{place.reasons[0]}</span>
                )}
                <Plate score={place.fit?.score} />
                <FitCaption fit={place.fit} />
              </span>
            </button>
            {isSelected && inlineDetail}
          </li>
        );
      })}
    </ol>
  );
}

function rowLabel(place: Place, isPick: boolean, hasTerms: boolean, mapped: string[]): string {
  const parts = [
    `${place.rank}. ${place.name}${isPick ? " (top pick)" : ""}`,
    categoryLabel(place.category),
    distanceLabel(place.distance_m),
  ];
  if (mapped.length) parts.push(`mapped on OpenStreetMap: ${mapped.join(", ")}`);
  if (hasTerms) parts.push(place.matched ? "matches your request" : "nearby alternative");
  const taste = tasteLabel(place.taste);
  if (taste) parts.push(taste);
  // With fit off, the visible plate still says Unrated, and nothing else about fit is shown,
  // so the label says no more than that.
  if (place.fit?.reason === "disabled") {
    parts.push("Unrated");
  } else if (place.fit) {
    parts.push(
      place.fit.score === null
        ? `Unrated. ${place.fit.explanation}`
        : `${place.fit.basis === "profile_model" ? "Cloudflare taste estimate" : "Cuisine fit"} ${place.fit.score} out of 100`,
    );
    if (place.fit.cached) parts.push("Cached");
  }
  return parts.join(", ");
}
