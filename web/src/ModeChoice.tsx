import { MODE_COPY } from "./searchMode";
import type { SearchMode } from "./types";

interface Props {
  mode: SearchMode;
  onChange: (mode: SearchMode) => void;
  /** Hide the sentences, for a phone where results already need the room. */
  compact?: boolean;
}

const MODES: SearchMode[] = ["recommend", "browse"];

/** The first choice on the page: a suggestion, or just a look at what is nearby. */
export function ModeChoice({ mode, onChange, compact = false }: Props) {
  return (
    <fieldset className="modes" data-compact={compact}>
      <legend className="sr-only">What do you want to do?</legend>
      {MODES.map((m) => (
        <label key={m} className="mode">
          <input
            type="radio"
            name="search-mode"
            value={m}
            checked={mode === m}
            onChange={() => onChange(m)}
          />
          <span className="mode-body">
            <span className="mode-label">{MODE_COPY[m].label}</span>
            <span className="mode-hint">{MODE_COPY[m].hint}</span>
          </span>
        </label>
      ))}
    </fieldset>
  );
}
