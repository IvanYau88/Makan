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
}

export function Filters({ places, filters, onChange, canMatch }: Props) {
  const set = (patch: Partial<PlaceFilters>) => onChange({ ...filters, ...patch });
  return (
    <div className="filters">
      <div className="field">
        <label htmlFor="filter-category">Category</label>
        <select
          id="filter-category"
          value={filters.category}
          onChange={(e) => set({ category: e.target.value })}
        >
          <option value="all">All categories</option>
          {categoryCounts(places).map(({ category, count }) => (
            <option key={category} value={category}>
              {categoryLabel(category)} ({count})
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="filter-sort">Sort by</label>
        <select
          id="filter-sort"
          value={filters.sort}
          onChange={(e) => set({ sort: e.target.value as PlaceFilters["sort"] })}
        >
          <option value="rank">Best match</option>
          <option value="distance">Nearest</option>
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
  );
}
