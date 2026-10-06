import { useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { categoryLabel, distanceLabel } from "./format";
import type { Place } from "./types";

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
  // A pin tapped on the map may be far down the list, so bring its row into view.
  useEffect(() => {
    selected.current?.scrollIntoView?.({ block: "nearest" });
  }, [selectedId]);

  return (
    <ol className="rows" aria-label="Nearby places">
      {places.map((place) => {
        const isSelected = place.id === selectedId;
        return (
          <li
            key={place.id}
            ref={isSelected ? selected : undefined}
            className={`row${isSelected ? " row-selected" : ""}${place.id === hoveredId ? " row-hover" : ""}`}
          >
            <button
              type="button"
              className="row-button"
              data-place-id={place.id}
              aria-current={isSelected ? "true" : undefined}
              aria-label={rowLabel(place, place.id === pickId, hasTerms)}
              onClick={() => onSelect(place.id)}
              onMouseEnter={() => onHover(place.id)}
              onMouseLeave={() => onHover(null)}
              onFocus={() => onHover(place.id)}
              onBlur={() => onHover(null)}
            >
              <span className="row-rank" aria-hidden="true">
                {place.rank}
              </span>
              <span className="row-text">
                <span className="row-name">
                  {place.name}
                  {place.id === pickId && <span className="chip">Top pick</span>}
                </span>
                <span className="row-meta">
                  {categoryLabel(place.category)} · {distanceLabel(place.distance_m)}
                </span>
                {hasTerms && (
                  <span className={`row-fit${place.matched ? " row-fit-matched" : ""}`}>
                    {place.matched ? "Matches your request" : "Nearby alternative"}
                  </span>
                )}
              </span>
            </button>
            {isSelected && inlineDetail}
          </li>
        );
      })}
    </ol>
  );
}

function rowLabel(place: Place, isPick: boolean, hasTerms: boolean): string {
  const parts = [
    `${place.rank}. ${place.name}${isPick ? " (top pick)" : ""}`,
    categoryLabel(place.category),
    distanceLabel(place.distance_m),
  ];
  if (hasTerms) parts.push(place.matched ? "matches your request" : "nearby alternative");
  return parts.join(", ");
}
