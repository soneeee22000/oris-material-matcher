/**
 * Typed client for the jobs API in docs/ui-spec.md §3. Every path and JSON key the UI depends on
 * lives in this module, so an integrator can align it with the Pydantic schemas in one place.
 */

export const API_BASE = "/v1";
const HTTP_UNAUTHORIZED = 401;
const BEARER_PREFIX = "Bearer ";
const REQUEST_ID_HEADER = "X-Request-ID";

export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled";
export type DecisionValue = "matched" | "needs_review" | "not_a_material";

/** How the server answers: the configured model, a recorded run's answers, or the offline fake. */
export type ServingMode = "live" | "replay" | "fake";

/** Serving fields every job body carries (`mode`, `replay_run`, `concurrency`). */
export interface ServingFields {
  mode?: ServingMode;
  replay_run?: string | null;
  concurrency?: number;
}

/** `GET /v1/serving` 200 body. */
export interface ServingInfo extends ServingFields {
  libraries: string[];
}

/** Library the job ran against (202 body). */
export interface JobLibrary {
  name: string;
  rows: number;
  sha256_12: string;
}

/** `POST /v1/jobs` 202 body. */
export interface JobCreated extends ServingFields {
  job_id: string;
  filename?: string;
  total_lines: number;
  library: JobLibrary;
  policy_resolution: string;
  encoding: string;
  warnings: string[];
}

/** `GET /v1/jobs/{id}` 200 body. */
export interface JobProgress extends ServingFields {
  status: JobStatus;
  filename?: string | null;
  library?: JobLibrary | null;
  done: number;
  total: number;
  errors: number;
  cost_usd: number;
  elapsed_s: number;
  eta_s: number | null;
  expires_at: string | null;
  failure?: string | null;
}

/** Per-line audit block; every field is optional because the drawer shows only what is returned. */
export interface RowAudit {
  gate?: unknown;
  evidence?: unknown;
  element_or_application?: unknown;
  top1?: unknown;
  top2?: unknown;
  confidence?: unknown;
  candidate_gap?: unknown;
  attribute_result?: unknown;
  raw_line_response?: unknown;
  error?: unknown;
}

/** Verbatim library labels of a top-1 or top-2 candidate row. */
export interface SuggestionLabels {
  material_type: string;
  material_usage: string;
  material_subtype: string;
  library_row_id: string;
}

/** One result row, in input order. */
export interface ResultRow {
  line_id: string;
  transport_id?: string | null;
  item_no: string;
  short: string;
  long: string;
  unit: string;
  qty?: string | number | null;
  level?: number | string | null;
  kind?: string | null;
  decision: DecisionValue;
  material_type: string | null;
  material_usage: string | null;
  material_subtype: string | null;
  reason: string;
  model?: string | null;
  prompt_version?: string | null;
  latency_ms?: number | null;
  cost_usd?: number | null;
  library_row_id?: string | null;
  call_ids?: string[] | string | null;
  suggestions?: (SuggestionLabels | null)[] | null;
  audit?: RowAudit | null;
}

/** Result summary. */
export interface ResultSummary {
  matched: number;
  needs_review: number;
  not_a_material: number;
  errors: number;
  cost_usd: number;
  wall_clock_s_per_line: number | null;
  mean_attributed_latency_ms?: number | null;
  p95_attributed_latency_ms?: number | null;
  served_models?: string[] | null;
  policy_resolution: string;
  mode?: string | null;
  run_id?: string | null;
  library?: JobLibrary | null;
}

/** `GET /v1/jobs/{id}/result` 200 body. */
export interface JobResult {
  summary: ResultSummary;
  rows: ResultRow[];
}

/** `GET /v1/libraries` entry. */
export interface LibraryEntry {
  id: string;
  rows: number;
  sha256_12: string;
}

/**
 * Which library a job uses: a built-in id from `GET /v1/libraries`. Custom library upload
 * (docs/ui-spec.md §4) is cut per the spec's cut order, so the server accepts only `library`.
 */
export interface LibraryChoice {
  kind: "builtin";
  id: string;
}

/** A non-2xx answer from the API, with its error code and detail. */
export class ApiError extends Error {
  /**
   * @param status HTTP status.
   * @param code Error code from the body (`error`), or a fallback.
   * @param detail The body's detail, verbatim.
   * @param requestId The `X-Request-ID` header, when present.
   */
  constructor(
    readonly status: number,
    readonly code: string,
    readonly detail: unknown,
    readonly requestId: string | null,
  ) {
    super(`HTTP ${status}: ${code}`);
    this.name = "ApiError";
  }

