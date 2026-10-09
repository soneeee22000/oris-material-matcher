/** Idle and uploading view: drop zone, library selector and the "Run matching" button. */

import type { LibraryChoice, LibraryEntry } from "../api";
import { button, el } from "../dom";
import { formatBytes } from "../format";
import { icon } from "../icons";

const ACCEPTED_BOQ = [".csv", ".xlsx"];
const MAX_UPLOAD_BYTES = 2 * 1024 * 1024;
const RUN_LABEL = "Run matching";
const BUSY_LABEL = "Uploading and checking columns…";
const UPLOAD_ICON_SIZE = 28;

/** One built-in library option shown in the segmented control. */
interface LibraryOption {
  id: string;
  label: string;
}

const BUILTIN_LIBRARIES: readonly LibraryOption[] = [
  { id: "global", label: "Global" },
  { id: "fr", label: "FR" },
];
const DEFAULT_LIBRARY = "global";

/** Called when the operator starts a run. */
export type RunHandler = (file: File, library: LibraryChoice) => void;

/** The upload panel's handle. */
export interface UploadPanel {
  root: HTMLElement;
  setBusy: (busy: boolean) => void;
  reset: () => void;
  setLibraries: (entries: readonly LibraryEntry[]) => void;
  showProblem: (text: string) => void;
  libraryId: () => string;
}

/** Mutable selection state. */
interface Selection {
  boq: File | null;
  libraryId: string;
}

/**
 * Tells whether a file name has one of the accepted extensions.
 * @param name File name.
 * @param accepted Extensions, lower case with the dot.
 * @returns Whether it is accepted.
 */
function hasExtension(name: string, accepted: readonly string[]): boolean {
  const lower = name.toLowerCase();
  return accepted.some((extension) => lower.endsWith(extension));
}

/**
 * Checks a BoQ file before upload; the server stays the authority.
 * @param file The file.
 * @returns A plain-language problem, or null when it looks fine.
 */
function boqProblem(file: File): string | null {
  if (!hasExtension(file.name, ACCEPTED_BOQ))
    return "Only .csv and .xlsx files are accepted (not .xls or .xlsm).";
  if (file.size === 0) return "The file is empty.";
  if (file.size > MAX_UPLOAD_BYTES) return "The file is larger than 2 MB.";
  return null;
}

/**
 * Wires drag-and-drop onto the drop zone.
 * @param zone The zone.
 * @param onFile Called with the dropped file.
 */
function wireDrop(zone: HTMLElement, onFile: (file: File) => void): void {
  zone.addEventListener("dragover", (event) => {
    event.preventDefault();
    zone.classList.add("dropzone--over");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("dropzone--over"));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("dropzone--over");
    const file = event.dataTransfer?.files[0];
    if (file) onFile(file);
  });
}

/**
 * Builds the drop zone: a label wrapping a real file input, so Space and Enter open the picker.
 * @param onFile Called with the chosen file.
 * @returns The zone and its input.
 */
function dropZone(onFile: (file: File) => void): {
  zone: HTMLLabelElement;
  input: HTMLInputElement;
} {
  const input = el("input", {
    className: "visually-hidden",
    attrs: { type: "file", id: "boq-file", accept: ACCEPTED_BOQ.join(",") },
  });
  const zone = el("label", { className: "dropzone", attrs: { for: "boq-file" } }, [
    icon("upload", UPLOAD_ICON_SIZE),
    el("span", {
      className: "dropzone__title",
      text: "Drop a BoQ (.csv or .xlsx) or choose a file",
    }),
    el("span", {
      className: "dropzone__hint",
      text: "Up to 2 MB and 2,000 rows. The server reads the file; nothing is parsed in the browser.",
    }),
    input,
  ]);
  input.addEventListener("change", () => {
    const file = input.files?.[0];
    if (file) onFile(file);
  });
  wireDrop(zone, onFile);
  return { zone, input };
}

/**
 * Builds the library segmented control (Global / FR).
 * @param selection Selection state.
 * @param onChange Called after any change.
 * @returns The fieldset and its buttons.
 */
function librarySelector(
  selection: Selection,
  onChange: () => void,
): { root: HTMLFieldSetElement; buttons: Map<string, HTMLButtonElement> } {
  const buttons = new Map<string, HTMLButtonElement>();
  const group = el("div", { className: "segmented" });
  for (const option of BUILTIN_LIBRARIES) {
    const item = button(option.label, "segmented__item");
    item.dataset["library"] = option.id;
    item.addEventListener("click", () => {
      selection.libraryId = option.id;
      onChange();
    });
    buttons.set(option.id, item);
    group.append(item);
  }
  const root = el("fieldset", { className: "library" }, [
    el("legend", { text: "Library" }),
    group,
  ]);
  return { root, buttons };
}

/**
 * Builds the explainer line for the three decisions.
 * @returns The paragraph.
 */
function explainer(): HTMLParagraphElement {
  return el("p", {
    className: "explainer",
    text: "Each line becomes Matched (one library label), Needs review (a person should decide) or Not a material (headers and service lines). Labels are never invented.",
  });
}

/**
 * Resolves the current library choice.
 * @param selection Selection state.
 * @returns The choice.
 */
