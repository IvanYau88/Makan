import type { Mode, Recommendation, RecommendRequest } from "./types";

/** The backend's stable error code for a rate limited or overloaded language model. */
export const MODEL_BUSY = "model_busy";

/**
 * A failure the UI can show as is: the message is already written for a person.
 * `code` is the backend's stable error code when it sent one, which the page can key on.
 */
export class ApiError extends Error {
  readonly kind: "network" | "invalid" | "server";
  readonly code: string | null;

  constructor(kind: ApiError["kind"], message: string, code: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.code = code;
  }
}

const GENERIC = "Something went wrong on our side. Try again.";

export async function fetchMode(signal?: AbortSignal): Promise<Mode | null> {
  try {
    const response = await fetch("/api/health", { signal });
    const body: unknown = await response.json();
    const mode = (body as { mode?: unknown }).mode;
    return mode === "demo" || mode === "live" ? mode : null;
  } catch {
    return null;
  }
}

export async function recommend(
  body: RecommendRequest,
  signal?: AbortSignal,
): Promise<Recommendation> {
  let response: Response;
  try {
    response = await fetch("/api/recommendations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("network", "Can't reach Makan. Check your connection and try again.");
  }
  if (response.ok) return (await response.json()) as Recommendation;
  const { message, code } = await errorDetails(response);
  throw new ApiError(response.status === 422 ? "invalid" : "server", message, code);
}

async function errorDetails(response: Response): Promise<{ message: string; code: string | null }> {
  // A proxy or gateway in front of the API can answer with HTML, so never trust the shape.
  try {
    const body: unknown = await response.json();
    const error = (body as { error?: { message?: unknown; code?: unknown } }).error;
    if (typeof error?.message === "string" && error.message) {
      return { message: error.message, code: typeof error.code === "string" ? error.code : null };
    }
  } catch {
    // fall through to the generic message
  }
  return { message: GENERIC, code: null };
}
