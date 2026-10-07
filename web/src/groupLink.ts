/**
 * A group's shared link is `/g/<token>`, and the page asks the API about it. The participant token
 * that proves who someone is in a group is kept on this device only, so a reload or a return visit
 * is still them. It is a credential for a session that expires, so nothing else uses it.
 */
const PATH = /^\/g\/([^/]+)\/?$/;
const KEY_PREFIX = "makan.group.";

/** The link token a page address carries, or null when the address is not a group link. */
export function linkFromPath(pathname: string): string | null {
  const match = PATH.exec(pathname);
  if (!match?.[1]) return null;
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return match[1];
  }
}

export const groupPath = (link: string): string => `/g/${encodeURIComponent(link)}`;

/** The full link to give to friends. */
export const groupUrl = (link: string): string => `${window.location.origin}${groupPath(link)}`;

/** This person's token for the group, or null when they have not joined it on this device. */
export function loadCredential(link: string): string | null {
  try {
    return window.localStorage.getItem(KEY_PREFIX + link);
  } catch {
    return null;
  }
}

export function saveCredential(link: string, token: string): void {
  try {
    window.localStorage.setItem(KEY_PREFIX + link, token);
  } catch {
    // Without storage the person stays in the group until they reload.
  }
}

export function forgetCredential(link: string): void {
  try {
    window.localStorage.removeItem(KEY_PREFIX + link);
  } catch {
    // Nothing was stored.
  }
}
