import { useEffect, useRef } from "react";
import type { FormEvent, RefObject } from "react";
import type { CoordinateErrors } from "./location";
import { radiusLabel } from "./format";

export interface FormValues {
  request: string;
  radiusM: number;
  manual: boolean;
  latitude: string;
  longitude: string;
}

export const RADII = [500, 1000, 2000, 5000] as const;
export const REQUEST_PLACEHOLDER = "something good to eat";
export const MAX_REQUEST_CHARS = 500;

interface Props {
  values: FormValues;
  onChange: (values: FormValues) => void;
  onSubmit: () => void;
  busy: boolean;
  /** Why the browser location failed, if it did. */
  locationError: string | null;
  coordinateErrors: CoordinateErrors;
}

export function SearchForm({
  values,
  onChange,
  onSubmit,
  busy,
  locationError,
  coordinateErrors,
}: Props) {
  const latitudeInput = useRef<HTMLInputElement>(null);
  // When the browser location fails, the way forward is typing coordinates, so go there.
  useEffect(() => {
    if (locationError) latitudeInput.current?.focus();
  }, [locationError]);

  const set = (patch: Partial<FormValues>) => onChange({ ...values, ...patch });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!busy) onSubmit();
  };

  return (
    <form className="card form" onSubmit={submit} noValidate>
      <div className="field">
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

      <div className="field">
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

      {values.manual && (
        <fieldset className="coordinates">
          <legend>Your location</legend>
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
          <p className="hint">Makan rounds this to about 100 m and does not store it.</p>
        </fieldset>
      )}

      {locationError && (
        <p className="notice notice-error" role="alert">
          {locationError}
        </p>
      )}

      <button type="submit" className="button button-primary" disabled={busy}>
        {busy ? (
          <>
            <span className="spinner" aria-hidden="true" />
            Finding food…
          </>
        ) : values.manual ? (
          "Find food here"
        ) : (
          "Locate me and find food"
        )}
      </button>

      <button
        type="button"
        className="button button-link"
        aria-expanded={values.manual}
        disabled={busy}
        onClick={() => set({ manual: !values.manual })}
      >
        {values.manual ? "Use my location instead" : "Enter coordinates instead"}
      </button>
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
        placeholder={id === "latitude" ? "3.148" : "101.695"}
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
