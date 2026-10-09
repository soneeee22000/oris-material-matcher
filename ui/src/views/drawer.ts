/** Audit drawer: a modal `<dialog>` (right drawer on desktop, bottom sheet on mobile). */

import type { ResultRow, SuggestionLabels } from "../api";
import { button, clear, el } from "../dom";
import { displayValue, formatUsd, prettyJson } from "../format";
import { ICON_SMALL, icon } from "../icons";
import { modeLabels } from "../mode";
import { decisionChip } from "./rows";

const CONFIDENCE_NOTE = "model self-report, uncalibrated";
const COPY_RESET_MS = 1500;
const LABEL_KEYS = ["material_type", "material_usage", "material_subtype"] as const;
const LABEL_SEPARATOR = " · ";

/** One label/value line in the drawer. */
type Field = [label: string, value: string];

/** The drawer's handle. */
export interface AuditDrawer {
  open: (row: ResultRow, origin: HTMLElement, mode: string | null) => void;
  isOpen: () => boolean;
}

/**
 * Renders a top-1 / top-2 candidate: its library labels and code when the code is a valid row
 * (the API's `suggestions` entry), else the code as the model returned it.
 * @param code Candidate code from the audit record.
 * @param labels The library row of that code, or null when it is not a valid row.
 * @returns Display text.
 */
function candidateText(code: unknown, labels: SuggestionLabels | null | undefined): string {
  const codeText = displayValue(code);
  if (!labels) return codeText;
  const triple = LABEL_KEYS.map((key) => labels[key] || "—").join(LABEL_SEPARATOR);
  return codeText ? `${triple} (${codeText})` : triple;
}

/**
 * Lists the decision fields in the spec's order: gate and reason, evidence, candidates, confidence.
 * @param row Result row.
 * @returns Label/value pairs, empty values included (filtered later).
 */
function decisionFields(row: ResultRow): Field[] {
  const audit = row.audit ?? {};
  const confidence = displayValue(audit.confidence);
  const gap = displayValue(audit.candidate_gap);
  return [
    ["Gate", displayValue(audit.gate)],
    ["Reason", row.reason],
    ["Evidence", displayValue(audit.evidence)],
    ["Element or application", displayValue(audit.element_or_application)],
    ["Top-1", candidateText(audit.top1, row.suggestions?.[0])],
    ["Top-2", candidateText(audit.top2, row.suggestions?.[1])],
    ["Confidence", confidence ? `${confidence} (${CONFIDENCE_NOTE})` : ""],
    ["Candidate gap", gap],
    ["Attributes", displayValue(audit.attribute_result)],
  ];
}

/**
 * Lists the provenance fields: model, prompt, calls, attributed cost and latency, error, ids.
 * A replay's latency and cost are the recorded run's; the fake model's are not measurements.
 * @param row Result row.
 * @param mode Run mode.
 * @returns Label/value pairs.
 */
function provenanceFields(row: ResultRow, mode: string | null): Field[] {
  const note = modeLabels(mode).attributedNote;
  const latency = typeof row.latency_ms === "number" ? `${row.latency_ms} ms (${note})` : "";
  const cost =
    typeof row.cost_usd === "number" ? `${formatUsd(row.cost_usd, true)} (${note})` : "";
  return [
    ["Model", displayValue(row.model)],
    ["Prompt version", displayValue(row.prompt_version)],
    ["Call ids", displayValue(row.call_ids)],
    ["Latency", latency],
    ["Cost", cost],
    ["Error", displayValue(row.audit?.error)],
    [
      row.decision === "matched" ? "Library row id" : "Top-1 library row id",
      displayValue(row.library_row_id),
    ],
    ["Line id", row.transport_id ? `${row.transport_id} · ${row.line_id}` : row.line_id],
  ];
}

/**
 * Renders label/value pairs as a description list, skipping empty values.
 * @param fields Pairs.
 * @returns The list.
 */
function fieldList(fields: readonly Field[]): HTMLDListElement {
  const list = el("dl", { className: "audit-fields" });
  for (const [label, value] of fields) {
    if (!value) continue;
    list.append(el("div", {}, [el("dt", { text: label }), el("dd", { text: value })]));
  }
  return list;
}

/**
 * Builds the collapsible raw JSON block with a Copy button.
 * @param raw The raw line response.
 * @returns The block, or null when the API returned none.
 */
