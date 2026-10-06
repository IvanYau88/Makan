import { useEffect, useRef } from "react";
import type { FormEvent, RefObject } from "react";
import type { CoordinateErrors } from "./location";
import { coordinateLabel, radiusLabel } from "./format";
import { MODE_COPY } from "./searchMode";
import type { Coordinates, SearchMode } from "./types";
import { milesToMeters } from "./units";

export interface FormValues {
  mode: SearchMode;
  request: string;
  radiusM: number;
  manual: boolean;
  latitude: string;
  longitude: string;
}

/** Radius choices in miles, kept inside the API's 100 to 5000 m limits (3 miles is 4828 m). */
export const RADII = [0.25, 0.5, 1, 2, 3].map(milesToMeters);
export const DEFAULT_RADIUS_M = milesToMeters(1);
export const REQUEST_EXAMPLE = "something good to eat";
/** What a "Pick for me" search with no request asks, in place of guessing one. */
export const REQUEST_PROMPT = "Anything in mind, or no preference?";
export const MAX_REQUEST_CHARS = 500;

interface Props {
  values: FormValues;
  onChange: (values: FormValues) => void;
  /** Search around the map's center, or around the typed coordinates when those are open. */
  onSearch: () => void;
  onLocate: () => void;
  busy: boolean;
  /** The map's center now: where "Find food here" will look. */
  mapCenter: Coordinates;
  /** False while the map shows the whole country and nowhere has been chosen. */
  placed: boolean;
  /** Why the browser location failed, if it did. */
  locationError: string | null;
  coordinateErrors: CoordinateErrors;
  /** The radius of the results on screen, to say when the picker no longer matches them. */
  appliedRadiusM: number | null;
  /** A "Pick for me" search was tried with nothing typed. */
  requestError: boolean;
}

