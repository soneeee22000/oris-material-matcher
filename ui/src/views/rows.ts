/** Building blocks of the results table: rows, chips, headers, filter chips and sort order. */

import type { DecisionValue, ResultRow } from "../api";
import { button, el } from "../dom";
import { isFailed } from "../failures";
import { DECISIONS, DECISION_LABELS, foldText, labelText, levelNumber } from "../format";
import type { Filter, SortKey, ViewState } from "../hash";
import { icon, type IconName } from "../icons";

const HEADER_LEVEL_MAX = 1;
const MAX_INDENT_LEVEL = 6;
const CHIP_ICON_SIZE = 14;
const EMPTY_LABEL = "—";

export const DECISION_ICONS: Record<DecisionValue, IconName> = {
  matched: "circleFilled",
  needs_review: "triangle",
  not_a_material: "circle",
};

export const FILTER_ORDER: readonly Filter[] = ["all", "needs_review", "matched", "not_a_material"];
export const FAILED_FILTER = "failed" satisfies Filter;
const FAILED_LABEL = "Could not be processed";

/** Callbacks from the table to the app. */
export interface TableHandlers {
  onOpen: (row: ResultRow, origin: HTMLElement) => void;
  onViewChange: (change: Partial<ViewState>) => void;
}

/** One rendered row and what search and sort need. */
export interface RowEntry {
  row: ResultRow;
  index: number;
  tr: HTMLTableRowElement;
  haystack: string;
}

/**
 * Builds a decision chip that pairs a shape and text with its colour.
 * @param decision Decision.
 * @returns The chip.
 */
export function decisionChip(decision: DecisionValue): HTMLSpanElement {
  return el("span", { className: `chip chip--${decision}` }, [
    icon(DECISION_ICONS[decision], CHIP_ICON_SIZE),
    el("span", { text: DECISION_LABELS[decision] }),
  ]);
}

/**
 * Builds the description cell: the short text, then the long text as a muted 2-line clamp.
 * @param row Result row.
 * @returns The cell.
 */
function descriptionCell(row: ResultRow): HTMLTableCellElement {
  const long = row.long && row.long !== row.short ? row.long : "";
  return el("td", { className: "cell-desc" }, [
    el("span", { className: "desc-short", text: row.short || long || EMPTY_LABEL }),
    long && row.short ? el("span", { className: "desc-long", text: long }) : null,
  ]);
}

/**
 * Builds one table row; every BoQ value goes through textContent.
 * @param row Result row.
 * @param onOpen Opens the audit drawer.
 * @returns The row element.
 */
export function rowElement(row: ResultRow, onOpen: TableHandlers["onOpen"]): HTMLTableRowElement {
  const open = el("button", {
    className: "row-open",
    attrs: { type: "button", "aria-label": `Open audit for ${row.item_no || row.line_id}` },
  });
  open.append(icon("chevron"));
  const tr = el("tr", { attrs: { tabindex: "-1" } }, [
    el("td", { className: "cell-item", text: row.item_no || EMPTY_LABEL }),
    descriptionCell(row),
    el("td", { className: "cell-unit", text: row.unit || "" }),
    el("td", { className: "cell-decision" }, [decisionChip(row.decision)]),
    el("td", {
      className: labelText(row) ? "cell-label" : "cell-label cell-label--empty",
      text: labelText(row) || EMPTY_LABEL,
    }),
    el("td", { className: "cell-reason", text: row.reason }),
    el("td", { className: "cell-open" }, [open]),
  ]);
  decorateRow(tr, row);
  open.addEventListener("click", () => onOpen(row, tr));
  tr.addEventListener("dblclick", () => onOpen(row, tr));
  return tr;
}

/**
 * Sets the data attributes and classes that drive the amber marker and indentation.
 * @param tr Row element.
 * @param row Result row.
 */
function decorateRow(tr: HTMLTableRowElement, row: ResultRow): void {
  tr.dataset["itemNo"] = row.item_no;
  tr.dataset["decision"] = row.decision;
  tr.dataset["lineId"] = row.line_id;
  tr.className = `row row--${row.decision === "needs_review" ? "review" : row.decision}`;
  const level = levelNumber(row.level);
  if (level === null) return;
  tr.style.setProperty("--level", String(Math.min(Math.max(level, 0), MAX_INDENT_LEVEL)));
  if (level <= HEADER_LEVEL_MAX && row.decision === "not_a_material")
    tr.classList.add("row--heading");
}

