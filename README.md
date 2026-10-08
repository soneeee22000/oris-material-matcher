# ORIS material matcher

This service maps each line of a Bill of Quantities to an exact `type / usage / subtype` row of an ORIS material library, or decides `not_a_material` or `needs_review`. It reports the evidence for its precision, coverage, cost and latency.

Start with [`DESIGN.md`](DESIGN.md), whose §0 is a one-page summary. The design was pre-registered before any model call (v2; v1 is tag `prereg-v1`).

| Document                                                                 | Purpose                                                                                                     |
| ------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------- |
| [`DESIGN.md`](DESIGN.md)                                                 | Approach, decision register, what is tried first, what is cut, and the evaluation and engineering contracts |
| [`docs/evaluation-protocol.md`](docs/evaluation-protocol.md)             | Full, pre-registered evaluation protocol                                                                    |
| [`docs/data-analysis.md`](docs/data-analysis.md)                         | Verified data findings                                                                                      |
| [`docs/requirements-traceability.md`](docs/requirements-traceability.md) | Every requirement in the brief, mapped to its evidence                                                      |
| [`docs/ui-spec.md`](docs/ui-spec.md)                                     | Operator UI specification                                                                                   |
| [`docs/exercise-brief.md`](docs/exercise-brief.md)                       | The exercise brief as received                                                                              |

> **Status: lockbox scored.** The single post-freeze session ran on Thu 8 Oct 2026 at the `eval-freeze` tag. **Matched precision is 98.9% in English (88/89) and 98.8% in French (79/80).** The one-sided 95% exact lower bounds are .948 and .942, so the result is **certified at the 90% bar in both languages**, with no material skipped as "not a material". See [`docs/evaluation.md`](docs/evaluation.md).

## The claim (lockbox, 113 labelled lines per language)

| | EN | FR |
|---|---|---|
| Matched precision | 98.9% (88/89) | 98.8% (79/80) |
| One-sided 95% exact lower bound | .948 | .942 |
| Coverage (correct / labelled) | .779 | .699 |
| False "not a material" | 0 | 0 |
| Cost per 100 lines; seconds per routed line | $0.22; 0.63 | $0.21; 0.68 |

In French, the abstention that buys this precision costs about 18 points of coverage against the B2 baseline. Those lines go to review with a reason, never to a wrong match.

## Quick start

You need Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --locked --all-extras --no-extra retrieval
uv run pytest
uv run oris doctor
```

`oris doctor` checks the config, the model allowlist, the pinned model, prices, keys and both libraries. `oris doctor --live` adds one 2-line call to the primary model, one to the fallback when its key is set, `count_tokens` for every rendering of both libraries, and the rate-limit tier.

The brief's command, run offline with the rules-only profile (no key, no model call):

```bash
uv run oris --input input/boq_dataset_input_fr.csv --library data/oris_materials_global.csv --output improved_output_fr.csv --profile b0
```

Without `--profile b0`, a live run over a whole exercise file is refused until the `eval-freeze` tag. The files hold lockbox items, and they are scored only once, after the freeze.

Live runs before the freeze go through the dev experiment runner, on dev items only:

```bash
uv run python eval/run_experiment.py --lang en --input input/boq_dataset_input_en.csv     --library data/oris_materials_global.csv --split eval/split_v1.json --side dev     --profile b2 --id <ledger-id> --hypothesis "..." --change "..."
```

Each run writes `runs/<run_id>/` (`calls.jsonl`, `audit.jsonl`, `manifest.json`, `score.json`, `prompts/`) and its output CSV, to `--output` when given (the G1 runs used `runs/<ledger-id>.csv`), else to `runs/<run_id>/output.csv`. It appends one row to the experiment ledger, a `failed` row when the run cannot be scored. `uv run oris replay` re-runs a recorded run at $0.

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
- **Fallback:** gpt-4o-mini was not certified, so if the primary model fails, the fallback decides at the strictest threshold, where almost every line goes to review (1 match in 324 dev lines when measured).

Live spend: $0.67 at G1, $3.46 at G2.

The files in `output/` are B0 placeholders (rules only). They show the output format, not the results. See [`output/README.md`](output/README.md).

Details: [`docs/gates/G1.md`](docs/gates/G1.md) and [`docs/gates/G2.md`](docs/gates/G2.md) (what was built, reviews, spend, results, observations), and [`eval/experiments.md`](eval/experiments.md) (the experiment ledger).

## What is open

| Gate | Cap | Work |
|---|---|---|
| G3 hardening | Wed 7 Oct, 22:00 | Fault matrix including a 429 storm; FR smoke set on the FR library; CLI/API parity; one cold live dev run for cost and latency |
| G4 freeze and lockbox | Thu 8 Oct, 12:00 | Tag `eval-freeze`; one lockbox session; final `output/` files; the claim |
| G5 release | Thu 8 Oct, 18:00 | Clean clone on Windows and Linux; final README with weaknesses and what I would do with more time |
