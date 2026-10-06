import { useEffect, useRef, useSyncExternalStore } from "react";
import { Filters } from "./Filters";
import { MapView } from "./MapView";
import type { Circle, MapMove } from "./MapView";
import { PlaceDetail } from "./PlaceDetail";
import { PlaceList } from "./PlaceList";
import { SearchBar } from "./SearchBar";
import type { FormValues } from "./SearchBar";
import { radiusLabel } from "./format";
import { DEFAULT_CENTER, hasMoved } from "./geo";
import { NO_FILTERS, isFiltered } from "./places";
import type { PlaceFilters } from "./places";
import type { CoordinateErrors } from "./location";
import type { Coordinates, MapConfig, Place, Recommendation } from "./types";
import type { RefObject } from "react";

export type View = "map" | "list";

export type Status =
  | { kind: "idle" }
  | { kind: "working"; text: string }
  | { kind: "failed"; message: string; busy: boolean };

interface Props {
  tiles: MapConfig | null;
  form: FormValues;
  onForm: (form: FormValues) => void;
  locationError: string | null;
  coordinateErrors: CoordinateErrors;
  status: Status;
  result: Recommendation | null;
  /** The results after the filters, in the chosen order. */
  visible: Place[];
  filters: PlaceFilters;
  onFilters: (filters: PlaceFilters) => void;
  selectedId: string | null;
  hoveredId: string | null;
  onSelect: (id: string | null) => void;
  onHover: (id: string | null) => void;
  mapCenter: Coordinates;
  onMapCenter: (center: Coordinates) => void;
  moveTo: MapMove | null;
  view: View;
  onView: (view: View) => void;
  sheetExpanded: boolean;
  onSheetToggle: () => void;
  onSearch: () => void;
  onSearchArea: () => void;
  onLocate: () => void;
  onRetry: () => void;
  onWiden: (() => void) | null;
  /** Changes when there is a new result or error to announce, by moving focus to its heading. */
  focusSignal: number;
}

const WIDE = "(min-width: 56rem)";

function useWide(): boolean {
  return useSyncExternalStore(
    (notify) => {
      if (typeof window.matchMedia !== "function") return () => undefined;
      const list = window.matchMedia(WIDE);
      list.addEventListener("change", notify);
      return () => list.removeEventListener("change", notify);
    },
    () => (typeof window.matchMedia === "function" ? window.matchMedia(WIDE).matches : true),
  );
}

