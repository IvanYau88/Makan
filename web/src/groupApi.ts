import { ApiError, UNREACHABLE, errorDetails } from "./api";
import type {
  GroupCreateRequest,
  GroupCreated,
  GroupCredential,
  GroupInputs,
  GroupResult,
  GroupView,
} from "./types";

/** The backend's stable error codes for the states a shared link can be in. */
export const GROUP_NOT_FOUND = "session_not_found";
export const GROUP_EXPIRED = "session_expired";
export const GROUP_CLOSED = "session_closed";
export const GROUP_FULL = "session_full";
export const NOT_A_PARTICIPANT = "not_a_participant";

interface Call {
  method?: "GET" | "POST" | "PUT";
  /** The caller's participant token, sent as a bearer credential. */
  token?: string | null;
  body?: unknown;
  signal?: AbortSignal;
}

async function call<T>(path: string, { method = "GET", token, body, signal }: Call): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/groups${path}`, {
      method,
      headers: {
        ...(body !== undefined && { "Content-Type": "application/json" }),
        ...(token && { Authorization: `Bearer ${token}` }),
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
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError("network", UNREACHABLE);
  }
}

const group = (link: string) => `/${encodeURIComponent(link)}`;

export function createGroup(body: GroupCreateRequest, signal?: AbortSignal): Promise<GroupCreated> {
  return call("", { method: "POST", body, signal });
}

export function readGroup(
  link: string,
  token: string | null,
  signal?: AbortSignal,
): Promise<GroupView> {
  return call(group(link), { token, signal });
}

export function joinGroup(
  link: string,
  displayName: string | undefined,
  signal?: AbortSignal,
): Promise<GroupCredential> {
  return call(`${group(link)}/participants`, {
    method: "POST",
    body: displayName ? { display_name: displayName } : {},
    signal,
  });
}

export function shareInputs(
  link: string,
  token: string,
  inputs: GroupInputs,
  signal?: AbortSignal,
): Promise<GroupView> {
  return call(`${group(link)}/me`, { method: "PUT", token, body: inputs, signal });
}

export function closeGroup(link: string, token: string, signal?: AbortSignal): Promise<GroupView> {
  return call(`${group(link)}/close`, { method: "POST", token, signal });
}

/** The closed group's result. It is worked out once, and each participant reads the same one. */
export function readGroupResult(
  link: string,
  token: string,
  signal?: AbortSignal,
): Promise<GroupResult> {
  return call(`${group(link)}/result`, { token, signal });
}
