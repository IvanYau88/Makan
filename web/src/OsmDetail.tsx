import {
  asOfLabel,
  checkDateLabel,
  dietLabel,
  serviceLabels,
  weekRows,
  weekdayIndex,
  wheelchairLabel,
} from "./osm";
import type { OsmFacts } from "./types";

interface Props {
  osm: OsmFacts;
  placeName: string;
  /** For which row is today. Tests pass a fixed day. */
  now?: Date;
}

/**
 * What OpenStreetMap volunteers mapped for a venue, with the ODbL credit beside it. Diet and
 * access tags carry the not-verified note, because they never establish safety.
 */
export function OsmDetail({ osm, placeName, now = new Date() }: Props) {
  const headingId = `osm-${osm.ref.replace("/", "-")}`;
  const services = serviceLabels(osm);
  const access = wheelchairLabel(osm.wheelchair);
  const checked = checkDateLabel(osm.check_date);
  const asOf = asOfLabel(osm.source.version);
  const hours = osm.hours;
  return (
    <section className="osm" aria-labelledby={headingId}>
      <h4 id={headingId} className="subtitle">
        From OpenStreetMap
      </h4>
      {hours?.status === "listed" ? (
        <div className="osm-hours">
          <p className="osm-label">Hours</p>
          <table className="osm-week">
            <caption className="sr-only">Opening hours for {placeName}</caption>
            <tbody>
              {weekRows(hours, weekdayIndex(now)).map((row) => (
                <tr key={row.days} className={row.today ? "osm-today" : undefined}>
                  <th scope="row">
                    {row.days}
                    {row.today && <span className="sr-only"> (today)</span>}
                  </th>
                  <td>{row.hours}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {hours.holidays_differ && <p className="hint">Holiday hours may differ.</p>}
        </div>
      ) : hours?.status === "unreadable" ? (
        <p className="meta">
          <span>
            Hours as mapped: <span className="osm-raw">{hours.text}</span>
          </span>
          <span>Makan cannot read this format, so check with the venue.</span>
        </p>
      ) : (
        <p className="meta">Hours are not mapped.</p>
      )}
      {services.length > 0 && <p className="meta">{services.join(" · ")}</p>}
      {osm.cuisines.length > 0 && (
        <p className="meta">
          {osm.cuisines.length > 1 ? "Cuisines" : "Cuisine"}:{" "}
          {osm.cuisines.map((c) => c.label).join(", ")}
        </p>
      )}
      {(osm.diets.length > 0 || access) && (
        <p className="diet-note">
          {osm.diets.length > 0 && (
            <>
              <strong>Mapped as {osm.diets.map(dietLabel).join(", ")}.</strong>{" "}
            </>
          )}
          {access && <strong>{access}.</strong>} Volunteers mapped this and it is not verified:
          check with the venue before relying on it for a diet, an allergy or access.
        </p>
      )}
      {checked && <p className="hint">Last checked by a mapper: {checked}.</p>}
      <p className="source">
        <a
          href={osm.source.licence_url}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`${osm.source.credit}, copyright and licence (opens a new tab)`}
        >
          {osm.source.credit}
        </a>
        , {osm.source.licence.replace("-", " ")}
        {asOf ? `, data as of ${asOf}` : ""}.
      </p>
    </section>
  );
}