  /** Tells whether the server asked for a Bearer token. */
  get unauthorized(): boolean {
    return this.status === HTTP_UNAUTHORIZED;
  }
}

/** The request never reached the server or the response never arrived. */
export class NetworkError extends Error {
  /** @param cause The underlying fetch failure. */
  constructor(cause: unknown) {
    super(cause instanceof Error ? cause.message : String(cause));
    this.name = "NetworkError";
  }
}

let bearerToken: string | null = null;

/**
 * Sets the Bearer token sent on every `/v1` call (only needed when the server returns 401).
 * @param token The token, or null to stop sending one.
 */
export function setToken(token: string | null): void {
  bearerToken = token && token.trim() ? token.trim() : null;
}

/**
 * Pulls the error code and detail out of FastAPI's or the spec's error body shapes.
 * @param body Parsed JSON body, or null.
 * @returns The code and the detail.
 */
function errorParts(body: unknown): { code: string; detail: unknown } {
  if (typeof body !== "object" || body === null) return { code: "", detail: body };
  const record = body as Record<string, unknown>;
  if (typeof record["error"] === "string")
    return { code: record["error"], detail: record["detail"] };
  const inner = record["detail"];
  if (typeof inner === "object" && inner !== null && !Array.isArray(inner))
    return errorParts(inner);
  return { code: typeof inner === "string" ? inner : "", detail: inner };
}

/**
 * Turns a non-2xx response into an ApiError.
 * @param response The response.
 * @returns The error.
 */
async function toApiError(response: Response): Promise<ApiError> {
  const text = await response.text().catch(() => "");
  let body: unknown = text;
  try {
    body = JSON.parse(text);
  } catch {
    body = text || null;
  }
  const { code, detail } = errorParts(body);
  const fallback = response.statusText || `http_${response.status}`;
  return new ApiError(
    response.status,
    code || fallback,
    detail,
    response.headers.get(REQUEST_ID_HEADER),
  );
}

/**
 * Fetches an API path with the token, mapping failures to NetworkError or ApiError.
 * @param path Path under the API base, starting with `/`.
 * @param init Request options.
 * @returns The 2xx response.
 */
async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (bearerToken) headers.set("Authorization", `${BEARER_PREFIX}${bearerToken}`);
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers,
      cache: "no-store",
    });
  } catch (error) {
    throw new NetworkError(error);
  }
  if (!response.ok) throw await toApiError(response);
  return response;
}

/**
 * Fetches and parses a JSON path.
 * @param path Path under the API base.
 * @param init Request options.
 * @returns The parsed body.
 */
async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await request(path, init);
  return (await response.json()) as T;
}

/**
 * Creates a matching job from an uploaded BoQ (`POST /v1/jobs`, multipart).
 * @param file The BoQ, .csv or .xlsx.
 * @param library The library choice.
 * @returns The 202 body.
 */
export function createJob(file: File, library: LibraryChoice): Promise<JobCreated> {
  const form = new FormData();
  form.append("file", file, file.name);
  form.append("library", library.id);
  return requestJson<JobCreated>("/jobs", { method: "POST", body: form });
}

/**
 * Polls a job (`GET /v1/jobs/{id}`).
 * @param jobId The job id.
 * @returns The progress body.
 */
export function getJob(jobId: string): Promise<JobProgress> {
  return requestJson<JobProgress>(`/jobs/${encodeURIComponent(jobId)}`);
}

/**
 * Fetches a finished job's rows and summary (`GET /v1/jobs/{id}/result`).
 * @param jobId The job id.
 * @returns The result body.
 */
export function getResult(jobId: string): Promise<JobResult> {
  return requestJson<JobResult>(`/jobs/${encodeURIComponent(jobId)}/result`);
}

/**
 * Fetches the server-written output CSV (`GET /v1/jobs/{id}/result.csv`), byte-identical to the CLI.
 * @param jobId The job id.
 * @returns The CSV as a blob, bytes untouched.
 */
export async function getResultCsv(jobId: string): Promise<Blob> {
  const response = await request(`/jobs/${encodeURIComponent(jobId)}/result.csv`);
  return response.blob();
}

/**
 * Lists the built-in libraries (`GET /v1/libraries`).
 * @returns The libraries.
 */
export function listLibraries(): Promise<LibraryEntry[]> {
  return requestJson<LibraryEntry[]>("/libraries");
}

/**
 * Asks how the server answers (`GET /v1/serving`): live, replay of a recorded run, or fake.
 * @returns The serving mode, replayed run, concurrency and library ids.
 */
export function getServing(): Promise<ServingInfo> {
  return requestJson<ServingInfo>("/serving");
}
