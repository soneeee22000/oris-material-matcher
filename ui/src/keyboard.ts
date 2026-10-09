/** Global shortcuts: `/` search, `1–4` filters, `d` download, `j`/`k` or arrows move the row focus. */

import type { Filter } from "./hash";

/** What the shortcuts act on; each is available only while results are shown. */
export interface ShortcutTarget {
  active: () => boolean;
  focusSearch: () => void;
  setFilter: (filter: Filter) => void;
  filters: () => readonly Filter[];
  download: () => void;
  moveFocus: (delta: number) => void;
}

const TYPING_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);
const NEXT_KEYS = new Set(["j", "ArrowDown"]);
const PREVIOUS_KEYS = new Set(["k", "ArrowUp"]);
const ARROW_KEYS = new Set(["ArrowDown", "ArrowUp"]);

/**
 * Tells whether a keydown should be left alone: typing, modifiers or an open dialog.
 * @param event The keydown.
 * @returns Whether to ignore it.
 */
function ignored(event: KeyboardEvent): boolean {
  if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return true;
  const target = event.target as HTMLElement | null;
  if (!target) return false;
  if (TYPING_TAGS.has(target.tagName) || target.isContentEditable) return true;
  return target.closest("dialog[open]") !== null;
}

/**
 * Handles one keydown against the target.
 * @param event The keydown.
 * @param target Shortcut target.
 */
function handle(event: KeyboardEvent, target: ShortcutTarget): void {
  const onRow = (event.target as HTMLElement | null)?.tagName === "TR";
  if (event.key === "/") {
    event.preventDefault();
    target.focusSearch();
  } else if (/^[1-4]$/.test(event.key)) {
    const filter = target.filters()[Number(event.key) - 1];
    if (filter) target.setFilter(filter);
  } else if (event.key === "d") {
    target.download();
  } else if (
    (NEXT_KEYS.has(event.key) || PREVIOUS_KEYS.has(event.key)) &&
    (onRow || !ARROW_KEYS.has(event.key))
  ) {
    event.preventDefault();
    target.moveFocus(NEXT_KEYS.has(event.key) ? 1 : -1);
  }
}

/**
 * Installs the global shortcuts.
 * @param target Shortcut target.
 */
export function installShortcuts(target: ShortcutTarget): void {
  document.addEventListener("keydown", (event) => {
    if (ignored(event) || !target.active()) return;
    handle(event, target);
  });
}
