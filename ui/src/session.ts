/** Per-tab memory of a job's file name and library, so a refresh keeps the header and download name. */

import type { JobMeta } from "./state";

const KEY_PREFIX = "oris-ui-job:";

/**
 * Remembers a job's meta; storage can be blocked, so failures are ignored.
 * @param jobId Job id.
 * @param meta Meta.
 */
export function saveMeta(jobId: string, meta: JobMeta): void {
  try {
    sessionStorage.setItem(`${KEY_PREFIX}${jobId}`, JSON.stringify(meta));
  } catch {
    return;
  }
}

/**
 * Recalls a job's meta.
 * @param jobId Job id.
 * @returns The meta, or a minimal one when nothing was stored.
 */
export function loadMeta(jobId: string): JobMeta {
  const fallback: JobMeta = { fileName: null, libraryId: "global", created: null };
  try {
    const raw = sessionStorage.getItem(`${KEY_PREFIX}${jobId}`);
    return raw ? { ...fallback, ...(JSON.parse(raw) as Partial<JobMeta>) } : fallback;
  } catch {
    return fallback;
  }
}
