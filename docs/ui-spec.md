# Operator UI specification

Pre-registered 4 Oct 2026. Built Thu 8 Oct, 09:00–15:00, after the lockbox freeze. The summary is in `DESIGN.md` §13.

## 1. Scope and non-goals

The README's optional UI asks for four things, and every one of them is a hard acceptance criterion:

| # | README ask | How the UI meets it | Test that proves it |
|---|---|---|---|
| U1 | Upload a BoQ (CSV or Excel) | Drop zone or file picker accepts `.csv` and `.xlsx`. The server parses the file, so the browser only uploads it | API test: the same BoQ as CSV and as XLSX gives identical row IDs |
| U2 | Run the matching | One primary button, "Run matching", which creates a job | API test with FakeLLM: job goes `queued → running → done` |
| U3 | Show the table when done, with the decision and labels for each line | Results table: Item No., description, unit, decision chip, `material_type / usage / subtype`, reason | API test: `/result` row count and order equal the input's |
| U4 | `needs_review` rows easy to spot | Amber chip and amber left border, sorted to the top by default, a one-click filter, and a count in the summary bar | Manual check plus a DOM assertion in the UI smoke script |

Non-goals, all cut to keep this to one day:
- No auth and no multiple users.
- No editing or overriding decisions in the UI. The output stays closed-world and the UI only reads it.
- No job persistence across restarts.
- No virtualised table, because about 320 rows render fine as plain DOM.
- No charts beyond the summary bar.
- No i18n of the UI chrome (English only). The data can be in any language.

## 2. Architecture

```
browser (ui/dist/app.js, ~30 KB)  ──HTTP──▶  FastAPI (same process, same service object as the CLI)
   GET /ui            static index.html + app.js + app.css   (StaticFiles mount, committed build)
   POST /v1/jobs      multipart upload ─▶ parse (csv / openpyxl) ─▶ BoQ rows ─▶ asyncio task
   GET  /v1/jobs/{id} progress poll (1 s)
   GET  /v1/jobs/{id}/result        JSON rows + summary
   GET  /v1/jobs/{id}/result.csv    exact output CSV (same writer as the CLI)
   GET  /v1/libraries               built-in libraries list
```

- **Stack:** vanilla TypeScript in strict mode with no framework. The app is about 6 small render modules, and the only dev dependencies are `esbuild` and `typescript`. `tsc --noEmit` type-checks, and esbuild bundles and minifies to `src/oris/api/static/ui/`.
- **The build output is committed**, so reviewers run only `uv run oris serve` and open `http://localhost:8000/ui`. They never need Node.
- **CI freshness gate:** `npm run build && git diff --exit-code src/oris/api/static/ui` catches a stale bundle.
- **One source of truth.** The UI never computes decisions, labels or metrics. Everything it shows comes from the same `MatchService` and CSV writer that produce the graded output files. That way a UI bug cannot diverge from the graded artefact.

## 3. API contract: async job vs synchronous call

| | `POST /v1/match` (sync) | `POST /v1/jobs` + poll (async) |
|---|---|---|
| 320 lines × ≤2 s/line, concurrency 8 | about 80–100 s held open on one request. Browser and proxy timeouts become a risk, with no progress feedback | Returns `202` with a `job_id` in under 200 ms, then the UI polls every 1 s |
| Progress bar | Not possible without streaming | `done / total` comes free from the counter |
| Partial failure | All or nothing, or a complex partial body | Each line's error is recorded and the job still finishes |
| Implementation cost | Already in the plan | About 60 lines: in-memory `dict[job_id, Job]` + `asyncio.create_task` + a semaphore |

