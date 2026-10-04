# Operator UI specification

Pre-registered 4 Oct 2026; revised in pre-registration v2 (merged before any model call). The UI is deferred work after the must-haves: it is built in G6, only after the eval-freeze, both output files and the clean-clone check (G5) are green, with a hard stop at Fri 9 Oct 12:00. It is first in the cut order if late. The summary is in `DESIGN.md` §13. Paragraphs changed in v2 end with their amendment id, e.g. [A14]. [A14, A32]

## 1. Scope and non-goals

The README's optional UI asks for four things, and every one of them is a hard acceptance criterion:

| #   | README ask                                                           | How the UI meets it                                                                                                                                               | Test that proves it                                                                      |
| --- | -------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| U1  | Upload a BoQ (CSV or Excel)                                          | Drop zone or file picker accepts `.csv` and `.xlsx`. The server parses the file, so the browser only uploads it                                                   | API test: the same BoQ as CSV and as XLSX gives identical line ids and cell values [A15] |
| U2  | Run the matching                                                     | One primary button, "Run matching", which creates a job                                                                                                           | API test with FakeLLM: job goes `queued → running → done`                                |
| U3  | Show the table when done, with the decision and labels for each line | Results table: Item No., description, unit, decision chip, `material_type / usage / subtype`, reason                                                              | API test: `/result` row count and order equal the input's                                |
| U4  | `needs_review` rows easy to spot                                     | Amber chip and amber left border, a one-click filter, and a count in the summary bar. The table stays in input order by default, so section context is kept [A14] | Manual check plus a DOM assertion in the UI smoke script                                 |

Non-goals, all cut to keep the build inside the G6 window (about 5–6 h):

- No auth and no multiple users.
- No editing or overriding decisions in the UI. The output stays closed-world and the UI only reads it.
- No job persistence across restarts. Jobs are in memory, in a single worker, and follow the limits in §3. [A14]
- No time-saved or minutes-saved figure. The report keeps one assumptions table; the UI shows only measured values. [A14]
- No virtualised table, because about 320 rows render fine as plain DOM.
- No charts beyond the summary bar.
- No i18n of the UI chrome (English only). The data can be in any language.

## 2. Architecture

```
browser (ui/dist/app.js, ~30 KB)  ──HTTP──▶  FastAPI (same process, same async MatchService as the CLI)
   GET /ui            static index.html + app.js + app.css   (StaticFiles mount, committed build)
   POST /v1/jobs      multipart upload ─▶ parse (csv / openpyxl + defusedxml) ─▶ BoQ lines ─▶ asyncio task
   GET  /v1/jobs/{id} progress poll (1 s)
   GET  /v1/jobs/{id}/result        JSON rows + summary
   GET  /v1/jobs/{id}/result.csv    exact output CSV (same writer as the CLI)
   GET  /v1/libraries               built-in libraries list
```

- **Stack:** vanilla TypeScript in strict mode with no framework. The app is about 6 small render modules, and the only dev dependencies are `esbuild` and `typescript`. `tsc --noEmit` type-checks, and esbuild bundles and minifies to `src/oris_matcher/api/static/ui/`. [A49]
- **The build output is committed**, so reviewers run only `uv run oris serve` and open `http://localhost:8000/ui`. They never need Node.
- **CI freshness gate:** `npm run build && git diff --exit-code src/oris_matcher/api/static/ui` catches a stale bundle. [A49]
- **One source of truth.** The UI never computes decisions, labels or metrics. Everything it shows comes from the same `MatchService` and CSV writer that produce the graded output files. That way a UI bug cannot diverge from the graded artefact. A job runs the whole uploaded file through the same `plan_batches(lines)` and whole-file section paths as the CLI, so its requests match a CLI run on the same file. [A50]

## 3. API contract: async job vs synchronous call

