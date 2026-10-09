/** Maps API and network failures to a plain-language cause plus the technical detail. */

import { ApiError, NetworkError } from "./api";
import { displayValue } from "./format";
import type { UiError } from "./state";

const HTTP_SERVER_ERROR = 500;
const INLINE_UPLOAD_STATUSES = new Set([400, 413, 422]);

/** Plain-language causes by error code (docs/ui-spec.md §3, §5). */
const CAUSES: Record<string, string> = {
  missing_columns: "The BoQ is missing required columns",
  unsupported_type: "Only .csv and .xlsx files are accepted (not .xls or .xlsm).",
  empty_file: "The file has no data rows.",
  unreadable_file: "The file could not be read as a BoQ (check its encoding and layout).",
  file_too_large: "The file is larger than 2 MB.",
  too_many_rows: "The file has more than 2,000 data rows.",
  workbook_too_large: "The workbook expands to far more data than a BoQ holds, so it was not opened.",
  unexpected_field: "The upload had an unexpected part; send one BoQ file and a built-in library.",
  unknown_library: "The server does not know this library.",
  model_unavailable: "The server has no model API key configured, so it cannot run matching.",
  queue_full: "Four jobs are already queued. Wait for one to finish, then start over.",
  job_not_found: "This job no longer exists, most likely because the server restarted.",
  job_expired: "This job's results expired (they are kept for 1 hour after it finishes).",
};

/** Plain-language causes by HTTP status when the code is unknown. */
const STATUS_CAUSES: Record<number, string> = {
  401: "The server needs an access token.",
  404: "The server does not know this job.",
  409: "The result is not ready yet.",
  413: "The file is too large: the limit is 2 MB and 2,000 rows.",
  422: "The server rejected the request (unknown library or unexpected field).",
  503: "The server cannot run matching right now.",
  429: CAUSES["queue_full"] ?? "",
};

/**
 * Explains an API error in plain language.
 * @param error The error.
 * @returns The cause.
 */
function apiCause(error: ApiError): string {
  const known = CAUSES[error.code];
  if (error.code === "missing_columns") {
    const columns = displayValue(error.detail);
    return columns ? `Missing column: ${columns}` : (known ?? "");
  }
  if (known) return known;
  if (error.status >= HTTP_SERVER_ERROR) return "The server failed while handling the request.";
  return STATUS_CAUSES[error.status] ?? "The server refused the request.";
}

/**
 * Builds the technical detail line for an API error.
 * @param error The error.
 * @returns Detail text.
 */
function apiDetail(error: ApiError): string {
  const parts = [`HTTP ${error.status}`, error.code];
  const detail = displayValue(error.detail);
  if (detail) parts.push(detail);
  if (error.requestId) parts.push(`request id ${error.requestId}`);
  return parts.join(" · ");
}

/**
 * Converts any failure into the error card's content.
 * @param error The failure.
 * @param retry What "Retry" does, when retrying makes sense.
 * @returns The UI error.
 */
export function describeError(error: unknown, retry: (() => void) | null = null): UiError {
  if (error instanceof ApiError) {
    return {
      title: error.unauthorized ? "Access token needed" : "The run could not continue",
      cause: apiCause(error),
      detail: apiDetail(error),
      needsToken: error.unauthorized,
      retry,
    };
  }
  if (error instanceof NetworkError) {
    return {
      title: "Connection lost",
      cause: "The server could not be reached.",
      detail: error.message,
      needsToken: false,
      retry,
    };
  }
  const detail = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
  return {
    title: "Something went wrong",
    cause: "The page hit an unexpected error.",
    detail,
    needsToken: false,
    retry,
  };
}

/**
 * Tells whether an upload was refused for the file or the form itself (400, 413, 422), which the
 * operator fixes by choosing another file, so the cause belongs under the drop zone.
 * @param error The failure.
 * @returns Whether to show it inline.
 */
export function isUploadProblem(error: unknown): error is ApiError {
  return error instanceof ApiError && INLINE_UPLOAD_STATUSES.has(error.status);
}
