import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { categoryLabel, distanceLabel } from "./format";
import { usZoom } from "./geo";
import { hasCoordinates } from "./places";
import type { Coordinates, MapConfig, Place } from "./types";

export interface Circle {
  center: Coordinates;
  radiusM: number;
}

export interface MapMove {
  center: Coordinates;
  /** Fit this radius in view, or keep the zoom when null. */
  radiusM: number | null;
  /** A new key means a new move, so moving to the same spot twice still happens. */
  key: number;
}

interface Props {
  tiles: MapConfig | null;
  /** Where to start when the person's location is not known. */
  initialCenter: Coordinates;
  /** The search the places belong to. */
  applied: Circle | null;
  /** A radius or center the person chose but has not searched yet, drawn dashed. */
  pending: Circle | null;
  moveTo: MapMove | null;
  /** The places to pin. Anything without coordinates is skipped. */
  places: Place[];
  /** Style pins by whether the place matched the request, which only means something if it named one. */
  highlightMatches: boolean;
  selectedId: string | null;
  hoveredId: string | null;
  busy: boolean;
  onSelect: (id: string | null) => void;
  onHover: (id: string | null) => void;
  /** Called when the map settles after being moved, with its center and zoom. */
  onCenterChange: (center: Coordinates, zoom: number) => void;
  /** Called when the person grabs the map, so a late location does not move it from under them. */
  onInteract: () => void;
  /** Panels drawn over the map, such as a place sheet. The map's own notices get their own space. */
  children?: ReactNode;
}

type Health = "ready" | "unavailable";
type PinState = "idle" | "hover" | "selected";

/** After this many failed tiles with none loaded, say the tiles are not coming. */
const TILE_FAILURES = 4;

const prefersReducedMotion = () =>
  typeof window.matchMedia === "function" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const latLng = (c: Coordinates): L.LatLngExpression => [c.latitude, c.longitude];

