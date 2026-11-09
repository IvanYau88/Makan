/**
 * Dish facts from a venue's own website, asked for only when a diner opens that place's detail.
 *
 * The request carries the server-signed `menu_ref` and nothing else: no location, no tastes, no
 * account token. Answers are kept in this tab until the server says they expire, so opening the
 * same place again asks nobody.
 */
import { errorDetails } from "./api";
import type { MenuDish, MenuFacts, MenuStatus } from "./types";

const STATUSES: MenuStatus[] = [
  "found",
  "none",
  "refused",
  "platform",
  "unreadable",
  "unavailable",
];
const MAX_REMEMBERED = 50;
const remembered = new Map<string, MenuFacts>();

/** A read of this ref still in date, or null. */
export function rememberedMenu(ref: string, now = Date.now()): MenuFacts | null {
  const facts = remembered.get(ref);
  if (!facts) return null;
  if (Date.parse(facts.source.expires_at) <= now) {
    remembered.delete(ref);
    return null;
  }
  return facts;
}

/** For tests: forget every read. */
export function forgetMenus(): void {
  remembered.clear();
}

export async function fetchMenu(ref: string, signal?: AbortSignal): Promise<MenuFacts> {
  const known = rememberedMenu(ref);
  if (known) return known;
  const response = await fetch("/api/places/menu", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ref }),
    signal,
  });
  if (!response.ok) throw new Error((await errorDetails(response)).message);
  const facts = readMenu(await response.json());
  if (!facts) throw new Error("Makan could not read the answer about this menu.");
  remembered.set(ref, facts);
  while (remembered.size > MAX_REMEMBERED) {
    const oldest = remembered.keys().next().value;
    if (oldest === undefined) break;
    remembered.delete(oldest);
  }
  return facts;
}

/** The server's answer, checked field by field, or null when it is not the shape it should be. */
export function readMenu(raw: unknown): MenuFacts | null {
  const body = raw as Partial<MenuFacts> | null;
  const source = body?.source;
  if (
    !body ||
    !STATUSES.includes(body.status as MenuStatus) ||
    !Array.isArray(body.dishes) ||
    typeof source?.url !== "string" ||
    typeof source.host !== "string" ||
    typeof source.retrieved_at !== "string" ||
    typeof source.expires_at !== "string"
  ) {
    return null;
  }
  const dishes = body.dishes.filter(
    (d): d is MenuDish =>
      typeof d?.name === "string" && (d.price === null || typeof d.price?.label === "string"),
  );
  return {
    status: body.status as MenuStatus,
    reason: typeof body.reason === "string" ? body.reason : null,
    dishes,
    truncated: body.truncated === true,
    source: {
      url: source.url,
      host: source.host,
      method: source.method ?? null,
      retrieved_at: source.retrieved_at,
      expires_at: source.expires_at,
    },
  };
}

/** Why a read gave no dishes, in words for the diner. The page it tried is named beside it. */
export function emptyMessage(facts: MenuFacts): string {
  switch (facts.status) {
    case "found":
    case "none":
      return "Makan read the restaurant's website and found no dish list it could read.";
    case "refused":
      return facts.reason === "forbidden"
        ? "The restaurant's website turned Makan's reader away, so Makan did not read its menu."
        : "The restaurant's website asks automated readers like Makan not to read it, so Makan did not.";
    case "platform":
      return "This menu is on an ordering, delivery or social platform, which Makan does not read.";
    case "unreadable":
      if (facts.reason === "pdf") return "The menu is a PDF, which Makan cannot read.";
      if (facts.reason === "image") return "The menu is an image, which Makan cannot read.";
      if (facts.reason === "too_large") return "The menu page is too large for Makan to read.";
      return "The menu page is not a kind Makan can read.";
    case "unavailable":
      return facts.reason === "not_found"
        ? "The menu page was not found, so there was no menu to read."
        : "The restaurant's website did not answer, so Makan could not read a menu.";
  }
}

/** An ISO timestamp as a date on this device, such as "Nov 8, 2026". */
export function readOnLabel(timestamp: string): string | null {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}
