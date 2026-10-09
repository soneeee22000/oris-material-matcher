/** Entry point: wires the state machine, hash routing, polling and the views. */

import {
  ApiError,
  createJob,
  getResult,
  getResultCsv,
  getServing,
  listLibraries,
  setToken,
  type JobProgress,
  type JobResult,
  type LibraryChoice,
  type ServingInfo,
} from "./api";
import { describeError, isUploadProblem } from "./errors";
import { retryFile } from "./failures";
import { csvFilename, fileStem } from "./format";
import { DEFAULT_VIEW, parseHash, writeHash, type ViewState } from "./hash";
import { installShortcuts } from "./keyboard";
import { JobPoller } from "./poller";
import { loadMeta, saveMeta } from "./session";
import { Machine, type JobMeta, type Phase } from "./state";
import { createDrawer, type AuditDrawer } from "./views/drawer";
import { createErrorCard } from "./views/error";
import { createHeader, type AppHeader, type LibraryBadge } from "./views/header";
import { createProgressView, type ProgressView } from "./views/progress";
import { createSummary } from "./views/summary";
import { createResultsTable, type ResultsTable } from "./views/table";
import { createUploadPanel, type UploadPanel } from "./views/upload";

const DEFAULT_STEM = "boq";

type ResultPhase = Extract<Phase, { kind: "done" | "partial" }>;

/** The operator app. */
class App {
  private readonly machine = new Machine();
  private readonly poller = new JobPoller();
  private readonly header: AppHeader = createHeader();
  private readonly upload: UploadPanel;
  private readonly progress: ProgressView = createProgressView();
  private readonly drawer: AuditDrawer = createDrawer();
  private readonly main: HTMLElement;
  private table: ResultsTable | null = null;
  private view: ViewState = parseHash(location.hash);
  private rendered: Phase | null = null;
  private serving: ServingInfo | null = null;

  /** @param root The element the app renders into. */
  constructor(root: HTMLElement) {
    this.upload = createUploadPanel((file, library) => void this.start(file, library));
    this.main = document.createElement("main");
    this.main.id = "main";
    this.main.className = "app-main";
    root.replaceChildren(this.header.root, this.main);
    this.machine.subscribe((phase) => this.render(phase));
    window.addEventListener("hashchange", () => this.onHashChange());
    installShortcuts({
      active: () => this.table !== null && !this.drawer.isOpen(),
      focusSearch: () => this.table?.focusSearch(),
      setFilter: (filter) => this.changeView({ filter }),
      filters: () => this.table?.filters ?? [],
      download: () => void this.download(),
      moveFocus: (delta) => this.table?.moveFocus(delta),
    });
  }

  /** Resumes a job named in the hash, then asks the server how it answers. */
  boot(): void {
    if (this.view.job) this.resume(this.view.job);
    else this.render(this.machine.current);
    void this.loadServer();
  }

  /**
   * Loads the serving mode and the library list; a 401 at boot asks for the token.
   */
  private async loadServer(): Promise<void> {
    try {
      const [serving, libraries] = await Promise.all([getServing(), listLibraries()]);
      this.serving = serving;
      this.header.showServing(serving);
      this.upload.setLibraries(libraries);
    } catch (error) {
      if (!(error instanceof ApiError) || !error.unauthorized) return;
      if (this.machine.current.kind !== "idle") return;
      this.machine.go({ kind: "error", error: describeError(error, null) });
    }
  }

  /**
   * Uploads a BoQ and starts polling the new job; a refused file is explained under the drop zone.
   * @param file The BoQ.
   * @param library The library choice.
   */
  private async start(file: File, library: LibraryChoice): Promise<void> {
    if (!this.machine.go({ kind: "uploading" })) return;
    this.upload.setBusy(true);
    try {
      const created = await createJob(file, library);
      const meta: JobMeta = { fileName: file.name, libraryId: library.id, created };
      saveMeta(created.job_id, meta);
      this.view = { ...DEFAULT_VIEW, job: created.job_id };
      writeHash(this.view);
      this.follow(created.job_id, meta);
    } catch (error) {
      this.refuseUpload(error, () => void this.start(file, library));
    } finally {
      this.upload.setBusy(false);
    }
  }

  /**
   * Shows a refused upload inline when a new file fixes it, else the error card.
   * @param error The failure.
   * @param retry Re-sends the same upload (queue full, network loss, 5xx).
   */
  private refuseUpload(error: unknown, retry: () => void): void {
    if (isUploadProblem(error)) {
      this.view = { ...DEFAULT_VIEW };
      writeHash(this.view);
      this.machine.go({ kind: "idle" });
      this.upload.showProblem(describeError(error).cause);
      return;
    }
    this.machine.go({ kind: "error", error: describeError(error, retry) });
  }

