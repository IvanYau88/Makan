import { CUISINES } from "./types";
import type { Cuisine, SessionTastes } from "./types";

export const EMPTY_TASTES: SessionTastes = { likes: [], dislikes: [] };

export function SessionTaste({
  tastes,
  onChange,
}: {
  tastes: SessionTastes;
  onChange: (tastes: SessionTastes) => void;
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
        Your tastes <span className="hint">{count ? `${count} selected` : "optional"}</span>
      </summary>
      <p className="hint" id="session-taste-help">
        This tab only. Choose cuisines you like or prefer to skip, then search again. Fit compares
        exact cuisine categories, not quality or dietary safety.
      </p>
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
      <button
        type="button"
        className="button button-link"
        disabled={!count}
        onClick={() => onChange(EMPTY_TASTES)}
      >
        Clear tastes
      </button>
    </details>
  );
}