|                                            | `POST /v1/match` (sync)                                                                                                                         | `POST /v1/jobs` + poll (async)                                                                       |
| ------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| 320 lines × ≤2 s/line, concurrency 4 [A12] | Minutes held open on one request (up to about 10 min at the 2 s/line gate). Browser and proxy timeouts become a risk, with no progress feedback | Returns `202` with a `job_id` in under 200 ms, then the UI polls every 1 s                           |
| Progress bar                               | Not possible without streaming                                                                                                                  | `done / total` comes free from the counter                                                           |
| Partial failure                            | All or nothing, or a complex partial body                                                                                                       | Each line's error is recorded and the job still finishes                                             |
| Implementation cost                        | Already in the plan                                                                                                                             | About 60 lines: in-memory `dict[job_id, Job]` + `asyncio.create_task`, using the service's semaphore |

**Decision:** keep both. The external review proposed dropping `/v1/jobs` (A40); that proposal was not adopted, so the jobs path stays, sequenced as deferred work after the must-haves. [A32]

- **`POST /v1/match`** stays the programmatic path, with a documented soft limit of 100 lines and a hard limit of **500 lines** (`413` above that), so a whole BoQ fits in one documented call (`DESIGN.md` §11.4). [A50]
- **The UI uses jobs only.** Polling is chosen over SSE or WebSockets because it is simpler, works through any proxy, survives a tab refresh (the job id is kept in `location.hash`), and 1 request/s against a local process is negligible.
- **Single uvicorn worker:** the job store is in-process, and this is documented as a known limit.
- **Job limits** (all documented in the README): [A14]
  - at most 4 jobs queued; a fifth `POST /v1/jobs` gets `429 {error: "queue_full"}`;
  - results are dropped 1 h after a job finishes; a later `GET` returns `404 {error: "job_expired"}`;
  - queued and running jobs are cancelled on shutdown, and nothing persists across restarts.
- **Concurrency is the service's fixed semaphore of 4** (A12, also used by the CLI). The jobs layer adds no concurrency of its own, and jobs share the service's budget stop and the `DESIGN.md` §11.3 breaker. [A12]

Schemas (Pydantic models; OpenAPI is generated automatically at `/docs`):

```
POST /v1/jobs   multipart: file (csv|xlsx, ≤2 MB, ≤2,000 rows), library (id from Settings.libraries: "global"|"fr")
                           | file part "library_file" (custom upload; content only, never a path)
  202 {job_id, total_lines, library: {name, rows, sha256_12}, policy_resolution, encoding, warnings: [...]}
  400 {error: "missing_columns", detail: ["BoQ Qty"]} | "unsupported_type" | "empty_file" | "bad_library"
      | "library_too_large"  (rendered library > 150k tokens; names the retrieval switch)
  401 if ORIS_API_TOKEN is set and the Bearer token is missing or wrong
  413 file > 2 MB or > 2,000 rows
  422 unknown library id, or an unexpected form field (extra='forbid')
  429 {error: "queue_full"}  (4 jobs already queued)
GET /v1/jobs/{id}
  200 {status: "queued"|"running"|"done"|"failed"|"cancelled", done, total, errors, cost_usd, elapsed_s, eta_s,
       expires_at|null}
  404 {error: "job_not_found"|"job_expired"}
GET /v1/jobs/{id}/result
  200 {summary: {matched, needs_review, not_a_material, errors, cost_usd, wall_clock_s_per_line,
                 mean_attributed_latency_ms, p95_attributed_latency_ms, served_models, policy_resolution},
       rows: [{line_id, item_no, short, long, unit, qty, level, decision, material_type, material_usage,
               material_subtype, reason, model, prompt_version, latency_ms, cost_usd, library_row_id, call_ids,
               audit: {gate, evidence, element_or_application, top1, top2, confidence, candidate_gap,
                       raw_line_response, error|null}}]}
  409 if not done
GET /v1/jobs/{id}/result.csv   text/csv, byte-identical to `oris match` output for the same input
GET /v1/libraries  [{id:"global", rows, sha256_12}, {id:"fr", ...}]
Every response carries X-Request-ID. No CORS middleware.
```

