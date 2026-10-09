/** Done view header: run line, Download CSV / New file, partial-failure banner and the summary bar. */

import type { JobProgress, JobResult, ResultSummary } from "../api";
import { button, el } from "../dom";
import { failureCounts } from "../failures";
import { formatDuration, formatUsd } from "../format";
import { ICON_SMALL, icon, type IconName } from "../icons";
import { FAKE, REPLAY, modeLabels } from "../mode";
import type { JobMeta } from "../state";

const UTF8_ENCODINGS = new Set(["utf-8", "utf8", "utf-8-sig"]);
const WALL_CLOCK_DIGITS = 2;
const REPLAY_COST_NOTE =
  "Replay: answers come from a recorded run, so nothing is spent; this is the recorded run's cost.";
const REPLAY_WALL_CLOCK_NOTE =
  "Replay copies recorded answers, so this is not model latency; see the recorded run for latency.";
const REPLAY_RETRY_NOTE =
  "Retrying needs a live model: this server replays a recording, which has no answer for them.";
const FAILURE_POLICY =
  "They are marked Needs review with the reason code shown verbatim, and are never dropped or guessed.";

/** Actions the done view offers. */
export interface SummaryHandlers {
  onDownload: () => void;
  onNew: () => void;
  onShowFailed: () => void;
  onRetryFailed: () => void;
}

/** Inputs for the done view header. */
export interface SummaryInput {
  meta: JobMeta;
  progress: JobProgress | null;
  result: JobResult;
  partial: boolean;
  mode: string | null;
}

/**
 * Builds one summary tile.
 * @param label Label.
 * @param value Value text.
 * @param tone Tile tone class suffix.
 * @param iconName Optional icon that pairs shape with colour.
 * @returns The tile.
 */
function tile(
  label: string,
  value: string,
  tone: string,
  iconName: IconName | null = null,
): HTMLDivElement {
  const head = el("dt", {}, [
    iconName ? icon(iconName, ICON_SMALL) : null,
    el("span", { text: label }),
  ]);
  return el("div", { className: `tile tile--${tone}` }, [head, el("dd", { text: value })]);
}

/**
 * Words the mean and p95 attributed latency, labelled for the mode.
 * @param summary Result summary.
 * @param mode Run mode.
 * @returns The note, or an empty string when the run made no model call.
 */
function latencyText(summary: ResultSummary, mode: string | null): string {
  const mean = summary.mean_attributed_latency_ms;
  const p95 = summary.p95_attributed_latency_ms;
  if (typeof mean !== "number" || typeof p95 !== "number") return "";
  const note = modeLabels(mode).latencyNote;
  return `Latency per line, ${note}: mean ${Math.round(mean)} ms, p95 ${Math.round(p95)} ms`;
}

/**
 * Builds the wall-clock tile, with the attributed latency shown under it, visibly labelled.
 * @param summary Result summary.
 * @param mode Run mode.
 * @returns The tile.
 */
function wallClockTile(summary: ResultSummary, mode: string | null): HTMLDivElement {
  const perLine = summary.wall_clock_s_per_line;
  const value = typeof perLine === "number" ? `${perLine.toFixed(WALL_CLOCK_DIGITS)} s` : "—";
  const node = tile(modeLabels(mode).wallClock, value, "neutral");
  if (mode === REPLAY) node.title = REPLAY_WALL_CLOCK_NOTE;
  const latency = latencyText(summary, mode);
  if (latency) node.append(el("p", { className: "tile__note", text: latency }));
  return node;
}

/**
 * Builds the cost tile; a replay shows the recorded run's cost, labelled so, since it spends $0.
 * @param summary Result summary.
 * @param mode Run mode.
 * @returns The tile.
 */
function costTile(summary: ResultSummary, mode: string | null): HTMLDivElement {
  const node = tile(modeLabels(mode).cost, formatUsd(summary.cost_usd), "neutral");
  if (mode === REPLAY) node.title = REPLAY_COST_NOTE;
  return node;
}

/**
 * Builds the summary bar: decision counts, cost, wall-clock per line and policy resolution.
 * @param summary Result summary.
 * @param mode Run mode.
 * @returns The bar.
 */
function summaryBar(summary: ResultSummary, mode: string | null): HTMLElement {
  return el("dl", { className: "summary", attrs: { "aria-label": "Run summary" } }, [
    tile("Matched", String(summary.matched), "matched", "circleFilled"),
    tile("Needs review", String(summary.needs_review), "review", "triangle"),
    tile("Not a material", String(summary.not_a_material), "muted", "circle"),
    costTile(summary, mode),
    wallClockTile(summary, mode),
    tile("Policy", summary.policy_resolution || "—", "text"),
  ]);
}

