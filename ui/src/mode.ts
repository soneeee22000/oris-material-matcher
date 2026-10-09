/**
 * Honest labels for how a run's numbers were made. A replay copies a recorded run's answers and
 * calls no model, so its wall clock is not model latency and its cost was spent by the recorded
 * run, not now; the offline fake model's numbers are not measurements at all.
 */

import type { ServingInfo } from "./api";

export const REPLAY = "replay";
export const FAKE = "fake";

/** Every mode-dependent label the views show. */
export interface ModeLabels {
  cost: string;
  runningCost: string;
  wallClock: string;
  latencyNote: string;
  attributedNote: string;
}

const LIVE_LABELS: ModeLabels = {
  cost: "Cost",
  runningCost: "Running cost",
  wallClock: "Wall-clock / line",
  latencyNote: "attributed (conservative under concurrency)",
  attributedNote: "attributed",
};

const REPLAY_LABELS: ModeLabels = {
  cost: "Recorded cost (replay, $0 spent)",
  runningCost: "Recorded cost (replay, $0 spent)",
  wallClock: "Replay wall-clock / line (no model called)",
  latencyNote: "recorded run, attributed (conservative under concurrency)",
  attributedNote: "recorded, attributed",
};

const FAKE_LABELS: ModeLabels = {
  cost: "Cost (offline fake model, not a measurement)",
  runningCost: "Cost (offline fake model, not a measurement)",
  wallClock: "Wall-clock / line (offline fake model, not a measurement)",
  latencyNote: "offline fake model, not a measurement",
  attributedNote: "offline fake model, not a measurement",
};

/**
 * Returns the labels for a run mode.
 * @param mode `live`, `cached`, `replay` or `fake`; null when unknown.
 * @returns The labels.
 */
export function modeLabels(mode: string | null | undefined): ModeLabels {
  if (mode === REPLAY) return REPLAY_LABELS;
  if (mode === FAKE) return FAKE_LABELS;
  return LIVE_LABELS;
}

/**
 * Describes the serving mode for the persistent header banner.
 * @param serving What `GET /v1/serving` returned, or null before it answers.
 * @returns Banner text, or an empty string for a live server.
 */
export function servingText(serving: ServingInfo | null): string {
  if (serving?.mode === REPLAY) {
    const run = serving.replay_run ? ` (run ${serving.replay_run})` : "";
    return (
      `Replay of recorded answers${run}: no model is called; a line that is not in the ` +
      "recording goes to Needs review (replay_miss)."
    );
  }
  if (serving?.mode === FAKE) {
    return "Offline fake model: answers are synthetic, nothing is spent, and no number is a measurement.";
  }
  return "";
}