function rawBlock(raw: unknown): HTMLDetailsElement | null {
  if (raw === null || raw === undefined || raw === "") return null;
  if (Array.isArray(raw) && raw.length === 0) return null;
  const text = prettyJson(raw);
  const copy = button("Copy", "btn btn--small", icon("copy", ICON_SMALL));
  copy.addEventListener("click", () => void copyText(text, copy));
  return el("details", { className: "raw" }, [
    el("summary", { text: "Raw line response (JSON)" }),
    el("pre", { text }),
    copy,
  ]);
}

/**
 * Copies text to the clipboard and confirms on the button.
 * @param text Text.
 * @param target Button whose label confirms.
 */
async function copyText(text: string, target: HTMLButtonElement): Promise<void> {
  const label = target.querySelector("span");
  try {
    await navigator.clipboard.writeText(text);
    if (label) label.textContent = "Copied";
  } catch {
    if (label) label.textContent = "Copy failed";
  }
  window.setTimeout(() => {
    if (label) label.textContent = "Copy";
  }, COPY_RESET_MS);
}

/**
 * Builds the drawer body for one row.
 * @param row Result row.
 * @param mode Run mode.
 * @returns The nodes.
 */
function drawerBody(row: ResultRow, mode: string | null): Node[] {
  const description = el("div", { className: "audit-desc" }, [
    el("p", { className: "audit-short", text: row.short }),
    row.long && row.long !== row.short
      ? el("p", { className: "audit-long", text: row.long })
      : null,
  ]);
  return [
    description,
    fieldList(decisionFields(row)),
    fieldList(provenanceFields(row, mode)),
    rawBlock(row.audit?.raw_line_response),
  ].filter((node): node is NonNullable<typeof node> => node !== null);
}

/**
 * Keeps Tab and Shift+Tab inside the dialog.
 * @param dialog The dialog.
 * @param event The keydown.
 */
function trapFocus(dialog: HTMLDialogElement, event: KeyboardEvent): void {
  if (event.key !== "Tab") return;
  const focusable = Array.from(
    dialog.querySelectorAll<HTMLElement>(
      "button, summary, [href], [tabindex]:not([tabindex='-1'])",
    ),
  ).filter((node) => !node.hasAttribute("disabled"));
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (!first || !last) return;
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

/** The audit dialog; focus returns to the row that opened it. */
class DrawerView implements AuditDrawer {
  private readonly title = el("h2", { className: "drawer__title", attrs: { id: "drawer-title" } });
  private readonly close = el("button", {
    className: "icon-btn",
    attrs: { type: "button", "aria-label": "Close audit" },
  });
  private readonly content = el("div", { className: "drawer__content" });
  private readonly dialog: HTMLDialogElement;
  private origin: HTMLElement | null = null;

  /** Builds the dialog and appends it to the body. */
  constructor() {
    this.close.append(icon("close"));
    this.dialog = el(
      "dialog",
      { className: "drawer", attrs: { "aria-labelledby": "drawer-title" } },
      [el("header", { className: "drawer__head" }, [this.title, this.close]), this.content],
    );
    this.close.addEventListener("click", () => this.dialog.close());
    this.dialog.addEventListener("keydown", (event) => trapFocus(this.dialog, event));
    this.dialog.addEventListener("click", (event) => {
      if (event.target === this.dialog) this.dialog.close();
    });
    this.dialog.addEventListener("close", () => this.origin?.focus());
    document.body.append(this.dialog);
  }

  /**
   * Opens the drawer for one row.
   * @param row Result row.
   * @param origin Element that gets focus back on close.
   * @param mode Run mode, for honest latency and cost labels.
   */
  open(row: ResultRow, origin: HTMLElement, mode: string | null): void {
    this.origin = origin;
    this.title.replaceChildren(
      el("span", { className: "mono", text: row.item_no || row.line_id }),
      decisionChip(row.decision),
    );
    clear(this.content);
    this.content.append(...drawerBody(row, mode));
    this.dialog.showModal();
    this.close.focus();
  }

  /** @returns Whether the drawer is open. */
  isOpen(): boolean {
    return this.dialog.open;
  }
}

/**
 * Creates the drawer and appends it to the body.
 * @returns The drawer handle.
 */
export function createDrawer(): AuditDrawer {
  return new DrawerView();
}