Schema changes in v2: library ids, auth, request id and field limits follow A51; there is no time-saved field (A14); `line_id` is the position-based line id (A15(2)); latency is attributed per A45 and the gate figure is wall-clock per line; `raw_line_response` and `call_ids` follow A36; `evidence` and `element_or_application` are the A43 output fields; `policy_resolution` follows A38. [A14, A15, A36, A38, A43, A45, A51]

## 4. Server-side parsing

The UI only uploads; all parsing happens on the server.

- **Upload limits:** the file must be ≤ 2 MB and ≤ 2,000 data rows, checked before any model call (413 otherwise). XLSX parsing needs the `[xlsx]` extra (openpyxl + defusedxml), and defusedxml is installed so the workbook XML is parsed safely. One test per limit. [A49, A51]

- **CSV:**
  - Try UTF-8 first (BOM tolerated). Only if that fails, decode as `cp1252`, record `encoding: cp1252` in the manifest, and show the warning in the UI. A UTF-8 file can never be mis-decoded, because UTF-8 is always tried first. [A15]
  - Detect the delimiter with `csv.Sniffer` over `,` and `;`, since French Excel exports use `;`.
- **XLSX:**
  - Open with `openpyxl.load_workbook(read_only=True, data_only=True)` and read the first visible sheet.
  - Reject `.xlsm` and `.xls`.
  - Read only cell values, never formulas or macros.
  - Convert numeric cells back to strings in a way that keeps item numbers like `09.01.0010.` intact: numeric item codes are rendered with their cell `number_format`.
- **Headers:** match case- and whitespace-insensitively against the five required columns (`Item No., Short Description, Long Description, Unit, BoQ Qty`). Both shipped inputs use these exact English headers. A small alias map (`N° article`, `Désignation`, `Unité`, `Quantité`) is cheap insurance for the live FR session.
- **Shared parser:** one parser module, `src/oris_matcher/io/boq_reader.py`, used by the CLI too (`oris match file.xlsx`), so the XLSX path gets the same unit tests. Headers and section paths are detected structurally (A48), so a renumbered or code-less file behaves as in the CLI. [A48, A49]
- **Row identity:** each row is identified by the §9.1 position-based line id (transport id `L<position>`, A8), assigned after parsing in file order. The UI, the result rows, retries and the audit drawer all use this id. The same file as CSV and as XLSX therefore gets the same ids, which is the U1 test. [A15]
- **Custom library upload:** the file is validated as three columns `material_type, material_usage, material_subtype`, with or without quoted headers, since the global CSV quotes them. It needs at least 1 row and at most 5,000. Its short hash is shown in the UI so the operator knows exactly which library produced the result. An uploaded library has no entry in `config/policy.yaml`, so it runs unenriched under the strictest policy; the UI shows the returned `policy_resolution: fallback_strictest` next to the hash. Library size follows A15(4): up to 30k rendered tokens runs whole; 30k–150k runs whole with a warning; above 150k is rejected with `library_too_large`; nothing is truncated. [A15, A38]

## 5. Screens and states

There is one screen, a single-page state machine (`idle → uploading → running → done | partial | error`).

