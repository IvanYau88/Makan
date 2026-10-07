import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { ApiError } from "./api";
import { coordinateLabel, radiusLabel } from "./format";
import { roundCenter } from "./geo";
import { createGroup } from "./groupApi";
import { MAX_NAME_CHARS } from "./groupForm";
import { LocationError, locate, parseCoordinates } from "./location";
import type { CoordinateErrors } from "./location";
import { DEFAULT_RADIUS_M, MAX_REQUEST_CHARS, RADII } from "./SearchBar";
import type { Coordinates, GroupCreated } from "./types";

interface Props {
  /** The Discover map's center now, and whether it is somewhere a person chose. */
  mapCenter: Coordinates;
  placed: boolean;
  onCreated: (created: GroupCreated) => void;
}

/** The host's first step: say what the group wants and where, and get a link to share. */
export function GroupCreate({ mapCenter, placed, onCreated }: Props) {
  const [request, setRequest] = useState("");
  const [radiusM, setRadiusM] = useState(DEFAULT_RADIUS_M);
  const [name, setName] = useState("");
  const [manual, setManual] = useState(false);
  const [latitude, setLatitude] = useState("");
  const [longitude, setLongitude] = useState("");
  const [requestError, setRequestError] = useState(false);
  const [coordinateErrors, setCoordinateErrors] = useState<CoordinateErrors>({});
  const [locationError, setLocationError] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const requestInput = useRef<HTMLInputElement>(null);
  const latitudeInput = useRef<HTMLInputElement>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    if (locationError) latitudeInput.current?.focus();
  }, [locationError]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (working) return;
    setFailure(null);
    setLocationError(null);
    setCoordinateErrors({});
    if (request.trim() === "") {
      setRequestError(true);
      requestInput.current?.focus();
      return;
    }
    setRequestError(false);
    setWorking(true);
    try {
      let where: Coordinates;
      if (manual) {
        const parsed = parseCoordinates(latitude, longitude);
        if ("errors" in parsed) {
          setCoordinateErrors(parsed.errors);
          setWorking(false);
          return;
        }
        where = roundCenter(parsed.coordinates);
      } else if (placed) {
        where = roundCenter(mapCenter);
      } else {
        try {
          where = await locate();
        } catch (error) {
          if (!mounted.current) return;
          setLocationError(
            error instanceof LocationError ? error.message : "Location failed. Enter coordinates.",
          );
          setManual(true);
          setWorking(false);
          return;
        }
      }
      const created = await createGroup({
        ...where,
        request: request.trim(),
        radius_m: radiusM,
        ...(name.trim() && { display_name: name.trim() }),
      });
      if (mounted.current) onCreated(created);
    } catch (error) {
      if (!mounted.current) return;
      setFailure(error instanceof ApiError ? error.message : "Something went wrong. Try again.");
      setWorking(false);
    }
  };

  return (
    <section className="group-page" aria-labelledby="group-create-heading">
      <div>
        <p className="eyebrow">Eating together</p>
        <h2 id="group-create-heading" className="page-title">
          Decide where to eat as a group
        </h2>
        <p className="lead">
          Start a group and send the link to your friends. They open it in any browser, with no
          account, and add their own dietary needs, budget, and tastes. Makan rules out places
          anyone refuses, scores what is left for each person, and picks a spot the whole group can
          live with.
        </p>
      </div>

      <form className="group-form" onSubmit={(e) => void submit(e)} noValidate>
        <div className="field">
          <label htmlFor="group-request">What is the group in the mood for?</label>
          <input
            id="group-request"
            ref={requestInput}
            type="text"
            value={request}
            maxLength={MAX_REQUEST_CHARS}
            placeholder="e.g. dinner for four"
            autoComplete="off"
            aria-invalid={requestError ? true : undefined}
            aria-describedby={requestError ? "group-request-error" : undefined}
            onChange={(e) => setRequest(e.target.value)}
          />
          {requestError && (
            <p className="field-error" id="group-request-error">
              Say what the group feels like eating, even if it is just "dinner".
            </p>
          )}
        </div>

        <div className="group-row">
          <div className="field">
            <label htmlFor="group-radius">How far will the group go?</label>
            <select
              id="group-radius"
              value={radiusM}
              onChange={(e) => setRadiusM(Number(e.target.value))}
            >
              {RADII.map((r) => (
                <option key={r} value={r}>
                  Within {radiusLabel(r)}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="group-host-name">Your name (optional)</label>
            <input
              id="group-host-name"
              type="text"
              value={name}
              maxLength={MAX_NAME_CHARS}
              placeholder="So friends see who started it"
              autoComplete="given-name"
              onChange={(e) => setName(e.target.value)}
            />
          </div>
        </div>

        <div className="searchbar-where">
          {manual ? (
            <span>Starting from the coordinates below.</span>
          ) : placed ? (
            <span>
              Starting from the Discover map center, near{" "}
              <strong>{coordinateLabel(mapCenter.latitude, mapCenter.longitude)}</strong>. Makan
              rounds it to about 0.1 mile and keeps it only until the group expires.
            </span>
          ) : (
            <span>
              Makan will ask your browser where you are, rounds it to about 0.1 mile, and keeps it
              only until the group expires.
            </span>
          )}
          <button
            type="button"
            className="button button-link"
            aria-expanded={manual}
            disabled={working}
            onClick={() => setManual(!manual)}
          >
            {manual ? "Use my location instead" : "Enter coordinates"}
          </button>
        </div>

        {manual && (
          <fieldset className="coordinates">
            <legend>Where the group is eating</legend>
            <div className="coordinate-row">
              <div className="field">
                <label htmlFor="group-latitude">Latitude</label>
                <input
                  id="group-latitude"
                  ref={latitudeInput}
                  type="text"
                  inputMode="decimal"
                  autoComplete="off"
                  value={latitude}
                  placeholder="40.713"
                  aria-invalid={coordinateErrors.latitude ? true : undefined}
                  aria-describedby={coordinateErrors.latitude ? "group-latitude-error" : undefined}
                  onChange={(e) => setLatitude(e.target.value)}
                />
                {coordinateErrors.latitude && (
                  <p className="field-error" id="group-latitude-error">
                    {coordinateErrors.latitude}
                  </p>
                )}
              </div>
              <div className="field">
                <label htmlFor="group-longitude">Longitude</label>
                <input
                  id="group-longitude"
                  type="text"
                  inputMode="decimal"
                  autoComplete="off"
                  value={longitude}
                  placeholder="-74.006"
                  aria-invalid={coordinateErrors.longitude ? true : undefined}
                  aria-describedby={
                    coordinateErrors.longitude ? "group-longitude-error" : undefined
                  }
                  onChange={(e) => setLongitude(e.target.value)}
                />
                {coordinateErrors.longitude && (
                  <p className="field-error" id="group-longitude-error">
                    {coordinateErrors.longitude}
                  </p>
                )}
              </div>
            </div>
          </fieldset>
        )}

        {locationError && (
          <p className="notice notice-error" role="alert">
            {locationError}
          </p>
        )}
        {failure && (
          <p className="notice notice-error" role="alert">
            {failure}
          </p>
        )}

        <div>
          <button type="submit" className="button button-primary button-stable" disabled={working}>
            <span className="button-state" aria-hidden={working || undefined}>
              Start the group
            </span>
            <span className="button-state button-state-busy" aria-hidden={!working || undefined}>
              <span className="spinner" aria-hidden="true" />
              Starting…
            </span>
          </button>
        </div>
      </form>
    </section>
  );
}
