import { useId } from "react";
import unratedPlate from "./assets/plate-c-unrated-compact.svg";

/** The selected ceramic plate. A rank or a request match is never a taste score. */
export function Plate({ score = null }: { score?: number | null }) {
  const id = useId();
  const rated = score !== null && Number.isFinite(score) && score >= 0 && score <= 100;
  const value = rated ? Math.round(score) : null;
  return (
    <span className="plate-fit">
      {value === null ? (
        <img
          className="plate"
          src={unratedPlate}
          width="52"
          height="52"
          alt="Unrated taste match"
        />
      ) : (
        <svg
          className="plate"
          viewBox="0 0 240 240"
          role="img"
          aria-label={`Taste match ${value} out of 100`}
        >
          <defs>
            <radialGradient id={id} cx="42%" cy="35%" r="66%">
              <stop offset="0" className="plate-white" />
              <stop offset=".76" className="plate-white" />
              <stop offset=".94" className="plate-glaze" />
              <stop offset="1" className="plate-edge" />
            </radialGradient>
          </defs>
          <circle className="plate-shadow" cx="120" cy="122" r="114" />
          <circle className="plate-ceramic" cx="120" cy="120" r="113" fill={`url(#${id})`} />
          <circle className="plate-rim" cx="120" cy="120" r="111" />
          <circle className="plate-rim" cx="120" cy="120" r="101" />
          <circle className="plate-track" cx="120" cy="120" r="82" />
          <circle
            className="plate-progress"
            cx="120"
            cy="120"
            r="82"
            pathLength="100"
            strokeDasharray={`${value} ${100 - value}`}
            transform="rotate(-90 120 120)"
          />
          <circle className="plate-score-rim" cx="120" cy="120" r="73" />
          <text
            className="plate-number"
            x="120"
            y="122"
            textAnchor="middle"
            dominantBaseline="central"
          >
            {value}
          </text>
        </svg>
      )}
      <span className="fit-label">{value === null ? "Unrated" : "fit / 100"}</span>
    </span>
  );
}
