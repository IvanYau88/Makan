import type { Coordinates } from "./types";

export type LocationFailure = "denied" | "unavailable" | "timeout" | "unsupported";

export class LocationError extends Error {
  readonly failure: LocationFailure;

  constructor(failure: LocationFailure, message: string) {
    super(message);
    this.name = "LocationError";
    this.failure = failure;
  }
}

const MESSAGES: Record<LocationFailure, string> = {
  denied:
    "Location access was denied. Allow it in your browser settings, or enter coordinates instead.",
  unavailable: "Your device could not work out where you are. Enter coordinates instead.",
  timeout: "Finding your location took too long. Try again, or enter coordinates instead.",
  unsupported:
    "This browser cannot share your location here (it may need a secure connection). Enter coordinates instead.",
};

/** The browser's location, rounded to about 100 m: that is all Makan keeps or sends on. */
export function locate(): Promise<Coordinates> {
  return new Promise((resolve, reject) => {
    if (!("geolocation" in navigator)) {
      reject(new LocationError("unsupported", MESSAGES.unsupported));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      ({ coords }) =>
        resolve({ latitude: round(coords.latitude), longitude: round(coords.longitude) }),
      (error) => {
        const failure: LocationFailure =
          error.code === error.PERMISSION_DENIED
            ? "denied"
            : error.code === error.TIMEOUT
              ? "timeout"
              : "unavailable";
        reject(new LocationError(failure, MESSAGES[failure]));
      },
      { enableHighAccuracy: false, timeout: 10_000, maximumAge: 60_000 },
    );
  });
}

function round(degrees: number): number {
  return Math.round(degrees * 1000) / 1000;
}

export interface CoordinateErrors {
  latitude?: string;
  longitude?: string;
}

/** Read typed coordinates, or say what is wrong with each field. */
export function parseCoordinates(
  latitude: string,
  longitude: string,
): { coordinates: Coordinates } | { errors: CoordinateErrors } {
  const lat = parseDegrees(latitude, 90);
  const lon = parseDegrees(longitude, 180);
  const errors: CoordinateErrors = {};
  if (typeof lat === "string") errors.latitude = lat;
  if (typeof lon === "string") errors.longitude = lon;
  if (typeof lat === "number" && typeof lon === "number") {
    return { coordinates: { latitude: lat, longitude: lon } };
  }
  return { errors };
}

function parseDegrees(text: string, limit: number): number | string {
  const trimmed = text.trim();
  if (!trimmed) return "Enter a number.";
  const value = Number(trimmed);
  if (!Number.isFinite(value)) return "Enter a number, such as 40.713.";
  if (Math.abs(value) > limit) return `Must be between -${limit} and ${limit}.`;
  return value;
}