**Decision:** keep both.
- **`POST /v1/match`** stays the programmatic path, capped at **500 lines** (`413` above that), so a whole BoQ fits in one documented call (`DESIGN.md` §11.4).
- **The UI uses jobs only.** Polling is chosen over SSE or WebSockets because it is simpler, works through any proxy, survives a tab refresh (the job id is kept in `location.hash`), and 1 request/s against a local process is negligible.
- **Single uvicorn worker:** the job store is in-process, and this is documented as a known limit.
- **Concurrency is bounded by the service's existing semaphore** (`ORIS_MAX_CONCURRENCY`, default 8). The jobs layer adds no concurrency of its own.

Schemas (Pydantic models; OpenAPI is generated automatically at `/docs`):

```
POST /v1/jobs   multipart: file (csv|xlsx, ≤2 MB, ≤2 000 rows), library ("global"|"fr"|file part "library_file")
  202 {job_id, total_lines, library: {name, rows, sha256_12}}
  400 {error: "missing_columns", detail: ["BoQ Qty"]} | "unsupported_type" | "empty_file" | "bad_library"
  413 too large
GET /v1/jobs/{id}
  200 {status: "queued"|"running"|"done"|"failed", done, total, errors, cost_usd, elapsed_s, eta_s}
GET /v1/jobs/{id}/result
  200 {summary: {matched, needs_review, not_a_material, errors, cost_usd, mean_latency_ms, p95_latency_ms,
                 reviewer_minutes_saved_est, assumptions:{min_per_line}},
       rows: [{line_index, item_no, short, long, unit, qty, level, decision, material_type, material_usage,
               material_subtype, reason, model, prompt_version, latency_ms, cost_usd,
               audit: {top1, top2, confidence, candidate_gap, gate, raw_response, error|null}}]}
  409 if not done
GET /v1/jobs/{id}/result.csv   text/csv, byte-identical to `oris match` output for the same input
GET /v1/libraries  [{id:"global", rows, sha256_12}, {id:"fr", ...}]
```

## 4. Server-side parsing

The UI only uploads; all parsing happens on the server.

- **CSV:**
  - Decode as `utf-8-sig`, falling back to `cp1252`.
  - Detect the delimiter with `csv.Sniffer` over `,` and `;`, since French Excel exports use `;`.
- **XLSX:**
  - Open with `openpyxl.load_workbook(read_only=True, data_only=True)` and read the first visible sheet.
  - Reject `.xlsm` and `.xls`.
  - Read only cell values, never formulas or macros.
  - Convert numeric cells back to strings in a way that keeps item numbers like `09.01.0010.` intact.
- **Headers:** match case- and whitespace-insensitively against the five required columns (`Item No., Short Description, Long Description, Unit, BoQ Qty`). Both shipped inputs use these exact English headers. A small alias map (`N° article`, `Désignation`, `Unité`, `Quantité`) is cheap insurance for the live FR session.
- **Shared parser:** one parser module, used by the CLI too (`oris match file.xlsx`), so the XLSX path gets the same unit tests.
- **Row IDs:** content-hash row IDs are computed after parsing. The same lines in CSV and XLSX therefore get the same IDs, which is the U1 test.
- **Custom library upload:** the file is validated as three columns `material_type, material_usage, material_subtype`, with or without quoted headers, since the global CSV quotes them. It needs at least 1 row and at most 5 000. Its short hash is shown in the UI so the operator knows exactly which library produced the result.

## 5. Screens and states

There is one screen, a single-page state machine (`idle → uploading → running → done | partial | error`).