| State               | What the operator sees                                                                                                                                                                                                                                                                                                                                                                                 | Exit                                                                                                                                                |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Empty / idle**    | Drop zone ("Drop a BoQ (.csv or .xlsx) or choose a file"), library selector (Global (default) / FR / Upload custom), and a disabled "Run matching" button. One line explains the three decisions                                                                                                                                                                                                       | File selected → the button is enabled and the file name, size and row count are shown after the server check                                        |
| **Uploading**       | Button spinner and "Uploading and checking columns…". Inputs are disabled                                                                                                                                                                                                                                                                                                                              | 202 → running. 4xx → error with an inline fix ("Missing column: BoQ Qty")                                                                           |
| **Running**         | Determinate progress bar `142 / 319 lines`, elapsed time, ETA, running cost in USD, and a "Model calls run 4 at a time" note [A12]. A skeleton table with the input rows already listed and decision cells as shimmer-free placeholders (no animation if `prefers-reduced-motion`)                                                                                                                     | status `done` → done or partial                                                                                                                     |
| **Done**            | Summary bar + results table in input order, with the needs_review count and one-click filter. Download CSV button, and "New file" to reset [A14]                                                                                                                                                                                                                                                       | —                                                                                                                                                   |
| **Partial failure** | Same as Done, plus an amber banner: "6 lines could not be processed (API error after retries / budget stop). They are marked needs_review with reason `LLM_FAILURE:<kind>` (or `LLM_UNAVAILABLE` / `BUDGET_CAP`), shown verbatim from the frozen reason-code enum. Retry failed lines". Retry posts only those lines, identified by line id and sent with their section paths, to a new job [A15, A50] | Closed-world is kept: an errored line is never dropped and never guessed. It becomes `needs_review`, matching the service's existing failure policy |
| **Error**           | Full-width card with a plain-language cause, the technical detail collapsed, and a "Start over" button. Covers network loss (polling backs off 1→2→5 s, then shows "Connection lost, retrying"), job not found after a server restart or after the 1 h expiry, a full queue (429), and 5xx [A14]                                                                                                       | Start over                                                                                                                                          |

## 6. Main screen wireframe (done state, desktop ≥1024 px)

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ [■] BoQ Matcher                                          Library: Global (342 rows) ▾  ◐  │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│  boq_dataset_input_fr.csv · 319 lines · done in 1 m 24 s           [⤓ Download CSV] [New]│
│ ┌──────────────┬──────────────┬────────────────┬─────────────┬──────────┬──────────────┐ │
│ │ ● Matched 168│ ▲ Review 105 │ ○ Not material │ Cost $0.30  │ ⏱ 0.4 s  │ Policy       │ │
│ │              │              │            46  │             │ wall/line│ dev_selection│ │
│ └──────────────┴──────────────┴────────────────┴─────────────┴──────────┴──────────────┘ │
│  [All 319] [▲ Needs review 105] [● Matched 168] [○ Not a material 46]  ⌕ [search…      ] │
├────┬──────────────┬──────────────────────────────┬──────┬───────────────┬────────────────┤
│    │ Item No. ⇅   │ Description                  │ Unit │ Decision ⇅    │ ORIS label     │
├────┼──────────────┼──────────────────────────────┼──────┼───────────────┼────────────────┤
│ ▌  │ 02.01.0010.  │ Collecteur PP DN 300 SN 8 …  │ m    │ ▲ Needs review│ —  no lib. eq. │ ›
│    │ 03.02.       │ Couches d'assise liées …     │      │ ○ Not material│ section header │ ›
│ ▌  │ 03.02.0080.  │ Couche de base C30/37, …     │ m³   │ ▲ Needs review│ —  low signal  │ ›
│    │ 03.02.0090.  │ Liant pour grave-ciment, CEM…│ t    │ ● Matched     │ ‹type› ·       │ ›
│    │              │                              │      │               │ ‹usage› · ‹sub›│
└────┴──────────────┴──────────────────────────────┴──────┴───────────────┴────────────────┘
 Rows are in input order (A14). All counts, costs and labels in this wireframe are ILLUSTRATIVE (not
 measured, not ground truth). The not_a_material count can never exceed 46 on this file (37 headers +
 9 service-unit lines).

 Audit drawer (opens from › or Enter, slides from the right; bottom sheet on mobile):
 ┌──────────────────────────────────────────┐
 │ 03.02.0080.  Needs review           [✕]  │
 │ Reason: LOW_SIGNAL:confidence            │
 │ Evidence "Couche de base C30/37"         │
 │ Element  base course                     │
 │ Top-1  ‹type› · ‹usage› · C30/37         │
 │ Top-2  ‹type› · ‹usage'› · C30/37        │
 │ Confidence 62 (model self-report,        │
 │   uncalibrated) · gap: narrow (ordinal)  │
 │ Attributes: strength C30/37 = agree      │
 │ Model  claude-haiku-4-5-20251001         │
 │ Prompt 3f9a1c · call_ids c17;c18         │
 │ Latency 1140 ms · Cost $0.001100 (attr.) │
 │ ▸ Raw line response (JSON)    [Copy]     │
 │ ▸ Line id  L57                           │
 └──────────────────────────────────────────┘
