import { useId, useMemo, useState } from "react";
import { choiceOf, choose, groupedMatches } from "./cuisineVocabulary";
import type { Choice } from "./cuisineVocabulary";
import type { CuisineTerm, CuisineVocabulary, SessionTastes } from "./types";

export const EMPTY_TASTES: SessionTastes = { likes: [], dislikes: [] };

/** The eight cuisines the frozen online scoring state knows. Others leave online fit Unrated. */
const ONLINE_CUISINES = "Chinese, Indian, Italian, Japanese, Korean, Mexican, Thai and Vietnamese";

export function SessionTaste({
  tastes,
  onChange,
  vocabulary,
  online = false,
  owner = false,
  consent = false,
  onConsent,
  working = false,
  failure = null,
}: {
  tastes: SessionTastes;
  onChange: (tastes: SessionTastes) => void;
  /** The server's cuisine vocabulary. Without one a guest has nothing to choose from. */
  vocabulary: CuisineVocabulary | null;
  online?: boolean;
  owner?: boolean;
  consent?: boolean;
  onConsent?: (consent: boolean) => void;
  working?: boolean;
  failure?: string | null;
}) {
  const count = tastes.likes.length + tastes.dislikes.length;
  return (
    <details className="session-taste">
      <summary>
        Your tastes{" "}
        <span className="hint">
          {owner ? "saved cuisines" : count ? `${count} selected` : "optional"}
        </span>
      </summary>
      <p className="hint" id="session-taste-help">
        {owner
          ? "Uses your saved cuisine tastes. Edit them on the Account page, then search again."
          : "This tab only. Choose cuisines you like or prefer to skip, then search again."}{" "}
        {online
          ? `Taste fit is an estimate, not a probability, quality rating or dietary safety check. Online scoring knows ${ONLINE_CUISINES} for now; other choices leave places Unrated.`
          : "Fit compares the cuisines a place's category lists, not quality or dietary safety."}
      </p>
      {!owner && vocabulary && (
        <TastePicker tastes={tastes} onChange={onChange} vocabulary={vocabulary} />
      )}
      {!owner && (
        <button
          type="button"
          className="button button-link"
          disabled={!count}
          onClick={() => onChange(EMPTY_TASTES)}
        >
          Clear tastes
        </button>
      )}
      {online && (
        <div className="fit-consent">
          <p className="hint" id="fit-consent-help">
            Online scoring sends your selected cuisine likes and dislikes and available place
            categories to Cloudflare. It sends no identity, location, chat or hard requirements. You
            can decline or withdraw and keep browsing with Unrated places.
          </p>
          <label className="check-field">
            <input
              type="checkbox"
              checked={consent}
              disabled={working}
              aria-describedby="fit-consent-help"
              onChange={(event) => onConsent?.(event.target.checked)}
            />
            Use online taste scoring in this tab
          </label>
          {working && (
            <p className="hint" role="status">
              Updating scoring choice…
            </p>
          )}
          {failure && (
            <p className="hint" role="status">
              {failure}
            </p>
          )}
        </div>
      )}
    </details>
  );
}

function TastePicker({
  tastes,
  onChange,
  vocabulary,
}: {
  tastes: SessionTastes;
  onChange: (tastes: SessionTastes) => void;
  vocabulary: CuisineVocabulary;
}) {
  const [query, setQuery] = useState("");
  const id = useId();
  const groups = useMemo(() => groupedMatches(vocabulary, query), [vocabulary, query]);
  const byId = useMemo(
    () => new Map(vocabulary.cuisines.map((term) => [term.id, term])),
    [vocabulary],
  );
  const shown = groups.reduce((total, group) => total + group.terms.length, 0);
  const set = (term: CuisineTerm, choice: Choice) => onChange(choose(tastes, term.id, choice));

  const chosen = (ids: string[], choice: Choice, title: string) => {
    const terms = ids.map((c) => byId.get(c)).filter((t): t is CuisineTerm => !!t);
    if (!terms.length) return null;
    return (
      <div className="taste-chosen">
        <span className="taste-chosen-title">{title}</span>
        <ul className="taste-chips">
          {terms.map((term) => (
            <li key={term.id}>
              <button
                type="button"
                className={`taste-chip taste-chip-${choice}`}
                aria-label={`Remove ${term.label} from ${choice === "like" ? "likes" : "skips"}`}
                onClick={() => set(term, choice)}
              >
                {term.label}
                <span aria-hidden="true" className="taste-chip-x">
                  ×
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    );
  };

  return (
    <div className="taste-picker">
      {chosen(tastes.likes, "like", "Like")}
      {chosen(tastes.dislikes, "skip", "Skip")}
      <div className="field taste-search">
        <label htmlFor={`${id}-search`}>Find a cuisine</label>
        <input
          id={`${id}-search`}
          type="search"
          value={query}
          placeholder="Thai, pizza, bbq…"
          autoComplete="off"
          spellCheck={false}
          aria-controls={`${id}-list`}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      <p className="sr-only" role="status">
        {query ? `${shown} ${shown === 1 ? "cuisine" : "cuisines"} found` : ""}
      </p>
      <div className="taste-list" id={`${id}-list`} role="group" aria-label="Cuisines">
        {groups.length === 0 && (
          <p className="hint taste-empty">
            No cuisine matches “{query.trim()}”. Fit can only use cuisines the place data lists.
          </p>
        )}
        {groups.map((group) => (
          <div key={group.name} className="taste-group">
            <h4 className="taste-group-title">{group.name}</h4>
            <ul className="taste-rows">
              {group.terms.map((term) => {
                const current = choiceOf(tastes, term.id);
                return (
                  <li key={term.id} className="taste-row">
                    <span className="taste-name" id={`${id}-term-${term.id}`}>
                      {term.label}
                    </span>
                    <span
                      className="taste-choices"
                      role="group"
                      aria-labelledby={`${id}-term-${term.id}`}
                    >
                      <button
                        type="button"
                        className="taste-choice taste-choice-like"
                        aria-pressed={current === "like"}
                        aria-label={`Like ${term.label}`}
                        onClick={() => set(term, "like")}
                      >
                        Like
                      </button>
                      <button
                        type="button"
                        className="taste-choice taste-choice-skip"
                        aria-pressed={current === "skip"}
                        aria-label={`Skip ${term.label}`}
                        onClick={() => set(term, "skip")}
                      >
                        Skip
                      </button>
                    </span>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}
