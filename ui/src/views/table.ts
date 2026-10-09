/** Results table: input order by default, decision filter chips with counts, search and sort. */

import type { ResultRow } from "../api";
import { button, el } from "../dom";
import { isFailed } from "../failures";
import { foldText } from "../format";
import { DEFAULT_VIEW, type Filter, type SortKey, type ViewState } from "../hash";
import { ICON_MEDIUM, ICON_SMALL, icon } from "../icons";
import {
  FILTER_ORDER,
  compareEntries,
  filterChips,
  filterCounts,
  headCells,
  rowElement,
  searchText,
  type RowEntry,
  type TableHandlers,
} from "./rows";

const SEARCH_DEBOUNCE_MS = 150;

/** The table's handle. */
export interface ResultsTable {
  root: HTMLElement;
  apply: (view: ViewState) => void;
  focusSearch: () => void;
  moveFocus: (delta: number) => void;
  filters: readonly Filter[];
}

/**
 * Tells whether a row passes the filter and the folded query.
 * @param entry Row entry.
 * @param filter Decision filter.
 * @param query Folded query.
 * @returns Whether it is shown.
 */
function isVisible(entry: RowEntry, filter: Filter, query: string): boolean {
  if (filter === "failed" && !isFailed(entry.row)) return false;
  if (filter !== "all" && filter !== "failed" && entry.row.decision !== filter) return false;
  return query === "" || entry.haystack.includes(query);
}

/** The results table and its toolbar. */
class ResultsTableView implements ResultsTable {
  readonly root: HTMLElement;
  readonly filters = FILTER_ORDER;
  private readonly entries: RowEntry[];
  private readonly body = el("tbody");
  private readonly head: HTMLTableCellElement[];
  private readonly chips: Map<Filter, HTMLButtonElement>;
  private readonly search = el("input", {
    attrs: {
      type: "search",
      id: "search",
      placeholder: "Search item no., description, label",
      autocomplete: "off",
    },
  });
  private readonly inputOrder = button(
    "Input order",
    "btn btn--ghost btn--small",
    icon("reset", ICON_SMALL),
  );
  private readonly status = el("p", {
    className: "visually-hidden",
    attrs: { "aria-live": "polite" },
  });
  private readonly empty = el("p", {
    className: "empty",
    text: "No lines match this filter and search.",
  });
  private view: ViewState = DEFAULT_VIEW;
  private timer: number | undefined;

  /**
   * @param rows Result rows in input order.
   * @param handlers App callbacks.
   */
  constructor(
    rows: readonly ResultRow[],
    private readonly handlers: TableHandlers,
  ) {
    this.entries = rows.map((row, index) => ({
      row,
      index,
      tr: rowElement(row, handlers.onOpen),
      haystack: searchText(row),
    }));
    this.head = headCells((key) => this.sortBy(key));
    this.chips = filterChips(filterCounts(rows), (filter) => handlers.onViewChange({ filter }));
    this.root = this.layout();
    this.wire();
  }

  /**
   * Lays out the toolbar, the table and the empty state.
   * @returns The section.
   */
  private layout(): HTMLElement {
    const searchBox = el("div", { className: "search" }, [
      el("label", { className: "visually-hidden", text: "Search lines", attrs: { for: "search" } }),
      icon("search", ICON_MEDIUM),
      this.search,
    ]);
    const group = el(
      "div",
      { className: "filters", attrs: { role: "group", "aria-label": "Filter by decision" } },
      [...this.chips.values()],
    );
    const toolbar = el("div", { className: "toolbar" }, [group, searchBox, this.inputOrder]);
    const table = el("table", { className: "results" }, [
      el("caption", {
        className: "visually-hidden",
        text: "Decision per BoQ line, in input order unless sorted",
      }),
      el("thead", {}, [el("tr", {}, this.head)]),
      this.body,
    ]);
    for (const entry of this.entries) this.body.append(entry.tr);
    return el("section", { className: "panel results-panel", attrs: { "aria-label": "Results" } }, [
      toolbar,
      this.status,
      el("div", { className: "table-wrap" }, [table]),
      this.empty,
    ]);
  }

