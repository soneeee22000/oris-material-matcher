# Changelog

Each release is an annotated git tag. The lockbox claim belongs to `v1.0`, evaluated at `eval-freeze`. Later releases leave it unchanged.

## v1.4.0 (2026-10-09)

Operator UI. No matching, decision, configuration, output or scored number changed.

- `oris serve` runs the API and the operator UI at `/ui` (`docs/ui-spec.md`) in one worker on `127.0.0.1:8000`. `--llm` picks the model: `live` (the pinned primary, paid), `fake` (offline, not a measurement) or `replay:<run_dir>` (recorded answers at $0; a line not in the recording goes to review as `LLM_FAILURE:replay_miss`, and only the recorded library is accepted). It warns when the UI build is missing, or when it binds beyond loopback without a token.
- The UI is vanilla TypeScript bundled by esbuild into `src/oris_matcher/api/static/ui/`, so serving it needs no Node. It covers U1–U4: upload, progress, the results table with `needs_review` rows marked, filters, search, sort, the per-line audit drawer, a banner naming each failure reason, "Retry failed lines" (live and fake only), shareable `#job=` links and the CSV download. A banner states replay or fake mode before the run, and replay figures are labelled as recorded, not measured.
- Async jobs API: `POST /v1/jobs`, `GET /v1/jobs/{id}`, `/result`, `/result.csv` (the CLI writer's bytes), `GET /v1/libraries` and `GET /v1/serving`. Jobs live in memory: at most 4 queued and 20 kept, results for one hour. Uploads are capped at 2 MB and 2,000 rows, a chunked body is counted as it is read, and a workbook that declares too large an uncompressed size is refused.
- `.xlsx` BoQs are read with the `xlsx` extra (defusedxml required) and give the same line ids and cells as the CSV.
- Replaying the EN and FR lockbox runs through `oris serve` gives a `result.csv` byte-identical to `output/improved_output_{en,fr}.csv`.
- CI: a `ui` job type-checks and rebuilds the UI, fails when the committed bundle differs from the build (missing or extra files included), and runs a Chromium smoke test against a mocked API.
- Fix: a blank or whitespace-only `ORIS_API_TOKEN` now leaves `/v1/*` open instead of refusing every request.
- Departures from `docs/ui-spec.md`: built after its G6 hard stop; custom library upload and the row count before the run are not built; the theme opens dark with a toggle instead of following the system setting; colours reuse the project page's tokens instead of §10; replay is `oris serve --llm replay:<run_dir>` rather than `ORIS_LLM=replay`; the browser adds the `{yyyymmdd-hhmm}` part of the download name; the axe contrast check was not run; the browser test uses a mocked API and a CSV only; the first upload of a session took 7.5 s to be accepted, against the specified 200 ms.

## v1.3.1 (2026-10-09)

Docs and project page only. No code, configuration or number changed.

- README: a "Set up on your machine" section (git clone, `uv sync`, copying `.env.example` to `.env` and where the Anthropic key goes, with the Windows command), ahead of the $0 commands.
- `.env.example`: the optional `OPENAI_API_KEY` and `ORIS_API_TOKEN` lines are commented out, so a plain copy works; a blank `ORIS_API_TOKEN` would otherwise make `/v1/*` refuse every request.
- README: coverage is also given over all material lines (EN .752, FR .675), the latency figure is named as batch wall clock, rerunning an exercise file live is explained (`git checkout eval-freeze`), and the scorer's `--join row-order` is documented for label-only references.
- Project page wording: "complementary checks" instead of "independent checks", and a skipped header no longer "costs nothing".
- The pipeline hero is ported from the original blueprint scene and corrected to the seven stages of `MatchService.match`: read (headers decided there), plan, two passes, the fallback (not engaged), validate + decision table, the sibling verifier on would-be matches only, decide + write. Every count comes from `site/data.json`.
- The exporter adds the run's call concurrency (`settings_effective.concurrency`) to the page data.
- The page opens in the dark theme; the light theme stays one click away.

## v1.3.0 (2026-10-09)

Docs and project page only. No code, configuration or number changed.

- `site/`: a static project page built from committed output by `scripts/export_site_data.py`, with a CI drift check (`--check`) that fails when the page data no longer matches the committed files.
- README rewritten in portfolio shape; the development-results ladder and the other long tables it dropped moved verbatim to `docs/development-results.md`.
- Adds `docs/WHY.md`: the problem, the layer-by-layer answer, and what this project is not.
- `DESIGN.md`: a dated post-submission orientation note under the title, outside the pre-registered text.
- `eval/cross_model_dev.py`: a post-freeze, dev-only analysis of the pre-registered cross-model vote (E-02(d)), using recorded GPT-4o-mini votes against the shipped Haiku decisions. It costs $0 and its result is not adopted under the pre-registered rule. The per-item votes are committed (`eval/cross_model_dev_votes.json`), so the report reproduces with `--check`.
- Adds `docs/alternatives.md`: each alternative model and method, what was measured, what was not, and why.

## v1.2.1 (2026-10-09)

Docs only. No code, configuration or number changed.

- `docs/gates/G6.md` is closed, with the release record and a fresh-clone check of `v1.2.0`.
- Adds this changelog.

## v1.2.0 (2026-10-09)

- The FR library is certified for the pinned model by the A10 smoke rule (A70). It runs `T8` with the sibling verifier and `data/enrichment/fr.yaml`, and the brief's FR command now prints `policy_resolution: exact (policy T8)`. The smoke set is 68 builder-labelled lines frozen before one live run; the verdict was a pass, with 0 closed-world violations, 0 false `not_a_material`, 0 decoy matches and 13 of 18 base positives correct. It is readiness evidence, not a held-out claim (`docs/gates/G6.md`).
- `eval/smoke_a10.py`: the A10 scorer. It refuses unlabelled or duplicate lines, a label set without 18 base positives, and a run whose configuration differs from the frozen one.
- Fix: the replay guard now compares the policy of the model that decided, so a run the fallback rescued replays.
- `docs/evaluation.md` reports both full output files: per-level accuracy, decision shares, cost and latency.

## v1.1.0 (2026-10-08)

G5 release: `oris explain` and `oris demo`, the four traced cases in `docs/traced-cases.md`, and the final README. The lockbox claim is unchanged.

## v1.0 (2026-10-08)

G4, the lockbox claim, scored once at `eval-freeze`:

- B3 matched precision EN .989 (88/89, one-sided 95% lower bound .948), FR .988 (79/80, .942).
- Certified at the 90% bar in both languages.
- F_NM 0.

## eval-freeze (2026-10-08)

The configuration the lockbox scores (DESIGN.md §10.3), after the G3 readiness set.

## v0.2 (2026-10-08)

G2 selection: the threshold, the sibling verifier and the enrichment were chosen on dev items only. Provisional and pre-freeze.

## v0.1 (2026-10-05)

G1: the first submittable service, with the CLI, the API, the validators and the audit trail. Provisional and pre-freeze.

## prereg-v1 (2026-10-04)

Pre-registration: the design, the evaluation protocol and the frozen split, committed before any model call.
