/** Polls a job every second; on network errors it backs off 1 → 2 → 5 s and keeps trying. */

import { getJob, NetworkError, type JobProgress } from "./api";

const POLL_INTERVAL_MS = 1000;
const BACKOFF_MS: readonly number[] = [1000, 2000, 5000];
const ACTIVE_STATUSES = new Set(["queued", "running"]);

/** What the poller reports. */
export interface PollHandlers {
  onProgress: (progress: JobProgress) => void;
  onFinished: (progress: JobProgress) => void;
  onConnectionLost: () => void;
  onFailure: (error: unknown) => void;
}

/** Polls one job at a time; starting or stopping invalidates any poll in flight. */
export class JobPoller {
  private generation = 0;

  /**
   * Starts polling a job, cancelling any earlier poll.
   * @param jobId Job id.
   * @param handlers Callbacks.
   */
  start(jobId: string, handlers: PollHandlers): void {
    this.generation += 1;
    void this.tick(jobId, this.generation, handlers, 0);
  }

  /** Stops polling. */
  stop(): void {
    this.generation += 1;
  }

  /**
   * Polls once, then schedules the next poll unless the job finished.
   * @param jobId Job id.
   * @param generation Generation that scheduled this poll.
   * @param handlers Callbacks.
   * @param failures Consecutive network failures so far.
   */
  private async tick(
    jobId: string,
    generation: number,
    handlers: PollHandlers,
    failures: number,
  ): Promise<void> {
    let progress: JobProgress;
    try {
      progress = await getJob(jobId);
    } catch (error) {
      if (generation !== this.generation) return;
      this.onError(error, jobId, generation, handlers, failures);
      return;
    }
    if (generation !== this.generation) return;
    if (!ACTIVE_STATUSES.has(progress.status)) {
      handlers.onFinished(progress);
      return;
    }
    handlers.onProgress(progress);
    window.setTimeout(() => void this.tick(jobId, generation, handlers, 0), POLL_INTERVAL_MS);
  }

  /**
   * Backs off on network errors; anything else ends the poll.
   * @param error The failure.
   * @param jobId Job id.
   * @param generation Generation of this poll.
   * @param handlers Callbacks.
   * @param failures Consecutive network failures before this one.
   */
  private onError(
    error: unknown,
    jobId: string,
    generation: number,
    handlers: PollHandlers,
    failures: number,
  ): void {
    if (!(error instanceof NetworkError)) {
      handlers.onFailure(error);
      return;
    }
    const delay = BACKOFF_MS[Math.min(failures, BACKOFF_MS.length - 1)] ?? POLL_INTERVAL_MS;
    handlers.onConnectionLost();
    window.setTimeout(() => void this.tick(jobId, generation, handlers, failures + 1), delay);
  }
}