export function MapView(props: Props) {
  const { tiles, places, selectedId, hoveredId, busy, applied, pending, moveTo, highlightMatches } =
    props;
  const map = useRef<L.Map | null>(null);
  const markers = useRef(new Map<string, { marker: L.Marker; look: string }>());
  const circles = useRef<{ applied?: L.Layer[]; pending?: L.Layer[] }>({});
  const tileLayer = useRef<L.TileLayer | null>(null);
  const wantedMove = useRef<MapMove | null>(null);
  // Callbacks change on every render, and the map's listeners are made once, so read the latest.
  const handlers = useRef(props);
  useEffect(() => {
    handlers.current = props;
  });

  const [health, setHealth] = useState<Health>("ready");
  const [tilesFailed, setTilesFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  // The map itself, made when its element mounts. A failure here (a blocked script, a browser
  // with no usable canvas) leaves the list working, and retrying remounts the element.
  const attach = useCallback((element: HTMLDivElement | null) => {
    if (!element) return;
    let instance: L.Map;
    try {
      instance = L.map(element, {
        zoomControl: true,
        attributionControl: false, // the page shows the attribution itself, outside the map
        zoomSnap: 0.5,
        worldCopyJump: true,
      }).setView(latLng(handlers.current.initialCenter), usZoom(element.clientWidth));
    } catch {
      setHealth("unavailable");
      return;
    }
    map.current = instance;
    setHealth("ready");
    instance.on("moveend", () => {
      const c = instance.getCenter();
      handlers.current.onCenterChange({ latitude: c.lat, longitude: c.lng }, instance.getZoom());
    });
    const interact = () => handlers.current.onInteract();
    for (const type of ["pointerdown", "wheel", "keydown"])
      element.addEventListener(type, interact);
    instance.on("click", () => handlers.current.onSelect(null));
    const resize = new ResizeObserver(() => {
      instance.invalidateSize();
      if (wantedMove.current && !hasNoSize(instance)) {
        applyMove(instance, wantedMove.current);
        wantedMove.current = null;
      }
    });
    resize.observe(element);
    const pins = markers.current;
    return () => {
      resize.disconnect();
      instance.remove();
      map.current = null;
      tileLayer.current = null;
      circles.current = {};
      pins.clear();
    };
  }, []);

  // Tiles come from configuration, and failing to load them never hides the pins.
  const tileUrl = tiles?.tile_url;
  useEffect(() => {
    const instance = map.current;
    if (!instance || !tileUrl || health !== "ready") return;
    let failed = 0;
    let loaded = 0;
    const layer = L.tileLayer(tileUrl, { maxZoom: 19, crossOrigin: "anonymous" });
    layer.on("tileload", () => {
      loaded += 1;
      if (loaded === 1) setTilesFailed(false);
    });
    layer.on("tileerror", () => {
      failed += 1;
      if (loaded === 0 && failed >= TILE_FAILURES) setTilesFailed(true);
    });
    layer.addTo(instance);
    tileLayer.current = layer;
    return () => {
      layer.remove();
      tileLayer.current = null;
    };
  }, [tileUrl, health, attempt]);

  const moveKey = moveTo?.key;
  useEffect(() => {
    const instance = map.current;
    const move = handlers.current.moveTo;
    if (!instance || !move) return;
    // A hidden map (the list is showing on a phone) has no size to fit, so wait until it has one.
    if (hasNoSize(instance)) wantedMove.current = move;
    else applyMove(instance, move);
  }, [moveKey, attempt]);

  // The searched circle and the not yet searched one.
  useEffect(() => {
    const instance = map.current;
    if (!instance) return;
    for (const key of ["applied", "pending"] as const) {
      circles.current[key]?.forEach((layer) => layer.remove());
      circles.current[key] = undefined;
    }
    if (applied) {
      circles.current.applied = [
        L.circle(latLng(applied.center), {
          radius: applied.radiusM,
          className: "map-radius",
          interactive: false,
        }).addTo(instance),
        L.circleMarker(latLng(applied.center), {
          radius: 6,
          className: "map-center",
          interactive: false,
        }).addTo(instance),
      ];
    }
    if (pending) {
      circles.current.pending = [
        L.circle(latLng(pending.center), {
          radius: pending.radiusM,
          className: "map-radius map-radius-pending",
          interactive: false,
        }).addTo(instance),
      ];
    }
  }, [applied, pending, attempt]);

  // The pins: the same places as the list rows, numbered by the same rank.
  useEffect(() => {
    const instance = map.current;
    if (!instance) return;
    const wanted = new Map(places.filter(hasCoordinates).map((p) => [p.id, p] as const));
    for (const [id, pin] of markers.current) {
      if (!wanted.has(id)) {
        pin.marker.remove();
        markers.current.delete(id);
      }
    }
    for (const [id, place] of wanted) {
      const state: PinState = id === selectedId ? "selected" : id === hoveredId ? "hover" : "idle";
      // Everything a pin shows, so a pin for the same venue is redrawn when its number changes.
      const look = `${state}:${place.rank}:${place.matched}:${highlightMatches}`;
      let pin = markers.current.get(id);
      if (!pin) {
        const marker = L.marker([place.lat, place.lon], {
          keyboard: false, // the list is the keyboard path to every pin
          icon: pinIcon(place, state, highlightMatches),
        });
        marker.on("click", (event) => {
          L.DomEvent.stopPropagation(event);
          handlers.current.onSelect(id);
        });
        marker.on("mouseover", () => handlers.current.onHover(id));
        marker.on("mouseout", () => handlers.current.onHover(null));
        marker.addTo(instance);
        describe(marker, place, state);
        pin = { marker, look };
        markers.current.set(id, pin);
      } else if (pin.look !== look) {
        pin.marker.setIcon(pinIcon(place, state, highlightMatches));
        describe(pin.marker, place, state);
        pin.look = look;
      }
      pin.marker.setZIndexOffset(state === "selected" ? 1000 : state === "hover" ? 500 : 0);
    }
  }, [places, selectedId, hoveredId, highlightMatches, attempt]);

  const unavailable = health === "unavailable";
  return (
    <div className="map-shell">
      {(unavailable || tilesFailed) && (
        <div className="map-fallback" role="alert">
          <p>
            <strong>
              {unavailable ? "The map could not start." : "Map tiles are not loading."}
            </strong>{" "}
            The list of places still works.
          </p>
          <button
            type="button"
            className="button button-secondary"
            onClick={() => {
              setTilesFailed(false);
              setAttempt((n) => n + 1);
            }}
          >
            Retry the map
          </button>
        </div>
      )}
      <div className="map-stage">
        <div
          key={attempt}
          ref={attach}
          className="map"
          role="region"
          aria-label="Map of nearby places"
          aria-busy={busy}
        />
        {busy && (
          <p className="map-busy" aria-hidden="true">
            <span className="spinner" /> Updating…
          </p>
        )}
        {props.children}
      </div>
    </div>
  );
}

/** Whether the map's element is hidden or collapsed. Leaflet's own size is cached, so ask the DOM. */
function hasNoSize(instance: L.Map): boolean {
  const element = instance.getContainer();
  return element.clientWidth === 0 || element.clientHeight === 0;
}

function applyMove(instance: L.Map, move: MapMove): void {
  const animate = !prefersReducedMotion();
  if (move.radiusM !== null) {
    const bounds = L.latLng(move.center.latitude, move.center.longitude).toBounds(move.radiusM * 2);
    instance.fitBounds(bounds, { animate, padding: [24, 24] });
  } else {
    instance.setView(latLng(move.center), instance.getZoom(), { animate });
  }
}

function pinIcon(place: Place, state: PinState, highlightMatches: boolean): L.DivIcon {
  return L.divIcon({
    className: `pin pin-${state}${place.matched || !highlightMatches ? " pin-matched" : ""}`,
    html: `<span>${place.rank}</span>`,
    iconSize: [34, 34],
    iconAnchor: [17, 17],
  });
}

/** Pins are not in the tab order, but a touch screen reader can still reach and name them. */
function describe(marker: L.Marker, place: Place, state: PinState): void {
  const element = marker.getElement();
  if (!element) return;
  element.setAttribute("role", "button");
  element.setAttribute("tabindex", "-1");
  element.setAttribute(
    "aria-label",
    `${place.rank}. ${place.name}, ${categoryLabel(place.category)}, ${distanceLabel(place.distance_m)}, map pin`,
  );
  element.setAttribute("aria-pressed", String(state === "selected"));
}
