import { ApiError, UNREACHABLE, errorDetails } from "./api";
import type { Me, Profile, Taste } from "./types";

/** The backend's stable error codes for a token it will not accept. */
export const TOKEN_EXPIRED = "token_expired";
export const INVALID_TOKEN = "invalid_token";
export const ACCOUNT_DELETED = "account_deleted";

const ENDED = new Set([TOKEN_EXPIRED, INVALID_TOKEN, ACCOUNT_DELETED]);

/** Whether a failure means the sign-in no longer counts, so the page should sign the person out. */
export function sessionEnded(error: unknown): boolean {
  return error instanceof ApiError && error.code !== null && ENDED.has(error.code);
}

interface Call {
  method?: "GET" | "PUT" | "DELETE";
  token: string;
  body?: unknown;
  signal?: AbortSignal;
}

async function call(
  path: string,
  { method = "GET", token, body, signal }: Call,
): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(`/api/me${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body !== undefined && { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
      cache: "no-store",
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("network", UNREACHABLE);
  }
  if (!response.ok) {
    const { message, code } = await errorDetails(response);
    throw new ApiError(response.status === 422 ? "invalid" : "server", message, code);
  }
  return response;
}

async function json<T>(response: Response): Promise<T> {
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError("network", UNREACHABLE);
  }
}

export async function fetchMe(token: string, signal?: AbortSignal): Promise<Me> {
  return json(await call("", { token, signal }));
}

export async function saveProfile(
  token: string,
  body: { display_name: string; location_history_opt_in: boolean },
): Promise<Profile> {
  const saved = await json<{ profile: Profile }>(
    await call("/profile", { method: "PUT", token, body }),
  );
  return saved.profile;
}

export async function saveTaste(token: string, taste: Taste): Promise<Taste> {
  const saved = await json<{ taste: Taste }>(
    await call("/taste", { method: "PUT", token, body: taste }),
  );
  return saved.taste;
}

/** Everything stored for the person, as the text of a JSON file to save. */
export async function exportData(token: string): Promise<string> {
  const response = await call("/export", { token });
  try {
    return await response.text();
  } catch {
    throw new ApiError("network", UNREACHABLE);
  }
}

/** Delete the person's data and their account. */
export async function deleteAccount(token: string): Promise<void> {
  await call("", { method: "DELETE", token });
}
