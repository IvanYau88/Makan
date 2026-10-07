import type { AppConfig, Recommendation, RecommendRequest, Run } from "./types";

/** The backend's stable error code for a rate limited or overloaded language model. */
export const MODEL_BUSY = "model_busy";

/**
 * A failure the UI can show as is: the message is already written for a person.
 * `code` is the backend's stable error code when it sent one, which the page can key on.
 * `run` is the trace of the run that failed, when it got far enough to have one.
 */
export class ApiError extends Error {
  readonly kind: "network" | "invalid" | "server";
  readonly code: string | null;
  readonly run: Run | null;

  constructor(
    kind: ApiError["kind"],
    message: string,
    code: string | null = null,
    run: Run | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.code = code;
    this.run = run;
  }
}

export const GENERIC = "Something went wrong on our side. Try again.";
export const UNREACHABLE = "Can't reach Makan. Check your connection and try again.";

export async function fetchConfig(signal?: AbortSignal): Promise<AppConfig | null> {
  try {
    const response = await fetch("/api/config", { signal });
    const body = (await response.json()) as Partial<AppConfig>;
    const { mode, map } = body;
    if ((mode !== "demo" && mode !== "live") || typeof map?.tile_url !== "string") return null;
    return { mode, map };
  } catch {
    return null;
  }
}

/**
 * Ask for a recommendation and follow the run as it goes.
 *
 * The server sends one JSON object per line: `run` snapshots while the graph works, then a
 * `result` or an `error`. `onRun` gets each snapshot. Aborting stops listening only: the server
 * cannot cancel work that has started.
 */
export async function recommend(
  body: RecommendRequest,
  onRun: (run: Run) => void,
  signal?: AbortSignal,
): Promise<Recommendation> {
  let response: Response;
  try {
    response = await fetch("/api/recommendations/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (isAbort(error)) throw error;
    throw new ApiError("network", UNREACHABLE);
  }
  if (!response.ok) {
    const { message, code } = await errorDetails(response);
    throw new ApiError(response.status === 422 ? "invalid" : "server", message, code);
  }

  let final: Recommendation | null = null;
  try {
    for await (const line of lines(response)) {
      const event = parse(line);
      if (event?.type === "run") onRun(event.run as Run);
      else if (event?.type === "result") final = event.recommendation as Recommendation;
      else if (event?.type === "error") {
        const { code, message } = event.error as { code?: string; message?: string };
        throw new ApiError(
          "server",
          typeof message === "string" && message ? message : GENERIC,
          typeof code === "string" ? code : null,
          (event.run as Run | undefined) ?? null,
        );
      }
    }
  } catch (error) {
    if (error instanceof ApiError || isAbort(error)) throw error;
    throw new ApiError("network", UNREACHABLE);
  }
  if (!final) throw new ApiError("network", UNREACHABLE);
  return final;
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function parse(line: string): { type?: string; [key: string]: unknown } | null {
  try {
    return JSON.parse(line) as { type?: string };
  } catch {
    return null;
  }
}

/** The lines of a newline-delimited body, as they arrive. */
async function* lines(response: Response): AsyncGenerator<string> {
  if (!response.body) {
    for (const line of (await response.text()).split("\n")) if (line.trim()) yield line;
    return;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    let newline = buffer.indexOf("\n");
    while (newline >= 0) {
      const line = buffer.slice(0, newline);
      buffer = buffer.slice(newline + 1);
      if (line.trim()) yield line;
      newline = buffer.indexOf("\n");
    }
    if (done) break;
  }
  if (buffer.trim()) yield buffer;
}

export async function errorDetails(
  response: Response,
): Promise<{ message: string; code: string | null }> {
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
