import type { Mode, Recommendation, RecommendRequest } from "./types";

/** A failure the UI can show as is: the message is already written for a person. */
export class ApiError extends Error {
  readonly kind: "network" | "invalid" | "server";

  constructor(kind: ApiError["kind"], message: string) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
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
  throw new ApiError(response.status === 422 ? "invalid" : "server", await errorMessage(response));
}

async function errorMessage(response: Response): Promise<string> {
  // A proxy or gateway in front of the API can answer with HTML, so never trust the shape.
  try {
    const body: unknown = await response.json();
    const message = (body as { error?: { message?: unknown } }).error?.message;
    if (typeof message === "string" && message) return message;
  } catch {
    // fall through to the generic message
  }
  return GENERIC;
}
