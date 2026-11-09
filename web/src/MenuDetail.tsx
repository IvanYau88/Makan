import { useEffect, useState } from "react";
import { emptyMessage, fetchMenu, readOnLabel, rememberedMenu } from "./menus";
import type { MenuDish, MenuFacts } from "./types";

export type MenuLoad =
  { kind: "off" } | { kind: "loading" } | { kind: "ready"; facts: MenuFacts } | { kind: "failed" };

interface Answer {
  ref: string;
  attempt: number;
  load: MenuLoad;
}

/**
 * The venue's menu, read when `active` (its detail is open) and the server gave a `ref`. A place
 * with no ref, or a detail that is collapsed, asks for nothing.
 */
export function useMenu(ref: string | null | undefined, active: boolean): [MenuLoad, () => void] {
  const [attempt, setAttempt] = useState(0);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const remembered = ref ? rememberedMenu(ref) : null;
  useEffect(() => {
    if (!ref || !active || remembered) return;
    const controller = new AbortController();
    fetchMenu(ref, controller.signal).then(
      (facts) => setAnswer({ ref, attempt, load: { kind: "ready", facts } }),
      () => {
        if (!controller.signal.aborted) setAnswer({ ref, attempt, load: { kind: "failed" } });
      },
    );
    return () => controller.abort();
  }, [ref, active, remembered, attempt]);
  const retry = () => setAttempt((n) => n + 1);
  if (!ref) return [{ kind: "off" }, retry];
  if (remembered) return [{ kind: "ready", facts: remembered }, retry];
  if (answer?.ref === ref && answer.attempt === attempt) return [answer.load, retry];
  return [active ? { kind: "loading" } : { kind: "off" }, retry];
}

const SHOWN = 8;
const COLLAPSE_OVER = 10; // hiding one or two dishes behind a button helps nobody

interface Props {
  load: MenuLoad;
  placeName: string;
  headingId: string;
  onRetry: () => void;
}

/**
 * Dish facts from the restaurant's own website, with the page they came from and when. They are
 * the page's own words for its dishes, never checked, so the section says so and links the page.
 */
export function MenuDetail({ load, placeName, headingId, onRetry }: Props) {
  if (load.kind === "off") return null;
  return (
    <section className="menu" aria-labelledby={headingId} aria-busy={load.kind === "loading"}>
      <h4 id={headingId} className="subtitle">
        From the restaurant's website
      </h4>
      {load.kind === "loading" && (
        <p className="progress menu-progress" role="status">
          <span className="spinner" aria-hidden="true" />
          Reading the menu on the restaurant's website…
        </p>
      )}
      {load.kind === "failed" && (
        <p className="meta menu-failed">
          <span>Makan could not get this menu right now.</span>
          <button type="button" className="button button-link menu-retry" onClick={onRetry}>
            Try again
          </button>
        </p>
      )}
      {load.kind === "ready" && <MenuFound facts={load.facts} placeName={placeName} />}
    </section>
  );
}

function MenuFound({ facts, placeName }: { facts: MenuFacts; placeName: string }) {
  const date = readOnLabel(facts.source.retrieved_at);
  const found = facts.status === "found" && facts.dishes.length > 0;
  return (
    <>
      {found ? (
        <Dishes
          key={facts.source.url}
          dishes={facts.dishes}
          truncated={facts.truncated}
          placeName={placeName}
        />
      ) : (
        <p className="meta">{emptyMessage(facts)}</p>
      )}
      <p className="source">
        {found ? "Read from " : "Tried "}
        <a
          href={facts.source.url}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`${facts.source.host}, the page Makan read (opens a new tab)`}
        >
          {facts.source.host}
        </a>
        {date ? ` on ${date}` : ""}.
        {found && " Dishes and prices can change, and none is checked for a diet or an allergy."}
      </p>
    </>
  );
}

function Dishes(props: { dishes: MenuDish[]; truncated: boolean; placeName: string }) {
  const { dishes, truncated, placeName } = props;
  const [all, setAll] = useState(false);
  const collapsible = dishes.length > COLLAPSE_OVER;
  const shown = all || !collapsible ? dishes : dishes.slice(0, SHOWN);
  return (
    <>
      <ul className="menu-dishes" aria-label={`Dishes at ${placeName}`}>
        {shown.map((dish, i) => (
          <li key={`${i}-${dish.name}`}>
            <span className="menu-dish">{dish.name}</span>
            {dish.price && <span className="menu-price">{dish.price.label}</span>}
          </li>
        ))}
      </ul>
      {collapsible && (
        <button
          type="button"
          className="button button-link menu-more"
          aria-expanded={all}
          onClick={() => setAll((v) => !v)}
        >
          {all ? "Show fewer dishes" : `Show all ${dishes.length} dishes`}
        </button>
      )}
      {truncated && <p className="hint">The page lists more dishes than Makan shows here.</p>}
    </>
  );
}
