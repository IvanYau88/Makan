import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { sessionEnded } from "./accountApi";
import { AccountView } from "./AccountView";
import { ApiError, MODEL_BUSY, fetchConfig, recommend } from "./api";
import { DiscoverView } from "./DiscoverView";
import type { Status, View } from "./DiscoverView";
import { ExecutionView } from "./ExecutionView";
import { GroupCreate } from "./GroupCreate";
import { GroupView } from "./GroupView";
import { groupPath, linkFromPath, saveCredential } from "./groupLink";
import { abandonRun, recordRun } from "./history";
import type { HistoryEntry } from "./history";
import { SEARCH_ZOOM, US_CENTER, roundCenter } from "./geo";
import { LocationError, locate, parseCoordinates } from "./location";
import type { CoordinateErrors } from "./location";
import type { MapMove } from "./MapView";
import { NO_FILTERS, visiblePlaces } from "./places";
import type { PlaceFilters } from "./places";
import { phaseText } from "./stages";
import { DEFAULT_RADIUS_M, RADII } from "./SearchBar";
import type { FormValues } from "./SearchBar";
import { loadMode, saveMode } from "./searchMode";
import { SESSION_EXPIRED, useAuth } from "./useAuth";
import type {
  AppConfig,
  Coordinates,
  GroupCreated,
  GroupView as GroupSnapshot,
  Recommendation,
} from "./types";

type Page = "discover" | "group" | "execution" | "account";

const PAGES: { page: Page; label: string }[] = [
  { page: "discover", label: "Discover" },
  { page: "group", label: "Group" },
  { page: "execution", label: "Execution" },
  { page: "account", label: "Account" },
];

