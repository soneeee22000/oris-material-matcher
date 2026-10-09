# Development results

These sections and tables left the top-level README when it was rewritten for v1.3.0; they are kept here verbatim, with relative links rebased to this folder. The replay and explain commands come from the v1.2.1 Live session section; the scaling and model notes, condensed in the README, are kept here in full.

## Development results (dev only, before the freeze)

Dev has 162 items per language, 139 of them labelled. FR input is scored against the global library, because the ground truth uses global-library strings. P is the precision of matched lines. CP-LB is its one-sided 95% Clopper–Pearson lower bound. C₂₅₂ is correct matches over labelled lines.

| Rung | EN P (correct/matched) | EN CP-LB | EN C₂₅₂ | FR P (correct/matched) | FR CP-LB | FR C₂₅₂ | F_NM |
|---|---|---|---|---|---|---|---|
| B0 rules only | no matches | n/a | .000 | no matches | n/a | .000 | 0 |
| B1 TF-IDF | .413 (43/104) | .332 | .309 | .429 (12/28) | .269 | .086 | 0 |
| B2 one Haiku pass | .780 (103/132) | .713 | .741 | .803 (106/132) | .737 | .763 | 0 |
| B2, 25-word evidence cap | .791 (110/139) | .727 | .791 | .813 (113/139) | .750 | .813 | 0 |
| B3, k = 2, selected `T8` | .891 (90/101) | .826 | .647 | .913 (95/104) | .854 | .683 | 0 |
| B3 + E-08 verifier | .988 (82/83) | .944 | .590 | .988 (81/82) | .943 | .583 | 0 |
| **B3 + verifier + E-01 enrichment (shipped)** | **.979 (92/94)** | **.935** | **.662** | **.990 (102/103)** | **.955** | **.734** | **0** |

The shipped configuration is the bold row (`config/policy.yaml`): Haiku with k = 2 passes over two renderings of the library, the threshold `T8`, the E-08 sibling verifier, and the E-01 bilingual library enrichment (`data/enrichment/global.yaml`). Every line it does not match goes to `needs_review` with a reason code.
- **Dev bar:** it meets P ≥ .95 with at least 40 matched lines in both languages, and the CP lower bound is at least .90 in both.
- **Cost and speed:** about $0.003 per line and 0.75–0.80 s of wall clock per routed line.
- **Review load:** 32 (EN) and 26 (FR) of every 100 output lines go to review.
- **B2** matches every valid answer, so it is a baseline, not the shipped system.
- **Fallback:** gpt-4o-mini was not certified, so if the primary model fails, the fallback decides at the strictest threshold, where almost every line goes to review (1 match over the 324 dev items when measured).

Live spend: $0.67 at G1, $3.46 at G2.

The files in `output/` are the B3 lockbox outputs, the system's results. See [`output/README.md`](../output/README.md).

Details: [`docs/gates/G1.md`](gates/G1.md) and [`docs/gates/G2.md`](gates/G2.md) (what was built, reviews, spend, results, observations), and [`eval/experiments.md`](../eval/experiments.md) (the experiment ledger).

## Dev experiment runner

Live runs before the freeze go through the dev experiment runner, on dev items only:

```bash
uv run python eval/run_experiment.py --lang en --input input/boq_dataset_input_en.csv     --library data/oris_materials_global.csv --split eval/split_v1.json --side dev     --profile b2 --id <ledger-id> --hypothesis "..." --change "..."
```

Each run writes `runs/<run_id>/` (`calls.jsonl`, `audit.jsonl`, `manifest.json`, `score.json`, `prompts/`) and its output CSV, to `--output` when given (the G1 runs used `runs/<ledger-id>.csv`), else to `runs/<run_id>/output.csv`. It appends one row to the experiment ledger, a `failed` row when the run cannot be scored. `uv run oris replay` re-runs a recorded run at $0.

## Replay and explain commands

From the v1.2.1 README's Live session section.

The next commands are read-only and cost $0: they never call a model, never read the ground truth and write nothing.

Replay the committed lockbox B3 runs (EN `runs/submission/20261008T023928Z-56f85fb8`, FR `runs/submission/20261008T024244Z-de394c39`) and check them byte for byte against `output/improved_output_{en,fr}.csv`:

```bash
uv run oris demo
uv run oris demo --lang en
uv run oris demo --lang fr
```