| State | What the operator sees | Exit |
|---|---|---|
| **Empty / idle** | Drop zone ("Drop a BoQ (.csv or .xlsx) or choose a file"), library selector (Global (default) / FR / Upload custom), and a disabled "Run matching" button. One line explains the three decisions | File selected → the button is enabled and the file name, size and row count are shown after the server check |
| **Uploading** | Button spinner and "Uploading and checking columns…". Inputs are disabled | 202 → running. 4xx → error with an inline fix ("Missing column: BoQ Qty") |
| **Running** | Determinate progress bar `142 / 319 lines`, elapsed time, ETA, running cost in USD, and a "Lines are matched in parallel (8 at a time)" note. A skeleton table with the input rows already listed and decision cells as shimmer-free placeholders (no animation if `prefers-reduced-motion`) | status `done` → done or partial |
| **Done** | Summary bar + results table, with `needs_review` sorted to the top. Download CSV button, and "New file" to reset | — |
| **Partial failure** | Same as Done, plus an amber banner: "6 lines could not be processed (API error after 3 retries / budget stop). They are marked needs_review with reason `LLM_FAILURE:<kind>` (or `LLM_UNAVAILABLE` / `BUDGET_CAP`), shown verbatim from the frozen reason-code enum. Retry failed lines". Retry posts only those row IDs to a new job | Closed-world is kept: an errored line is never dropped and never guessed. It becomes `needs_review`, matching the service's existing failure policy |
| **Error** | Full-width card with a plain-language cause, the technical detail collapsed, and a "Start over" button. Covers network loss (polling backs off 1→2→5 s, then shows "Connection lost, retrying"), job not found after a server restart, and 5xx | Start over |

## 6. Main screen wireframe (done state, desktop ≥1024 px)

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ [■] BoQ Matcher                                          Library: Global (342 rows) ▾  ◐  │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│  boq_dataset_input_fr.csv · 319 lines · done in 1 m 24 s           [⤓ Download CSV] [New]│
│ ┌──────────────┬──────────────┬────────────────┬─────────────┬──────────┬──────────────┐ │
│ │ ● Matched 168│ ▲ Review 105 │ ○ Not material │ Cost $0.30  │ ⏱ 0.4 s  │ ~2.6 h saved*│ │
│ │              │              │            46  │             │ mean/line│              │ │
│ └──────────────┴──────────────┴────────────────┴─────────────┴──────────┴──────────────┘ │
│  [All 319] [▲ Needs review 105] [● Matched 168] [○ Not a material 46]  ⌕ [search…      ] │
├────┬──────────────┬──────────────────────────────┬──────┬───────────────┬────────────────┤
│    │ Item No. ⇅   │ Description                  │ Unit │ Decision ⇅    │ ORIS label     │
├────┼──────────────┼──────────────────────────────┼──────┼───────────────┼────────────────┤
│ ▌  │ 03.02.0080.  │ Couche de base C30/37, …     │ m³   │ ▲ Needs review│ —  gap: narrow │ ›
│ ▌  │ 02.01.0010.  │ Collecteur PP DN 300 SN 8 …  │ m    │ ▲ Needs review│ —  no lib. eq. │ ›
│    │ 03.02.0090.  │ Liant pour grave-ciment, CEM…│ t    │ ● Matched     │ ‹type› ·       │ ›
│    │              │                              │      │               │ ‹usage› · ‹sub›│
│    │ 03.02.       │ Couches d'assise liées …     │      │ ○ Not material│ section header │ ›
└────┴──────────────┴──────────────────────────────┴──────┴───────────────┴────────────────┘
 * estimate: 1.5 min manual lookup per auto-decided line; editable assumption (see audit)
 All counts, costs and labels in this wireframe are ILLUSTRATIVE (not measured, not ground truth). The
 not_a_material count can never exceed 46 on this file (37 headers + 9 service-unit lines).

 Audit drawer (opens from › or Enter, slides from the right; bottom sheet on mobile):
 ┌──────────────────────────────────────────┐
 │ 03.02.0080.  Needs review           [✕]  │
 │ Reason: LOW_SIGNAL:gap                   │
 │ Top-1  ‹type› · ‹usage› · C30/37         │
 │ Top-2  ‹type› · ‹usage'› · C30/37        │
 │ Confidence 62 · gap: narrow (ordinal)    │
 │ Attributes: strength C30/37 = literal    │
 │ Model  claude-haiku-4-5 · prompt 3f9a1c  │
 │ Latency 1 140 ms · Cost $0.0011          │
 │ ▸ Raw model response (JSON)   [Copy]     │
 │ ▸ Row id  a41c…e09                       │
 └──────────────────────────────────────────┘
