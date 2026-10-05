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

> **Status: v0.1, provisional, pre-freeze.** The numbers below are dev-only. They are not the lockbox claim, which is made once, after `eval-freeze` (gate G4).

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

## Results so far (dev only, pre-freeze)

Dev has 162 items per language, 139 of them labelled. FR input is scored against the global library, because the ground truth uses global-library strings. P is the precision of matched lines. CP-LB is its one-sided 95% Clopper–Pearson lower bound. C₂₅₂ is correct matches over labelled lines.

| Rung | EN P (correct/matched) | EN CP-LB | EN C₂₅₂ | FR P (correct/matched) | FR CP-LB | FR C₂₅₂ | F_NM |
|---|---|---|---|---|---|---|---|
| B0 rules only | no matches | n/a | .000 | no matches | n/a | .000 | 0 |
| B1 TF-IDF | .413 (43/104) | .332 | .309 | .429 (12/28) | .269 | .086 | 0 |
| B2 one Haiku pass | .780 (103/132) | .713 | .741 | .803 (106/132) | .737 | .763 | 0 |

B2 costs about $0.001 per line and about 0.25 s per routed line. It matches every valid answer, so it is not yet the shipped system. The target is precision with a lower bound of at least .90, which needs the abstention rule selected in G2. Live spend so far: $0.67 of the $3 G1 cap.

The files in `output/` are B0 placeholders (rules only). They show the output format, not the results. See [`output/README.md`](output/README.md).

Details: [`docs/gates/G1.md`](docs/gates/G1.md) (what was built, reviews, spend, results, observations) and [`eval/experiments.md`](eval/experiments.md) (the experiment ledger).

## What is open

| Gate | Cap | Work |
|---|---|---|
| G2 selection | Wed 7 Oct, 12:00 | B3 with k = 2 passes; threshold selection on dev; error taxonomy; the evidence-length fix (G1 observation O10) |
| G3 hardening | Wed 7 Oct, 22:00 | Fault matrix including a 429 storm; FR smoke set on the FR library; CLI/API parity; one cold live dev run for cost and latency |
| G4 freeze and lockbox | Thu 8 Oct, 12:00 | Tag `eval-freeze`; one lockbox session; final `output/` files; the claim |
| G5 release | Thu 8 Oct, 18:00 | Clean clone on Windows and Linux; final README with weaknesses and what I would do with more time |
