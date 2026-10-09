/**
 * App header: wordmark, the library and policy a result came from, the theme toggle, and a
 * persistent banner when the server replays a recording or runs the offline fake model.
 */

import type { JobLibrary, ServingInfo } from "../api";
import { el } from "../dom";
import { icon } from "../icons";
import { servingText } from "../mode";

const THEME_KEY = "oris-ui-theme";
const DEFAULT_THEME: Theme = "dark";
const LIBRARY_NAMES: Record<string, string> = { global: "Global", fr: "FR" };

type Theme = "light" | "dark";

/** What the header says about the library a job ran against. */
export interface LibraryBadge {
  libraryId: string;
  library: JobLibrary | null;
  policy: string | null;
}

/** The header's handle. */
export interface AppHeader {
  root: HTMLElement;
  showLibrary: (badge: LibraryBadge | null) => void;
  showServing: (serving: ServingInfo | null) => void;
}

/**
 * Reads the remembered theme; storage can be blocked, so this never throws.
 * @returns The theme.
 */
function storedTheme(): Theme {
  try {
    const value = localStorage.getItem(THEME_KEY);
    return value === "light" || value === "dark" ? value : DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}

/**
 * Applies and remembers a theme.
 * @param theme Theme.
 * @param toggle The toggle button, relabelled for the next action.
 */
function applyTheme(theme: Theme, toggle: HTMLButtonElement): void {
  document.documentElement.dataset["theme"] = theme;
  const next = theme === "dark" ? "light" : "dark";
  toggle.setAttribute("aria-label", `Switch to ${next} theme`);
  toggle.title = `Switch to ${next} theme`;
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    return;
  }
}

/**
 * Describes the library a job ran against: name, row count, short hash and policy resolution.
 * @param badge The server's library record, the job's library id and the policy.
 * @returns Badge text, or an empty string when nothing is known.
 */
export function libraryText(badge: LibraryBadge | null): string {
  if (!badge) return "";
  const { library, libraryId, policy } = badge;
  const id = library?.name ?? libraryId;
  const name = LIBRARY_NAMES[id] ?? id;
  const parts = [library ? `Library: ${name} (${library.rows} rows)` : `Library: ${name}`];
  if (library?.sha256_12) parts.push(library.sha256_12);
  if (policy) parts.push(`policy ${policy}`);
  return parts.join(" · ");
}

/**
 * Builds the header.
 * @returns The header handle.
 */
export function createHeader(): AppHeader {
  const toggle = el("button", { className: "icon-btn theme-toggle", attrs: { type: "button" } });
  toggle.append(icon("theme"));
  applyTheme(storedTheme(), toggle);
  toggle.addEventListener("click", () => {
    applyTheme(document.documentElement.dataset["theme"] === "dark" ? "light" : "dark", toggle);
  });
  const badge = el("p", { className: "library-badge mono", attrs: { hidden: "" } });
  const servingLabel = el("span");
  const serving = el("p", { className: "mode-banner", attrs: { hidden: "", role: "note" } }, [
    icon("alert"),
    servingLabel,
  ]);
  const mark = el("span", { className: "wordmark__mark", attrs: { "aria-hidden": "true" } });
  const root = el("header", { className: "app-header" }, [
    el("div", { className: "app-header__inner" }, [
      el("h1", { className: "wordmark" }, [mark, el("span", { text: "BoQ Matcher" })]),
      badge,
      toggle,
    ]),
    serving,
  ]);
  return {
    root,
    showLibrary: (next) => {
      badge.textContent = libraryText(next);
      badge.hidden = badge.textContent === "";
    },
    showServing: (info) => {
      servingLabel.textContent = servingText(info);
      serving.hidden = servingLabel.textContent === "";
    },
  };
}