export function SearchBar({
  values,
  onChange,
  onSearch,
  onLocate,
  busy,
  mapCenter,
  placed,
  locationError,
  coordinateErrors,
  appliedRadiusM,
  requestError,
}: Props) {
  const latitudeInput = useRef<HTMLInputElement>(null);
  const requestInput = useRef<HTMLInputElement>(null);
  const browsing = values.mode === "browse";
  const action = browsing ? "Browse here" : "Find food here";
  // "Pick for me instead" is a one tap switch, and the request box it brings back is the next thing.
  const focusRequest = useRef(false);
  useEffect(() => {
    if (focusRequest.current && !browsing) {
      focusRequest.current = false;
      requestInput.current?.focus();
    }
  }, [browsing]);
  // An empty request is asked about, not guessed at, so the question is where focus goes.
  useEffect(() => {
    if (requestError) requestInput.current?.focus();
  }, [requestError]);
  // When the browser location fails, the way forward is typing coordinates, so go there.
  useEffect(() => {
    if (locationError) latitudeInput.current?.focus();
  }, [locationError]);

  const set = (patch: Partial<FormValues>) => onChange({ ...values, ...patch });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!busy) onSearch();
  };
  const radiusPending = appliedRadiusM !== null && appliedRadiusM !== values.radiusM;

  return (
    <form className="searchbar" onSubmit={submit} noValidate>
      <div className="searchbar-row" data-browse={browsing}>
        {browsing ? (
          <div className="field field-grow browse-note">
            <button
              type="button"
              className="button button-link"
              disabled={busy}
              onClick={() => {
                focusRequest.current = true;
                set({ mode: "recommend" });
              }}
            >
              Pick for me instead
            </button>
          </div>
        ) : (
          <div className="field field-grow">
            <label htmlFor="request">What are you in the mood for?</label>
            <input
              id="request"
              ref={requestInput}
              type="text"
              value={values.request}
              maxLength={MAX_REQUEST_CHARS}
              placeholder={`e.g. ${REQUEST_EXAMPLE}`}
              autoComplete="off"
              enterKeyHint="search"
              aria-invalid={requestError ? true : undefined}
              aria-describedby={requestError ? "request-error" : undefined}
              onChange={(e) => set({ request: e.target.value })}
            />
            {requestError && (
              <p className="field-error" id="request-error">
                {REQUEST_PROMPT} Say what you feel like, or choose {MODE_COPY.browse.label} to look
                around.
              </p>
            )}
          </div>
        )}

        <div className="field field-radius">
          <label htmlFor="radius">How far will you go?</label>
          <select
            id="radius"
            value={values.radiusM}
            onChange={(e) => set({ radiusM: Number(e.target.value) })}
          >
            {RADII.map((r) => (
              <option key={r} value={r}>
                Within {radiusLabel(r)}
              </option>
            ))}
          </select>
        </div>

        <div className="searchbar-actions">
          <button type="submit" className="button button-primary button-stable" disabled={busy}>
            {/* Both labels are always laid out, so the button never changes size when it goes busy. */}
            <span className="button-state" aria-hidden={busy || undefined}>
              {action}
            </span>
            <span className="button-state button-state-busy" aria-hidden={!busy || undefined}>
              <span className="spinner" aria-hidden="true" />
              <span className="busy-long">{browsing ? "Finding places…" : "Finding food…"}</span>
              <span className="busy-short">Finding…</span>
            </span>
          </button>
          <button
            type="button"
            className="button button-secondary"
            disabled={busy}
            onClick={onLocate}
          >
            Use my location
          </button>
        </div>
      </div>

      <div className="searchbar-where">
        {values.manual ? (
          <span>Searching the coordinates below.</span>
        ) : !placed ? (
          <span>
            Zoom the map in on where you want to eat, or use your location. Makan rounds the center
            to about 0.1 mile and does not store it.
          </span>
        ) : (
          <span>
            <span className="where-long">
              Searching around the map center, near{" "}
              <strong>{coordinateLabel(mapCenter.latitude, mapCenter.longitude)}</strong>. Move the
              map to look somewhere else. Makan rounds the center to about 0.1 mile and does not
              store it.
            </span>
            <span className="where-short">
              Map center:{" "}
              <strong>{coordinateLabel(mapCenter.latitude, mapCenter.longitude)}</strong>, rounded
              to about 0.1 mile and not stored.
            </span>
          </span>
        )}
        <button
          type="button"
          className="button button-link"
          aria-expanded={values.manual}
          disabled={busy}
          onClick={() => set({ manual: !values.manual })}
        >
          {values.manual ? "Use the map center instead" : "Enter coordinates"}
        </button>
      </div>

      {radiusPending && (
        <p className="notice notice-info" role="status">
          The radius changed. Press {action} to update the results.
        </p>
      )}

      {values.manual && (
        <fieldset className="coordinates">
          <legend>Search center</legend>
          <div className="coordinate-row">
            <CoordinateField
              id="latitude"
              label="Latitude"
              inputRef={latitudeInput}
              value={values.latitude}
              error={coordinateErrors.latitude}
              onChange={(latitude) => set({ latitude })}
            />
            <CoordinateField
              id="longitude"
              label="Longitude"
              value={values.longitude}
              error={coordinateErrors.longitude}
              onChange={(longitude) => set({ longitude })}
            />
          </div>
        </fieldset>
      )}

      {locationError && (
        <p className="notice notice-error" role="alert">
          {locationError}
        </p>
      )}
    </form>
  );
}

function CoordinateField({
  id,
  label,
  value,
  error,
  inputRef,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  error: string | undefined;
  inputRef?: RefObject<HTMLInputElement | null>;
  onChange: (value: string) => void;
}) {
  const errorId = `${id}-error`;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        ref={inputRef}
        type="text"
        inputMode="decimal"
        autoComplete="off"
        value={value}
        placeholder={id === "latitude" ? "40.713" : "-74.006"}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
        onChange={(e) => onChange(e.target.value)}
      />
      {error && (
        <p className="field-error" id={errorId}>
          {error}
        </p>
      )}
    </div>
  );
}
