/** Pure formatting helpers: no DOM, no state. */

import type { DecisionValue, ResultRow } from "./api";

const SECONDS_PER_MINUTE = 60;
const BYTES_PER_KIB = 1024;
const BYTES_PER_MIB = BYTES_PER_KIB * BYTES_PER_KIB;
const USD_TOTAL_DIGITS = 4;
const USD_LINE_DIGITS = 6;
const SECONDS_DIGITS = 1;
const PAD_WIDTH = 2;
const DECIMAL_RADIX = 10;
const COMBINING_MARKS = /[̀-ͯ]/g;
const LABEL_SEPARATOR = " · ";
const EMPTY = "—";

export const DECISION_LABELS: Record<DecisionValue, string> = {
  matched: "Matched",
  needs_review: "Needs review",
  not_a_material: "Not a material",
};

export const DECISIONS: readonly DecisionValue[] = ["needs_review", "matched", "not_a_material"];

/**
 * Formats seconds as `1 m 24 s` or `12.3 s`.
 * @param seconds Seconds, may be fractional.
 * @returns Readable duration.
 */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return EMPTY;
  if (seconds < SECONDS_PER_MINUTE) return `${seconds.toFixed(SECONDS_DIGITS)} s`;
  const minutes = Math.floor(seconds / SECONDS_PER_MINUTE);
  const rest = Math.round(seconds - minutes * SECONDS_PER_MINUTE);
  return `${minutes} m ${rest} s`;
}

/**
 * Formats a USD amount.
 * @param value Amount.
 * @param perLine Use the per-line precision (6 digits) instead of the total (4 digits).
 * @returns `$0.0042`, or a dash when absent.
 */
export function formatUsd(value: number | null | undefined, perLine = false): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return EMPTY;
  return `$${value.toFixed(perLine ? USD_LINE_DIGITS : USD_TOTAL_DIGITS)}`;
}

/**
 * Formats a byte count.
 * @param bytes Size in bytes.
 * @returns `12 KB` or `1.4 MB`.
 */
export function formatBytes(bytes: number): string {
  if (bytes >= BYTES_PER_MIB) return `${(bytes / BYTES_PER_MIB).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / BYTES_PER_KIB))} KB`;
}

/**
 * Folds text for case- and accent-insensitive search.
 * @param text Any text.
 * @returns Lower-case text with combining marks removed.
 */
export function foldText(text: string): string {
  return text.normalize("NFD").replace(COMBINING_MARKS, "").toLowerCase();
}

/**
 * Joins a row's ORIS labels as `type · usage · subtype`.
 * @param row Result row.
 * @returns The label, or an empty string when the row has none.
 */
export function labelText(row: ResultRow): string {
  const parts = [row.material_type, row.material_usage, row.material_subtype];
  return parts.every((part) => !part)
    ? ""
    : parts.map((part) => part ?? EMPTY).join(LABEL_SEPARATOR);
}

/**
 * Renders any API value as display text; objects become compact JSON.
 * @param value Value.
 * @returns Text, empty for null or undefined.
 */
export function displayValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value) && value.every((item) => typeof item === "string"))
    return value.join("; ");
  return JSON.stringify(value);
}

/**
 * Pretty-prints a value as JSON; a string that is JSON is parsed first.
 * @param value Value.
 * @returns Indented JSON or the original string.
 */
export function prettyJson(value: unknown): string {
  if (typeof value !== "string") return JSON.stringify(value, null, PAD_WIDTH);
  try {
    return JSON.stringify(JSON.parse(value), null, PAD_WIDTH);
  } catch {
    return value;
  }
}

/**
 * Returns the file name without its extension.
 * @param name File name.
 * @returns The stem.
 */
export function fileStem(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(0, dot) : name;
}

/**
 * Builds the download name `{input_stem}_matched_{library}_{yyyymmdd-hhmm}.csv`.
 * @param stem Input file stem.
 * @param library Library id.
 * @param now Current time.
 * @returns The file name.
 */
export function csvFilename(stem: string, library: string, now: Date): string {
  const pad = (value: number): string => String(value).padStart(PAD_WIDTH, "0");
  const date = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`;
  const time = `${pad(now.getHours())}${pad(now.getMinutes())}`;
  return `${stem}_matched_${library}_${date}-${time}.csv`;
}

/**
 * Reads a row's hierarchy level as a number (`2`, `"2"` or `"L2"`).
 * @param level Raw level.
 * @returns The level, or null when absent.
 */
export function levelNumber(level: ResultRow["level"]): number | null {
  if (typeof level === "number") return level;
  if (typeof level !== "string") return null;
  const parsed = Number.parseInt(level.replace(/^L/i, ""), DECIMAL_RADIX);
  return Number.isNaN(parsed) ? null : parsed;
}