/**
 * Names where the answers came from: the served models, a recording of them, or the fake model.
 * @param models Served model ids.
 * @param mode Run mode.
 * @returns The run-line part, or an empty string.
 */
function sourceText(models: readonly string[], mode: string | null): string {
  const names = models.join(", ");
  if (mode === REPLAY)
    return names ? `recorded answers of ${names}, replayed at $0` : "replayed at $0";
  if (mode === FAKE) return "offline fake model, no API call";
  return names ? `model ${names}` : "";
}

/**
 * Builds the run line: file, line count, duration and where the answers came from.
 * @param input Done view input.
 * @returns The paragraph.
 */
function runLine(input: SummaryInput): HTMLParagraphElement {
  const parts = [input.meta.fileName ?? "Uploaded BoQ", `${input.result.rows.length} lines`];
  if (input.progress) parts.push(`done in ${formatDuration(input.progress.elapsed_s)}`);
  const source = sourceText(input.result.summary.served_models ?? [], input.mode);
  if (source) parts.push(source);
  return el("p", { className: "run-line", text: parts.join(" · ") });
}

/**
 * Words the partial-failure banner: the failed-line count and each reason code, verbatim.
 * @param input Done view input.
 * @returns The text.
 */
function failureText(input: SummaryInput): string {
  const counts = failureCounts(input.result.rows);
  const errors = input.result.summary.errors;
  const reasons = [...counts].map(([reason, count]) => `${count} × ${reason}`).join(", ");
  const head = `${errors} line${errors === 1 ? "" : "s"} could not be processed`;
  return `${reasons ? `${head}: ${reasons}` : head}. ${FAILURE_POLICY}`;
}

/**
 * Builds the partial-failure banner with "Show these lines" and "Retry failed lines".
 * @param input Done view input.
 * @param handlers Done view actions.
 * @returns The banner.
 */
function failureBanner(input: SummaryInput, handlers: SummaryHandlers): HTMLElement {
  const show = button("Show these lines", "btn btn--small", icon("filter", ICON_SMALL));
  show.addEventListener("click", handlers.onShowFailed);
  const actions = el("div", { className: "banner__actions" }, [show]);
  if (input.mode === REPLAY) {
    actions.append(el("span", { className: "banner__note", text: REPLAY_RETRY_NOTE }));
  } else {
    const retry = button("Retry failed lines", "btn btn--small", icon("reset", ICON_SMALL));
    retry.addEventListener("click", handlers.onRetryFailed);
    actions.append(retry);
  }
  return el(
    "div",
    { className: "banner banner--review banner--failure", attrs: { role: "status" } },
    [
      icon("alert"),
      el("div", { className: "banner__body" }, [el("p", { text: failureText(input) }), actions]),
    ],
  );
}

/**
 * Builds the warning banners: partial failure, non-UTF-8 decoding and server warnings.
 * @param input Done view input.
 * @param handlers Done view actions.
 * @returns The banners, possibly none.
 */
function banners(input: SummaryInput, handlers: SummaryHandlers): HTMLElement[] {
  const notes: string[] = [];
  const encoding = input.meta.created?.encoding;
  if (encoding && !UTF8_ENCODINGS.has(encoding.toLowerCase())) {
    notes.push(`The file was not UTF-8 and was decoded as ${encoding}. Check accented text.`);
  }
  notes.push(...(input.meta.created?.warnings ?? []));
  const plain = notes.map((text) =>
    el("p", { className: "banner banner--review", attrs: { role: "status" } }, [
      icon("alert"),
      el("span", { text }),
    ]),
  );
  return input.partial ? [failureBanner(input, handlers), ...plain] : plain;
}

/**
 * Builds the done view header.
 * @param input Done view input.
 * @param handlers Download, reset, show-failed and retry actions.
 * @returns The section.
 */
export function createSummary(input: SummaryInput, handlers: SummaryHandlers): HTMLElement {
  const download = button("Download CSV", "btn btn--primary", icon("download"));
  download.addEventListener("click", handlers.onDownload);
  const reset = button("New file", "btn", icon("reset"));
  reset.addEventListener("click", handlers.onNew);
  const head = el("div", { className: "run-head" }, [
    runLine(input),
    el("div", { className: "actions" }, [download, reset]),
  ]);
  return el("section", { className: "panel done", attrs: { "aria-label": "Run result" } }, [
    head,
    ...banners(input, handlers),
    summaryBar(input.result.summary, input.mode),
  ]);
}