export function App() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const auth = useAuth(config?.auth ?? null);
  const { token: authToken, endSession } = auth;
  // A shared group link opens the page on the group it names.
  const [groupLink, setGroupLink] = useState<string | null>(() =>
    linkFromPath(window.location.pathname),
  );
  // The group a host just started, so its page opens without asking the server again.
  const [startedGroup, setStartedGroup] = useState<GroupSnapshot | null>(null);
  const [page, setPage] = useState<Page>(() =>
    linkFromPath(window.location.pathname) ? "group" : "discover",
  );
  const [form, setForm] = useState<FormValues>(() => ({
    mode: loadMode(),
    request: "",
    radiusM: DEFAULT_RADIUS_M,
    manual: false,
    latitude: "",
    longitude: "",
  }));
  // "Pick for me" was tried with nothing typed: the form asks about it and does not search.
  const [requestError, setRequestError] = useState(false);
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [result, setResult] = useState<Recommendation | null>(null);
  const [locationError, setLocationError] = useState<string | null>(null);
  const [coordinateErrors, setCoordinateErrors] = useState<CoordinateErrors>({});
  const [mapCenter, setMapCenter] = useState<Coordinates>(US_CENTER);
  // False while the map shows the whole country because nobody has said where they are. Its
  // center is then not a place to search, so searching asks for the person's location instead.
  const [placed, setPlaced] = useState(false);
  const [moveTo, setMoveTo] = useState<MapMove | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [filters, setFilters] = useState<PlaceFilters>(NO_FILTERS);
  const [view, setView] = useState<View>("map");
  const [sheetExpanded, setSheetExpanded] = useState(false);
  // One tab's runs, newest first. Nothing is stored: a reload starts empty.
  const [history, setHistory] = useState<HistoryEntry[]>([]);

  // Set once the map has a place, from a location, a search, or the person moving the map.
  const claimed = useRef(false);

  const inflight = useRef<{ seq: number; controller: AbortController } | null>(null);
  const counter = useRef(0);
  // Counts the results and errors to announce. DiscoverView moves focus when it changes.
  const [focusHeading, setFocusHeading] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    void fetchConfig(controller.signal).then(setConfig);
    return () => controller.abort();
  }, []);

  useEffect(() => () => inflight.current?.controller.abort(), []);

  // The address is the group's link only while the group page is showing, so a copied address
  // always opens what is on screen.
  useEffect(() => {
    const want = page === "group" && groupLink ? groupPath(groupLink) : "/";
    if (window.location.pathname !== want) window.history.replaceState(null, "", want);
  }, [page, groupLink]);

  const groupCreated = (created: GroupCreated) => {
    saveCredential(created.link_token, created.participant_token);
    setStartedGroup({ session: created.session, you: created.you });
    setGroupLink(created.link_token);
  };

  const leaveGroup = () => {
    setGroupLink(null);
    setStartedGroup(null);
  };

  // The choice belongs to the device, so the next visit starts where this one left off.
  useEffect(() => saveMode(form.mode), [form.mode]);

  // Start the map on the person's own location. If the browser cannot say, or they decline, it
  // stays on the view of the United States and says nothing: they never asked for a search.
  useEffect(() => {
    let ignore = false;
    locate().then(
      (where) => {
        if (ignore || claimed.current) return;
        claimed.current = true;
        setMapCenter(where);
        setPlaced(true);
        setMoveTo({ center: where, radiusM: DEFAULT_RADIUS_M, key: -++counter.current });
      },
      () => undefined,
    );
    return () => {
      ignore = true;
    };
  }, []);

  /** Start a search, giving up on any one still going. Returns its number and abort signal. */
  const begin = useCallback((text: string) => {
    const previous = inflight.current;
    if (previous) {
      previous.controller.abort();
      setHistory((h) => abandonRun(h, previous.seq));
    }
    claimed.current = true;
    const seq = ++counter.current;
    const controller = new AbortController();
    inflight.current = { seq, controller };
    setLocationError(null);
    setCoordinateErrors({});
    setStatus({ kind: "working", text });
    return { seq, signal: controller.signal };
  }, []);

  const finishIdle = (seq: number) => inflight.current?.seq === seq;

  const search = useCallback(
    async (
      values: FormValues,
      where: Coordinates,
      started?: { seq: number; signal: AbortSignal },
    ) => {
      const { seq, signal } = started ?? begin("Starting the search…");
      setStatus({ kind: "working", text: "Starting the search…" });
      try {
        // A guest has no token and sends none. A signed-in person's is refreshed if it ran out.
        const token = await authToken();
        const answer = await recommend(
          {
            mode: values.mode,
            ...where,
            ...(values.mode === "recommend" && { request: values.request.trim() }),
            radius_m: values.radiusM,
          },
          (run) => {
            if (!finishIdle(seq)) return;
            setHistory((h) => recordRun(h, seq, run));
            setStatus({ kind: "working", text: phaseText(run) });
          },
          signal,
          token,
        );
        if (!finishIdle(seq)) return;
        if (answer.run) {
          const run = answer.run;
          setHistory((h) => recordRun(h, seq, run));
        }
        setResult(answer);
        setSelectedId(null);
        setHoveredId(null);
        setFilters(NO_FILTERS);
        setMoveTo({
          center: { latitude: answer.query.latitude, longitude: answer.query.longitude },
          radiusM: answer.query.radius_m,
          key: seq,
        });
        setStatus({ kind: "idle" });
        setFocusHeading((n) => n + 1);
        inflight.current = null;
      } catch (error) {
        if (signal.aborted || !finishIdle(seq)) return;
        const failedRun = error instanceof ApiError ? error.run : null;
        // A run that never reported its end, such as a dropped connection, is one we lost sight of.
        if (failedRun) setHistory((h) => recordRun(h, seq, failedRun));
        else setHistory((h) => abandonRun(h, seq));
        // A token the server refused means the sign-in is over. Say so, and sign out here too, so
        // trying again is a plain guest search and not another refusal.
        const ended = sessionEnded(error);
        if (ended) void endSession(SESSION_EXPIRED);
        setStatus({
          kind: "failed",
          message: ended
            ? "Your session expired and you are signed out. Search again to continue as a guest, or sign in on the Account page."
            : error instanceof ApiError
              ? error.message
              : "Something went wrong. Try again.",
          busy: error instanceof ApiError && error.code === MODEL_BUSY,
        });
        setFocusHeading((n) => n + 1);
        inflight.current = null;
      }
    },
    [begin, authToken, endSession],
  );

  const goTo = (center: Coordinates, radiusM: number | null) => {
    claimed.current = true;
    setPlaced(true);
    setMapCenter(center);
    setMoveTo({ center, radiusM, key: -++counter.current });
  };

  const changeForm = (next: FormValues) => {
    if (next.request !== form.request || next.mode !== form.mode) setRequestError(false);
    setForm(next);
  };

  /** A "Pick for me" search needs something typed. Ask when there is not, rather than guess. */
  const requestReady = (values: FormValues) => {
    if (values.mode === "recommend" && values.request.trim() === "") {
      setRequestError(true);
      return false;
    }
    return true;
  };

  const searchHere = () => {
    if (!requestReady(form)) return;
    if (form.manual) {
      const parsed = parseCoordinates(form.latitude, form.longitude);
      if ("errors" in parsed) {
        setCoordinateErrors(parsed.errors);
        return;
      }
      const where = roundCenter(parsed.coordinates);
      goTo(where, form.radiusM);
      void search(form, where);
      return;
    }
    // Nowhere chosen yet: "here" means where the person is.
    if (!placed) {
      void locateAndSearch();
      return;
    }
    void search(form, roundCenter(mapCenter));
  };

  const locateAndSearch = async () => {
    const started = begin("Getting your location…");
    let where: Coordinates;
    try {
      where = await locate();
    } catch (error) {
      if (started.signal.aborted) return;
      setLocationError(error instanceof LocationError ? error.message : "Location failed.");
      setForm((current) => ({ ...current, manual: true }));
      setStatus({ kind: "idle" });
      inflight.current = null;
      return;
    }
    if (started.signal.aborted) return;
    goTo(where, form.radiusM);
    await search(form, where, started);
  };

  const locateHere = () => {
    if (requestReady(form)) void locateAndSearch();
  };

  const searchArea = () => {
    if (requestReady(form)) void search(form, roundCenter(mapCenter));
  };

  const retry = () => {
    if (!requestReady(form)) return;
    const where = result
      ? { latitude: result.query.latitude, longitude: result.query.longitude }
      : roundCenter(mapCenter);
    void search(form, where);
  };

  const wider = RADII.find((r) => r > (result?.query.radius_m ?? form.radiusM));
  const widen = () => {
    if (wider === undefined || !result || !requestReady(form)) return;
    const next = { ...form, radiusM: wider };
    setForm(next);
    void search(next, { latitude: result.query.latitude, longitude: result.query.longitude });
  };

  const visible = useMemo(
    () => (result ? visiblePlaces(result.places, filters) : []),
    [result, filters],
  );

  const changeFilters = (next: PlaceFilters) => {
    setFilters(next);
    if (result && selectedId) {
      const stillShown = visiblePlaces(result.places, next).some((p) => p.id === selectedId);
      if (!stillShown) setSelectedId(null);
    }
  };

  const select = (id: string | null) => {
    setSelectedId(id);
    if (id) setSheetExpanded(false);
  };

  return (
    <>
      {config?.mode === "demo" && (
        <p className="banner" role="note">
          Demo mode: sample places, not real venues or a real language model.
        </p>
      )}
      <header className="topbar">
        <h1 className="brand">Makan</h1>
        <nav aria-label="Pages" className="tabs">
          {PAGES.filter(({ page: p }) => p !== "account" || auth.state.status !== "off").map(
            ({ page: p, label }) => (
              <button
                key={p}
                type="button"
                className="tab"
                aria-current={page === p ? "page" : undefined}
                onClick={() => setPage(p)}
              >
                {label}
              </button>
            ),
          )}
        </nav>
      </header>
      {auth.state.status === "out" && auth.state.notice && page !== "account" && (
        <p className="notice notice-warn session-note" role="status">
          {auth.state.notice}{" "}
          <button type="button" className="button button-link" onClick={() => setPage("account")}>
            Sign in
          </button>
        </p>
      )}

      <main>
        <div hidden={page !== "discover"}>
          <DiscoverView
            tiles={config?.map ?? null}
            form={form}
            onForm={changeForm}
            requestError={requestError}
            locationError={locationError}
            coordinateErrors={coordinateErrors}
            status={status}
            result={result}
            visible={visible}
            filters={filters}
            onFilters={changeFilters}
            selectedId={selectedId}
            hoveredId={hoveredId}
            onSelect={select}
            onHover={setHoveredId}
            mapCenter={mapCenter}
            placed={placed}
            onMapCenter={(center, zoom) => {
              setMapCenter(center);
              if (zoom >= SEARCH_ZOOM) setPlaced(true);
            }}
            onMapInteract={() => {
              claimed.current = true;
            }}
            moveTo={moveTo}
            view={view}
            onView={setView}
            sheetExpanded={sheetExpanded}
            onSheetToggle={() => setSheetExpanded((open) => !open)}
            onSearch={searchHere}
            onSearchArea={searchArea}
            onLocate={locateHere}
            onRetry={retry}
            onWiden={wider === undefined ? null : widen}
            focusSignal={focusHeading}
          />
        </div>
        <div hidden={page !== "group"}>
          {groupLink ? (
            <GroupView
              key={groupLink}
              link={groupLink}
              initial={startedGroup}
              onLeave={leaveGroup}
              paused={page !== "group"}
            />
          ) : (
            <GroupCreate mapCenter={mapCenter} placed={placed} onCreated={groupCreated} />
          )}
        </div>
        <div hidden={page !== "execution"}>
          <ExecutionView history={history} onClear={() => setHistory([])} />
        </div>
        {page === "account" && <AccountView auth={auth} />}
      </main>
    </>
  );
}