  /** Wires search, the input-order reset and row keyboard access. */
  private wire(): void {
    this.search.addEventListener("input", () => {
      window.clearTimeout(this.timer);
      this.timer = window.setTimeout(
        () => this.handlers.onViewChange({ query: this.search.value }),
        SEARCH_DEBOUNCE_MS,
      );
    });
    this.inputOrder.addEventListener("click", () =>
      this.handlers.onViewChange({ sort: "input", direction: "asc" }),
    );
    this.body.addEventListener("keydown", (event) => {
      const tr = (event.target as HTMLElement).closest("tr");
      const entry = this.entries.find((candidate) => candidate.tr === tr);
      if (event.key !== "Enter" || !entry || event.target !== tr) return;
      event.preventDefault();
      this.handlers.onOpen(entry.row, entry.tr);
    });
  }

  /**
   * Toggles the sort on a column: a new key sorts ascending, the same key flips direction.
   * @param key Sort key.
   */
  private sortBy(key: SortKey): void {
    const same = this.view.sort === key;
    const direction = same && this.view.direction === "asc" ? "desc" : "asc";
    this.handlers.onViewChange({ sort: key, direction });
  }

  /**
   * Applies a view: filter, search and sort.
   * @param view View state.
   */
  apply(view: ViewState): void {
    this.view = view;
    if (this.search.value !== view.query) this.search.value = view.query;
    const query = foldText(view.query.trim());
    const sorted = [...this.entries].sort((left, right) => {
      const result = compareEntries(view.sort, left, right);
      return view.direction === "desc" && view.sort !== "input" ? -result : result;
    });
    let shown = 0;
    for (const entry of sorted) {
      entry.tr.hidden = !isVisible(entry, view.filter, query);
      shown += entry.tr.hidden ? 0 : 1;
      this.body.append(entry.tr);
    }
    this.syncControls(view, shown);
  }

  /**
   * Syncs chips, headers, the reset button, the empty state and the roving tab stop.
   * @param view View state.
   * @param shown Number of visible rows.
   */
  private syncControls(view: ViewState, shown: number): void {
    for (const [filter, chip] of this.chips)
      chip.setAttribute("aria-pressed", String(filter === view.filter));
    for (const th of this.head) {
      if (!th.dataset["sort"]) continue;
      const active = th.dataset["sort"] === view.sort;
      th.setAttribute(
        "aria-sort",
        active ? (view.direction === "asc" ? "ascending" : "descending") : "none",
      );
    }
    this.inputOrder.hidden = view.sort === "input";
    this.empty.hidden = shown > 0;
    this.status.textContent = `Showing ${shown} of ${this.entries.length} lines`;
    const visible = this.visibleRows();
    const current = visible.find((tr) => tr.tabIndex === 0) ?? visible[0];
    for (const entry of this.entries) entry.tr.tabIndex = entry.tr === current ? 0 : -1;
  }

  /** @returns Visible rows in DOM order. */
  private visibleRows(): HTMLTableRowElement[] {
    return Array.from(this.body.rows).filter((tr) => !tr.hidden);
  }

  /** Focuses the search box. */
  focusSearch(): void {
    this.search.focus();
    this.search.select();
  }

  /**
   * Moves the roving row focus up or down.
   * @param delta +1 for the next row, -1 for the previous.
   */
  moveFocus(delta: number): void {
    const visible = this.visibleRows();
    if (visible.length === 0) return;
    const active = document.activeElement;
    const position = visible.findIndex((tr) => tr === active || tr.tabIndex === 0);
    const next = visible[Math.min(Math.max(position + delta, 0), visible.length - 1)];
    if (!next) return;
    for (const tr of visible) tr.tabIndex = tr === next ? 0 : -1;
    next.focus();
  }
}

/**
 * Builds the results table.
 * @param rows Result rows in input order.
 * @param handlers App callbacks.
 * @returns The table handle.
 */
export function createResultsTable(
  rows: readonly ResultRow[],
  handlers: TableHandlers,
): ResultsTable {
  return new ResultsTableView(rows, handlers);
}
