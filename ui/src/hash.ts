/** View state kept in `location.hash`, so a refresh or a shared link keeps the job and the view. */

import type { DecisionValue } from "./api";

export type Filter = "all" | DecisionValue | "failed";
export type SortKey = "input" | "item_no" | "decision";
export type SortDirection = "asc" | "desc";

/** Everything the hash holds. */
export interface ViewState {
  job: string | null;
  filter: Filter;
  sort: SortKey;
  direction: SortDirection;
  query: string;
}

const FILTERS: readonly Filter[] = ["all", "needs_review", "matched", "not_a_material", "failed"];
const SORT_KEYS: readonly SortKey[] = ["input", "item_no", "decision"];
const DIRECTION_SEPARATOR = ":";

export const DEFAULT_VIEW: ViewState = {
  job: null,
  filter: "all",
  sort: "input",
  direction: "asc",
  query: "",
};

/**
 * Narrows a string to one of a list of allowed values.
 * @param value Raw value.
 * @param allowed Allowed values.
 * @param fallback Fallback.
 * @returns The value when allowed, else the fallback.
 */
function oneOf<T extends string>(value: string | null, allowed: readonly T[], fallback: T): T {
  return allowed.find((candidate) => candidate === value) ?? fallback;
}

/**
 * Parses the current hash.
 * @param hash `location.hash`.
 * @returns The view state, defaults filled in.
 */
export function parseHash(hash: string): ViewState {
  const params = new URLSearchParams(hash.replace(/^#/, ""));
  const [sortRaw = null, directionRaw = null] = (params.get("sort") ?? "").split(
    DIRECTION_SEPARATOR,
  );
  return {
    job: params.get("job") || null,
    filter: oneOf(params.get("filter"), FILTERS, DEFAULT_VIEW.filter),
    sort: oneOf(sortRaw, SORT_KEYS, DEFAULT_VIEW.sort),
    direction: directionRaw === "desc" ? "desc" : "asc",
    query: params.get("q") ?? "",
  };
}

/**
 * Serialises a view state, leaving defaults out.
 * @param view View state.
 * @returns The hash, with its leading `#`, or an empty string.
 */
export function formatHash(view: ViewState): string {
  const params = new URLSearchParams();
  if (view.job) params.set("job", view.job);
  if (view.filter !== DEFAULT_VIEW.filter) params.set("filter", view.filter);
  if (view.sort !== DEFAULT_VIEW.sort)
    params.set("sort", `${view.sort}${DIRECTION_SEPARATOR}${view.direction}`);
  if (view.query) params.set("q", view.query);
  const text = params.toString();
  return text ? `#${text}` : "";
}

/**
 * Writes the view state to the hash without adding a history entry.
 * @param view View state.
 */
export function writeHash(view: ViewState): void {
  const next = formatHash(view);
  if (next === location.hash || (next === "" && location.hash === "")) return;
  history.replaceState(null, "", `${location.pathname}${location.search}${next}`);
}
