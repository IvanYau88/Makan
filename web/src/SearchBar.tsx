import { useEffect, useRef } from "react";
import type { FormEvent, RefObject } from "react";
import type { CoordinateErrors } from "./location";
import { coordinateLabel, radiusLabel } from "./format";
import type { Coordinates } from "./types";
import { milesToMeters } from "./units";

export interface FormValues {
  request: string;
  radiusM: number;
  manual: boolean;
  latitude: string;
  longitude: string;
}

/** Radius choices in miles, kept inside the API's 100 to 5000 m limits (3 miles is 4828 m). */
export const RADII = [0.25, 0.5, 1, 2, 3].map(milesToMeters);
export const DEFAULT_RADIUS_M = milesToMeters(1);
export const REQUEST_PLACEHOLDER = "something good to eat";
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
}: Props) {
  const latitudeInput = useRef<HTMLInputElement>(null);
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
      <div className="searchbar-row">
        <div className="field field-grow">
          <label htmlFor="request">What are you in the mood for?</label>
          <input
            id="request"
            type="text"
            value={values.request}
            maxLength={MAX_REQUEST_CHARS}
            placeholder={`e.g. ${REQUEST_PLACEHOLDER}`}
            autoComplete="off"
            enterKeyHint="search"
            onChange={(e) => set({ request: e.target.value })}
          />
        </div>

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
          <button type="submit" className="button button-primary" disabled={busy}>
            {busy ? (
              <>
                <span className="spinner" aria-hidden="true" />
                Finding food…
              </>
            ) : (
              "Find food here"
            )}
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
          The radius changed. Press Find food here to update the results.
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
