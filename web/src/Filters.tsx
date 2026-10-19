import { categoryLabel } from "./format";
import { categoryCounts } from "./places";
import type { PlaceFilters } from "./places";
import type { Place } from "./types";

interface Props {
  places: Place[];
  filters: PlaceFilters;
  onChange: (filters: PlaceFilters) => void;
  /** Whether the request named a cuisine or venue type, so matching it means something. */
  canMatch: boolean;
  /** The places came from browsing, so they are in distance order and no match is "best". */
  browsed: boolean;
}

export function Filters({ places, filters, onChange, canMatch, browsed }: Props) {
  const set = (patch: Partial<PlaceFilters>) => onChange({ ...filters, ...patch });
  return (
    <div className="result-filters">
      <div className="category-chips" role="group" aria-label="Categories">
        <button
          type="button"
          className="category-chip"
          aria-pressed={filters.category === "all"}
          onClick={() => set({ category: "all" })}
        >
          All
        </button>
        {categoryCounts(places).map(({ category, count }) => (
          <button
            type="button"
            className="category-chip"
            key={category}
            aria-pressed={filters.category === category}
            onClick={() => set({ category })}
          >
            {categoryLabel(category)} ({count})
          </button>
        ))}
      </div>
      <div className="filters">
        <div className="field">
          <label htmlFor="filter-sort">Sort by</label>
          <select
            id="filter-sort"
            value={filters.sort}
            onChange={(e) => set({ sort: e.target.value as PlaceFilters["sort"] })}
          >
            {browsed ? (
              <>
                <option value="rank">Nearest first</option>
                <option value="name">Name, A to Z</option>
              </>
            ) : (
              <>
                <option value="rank">Best match</option>
                <option value="distance">Nearest</option>
              </>
            )}
          </select>
        </div>
        {canMatch && (
          <label className="check" htmlFor="filter-matched">
            <input
              id="filter-matched"
              type="checkbox"
              checked={filters.matchedOnly}
              onChange={(e) => set({ matchedOnly: e.target.checked })}
            />
            Only places that match my request
          </label>
        )}
      </div>
    </div>
  );
}