```

Wireframe changes in v2: input order, no time-saved tile, confidence after the reason code and evidence with its "model self-report, uncalibrated" label, served model, call ids, attributed cost and latency, and the position-based line id. [A14, A15, A35, A36, A43, A45]

## 7. Components and behaviour

- **Library selector:** a segmented control (Global / FR / Custom…). Picking Custom reveals a second file input.
  - The selected library is shown with its row count and 12-character hash in the header after the run, so a result is never ambiguous about which library produced it. This matters for the live FR session.
  - Default is Global, per the README's scoring instruction.
  - The header also shows the policy resolution returned by the server (`dev_selection`, `smoke_A10` or `fallback_strictest`, `DESIGN.md` §10.6), so an operator can see when an unknown library ran under the strictest policy. [A38]
- **Decision chips:** each chip pairs a shape and text with its colour, so it never relies on colour alone (WCAG 1.4.1). Labels are always the literal output enum, `matched / needs_review / not_a_material`, displayed as "Matched / Needs review / Not a material".

  | Decision       | Shape         | Colour                                          |
  | -------------- | ------------- | ----------------------------------------------- |
  | matched        | filled circle | primary                                         |
  | needs_review   | triangle      | amber, plus a 4 px amber left border on the row |
  | not_a_material | hollow circle | muted                                           |

- **Table:**
  - Language-agnostic: descriptions are shown verbatim and never translated.
  - The long description is a second muted line, clamped to 2 lines and expanded in the drawer.
  - Hierarchy level shows as indentation (L0/L1 headers bold), which makes the `not_a_material` header logic self-evident.
- **Sorting:** the default order is input order, because operators think in BoQ order and review-first sorting breaks section context. needs_review rows are found with the one-click filter and the count, not by re-sorting. Clicking the Item No. or Decision headers toggles the sort, and "Input order" restores the default. [A14]
- **Filter and search:**
  - Filter chips double as counts.
  - Search is client-side, case- and accent-insensitive (`normalize('NFD')`), over item no., descriptions and labels, with a 150 ms debounce.
  - Filter, sort and search state live in `location.hash`, so a refresh or a shared link keeps the view.
- **Summary bar:** decision counts, total cost, wall-clock time per line, and the policy resolution. Mean and p95 latency are shown in a tooltip, labelled "attributed (conservative under concurrency)". [A14, A45]
  - No time-saved or minutes-saved figure appears anywhere in the UI. The report keeps one assumptions table. [A14]
- **Audit drawer:** shows, in this order, the gate that fired and the reason code, the evidence span and element_or_application, top-1 and top-2, then the confidence labelled "model self-report, uncalibrated" with the candidate gap, then the served model, prompt version, call ids, attributed latency and cost, error if any, and the line's raw JSON response (`raw_line_response`) in a collapsible `<pre>` with a Copy button. [A14, A36, A43, A45]
  - Confidence never appears in the table or the summary bar, only in the drawer after the reason and evidence. [A14]
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

| Token                 | Light                 | Dark                  | Use                                 |
| --------------------- | --------------------- | --------------------- | ----------------------------------- |
| `--bg`                | `#F7F7F5`             | `#121416`             | page                                |
| `--surface`           | `#FFFFFF`             | `#1B1E21`             | cards, table, drawer                |
| `--fg` / `--fg-muted` | `#1A1D1F` / `#5F666D` | `#E8EAEC` / `#9AA1A8` | text, not_a_material chip           |
| `--primary`           | `#1F6F5C` (deep teal) | `#4FB39A`             | matched chip, primary button, focus |
| `--review`            | `#B45309` (amber)     | `#F2A541`             | needs_review chip, border, banner   |