  /**
   * Resumes a job after a refresh or from a shared link.
   * @param jobId Job id.
   */
  private resume(jobId: string): void {
    this.follow(jobId, loadMeta(jobId));
  }

  /**
   * Enters the running phase and polls the job until it finishes.
   * @param jobId Job id.
   * @param initial Job meta known so far.
   */
  private follow(jobId: string, initial: JobMeta): void {
    let meta = initial;
    this.machine.go({ kind: "running", meta, progress: null, connectionLost: false });
    this.poller.start(jobId, {
      onProgress: (progress) => {
        meta = withProgress(meta, progress);
        this.machine.go({ kind: "running", meta, progress, connectionLost: false });
      },
      onConnectionLost: () => this.markConnectionLost(meta),
      onFinished: (progress) => void this.finish(jobId, withProgress(meta, progress), progress),
      onFailure: (error) => this.fail(error, jobId),
    });
  }

  /**
   * Keeps the running view but shows "Connection lost, retrying".
   * @param meta Job meta.
   */
  private markConnectionLost(meta: JobMeta): void {
    const current = this.machine.current;
    const progress = current.kind === "running" ? current.progress : null;
    this.machine.go({ kind: "running", meta, progress, connectionLost: true });
  }

  /**
   * Fetches the result of a finished job, or explains why there is none.
   * @param jobId Job id.
   * @param meta Job meta.
   * @param progress Final progress.
   */
  private async finish(jobId: string, meta: JobMeta, progress: JobProgress): Promise<void> {
    if (progress.status !== "done") {
      this.machine.go({ kind: "error", error: unfinishedError(progress) });
      return;
    }
    try {
      const result = await getResult(jobId);
      const known = withResult(meta, result);
      saveMeta(jobId, known);
      const partial = result.summary.errors > 0;
      this.machine.go({ kind: partial ? "partial" : "done", meta: known, progress, result });
    } catch (error) {
      this.fail(error, jobId);
    }
  }

  /**
   * Shows the error card; a retry resumes the same job.
   * @param error The failure.
   * @param jobId Job id.
   */
  private fail(error: unknown, jobId: string): void {
    this.machine.go({ kind: "error", error: describeError(error, () => this.resume(jobId)) });
  }

  /** Forgets the job and returns to the empty state. */
  private reset(): void {
    this.poller.stop();
    this.view = { ...DEFAULT_VIEW };
    writeHash(this.view);
    this.upload.reset();
    this.machine.go({ kind: "idle" });
  }

  /**
   * Stores a token after a 401, reloads what the server lists and retries what failed.
   * @param token Bearer token.
   * @param retry What failed, or null to start over.
   */
  private useToken(token: string, retry: (() => void) | null): void {
    setToken(token);
    void this.loadServer();
    if (retry) retry();
    else this.reset();
  }

  /**
   * Updates the view state, the hash and the table.
   * @param change Changed fields.
   */
  private changeView(change: Partial<ViewState>): void {
    this.view = { ...this.view, ...change };
    writeHash(this.view);
    this.table?.apply(this.view);
  }

  /** Follows a hash edited by hand or a pasted link. */
  private onHashChange(): void {
    const next = parseHash(location.hash);
    if (next.job === this.view.job) {
      this.changeView(next);
      return;
    }
    this.view = next;
    if (next.job) this.resume(next.job);
    else this.reset();
  }

  /** Downloads the server-written CSV, byte-identical to the CLI output. */
  private async download(): Promise<void> {
    const phase = this.machine.current;
    const jobId = this.view.job;
    if (!jobId || (phase.kind !== "done" && phase.kind !== "partial")) return;
    try {
      const blob = await getResultCsv(jobId);
      const library = phase.result.summary.library?.name ?? phase.meta.libraryId;
      const name = csvFilename(fileStem(phase.meta.fileName ?? DEFAULT_STEM), library, new Date());
      saveBlob(blob, name);
    } catch (error) {
      this.fail(error, jobId);
    }
  }

  /**
   * Sends only the failed lines, with their section headers, to a new job and follows it.
   * @param phase The partial result.
   */
  private retryFailed(phase: ResultPhase): void {
    const stem = fileStem(phase.meta.fileName ?? DEFAULT_STEM);
    const library = phase.result.summary.library?.name ?? phase.meta.libraryId;
    void this.start(retryFile(phase.result.rows, stem), { kind: "builtin", id: library });
  }

