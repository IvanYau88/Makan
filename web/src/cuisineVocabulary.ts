import type { CuisineTerm, CuisineVocabulary, SessionTastes } from "./types";

/** The server's vocabulary as sent, keeping only well-formed terms. Null when there is none. */
export function readVocabulary(raw: unknown): CuisineVocabulary | null {
  if (!raw || typeof raw !== "object") return null;
  const { version, groups, cuisines } = raw as Record<string, unknown>;
  if (typeof version !== "string" || !Array.isArray(groups) || !Array.isArray(cuisines)) {
    return null;
  }
  const seen = new Set<string>();
  const terms: CuisineTerm[] = [];
  for (const item of cuisines as unknown[]) {
    if (!item || typeof item !== "object") continue;
    const { id, label, group, aliases } = item as Record<string, unknown>;
    if (typeof id !== "string" || typeof label !== "string" || typeof group !== "string") continue;
    if (!id || !label || seen.has(id)) continue;
    seen.add(id);
    terms.push({
      id,
      label,
      group,
      aliases: Array.isArray(aliases) ? aliases.filter((a) => typeof a === "string") : [],
    });
  }
  if (!terms.length) return null;
  return {
    version,
    groups: groups.filter((g): g is string => typeof g === "string"),
    cuisines: terms,
  };
}

/** Lowercase, accents off, and only letters and digits between single spaces. */
export function searchKey(text: string): string {
  return text
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/'/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

export interface CuisineGroup {
  name: string;
  terms: CuisineTerm[];
}

/**
 * The terms whose label, id or an alias has a word starting with the query, in the server's
 * group order, alphabetical within a group. An empty query lists everything.
 */
export function groupedMatches(vocabulary: CuisineVocabulary, query: string): CuisineGroup[] {
  const wanted = searchKey(query);
  const matches = (term: CuisineTerm) =>
    !wanted ||
    [term.label, term.id.replace(/_/g, " "), ...term.aliases].some((name) => {
      const key = searchKey(name);
      return key.startsWith(wanted) || key.includes(` ${wanted}`);
    });
  const order = [
    ...vocabulary.groups,
    ...vocabulary.cuisines.map((t) => t.group).filter((g) => !vocabulary.groups.includes(g)),
  ];
  return [...new Set(order)]
    .map((name) => ({
      name,
      terms: vocabulary.cuisines
        .filter((t) => t.group === name && matches(t))
        .sort((a, b) => a.label.localeCompare(b.label)),
    }))
    .filter((group) => group.terms.length > 0);
}

export type Choice = "like" | "skip" | "";

export function choiceOf(tastes: SessionTastes, id: string): Choice {
  if (tastes.likes.includes(id)) return "like";
  if (tastes.dislikes.includes(id)) return "skip";
  return "";
}

/** Set one cuisine's choice; choosing the current choice again clears it. */
export function choose(tastes: SessionTastes, id: string, choice: Choice): SessionTastes {
  const next = choiceOf(tastes, id) === choice ? "" : choice;
  return {
    likes: [...tastes.likes.filter((c) => c !== id), ...(next === "like" ? [id] : [])],
    dislikes: [...tastes.dislikes.filter((c) => c !== id), ...(next === "skip" ? [id] : [])],
  };
}
