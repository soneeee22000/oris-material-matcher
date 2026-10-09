/** Running view: determinate progress, elapsed time, ETA, running cost and a placeholder table. */

import type { JobProgress, ServingInfo } from "../api";
import { el } from "../dom";
import { formatDuration, formatUsd } from "../format";
import { REPLAY, modeLabels } from "../mode";
import type { JobMeta } from "../state";

const ANNOUNCE_STEP_PERCENT = 10;
const PERCENT = 100;
const SKELETON_ROWS = 8;
const SKELETON_CELLS = 5;
const CONNECTION_LOST = "Connection lost, retrying…";

/** What the running view shows. */
export interface ProgressInput {
  meta: JobMeta;
  progress: JobProgress | null;
  connectionLost: boolean;
  serving: ServingInfo | null;
}

/** The running view's handle. */
export interface ProgressView {
  root: HTMLElement;
  update: (input: ProgressInput) => void;
}

/** One labelled statistic. */
interface Stat {
  root: HTMLDivElement;
  label: HTMLElement;
  value: HTMLElement;
}

/**
 * Builds one labelled statistic.
 * @param label Label.
 * @returns The wrapper, its label and its value node.
 */
function stat(label: string): Stat {
  const value = el("dd", { text: "—" });
  const term = el("dt", { text: label });
  return { root: el("div", { className: "stat" }, [term, value]), label: term, value };
}

/**
 * Returns the server's mode for a job: from its status, its 202 body, then the server.
 * @param input Running view input.
 * @returns The mode, or null when unknown.
 */
function jobMode(input: ProgressInput): string | null {
  return input.progress?.mode ?? input.meta.created?.mode ?? input.serving?.mode ?? null;
}

/**
 * Words the concurrency note from the server's setting; none in replay, where no model is called.
 * @param input Running view input.
 * @returns The note, or an empty string.
 */
function concurrencyNote(input: ProgressInput): string {
  if (jobMode(input) === REPLAY) return "";
  const concurrency =
    input.progress?.concurrency ?? input.meta.created?.concurrency ?? input.serving?.concurrency;
  return typeof concurrency === "number" ? `Model calls run ${concurrency} at a time.` : "";
}

/**
 * Titles the running view with the file name and the line count the server found.
 * @param input Running view input.
 * @returns The title.
 */
function progressTitle(input: ProgressInput): string {
  const name = input.meta.fileName ?? input.progress?.filename ?? null;
  const total = input.meta.created?.total_lines ?? input.progress?.total ?? null;
  const lines = typeof total === "number" ? ` · ${total} lines` : "";
  return name ? `Matching ${name}${lines}` : `Matching…${lines}`;
}

/**
 * Builds the placeholder table shown while lines are being decided.
 * @returns The table.
 */
function skeletonTable(): HTMLTableElement {
  const body = el("tbody");
  for (let index = 0; index < SKELETON_ROWS; index += 1) {
    const cells = Array.from({ length: SKELETON_CELLS }, () =>
      el("td", {}, [el("span", { className: "skeleton" })]),
    );
    body.append(el("tr", {}, cells));
  }
  return el("table", { className: "skeleton-table", attrs: { "aria-hidden": "true" } }, [body]);
}

/** Progress panel that announces every 10 % instead of every line. */
class ProgressPanel implements ProgressView {
  readonly root: HTMLElement;
  private readonly title = el("h2", { className: "panel__title", text: "Matching…" });
  private readonly bar = el("progress", {
    attrs: { max: "1", value: "0", "aria-label": "Lines decided" },
  });
  private readonly count = el("p", { className: "progress__count", text: "Queued" });
  private readonly live = el("p", {
    className: "visually-hidden",
    attrs: { "aria-live": "polite" },
  });
  private readonly warning = el("p", { className: "banner banner--review", attrs: { hidden: "" } });
  private readonly elapsed = stat("Elapsed");
  private readonly eta = stat("ETA");
  private readonly cost = stat("Running cost");
  private readonly note = el("p", { className: "note" });
  private announced = -1;

  /** Builds the panel. */
  constructor() {
    const stats = el("dl", { className: "stats" }, [
      this.elapsed.root,
      this.eta.root,
      this.cost.root,
    ]);
    this.root = el(
      "section",
      { className: "panel progress", attrs: { "aria-labelledby": "progress-title" } },
      [
        this.title,
        this.bar,
        this.count,
        stats,
        this.note,
        this.warning,
        this.live,
        skeletonTable(),
      ],
    );
    this.title.id = "progress-title";
  }

  /**
   * Shows the latest poll.
   * @param input Job meta, latest progress (null before the first poll), polling state, server.
   */
  update(input: ProgressInput): void {
    const { progress, connectionLost } = input;
    this.title.textContent = progressTitle(input);
    this.note.textContent = concurrencyNote(input);
    this.note.hidden = this.note.textContent === "";
    this.cost.label.textContent = modeLabels(jobMode(input)).runningCost;
    this.warning.hidden = !connectionLost;
    this.warning.textContent = connectionLost ? CONNECTION_LOST : "";
    if (!progress) return;
    const total = Math.max(progress.total, 1);
    this.bar.max = total;
    this.bar.value = progress.done;
    this.count.textContent = `${progress.done} / ${progress.total} lines · ${progress.status}`;
    this.elapsed.value.textContent = formatDuration(progress.elapsed_s);
    this.eta.value.textContent = progress.eta_s === null ? "—" : formatDuration(progress.eta_s);
    this.cost.value.textContent = formatUsd(progress.cost_usd);
    this.announce(Math.floor((progress.done / total) * PERCENT));
  }

  /**
   * Updates the live region when progress crosses a 10 % step.
   * @param percent Whole percent done.
   */
  private announce(percent: number): void {
    const step = Math.floor(percent / ANNOUNCE_STEP_PERCENT) * ANNOUNCE_STEP_PERCENT;
    if (step === this.announced) return;
    this.announced = step;
    this.live.textContent = `${step} percent of lines decided`;
  }
}

/**
 * Builds the running view.
 * @returns The view handle.
 */
export function createProgressView(): ProgressView {
  return new ProgressPanel();
}