  /**
   * Renders a phase; the running view updates in place between polls.
   * @param phase Phase.
   */
  private render(phase: Phase): void {
    const sameKind = this.rendered?.kind === phase.kind;
    this.rendered = phase;
    this.header.showLibrary(libraryBadge(phase));
    if (phase.kind === "running") {
      const { meta, progress, connectionLost } = phase;
      this.progress.update({ meta, progress, connectionLost, serving: this.serving });
      if (!sameKind) this.mount([this.progress.root]);
      return;
    }
    if (phase.kind === "idle" || phase.kind === "uploading") {
      if (!sameKind) this.mount([this.upload.root]);
      return;
    }
    if (phase.kind === "error") {
      this.renderError(phase);
      return;
    }
    this.renderResult(phase);
  }

  /**
   * Renders the done or partial view.
   * @param phase Done or partial phase.
   */
  private renderResult(phase: ResultPhase): void {
    const mode = phase.result.summary.mode ?? this.serving?.mode ?? null;
    const summary = createSummary(
      { ...phase, partial: phase.kind === "partial", mode },
      {
        onDownload: () => void this.download(),
        onNew: () => this.reset(),
        onShowFailed: () => this.changeView({ filter: "failed" }),
        onRetryFailed: () => this.retryFailed(phase),
      },
    );
    this.table = createResultsTable(phase.result.rows, {
      onOpen: (row, origin) => this.drawer.open(row, origin, mode),
      onViewChange: (change) => this.changeView(change),
    });
    this.table.apply(this.view);
    this.mount([summary, this.table.root]);
  }

  /**
   * Renders the error card.
   * @param phase Error phase.
   */
  private renderError(phase: Extract<Phase, { kind: "error" }>): void {
    const card = createErrorCard(phase.error, {
      onStartOver: () => this.reset(),
      onToken: (token) => this.useToken(token, phase.error.retry),
    });
    this.mount([card]);
    card.querySelector<HTMLElement>("input, button")?.focus();
  }

  /**
   * Replaces the main content.
   * @param nodes New content.
   */
  private mount(nodes: readonly HTMLElement[]): void {
    if (!nodes.some((node) => node === this.table?.root)) this.table = null;
    this.main.replaceChildren(...nodes);
  }
}

/**
 * Fills what a job opened from a link lacks (file name, library) from its status body.
 * @param meta Job meta known so far.
 * @param progress A status body.
 * @returns The meta.
 */
function withProgress(meta: JobMeta, progress: JobProgress): JobMeta {
  return {
    ...meta,
    fileName: meta.fileName ?? progress.filename ?? null,
    libraryId: progress.library?.name ?? meta.libraryId,
  };
}

/**
 * Takes the library from the result itself, so a shared link names the library that ran.
 * @param meta Job meta.
 * @param result The job's result.
 * @returns The meta.
 */
function withResult(meta: JobMeta, result: JobResult): JobMeta {
  return { ...meta, libraryId: result.summary.library?.name ?? meta.libraryId };
}

/**
 * Explains a job that ended without a result.
 * @param progress Final status body.
 * @returns The error card's content.
 */
function unfinishedError(progress: JobProgress): ReturnType<typeof describeError> {
  const cause =
    progress.status === "cancelled"
      ? "The job was cancelled (the server shut down)."
      : "The job failed on the server.";
  const detail = [`status ${progress.status}`, progress.failure ?? ""].filter(Boolean).join(" · ");
  return { title: "The run did not finish", cause, detail, needsToken: false, retry: null };
}

/**
 * Describes the library a phase knows about: the result's, then the status body's, then the 202's.
 * @param phase Phase.
 * @returns The badge, or null when no job is shown.
 */
function libraryBadge(phase: Phase): LibraryBadge | null {
  if (phase.kind === "done" || phase.kind === "partial") {
    const summary = phase.result.summary;
    const library = summary.library ?? phase.meta.created?.library ?? null;
    return { libraryId: phase.meta.libraryId, library, policy: summary.policy_resolution };
  }
  if (phase.kind !== "running") return null;
  const library = phase.progress?.library ?? phase.meta.created?.library ?? null;
  const policy = phase.meta.created?.policy_resolution ?? null;
  return { libraryId: phase.meta.libraryId, library, policy };
}

/**
 * Saves a blob through a temporary download link.
 * @param blob Content.
 * @param name File name.
 */
function saveBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

const root = document.getElementById("app");
if (root) new App(root).boot();