/**
 * Compares two rows under a sort key, falling back to input order.
 * @param key Sort key.
 * @param left Left entry.
 * @param right Right entry.
 * @returns Comparison result.
 */
export function compareEntries(key: SortKey, left: RowEntry, right: RowEntry): number {
  let result = 0;
  if (key === "item_no") {
    result = left.row.item_no.localeCompare(right.row.item_no, undefined, { numeric: true });
  } else if (key === "decision") {
    result = DECISIONS.indexOf(left.row.decision) - DECISIONS.indexOf(right.row.decision);
  }
  return result === 0 ? left.index - right.index : result;
}

/**
 * Builds a sortable column header with `aria-sort`.
 * @param label Header text.
 * @param key Sort key.
 * @param onSort Called when clicked.
 * @returns The header cell.
 */
export function sortableHeader(
  label: string,
  key: SortKey,
  onSort: (key: SortKey) => void,
): HTMLTableCellElement {
  const toggle = button(label, "sort-toggle");
  toggle.append(el("span", { className: "sort-arrow", attrs: { "aria-hidden": "true" } }));
  toggle.addEventListener("click", () => onSort(key));
  const th = el("th", { attrs: { scope: "col", "aria-sort": "none" } }, [toggle]);
  th.dataset["sort"] = key;
  return th;
}

/**
 * Builds the table head.
 * @param onSort Sort handler.
 * @returns The head row's cells.
 */
export function headCells(onSort: (key: SortKey) => void): HTMLTableCellElement[] {
  const plain = (text: string): HTMLTableCellElement => el("th", { text, attrs: { scope: "col" } });
  return [
    sortableHeader("Item No.", "item_no", onSort),
    plain("Description"),
    plain("Unit"),
    sortableHeader("Decision", "decision", onSort),
    plain("ORIS label (type · usage · subtype)"),
    plain("Reason"),
    el("th", { attrs: { scope: "col" } }, [el("span", { className: "visually-hidden", text: "Audit" })]),
  ];
}

/**
 * Counts rows per filter.
 * @param rows Result rows.
 * @returns Count per filter.
 */
export function filterCounts(rows: readonly ResultRow[]): Record<Filter, number> {
  const counts: Record<Filter, number> = {
    all: rows.length,
    needs_review: 0,
    matched: 0,
    not_a_material: 0,
    failed: 0,
  };
  for (const row of rows) {
    counts[row.decision] += 1;
    if (isFailed(row)) counts.failed += 1;
  }
  return counts;
}

/**
 * Builds one filter chip showing its count.
 * @param filter Filter.
 * @param count Rows it shows.
 * @param onFilter Filter handler.
 * @returns The chip.
 */
function filterChip(
  filter: Filter,
  count: number,
  onFilter: (filter: Filter) => void,
): HTMLButtonElement {
  const label =
    filter === "all" ? "All" : filter === FAILED_FILTER ? FAILED_LABEL : DECISION_LABELS[filter];
  const chip = button(`${label} ${count}`, `filter-chip filter-chip--${filter}`);
  if (filter === FAILED_FILTER) chip.prepend(icon("alert", CHIP_ICON_SIZE));
  else if (filter !== "all") chip.prepend(icon(DECISION_ICONS[filter], CHIP_ICON_SIZE));
  chip.dataset["filter"] = filter;
  chip.addEventListener("click", () => onFilter(filter));
  return chip;
}

/**
 * Builds the filter chips; each chip shows its count. The "Could not be processed" chip appears
 * only when some line failed.
 * @param counts Count per filter.
 * @param onFilter Filter handler.
 * @returns The chips by filter, in display order.
 */
export function filterChips(
  counts: Record<Filter, number>,
  onFilter: (filter: Filter) => void,
): Map<Filter, HTMLButtonElement> {
  const chips = new Map<Filter, HTMLButtonElement>();
  for (const [position, filter] of FILTER_ORDER.entries()) {
    const chip = filterChip(filter, counts[filter], onFilter);
    chip.setAttribute("aria-keyshortcuts", String(position + 1));
    chips.set(filter, chip);
  }
  if (counts.failed > 0)
    chips.set(FAILED_FILTER, filterChip(FAILED_FILTER, counts.failed, onFilter));
  return chips;
}

/**
 * Builds a row's folded search text: item no., descriptions and labels.
 * @param row Result row.
 * @returns Case- and accent-folded text.
 */
export function searchText(row: ResultRow): string {
  return foldText([row.item_no, row.short, row.long, labelText(row)].join("\n"));
}