function libraryChoice(selection: Selection): LibraryChoice {
  return { kind: "builtin", id: selection.libraryId };
}

/** The upload panel: owns the selection and enables "Run matching" only when it is complete. */
class UploadPanelView implements UploadPanel {
  readonly root: HTMLElement;
  private readonly selection: Selection = {
    boq: null,
    libraryId: DEFAULT_LIBRARY,
  };
  private readonly info = el("p", { className: "file-info", attrs: { "aria-live": "polite" } });
  private readonly run = button(RUN_LABEL, "btn btn--primary btn--run", icon("play"));
  private readonly drop: { zone: HTMLLabelElement; input: HTMLInputElement };
  private readonly library: { root: HTMLFieldSetElement; buttons: Map<string, HTMLButtonElement> };
  private busy = false;

  /** @param onRun Called when the operator clicks "Run matching". */
  constructor(onRun: RunHandler) {
    this.drop = dropZone((file) => this.choose(file));
    this.library = librarySelector(this.selection, () => this.refresh());
    this.run.addEventListener("click", () => {
      if (this.selection.boq) onRun(this.selection.boq, libraryChoice(this.selection));
    });
    this.root = el(
      "section",
      { className: "panel upload", attrs: { "aria-labelledby": "upload-title" } },
      [
        el("h2", {
          className: "panel__title",
          text: "Match a bill of quantities",
          attrs: { id: "upload-title" },
        }),
        this.drop.zone,
        this.info,
        this.library.root,
        explainer(),
        el("div", { className: "actions" }, [this.run]),
      ],
    );
    this.refresh();
  }

  /**
   * Records a chosen BoQ, or explains why it cannot be used.
   * @param file The file.
   */
  private choose(file: File): void {
    const problem = boqProblem(file);
    this.selection.boq = problem ? null : file;
    this.info.textContent = problem ?? `${file.name} · ${formatBytes(file.size)}`;
    this.info.classList.toggle("file-info--problem", problem !== null);
    this.refresh();
  }

  /**
   * Shows why the server refused the file under the drop zone, and asks for another file.
   * @param text The plain-language cause, e.g. "Missing column: BoQ Qty".
   */
  showProblem(text: string): void {
    this.selection.boq = null;
    this.drop.input.value = "";
    this.info.textContent = `${text} Fix the file, then choose it again.`;
    this.info.classList.add("file-info--problem");
    this.refresh();
  }

  /** Syncs the run button and the pressed library button with the selection. */
  private refresh(): void {
    this.run.disabled = this.selection.boq === null || this.busy;
    for (const [id, item] of this.library.buttons) {
      item.setAttribute("aria-pressed", String(id === this.selection.libraryId));
    }
  }

  /**
   * Disables the inputs while an upload is in flight.
   * @param busy Whether an upload is in flight.
   */
  setBusy(busy: boolean): void {
    this.busy = busy;
    setRunBusy(this.run, busy);
    this.drop.input.disabled = busy;
    for (const item of this.library.buttons.values()) item.disabled = busy;
    this.refresh();
  }

  /** Forgets the chosen BoQ, keeping the library choice. */
  reset(): void {
    this.selection.boq = null;
    this.drop.input.value = "";
    this.info.textContent = "";
    this.refresh();
  }

  /**
   * Shows only the libraries the server lists, with their row counts; a replay server lists only
   * the library its recording was made on.
   * @param entries Libraries from the server.
   */
  setLibraries(entries: readonly LibraryEntry[]): void {
    labelLibraries(this.library.buttons, entries);
    const listed = new Set(entries.map((entry) => entry.id));
    for (const [id, item] of this.library.buttons) item.hidden = !listed.has(id);
    const first = entries.find((entry) => this.library.buttons.has(entry.id));
    if (!listed.has(this.selection.libraryId) && first) this.selection.libraryId = first.id;
    this.refresh();
  }

  /** @returns The selected library id (`global` or `fr`). */
  libraryId(): string {
    return this.selection.libraryId;
  }
}

/**
 * Builds the upload panel.
 * @param onRun Called when the operator clicks "Run matching".
 * @returns The panel handle.
 */
export function createUploadPanel(onRun: RunHandler): UploadPanel {
  return new UploadPanelView(onRun);
}

/**
 * Swaps the run button between its idle and busy labels.
 * @param run The button.
 * @param busy Whether an upload is in flight.
 */
function setRunBusy(run: HTMLButtonElement, busy: boolean): void {
  const label = run.querySelector("span");
  if (label) label.textContent = busy ? BUSY_LABEL : RUN_LABEL;
  run.classList.toggle("btn--busy", busy);
  run.setAttribute("aria-busy", String(busy));
}

/**
 * Adds row counts to the built-in library buttons once `/v1/libraries` answers.
 * @param buttons Buttons by library id.
 * @param entries Libraries from the server.
 */
function labelLibraries(
  buttons: Map<string, HTMLButtonElement>,
  entries: readonly LibraryEntry[],
): void {
  for (const entry of entries) {
    const label = buttons.get(entry.id)?.querySelector("span");
    const option = BUILTIN_LIBRARIES.find((candidate) => candidate.id === entry.id);
    if (label && option) label.textContent = `${option.label} (${entry.rows} rows)`;
  }
}