It prints, per language, the rows, the decisions (matched / needs_review / not_a_material) and the replay verdict, and exits 1 if a replay is not byte-identical. A live run of the same input can differ from the replay by up to the measured decision flip rate, because temperature 0 is not bit-deterministic on hosted APIs. On dev, the G3 cold live rerun of the shipped configuration measured 0 decision flips in 199 lines per language (and 0 row flips; [`docs/gates/G3.md`](gates/G3.md) §5). The demo only replays: it does not score the outputs (use `uv run oris score`) and has no `--live` option (run the brief's command instead). DESIGN.md A69 records this narrowing of §11.

"Why did line X get this?":

```bash
uv run oris explain --run runs/submission/20261008T023928Z-56f85fb8 --item 01.02.0010.
uv run oris explain --run runs/submission/20261008T023928Z-56f85fb8 --item 01.02.0010. --json
```

For that item it prints the input text and section path, the decision, the reason code and the decision-table rule that fired, each pass's top1 / confidence / evidence, the policy and threshold the run decided at, the E-08 verifier fields, the matched library row and the top-2 suggestions, the attributed cost and latency, and every call id with its request SHA-256, its stored system prompt under `prompts/` and the provider request id. Each call line also gives its attempt number and HTTP status or error class, and a line with no valid answer says so. `--full` adds each call's user message and raw response. A header line says that no model call was made. `--item` also takes a line id; an unknown item exits 2. `--json` prints the same as one JSON object with sorted keys. It reads the run folder and the input and library files the manifest names, and refuses either if its bytes changed.

## Scaling to large libraries

The whole library is shown to the model (D-01), because lexical recall@20 is only .869 in English and .690 in French: a shortlist would cap accuracy before the model is called.

- **Size handling:** a run applies the D-01 tiers to a character-based estimate of each rendered library's tokens, and `oris doctor --live` measures them with `count_tokens`. The tiers are:
  - up to 30k tokens: the whole library;
  - 30k–150k: the whole library, with a warning and a cache check;
  - above 150k: an explicit error that names the retrieval switch.
- **Retrieval:** candidate selection sits behind a `CandidateProvider` port, with one implementation, `WholeLibrary`. A `HybridRetriever` (BM25 plus dense retrieval) for libraries above 30k tokens is designed (§10.5) but not built, so the error above 150k tokens names a switch that is not yet available.

## Models, memory and provenance

- **Model:** pinned to `claude-haiku-4-5-20251001`, whose published retirement floor is not before 2026-10-15 (A26). The latency numbers were measured at the account's custom rate-limit tier, above tier 4 (`evidence/doctor_2026-10-08.json`).
- **Local models:** the model allowlist admits local ≤ 8B model ids, but no local adapter is built or benchmarked.
- **Memory:** no conversational or correction memory is used. The classifier is stateless and closed-world; memory derived from labels would leak (D-02). Reviewer-correction memory keyed by library hash is a production next step.
- **Labelled-data disclosure:** all 252 labelled lines, lockbox included, were profiled during data analysis before the split was frozen. Glossary and enrichment entries carry provenance tags (`standard`, `library`, `dev_error`). The lockbox claim also holds without the lines touched by `dev_error` entries: EN .989, FR .987 (`docs/evaluation.md`). The error-cause labels were produced by a panel of two blind labeller agents and an adjudicator, reviewed by me (κ .844).
- **Replays:** `oris demo` replays the committed lockbox runs at $0. Live numbers can differ from a replay by the run-to-run flip rate. On dev, two independent live samples of the shipped configuration gave 0 flips in 199 lines per language (`docs/gates/G3.md` §5).

## Document index

| Document                                                                 | Purpose                                                                                                     |
| ------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------- |
| [`DESIGN.md`](../DESIGN.md)                                                 | Approach, decision register, what is tried first, what is cut, and the evaluation and engineering contracts |
| [`docs/evaluation-protocol.md`](evaluation-protocol.md)             | Full, pre-registered evaluation protocol                                                                    |
| [`docs/data-analysis.md`](data-analysis.md)                         | Verified data findings                                                                                      |
| [`docs/requirements-traceability.md`](requirements-traceability.md) | Every requirement in the brief, mapped to its evidence                                                      |
| [`docs/ui-spec.md`](ui-spec.md)                                     | Operator UI specification                                                                                   |
| [`docs/exercise-brief.md`](exercise-brief.md)                       | The exercise brief as received                                                                              |