```

## 7. Components and behaviour

- **Library selector:** a segmented control (Global / FR / Custom…). Picking Custom reveals a second file input.
  - The selected library is shown with its row count and 12-character hash in the header after the run, so a result is never ambiguous about which library produced it. This matters for the live FR session.
  - Default is Global, per the README's scoring instruction.
- **Decision chips:** each chip pairs a shape and text with its colour, so it never relies on colour alone (WCAG 1.4.1). Labels are always the literal output enum, `matched / needs_review / not_a_material`, displayed as "Matched / Needs review / Not a material".

  | Decision | Shape | Colour |
  |---|---|---|
  | matched | filled circle | primary |
  | needs_review | triangle | amber, plus a 4 px amber left border on the row |
  | not_a_material | hollow circle | muted |

- **Table:**
  - Language-agnostic: descriptions are shown verbatim and never translated.
  - The long description is a second muted line, clamped to 2 lines and expanded in the drawer.
  - Hierarchy level shows as indentation (L0/L1 headers bold), which makes the `not_a_material` header logic self-evident.
- **Sorting:** the default order is decision priority (`needs_review` first), then original order. Clicking the Item No. or Decision headers toggles the sort. "Original order" is one click away, because operators think in BoQ order.
- **Filter and search:**
  - Filter chips double as counts.
  - Search is client-side, case- and accent-insensitive (`normalize('NFD')`), over item no., descriptions and labels, with a 150 ms debounce.
  - Filter, sort and search state live in `location.hash`, so a refresh or a shared link keeps the view.
- **Summary bar:** decision counts, total cost, mean and p95 latency, and estimated reviewer minutes saved.
  - The formula is `(matched + not_a_material) × min_per_line`, with `min_per_line = 1.5` as a stated, editable assumption in `config`.
  - It is labelled "estimate" with an asterisk. It is never presented as measured.
- **Audit drawer:** shows the model, prompt version, gate that fired, reason, top-1 and top-2 with confidence and candidate gap, latency, cost, error if any, and the raw JSON response in a collapsible `<pre>` with a Copy button.
  - This turns the evaluation story (abstention signals, gates) into something an operator can inspect, which is the point of building it.
- **Download CSV:** fetches `result.csv` from the server rather than serialising on the client, so the file is byte-identical to the CLI output. The filename is `{input_stem}_matched_{library}_{yyyymmdd-hhmm}.csv`.

## 8. Accessibility and keyboard

- **Semantic HTML:**
  - The table is a real `<table>` with `<th scope>` and `aria-sort`.
  - Filter chips are `<button aria-pressed>`.
  - Progress is `<progress>` plus an `aria-live="polite"` text that updates every 10 % rather than every line.
  - The drawer is a `<dialog>` with focus trap, `Esc` to close, and focus returned to the row on close.
- **Keyboard:**
  - `/` focuses search.
  - `j / k` or `↑ / ↓` move the row focus (roving `tabindex`), and `Enter` opens the drawer.
  - `1–4` switch the filters, and `d` downloads.
  - The drop zone is also a real `<input type=file>` label, so Space/Enter opens the picker.
- **Visuals:** visible `:focus-visible` rings (2 px primary, offset 2 px), and colour contrast of at least 4.5:1 for text in both themes, validated once with axe in DevTools.
- **Motion:** `prefers-reduced-motion` turns off the drawer slide and the progress transitions.

## 9. Responsive behaviour (mobile-first)

- **Base (<640 px):**
  - Single column. The summary bar is a 2×3 grid of tiles, and the filter chips scroll horizontally in their own row (the page itself never scrolls sideways).
  - The table collapses to stacked cards: item no. and chip on top, description, then label.
  - The drawer becomes a full-height bottom sheet.
- **Touch and input sizes:** all interactive targets are at least 44×44 px, and inputs and selects use a 16 px font so iOS does not zoom.
- **Layout:** 16 px side gutters. From 640 px the table view returns, and from 1024 px the max content width is 1280 px with a sticky table header.

## 10. Design tokens

All tokens are defined once in `app.css` `:root`, with no raw colours in the components.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#F7F7F5` | `#121416` | page |
| `--surface` | `#FFFFFF` | `#1B1E21` | cards, table, drawer |
| `--fg` / `--fg-muted` | `#1A1D1F` / `#5F666D` | `#E8EAEC` / `#9AA1A8` | text, not_a_material chip |
| `--primary` | `#1F6F5C` (deep teal) | `#4FB39A` | matched chip, primary button, focus |
| `--review` | `#B45309` (amber) | `#F2A541` | needs_review chip, border, banner |

