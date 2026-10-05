import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, MODEL_BUSY, fetchMode, recommend } from "./api";
import { LocationError, locate, parseCoordinates } from "./location";
import type { CoordinateErrors } from "./location";
import { RADII, REQUEST_PLACEHOLDER, SearchForm } from "./SearchForm";
import type { FormValues } from "./SearchForm";
import { ResultView } from "./ResultView";
import type { Mode, Recommendation } from "./types";

type Status =
  | { kind: "idle" }
  | { kind: "working"; phase: "locating" | "searching" }
  | { kind: "done"; result: Recommendation; radiusM: number }
  | { kind: "failed"; message: string; busy: boolean };

const PHASE_TEXT = {
  locating: "Getting your location…",
  searching: "Researching places nearby…",
} as const;

export function App() {
  const [values, setValues] = useState<FormValues>({
    request: "",
    radiusM: 1000,
    manual: false,
    latitude: "",
    longitude: "",
  });
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [locationError, setLocationError] = useState<string | null>(null);
  const [coordinateErrors, setCoordinateErrors] = useState<CoordinateErrors>({});
  const [mode, setMode] = useState<Mode | null>(null);
  const inflight = useRef<AbortController | null>(null);
  const resultHeading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    void fetchMode(controller.signal).then(setMode);
    return () => controller.abort();
  }, []);

  useEffect(() => () => inflight.current?.abort(), []);

  // A new result or error is announced by moving focus to it, so keyboard and screen reader
  // users land on the answer instead of having to find it.
  useEffect(() => {
    if (status.kind === "done" || status.kind === "failed") resultHeading.current?.focus();
  }, [status]);

  const search = useCallback(async (form: FormValues) => {
    inflight.current?.abort();
    const controller = new AbortController();
    inflight.current = controller;
    setLocationError(null);
    setCoordinateErrors({});

    let where;
    if (form.manual) {
      const parsed = parseCoordinates(form.latitude, form.longitude);
      if ("errors" in parsed) {
        setCoordinateErrors(parsed.errors);
        setStatus({ kind: "idle" });
        return;
      }
      where = parsed.coordinates;
    } else {
      setStatus({ kind: "working", phase: "locating" });
      try {
        where = await locate();
      } catch (error) {
        if (controller.signal.aborted) return;
        setLocationError(error instanceof LocationError ? error.message : "Location failed.");
        setValues((current) => ({ ...current, manual: true }));
        setStatus({ kind: "idle" });
        return;
      }
    }
    if (controller.signal.aborted) return;

    setStatus({ kind: "working", phase: "searching" });
    try {
      const result = await recommend(
        { ...where, request: form.request.trim() || REQUEST_PLACEHOLDER, radius_m: form.radiusM },
        controller.signal,
      );
      setStatus({ kind: "done", result, radiusM: form.radiusM });
    } catch (error) {
      if (controller.signal.aborted) return;
      setStatus({
        kind: "failed",
        message: error instanceof ApiError ? error.message : "Something went wrong. Try again.",
        busy: error instanceof ApiError && error.code === MODEL_BUSY,
      });
    }
  }, []);

  const busy = status.kind === "working";
  const wider = RADII.find((r) => r > values.radiusM);
  const widen = () => {
    if (wider === undefined) return;
    const next = { ...values, radiusM: wider };
    setValues(next);
    void search(next);
  };

  return (
    <>
      {mode === "demo" && (
        <p className="banner" role="note">
          Demo mode: sample places, not real venues.
        </p>
      )}
      <div className="app">
        <header className="header">
          <h1 className="brand">Makan</h1>
          <p className="tagline">
            Tell Makan where you are and what you fancy. It picks a place to eat.
          </p>
        </header>

        <main>
          <SearchForm
            values={values}
            onChange={setValues}
            onSubmit={() => void search(values)}
            busy={busy}
            locationError={locationError}
            coordinateErrors={coordinateErrors}
          />

          <div className="output">
            <div className="sr-only" role="status" aria-live="polite">
              {status.kind === "working" ? PHASE_TEXT[status.phase] : ""}
            </div>
            {status.kind === "working" && (
              <div className="loading card" aria-hidden="true">
                <span className="spinner spinner-large" />
                <p>{PHASE_TEXT[status.phase]}</p>
                <div className="skeleton">
                  <span />
                  <span />
                  <span />
                </div>
              </div>
            )}

            {status.kind === "failed" && (
              <section className="card failure" role="alert" aria-labelledby="failure-heading">
                <h2
                  id="failure-heading"
                  ref={resultHeading}
                  tabIndex={-1}
                  className="section-title"
                >
                  {status.busy ? "The model is busy" : "That did not work"}
                </h2>
                <p>{status.message}</p>
                <button
                  type="button"
                  className="button button-secondary"
                  onClick={() => void search(values)}
                >
                  Try again
                </button>
              </section>
            )}

            {status.kind === "done" && (
              <ResultView
                ref={resultHeading}
                result={status.result}
                radiusM={status.radiusM}
                onWiden={wider === undefined ? null : widen}
              />
            )}
          </div>
        </main>
      </div>
    </>
  );
}
