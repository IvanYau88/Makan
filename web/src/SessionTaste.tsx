import { CUISINES } from "./types";
import type { Cuisine, SessionTastes } from "./types";

export const EMPTY_TASTES: SessionTastes = { likes: [], dislikes: [] };

export function SessionTaste({
  tastes,
  onChange,
  online = false,
  owner = false,
  consent = false,
  onConsent,
  working = false,
  failure = null,
}: {
  tastes: SessionTastes;
  onChange: (tastes: SessionTastes) => void;
  online?: boolean;
  owner?: boolean;
  consent?: boolean;
  onConsent?: (consent: boolean) => void;
  working?: boolean;
  failure?: string | null;
}) {
  const count = tastes.likes.length + tastes.dislikes.length;
  const choose = (cuisine: Cuisine, value: string) => {
    onChange({
      likes: [...tastes.likes.filter((c) => c !== cuisine), ...(value === "like" ? [cuisine] : [])],
      dislikes: [
        ...tastes.dislikes.filter((c) => c !== cuisine),
        ...(value === "skip" ? [cuisine] : []),
      ],
    });
  };
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
          ? "Taste fit is an estimate, not a probability, quality rating or dietary safety check."
          : "Fit compares exact cuisine categories, not quality or dietary safety."}
      </p>
      {!owner && (
        <div className="session-taste-fields" aria-describedby="session-taste-help">
          {CUISINES.map((cuisine) => (
            <label key={cuisine} className="session-taste-field">
              <span>{cuisine.charAt(0).toUpperCase() + cuisine.slice(1)}</span>
              <select
                name={`taste-${cuisine}`}
                value={
                  tastes.likes.includes(cuisine)
                    ? "like"
                    : tastes.dislikes.includes(cuisine)
                      ? "skip"
                      : ""
                }
                onChange={(event) => choose(cuisine, event.target.value)}
              >
                <option value="">No preference</option>
                <option value="like">Like</option>
                <option value="skip">Prefer to skip</option>
              </select>
            </label>
          ))}
        </div>
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