- **Palette size:** 5 colours (2 neutrals, `--fg` with its muted tint, 1 primary, 1 accent). Errors reuse `--review` together with an icon and text. No gradients anywhere.
- **Fonts:** two system families with zero network fetch, which works offline in an interview room: `system-ui` for UI text and `ui-monospace` for item numbers, hashes and raw JSON.
- **Neutral identity:** a deliberately neutral palette and wordmark ("BoQ Matcher"). ORIS's logo or brand is never used.
- **Theme:** follows `prefers-color-scheme` by default, with a header toggle (◐) that sets `data-theme` (`light`/`dark`) and is remembered in `localStorage` inside a try/catch.
- **Icons:** about 10 inline SVGs in `icons.ts` (upload, play, download, search, filter, close, chevron, triangle-alert, circle, copy), 1.5 px stroke, `currentColor`, `aria-hidden` next to text labels. No emoji and no icon font.

## 11. Code layout and one-day build plan

```
ui/
  package.json            esbuild, typescript (devDeps only)
  tsconfig.json           strict
  src/main.ts             state machine + hash routing
  src/api.ts              typed fetch client (types mirror Pydantic schemas)
  src/views/upload.ts  progress.ts  summary.ts  table.ts  drawer.ts
  src/icons.ts  src/format.ts  src/app.css
  index.html
src/oris/api/static/ui/   committed build output (index.html, app.js, app.css)
src/oris/api/jobs.py      JobStore + routes
src/oris/io/boq_reader.py csv/xlsx parsing (shared with CLI)
```

| Block (Thu 8 Oct) | Work | Done when |
|---|---|---|
| 09:00–10:30 | `jobs.py` + tests on FakeLLM: lifecycle, 400/413/409, per-line failure → `needs_review`, `result.csv` equals the CLI output, CSV/XLSX row-id parity (the reader is shared and already built Mon) | pytest green, mypy clean |
| 10:30–12:30 | Upload and running views, polling with backoff, library selector | A real 319-line job runs end-to-end on ReplayLLM at $0 |
| 13:00–14:30 | Table, chips, filter, search, sort, summary bar, audit drawer, download | U1–U4 pass manually on the EN and FR inputs |
| 14:30–15:00 | Responsive cards, committed build, CI freshness check, README screenshot | Clean clone + `uv run oris serve` → `/ui` works without Node |

**Hard stop at 15:00.** Anything unfinished follows the cut order below. Thursday afternoon belongs to the README and the live rehearsal.

**Demo cost discipline:** the UI defaults to the service's configured LLM. With `ORIS_LLM=replay` it replays `calls.jsonl`, so the demo of the graded inputs costs $0 and is deterministic. A live upload of an unseen BoQ uses Haiku under the existing budget stop.

**Cut order if time runs short:** keyboard shortcuts beyond `/` and `Esc` → dark-mode toggle (keep the auto theme) → retry of failed lines → custom library upload (keep Global/FR). U1–U4, the drawer and the CSV download are never cut.