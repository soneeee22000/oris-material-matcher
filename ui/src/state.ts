/** The single-page state machine: idle → uploading → running → done | partial | error. */

import type { JobCreated, JobProgress, JobResult } from "./api";

/** A failure the error card can explain. */
export interface UiError {
  title: string;
  cause: string;
  detail: string;
  needsToken: boolean;
  retry: (() => void) | null;
}

/** What the job is about, kept across a refresh in session storage. */
export interface JobMeta {
  fileName: string | null;
  libraryId: string;
  created: JobCreated | null;
}

export type Phase =
  | { kind: "idle" }
  | { kind: "uploading" }
  | {
      kind: "running";
      meta: JobMeta;
      progress: JobProgress | null;
      connectionLost: boolean;
    }
  | {
      kind: "done";
      meta: JobMeta;
      progress: JobProgress | null;
      result: JobResult;
    }
  | {
      kind: "partial";
      meta: JobMeta;
      progress: JobProgress | null;
      result: JobResult;
    }
  | { kind: "error"; error: UiError };

export type PhaseKind = Phase["kind"];

const TRANSITIONS: Record<PhaseKind, readonly PhaseKind[]> = {
  idle: ["idle", "uploading", "running", "error"],
  uploading: ["running", "error", "idle"],
  running: ["running", "done", "partial", "error", "idle"],
  done: ["idle", "uploading", "running", "done", "error"],
  partial: ["idle", "uploading", "running", "partial", "error"],
  error: ["idle", "uploading", "running", "error"],
};

type Listener = (phase: Phase) => void;

/** Holds the current phase and refuses transitions the machine does not allow. */
export class Machine {
  private phase: Phase = { kind: "idle" };
  private readonly listeners: Listener[] = [];

  /** @returns The current phase. */
  get current(): Phase {
    return this.phase;
  }

  /**
   * Registers a listener called after every accepted transition.
   * @param listener Listener.
   */
  subscribe(listener: Listener): void {
    this.listeners.push(listener);
  }

  /**
   * Moves to the next phase when the edge is allowed.
   * @param next Next phase.
   * @returns Whether the transition happened.
   */
  go(next: Phase): boolean {
    if (!TRANSITIONS[this.phase.kind].includes(next.kind)) return false;
    this.phase = next;
    for (const listener of this.listeners) listener(next);
    return true;
  }
}