export function DiscoverView(props: Props) {
  const { result, status, visible, filters, selectedId, form, mapCenter, view, focusSignal } =
    props;
  const wide = useWide();
  const headingRef = useRef<HTMLHeadingElement>(null);
  // A new result or error is announced by moving focus to it, so keyboard and screen reader
  // users land on the answer instead of having to find it.
  useEffect(() => {
    if (focusSignal > 0) headingRef.current?.focus();
  }, [focusSignal]);
  const working = status.kind === "working";
  const applied: Circle | null = result
    ? {
        center: { latitude: result.query.latitude, longitude: result.query.longitude },
        radiusM: result.query.radius_m,
      }
    : null;
  const centerMoved = applied ? hasMoved(applied.center, mapCenter) : false;
  const radiusChanged = applied ? applied.radiusM !== form.radiusM : false;
  const pending: Circle | null =
    applied && (centerMoved || radiusChanged) ? { center: mapCenter, radiusM: form.radiusM } : null;

  const selected = result?.places.find((p) => p.id === selectedId && visible.includes(p)) ?? null;
  const { onSelect } = props;
  // Escape closes the open place from anywhere on the page, since focus is usually in the list.
  useEffect(() => {
    if (!selected) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented) onSelect(null);
    };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, [selected, onSelect]);
  // Closing the detail unmounts it, and focus inside it would drop to the page body. Remember
  // what had focus when the place opened and hand focus back once the detail is gone.
  const selectedKey = selected?.id ?? null;
  const rootRef = useRef<HTMLDivElement>(null);
  const invoker = useRef<{ id: string; element: HTMLElement | null } | null>(null);
  useEffect(() => {
    const active = document.activeElement;
    if (selectedKey) {
      invoker.current = {
        id: selectedKey,
        element: active instanceof HTMLElement && active !== document.body ? active : null,
      };
      return;
    }
    const opened = invoker.current;
    invoker.current = null;
    if (!opened || (active && active !== document.body)) return;
    const row = Array.from(
      rootRef.current?.querySelectorAll<HTMLElement>(".row-button") ?? [],
    ).find((button) => button.dataset.placeId === opened.id);
    const toggle = rootRef.current?.querySelector<HTMLElement>(
      ".view-toggle [aria-pressed='true']",
    );
    // The invoking control first, unless it is a map pin, which the keyboard cannot reach, so
    // then its row comes first. A control on a hidden pane cannot take focus, so each candidate
    // must be rendered, and the control for the current view is the last resort.
    const fromMap = !!opened.element?.closest(".map-pane");
    const candidates = fromMap ? [row, opened.element, toggle] : [opened.element, row, toggle];
    const target = candidates.find((element) => element && canFocus(element));
    target?.focus();
  }, [selectedKey]);
  const detail = (inline: boolean) =>
    selected && result ? (
      <PlaceDetail
        place={selected}
        isPick={result.pick?.id === selected.id}
        mode={result.mode}
        expanded={inline || wide || props.sheetExpanded}
        onToggle={inline || wide ? undefined : props.onSheetToggle}
        onClose={() => props.onSelect(null)}
      />
    ) : null;
  const inlineDetail = !wide && view === "list";
  // On a phone the map is taller than the room left under the search form, so a sheet anchored to
  // the map's bottom edge can start below the screen. Scroll it, and so the map above it, into view.
  const detailRef = useRef<HTMLDivElement>(null);
  const sheetShown = !inlineDetail && selected !== null;
  const sheetKey = selected?.id;
  useEffect(() => {
    if (wide || !sheetShown) return;
    detailRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [wide, sheetShown, sheetKey, props.sheetExpanded]);
  const hasTerms = !!(result?.intent?.cuisine || result?.intent?.category);

  // What a search came to: progress, failure and recovery, the result heading, and the empty and
  // partial states. On a phone it sits above the switched panes, so the Map view cannot hide it
  // and focus always lands on rendered content. Beside the map it heads the results column.
  const outcome = (
    <>
      <div className="sr-only" role="status" aria-live="polite">
        {status.kind === "working" ? status.text : ""}
      </div>
      {working && (
        <p className="progress" aria-hidden="true">
          <span className="spinner" />
          {status.text}
        </p>
      )}

      {status.kind === "failed" && (
        <section className="failure" role="alert" aria-labelledby="failure-heading">
          <h2 id="failure-heading" ref={headingRef} tabIndex={-1} className="section-title">
            {status.busy ? "The model is busy" : "That did not work"}
          </h2>
          <p>{status.message}</p>
          {result && <p className="hint">The results below are from your previous search.</p>}
          <button type="button" className="button button-secondary" onClick={props.onRetry}>
            Try again
          </button>
        </section>
      )}

      {result && (
        <ResultsHeader
          result={result}
          shown={visible.length}
          filtered={isFiltered(filters)}
          headingRef={status.kind === "failed" ? undefined : headingRef}
        />
      )}

      {result && result.partial && (
        <p className="notice notice-warn" role="status">
          Part of the search failed, so this list may be missing places.
        </p>
      )}

      {result && result.places.length === 0 && (
        <div className="empty">
          <p>
            Makan found nothing to eat within {radiusLabel(result.query.radius_m)} of that spot.
          </p>
          <p className="hint">Try a wider search, or move the map and search another area.</p>
          {props.onWiden && (
            <button type="button" className="button button-secondary" onClick={props.onWiden}>
              Search a wider area
            </button>
          )}
        </div>
      )}
    </>
  );
  const hasOutcome = working || status.kind === "failed" || result !== null;

  return (
    <div className="discover" data-view={view} ref={rootRef}>
      <SearchBar
        values={form}
        onChange={props.onForm}
        onSearch={props.onSearch}
        onLocate={props.onLocate}
        busy={working}
        mapCenter={mapCenter}
        locationError={props.locationError}
        coordinateErrors={props.coordinateErrors}
        appliedRadiusM={applied?.radiusM ?? null}
      />

      <div className="view-toggle" role="group" aria-label="Show the map or the list">
        {(["map", "list"] as const).map((v) => (
          <button
            key={v}
            type="button"
            className="button"
            aria-pressed={view === v}
            onClick={() => props.onView(v)}
          >
            {v === "map" ? "Map" : "List"}
          </button>
        ))}
      </div>

      {!wide && (
        <div className="outcome" data-active={hasOutcome}>
          {outcome}
        </div>
      )}

      <div className="panes">
        <section className="results-pane" aria-label="Results" data-stale={working && !!result}>
          {wide && outcome}

          {!result && status.kind !== "failed" && !working && (
            <div className="intro">
              <h2 className="section-title">Find somewhere to eat</h2>
              <p>
                Move the map to where you want to eat, or use your location, then press Find food
                here. Makan shows a bounded list of nearby options, not every restaurant.
              </p>
            </div>
          )}

          {!result && working && (
            <div className="skeleton" aria-hidden="true">
              <span />
              <span />
              <span />
            </div>
          )}

          {result && result.places.length > 0 && (
            <>
              <Filters
                places={result.places}
                filters={filters}
                onChange={props.onFilters}
                canMatch={hasTerms}
              />
              {visible.length === 0 ? (
                <div className="empty">
                  <p>No options match these filters.</p>
                  <button
                    type="button"
                    className="button button-secondary"
                    onClick={() => props.onFilters(NO_FILTERS)}
                  >
                    Clear filters
                  </button>
                </div>
              ) : (
                <PlaceList
                  places={visible}
                  selectedId={selected?.id ?? null}
                  hoveredId={props.hoveredId}
                  pickId={result.pick?.id ?? null}
                  hasTerms={hasTerms}
                  onSelect={props.onSelect}
                  onHover={props.onHover}
                  inlineDetail={inlineDetail ? detail(true) : null}
                />
              )}
            </>
          )}

          {result && <ResultNotes result={result} />}
        </section>

        <section className="map-pane" aria-label="Map">
          <div className="map-frame">
            <MapView
              tiles={props.tiles}
              initialCenter={DEFAULT_CENTER}
              applied={applied}
              pending={pending}
              moveTo={props.moveTo}
              places={visible}
              highlightMatches={hasTerms}
              selectedId={selected?.id ?? null}
              hoveredId={props.hoveredId}
              busy={working}
              onSelect={props.onSelect}
              onHover={props.onHover}
              onCenterChange={props.onMapCenter}
            >
              {centerMoved && (
                <button
                  type="button"
                  className="button button-primary map-search-area"
                  onClick={props.onSearchArea}
                >
                  Search this area
                </button>
              )}
              {!inlineDetail && selected && (
                <div className="map-detail" ref={detailRef}>
                  {detail(false)}
                </div>
              )}
            </MapView>
          </div>
          <p className="map-attribution">
            Map tiles:{" "}
            {props.tiles?.attribution_url ? (
              <a href={props.tiles.attribution_url} target="_blank" rel="noopener noreferrer">
                {props.tiles.attribution}
              </a>
            ) : (
              (props.tiles?.attribution ?? "not loaded")
            )}
            . The circle shows the search radius around an approximate center, and distances are
            straight-line.
          </p>
        </section>
      </div>
    </div>
  );
}

