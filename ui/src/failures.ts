/**
 * Lines the model could not process (`audit.error` set: `LLM_FAILURE:<kind>`, `LLM_UNAVAILABLE`
 * or `BUDGET_CAP`), and the retry file that sends only them, with their section headers, to a
 * new job. Nothing here decides a line: the server re-reads the file and re-derives the sections.
 */

import type { ResultRow } from "./api";
import { levelNumber } from "./format";

const CSV_HEADER = ["Item No.", "Short Description", "Long Description", "Unit", "BoQ Qty"];
const CSV_LINE_END = "\r\n";
const CSV_TYPE = "text/csv";
const HEADER_KIND_PREFIX = "header";
const RETRY_SUFFIX = "_retry_failed.csv";
const QUOTE = '"';

/**
 * Tells whether a row is a line the model could not process.
 * @param row Result row.
 * @returns Whether its audit carries a failure reason.
 */
export function isFailed(row: ResultRow): boolean {
  const error = row.audit?.error;
  return typeof error === "string" && error !== "";
}

/**
 * Counts the failed lines per reason code, verbatim, in first-seen order.
 * @param rows Result rows.
 * @returns Count per reason code.
 */
export function failureCounts(rows: readonly ResultRow[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const row of rows) {
    if (!isFailed(row)) continue;
    const reason = String(row.audit?.error);
    counts.set(reason, (counts.get(reason) ?? 0) + 1);
  }
  return counts;
}

/**
 * Tells whether a row is a section header (confirmed or not).
 * @param row Result row.
 * @returns Whether it is a header.
 */
function isHeader(row: ResultRow): boolean {
  return (row.kind ?? "").startsWith(HEADER_KIND_PREFIX);
}

/**
 * Collects the section headers above a line, nearest level first, walking back up the file.
 * @param rows All rows in input order.
 * @param position The line's position.
 * @returns Positions of its ancestor headers.
 */
function ancestorHeaders(rows: readonly ResultRow[], position: number): number[] {
  let needed = levelNumber(rows[position]?.level ?? null) ?? 0;
  const found: number[] = [];
  for (let index = position - 1; index >= 0 && needed > 0; index -= 1) {
    const candidate = rows[index];
    const level = candidate ? levelNumber(candidate.level ?? null) : null;
    if (!candidate || !isHeader(candidate) || level === null || level >= needed) continue;
    found.push(index);
    needed = level;
  }
  return found;
}

/**
 * Picks the failed lines and their section headers, in input order.
 * @param rows All rows in input order.
 * @returns The rows a retry sends.
 */
export function retryRows(rows: readonly ResultRow[]): ResultRow[] {
  const keep = new Set<number>();
  rows.forEach((row, position) => {
    if (!isFailed(row)) return;
    keep.add(position);
    for (const header of ancestorHeaders(rows, position)) keep.add(header);
  });
  return [...keep].sort((left, right) => left - right).flatMap((position) => rows[position] ?? []);
}

/**
 * Quotes one CSV cell, doubling inner quotes.
 * @param value Cell value.
 * @returns The quoted cell.
 */
function csvCell(value: unknown): string {
  const text = value === null || value === undefined ? "" : String(value);
  return `${QUOTE}${text.split(QUOTE).join(QUOTE + QUOTE)}${QUOTE}`;
}

/**
 * Writes rows as a BoQ CSV with the five required columns.
 * @param rows Rows to write.
 * @returns The CSV text.
 */
export function retryCsv(rows: readonly ResultRow[]): string {
  const records = rows.map((row) => [row.item_no, row.short, row.long, row.unit, row.qty]);
  return [CSV_HEADER, ...records]
    .map((record) => record.map(csvCell).join(","))
    .join(CSV_LINE_END)
    .concat(CSV_LINE_END);
}

/**
 * Builds the retry upload: the failed lines and their headers as a new CSV file.
 * @param rows All rows of the finished job, in input order.
 * @param stem The original file's stem.
 * @returns The file.
 */
export function retryFile(rows: readonly ResultRow[], stem: string): File {
  return new File([retryCsv(retryRows(rows))], `${stem}${RETRY_SUFFIX}`, { type: CSV_TYPE });
}