- **Palette size:** 5 colours (2 neutrals, `--fg` with its muted tint, 1 primary, 1 accent). Errors reuse `--review` together with an icon and text. No gradients anywhere.
- **Fonts:** two system families with zero network fetch, which works offline in an interview room: `system-ui` for UI text and `ui-monospace` for item numbers, hashes and raw JSON.
- **Neutral identity:** a deliberately neutral palette and wordmark ("BoQ Matcher"). ORIS's logo or brand is never used.
- **Theme:** follows `prefers-color-scheme` by default, with a header toggle (◐) that sets `data-theme` (`light`/`dark`) and is remembered in `localStorage` inside a try/catch.
- **Icons:** about 10 inline SVGs in `icons.ts` (upload, play, download, search, filter, close, chevron, triangle-alert, circle, copy), 1.5 px stroke, `currentColor`, `aria-hidden` next to text labels. No emoji and no icon font.

## 11. Code layout and G6 build plan

```
ui/
  package.json            esbuild, typescript (devDeps only)
  tsconfig.json           strict
  src/main.ts             state machine + hash routing
  src/api.ts              typed fetch client (types mirror Pydantic schemas)
  src/views/upload.ts  progress.ts  summary.ts  table.ts  drawer.ts
  src/icons.ts  src/format.ts  src/app.css
  index.html
src/oris_matcher/api/static/ui/   committed build output (index.html, app.js, app.css)
src/oris_matcher/api/jobs.py      JobStore + routes (queue cap, expiry, cancel on shutdown)
src/oris_matcher/io/boq_reader.py csv/xlsx parsing (shared with CLI)
```

Package paths follow A49 (`oris_matcher` everywhere; the CLI command stays `oris`). [A49]

| Block (G6, hours from start) | Work                                                                                                                                                                                                                                                                                                           | Done when                                                    |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| 0:00–1:30                    | `jobs.py` + tests on FakeLLM: lifecycle, 400/401/404/409/413/422/429, queue cap of 4, 1 h expiry, cancel on shutdown, per-line failure → `needs_review`, `result.csv` equals the CLI output, CSV/XLSX line-id parity, one test per upload limit (the reader is shared and already built in G1) [A14, A15, A51] | pytest green, mypy clean                                     |
| 1:30–3:30                    | Upload and running views, polling with backoff, library selector                                                                                                                                                                                                                                               | A real 319-line job runs end-to-end on ReplayLLM at $0       |
| 3:30–5:00                    | Table, chips, filter, search, sort, summary bar, audit drawer, download                                                                                                                                                                                                                                        | U1–U4 pass manually on the EN and FR inputs                  |
| 5:00–5:30                    | Responsive cards, committed build, CI freshness check, README screenshot                                                                                                                                                                                                                                       | Clean clone + `uv run oris serve` → `/ui` works without Node |

**Hard stop at Fri 9 Oct 12:00 (the G6 cap).** G6 opens only if G5 is green. Anything unfinished follows the cut order below. Fri 12:00–17:00 belongs to the README and the submission only. [A32]

**Demo cost discipline:** the UI defaults to the service's configured LLM. With `ORIS_LLM=replay` it replays the committed `runs/submission/` calls.jsonl, so the demo of the graded inputs costs $0 and is deterministic. A replay miss marks the line needs_review with `LLM_FAILURE:replay_miss` and never falls through to a live call. A live upload of an unseen BoQ uses the pinned `claude-haiku-4-5-20251001` under the existing budget stop; only allowlisted models can serve it, fallback included. [A33, A36, A50]

**Cut order if time runs short:** keyboard shortcuts beyond `/` and `Esc` → dark-mode toggle (keep the auto theme) → retry of failed lines → custom library upload (keep Global/FR). U1–U4, the drawer and the CSV download are never cut while the UI ships. If G6 itself runs short, the whole UI is the first item in the `DESIGN.md` §8 cut order, and the README lists it as in progress. [A32]