function canFocus(element: HTMLElement): boolean {
  return element.isConnected && (element.checkVisibility?.() ?? true);
}

function ResultsHeader({
  result,
  shown,
  filtered,
  headingRef,
}: {
  result: Recommendation;
  shown: number;
  filtered: boolean;
  headingRef: RefObject<HTMLHeadingElement | null> | undefined;
}) {
  const total = result.places.length;
  return (
    <header className="results-head">
      <h2 id="result-heading" ref={headingRef} tabIndex={-1} className="section-title">
        {total === 0 ? "No places found" : "Nearby options"}
      </h2>
      {total > 0 && (
        <p className="hint">
          {filtered ? `${shown} of ${total} shown` : `${total} options`} · within{" "}
          {radiusLabel(result.query.radius_m)} · straight-line distance
          {result.truncated && ". Each search is capped at 20 places, so more may be nearby."}
        </p>
      )}
    </header>
  );
}

function ResultNotes({ result }: { result: Recommendation }) {
  return (
    <div className="notes">
      {result.stale_facts.length > 0 && (
        <section>
          <h3 className="subtitle">Check your saved tastes</h3>
          <p className="hint">
            These saved preferences are out of date, so Makan did not use them. Are they still true?
          </p>
          <ul>
            {result.stale_facts.map((fact) => (
              <li key={fact.id}>
                {fact.kind.replace(/_/g, " ")}: {describe(fact.content)}
              </li>
            ))}
          </ul>
        </section>
      )}
      {result.warnings.length > 0 && (
        <section>
          <h3 className="subtitle">Good to know</h3>
          <ul>
            {result.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </section>
      )}
      <p className="source">
        {result.mode === "demo" ? "Sample places for the demo, not real venues. " : null}
        {result.attribution}
      </p>
    </div>
  );
}

function describe(content: unknown): string {
  return typeof content === "string" ? content : JSON.stringify(content);
}
