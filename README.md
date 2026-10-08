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

Without `--profile b0`, a live run over a whole exercise file is refused unless the `eval-freeze` tag is at HEAD. The files hold lockbox items, and they are scored only once, after the freeze.

Live runs before the freeze go through the dev experiment runner, on dev items only:

```bash
uv run python eval/run_experiment.py --lang en --input input/boq_dataset_input_en.csv     --library data/oris_materials_global.csv --split eval/split_v1.json --side dev     --profile b2 --id <ledger-id> --hypothesis "..." --change "..."
```

Each run writes `runs/<run_id>/` (`calls.jsonl`, `audit.jsonl`, `manifest.json`, `score.json`, `prompts/`) and its output CSV, to `--output` when given (the G1 runs used `runs/<ledger-id>.csv`), else to `runs/<run_id>/output.csv`. It appends one row to the experiment ledger, a `failed` row when the run cannot be scored. `uv run oris replay` re-runs a recorded run at $0.

### Live session

Run an unseen BoQ on the FR library with the brief's literal command (`match` is the default command; it needs `ANTHROPIC_API_KEY` and makes paid calls):

```bash
uv run oris --input new.csv --library data/oris_materials_fr.csv --output out.csv
```

The policy is resolved per (model, library). The FR smoke certification (A10, G3-T24) has not run, so `config/policy.yaml` has no entry for the pinned model on the FR library, and the run decides at the strictest threshold, `T1`. It logs this warning and prints this summary line:

```text
policy_resolution=fallback_strictest: no certified policy for (claude-haiku-4-5-20251001, 6bfe1857fd36); deciding at the strictest threshold T1
policy_resolution: fallback_strictest (policy T1)
```

Expect most lines to go to `needs_review`. Once a smoke-certified FR entry exists, the line reads `policy_resolution: exact (policy <id>)`. The `eval-freeze` refusal does not apply to this run: it only covers live runs over the two exercise input files, which are refused unless the `eval-freeze` tag is at HEAD. An unseen BoQ is never refused.

The next commands are read-only and cost $0: they never call a model, never read the ground truth and write nothing.

Replay the committed lockbox B3 runs (EN `runs/submission/20261008T023928Z-56f85fb8`, FR `runs/submission/20261008T024244Z-de394c39`) and check them byte for byte against `output/improved_output_{en,fr}.csv`:

```bash
uv run oris demo
uv run oris demo --lang en
uv run oris demo --lang fr
```

It prints, per language, the rows, the decisions (matched / needs_review / not_a_material) and the replay verdict, and exits 1 if a replay is not byte-identical. A live run of the same input can differ from the replay by up to the measured decision flip rate, because temperature 0 is not bit-deterministic on hosted APIs. On dev, the G3 cold live rerun of the shipped configuration measured 0 decision flips in 199 lines per language (and 0 row flips; [`docs/gates/G3.md`](docs/gates/G3.md) §5). The demo only replays: it does not score the outputs (use `uv run oris score`) and has no `--live` option (run the brief's command instead). DESIGN.md A69 records this narrowing of §11.

"Why did line X get this?":

```bash
uv run oris explain --run runs/submission/20261008T023928Z-56f85fb8 --item 01.02.0010.
uv run oris explain --run runs/submission/20261008T023928Z-56f85fb8 --item 01.02.0010. --json
```

For that item it prints the input text and section path, the decision, the reason code and the decision-table rule that fired, each pass's top1 / confidence / evidence, the policy and threshold the run decided at, the E-08 verifier fields, the matched library row and the top-2 suggestions, the attributed cost and latency, and every call id with its request SHA-256, its stored system prompt under `prompts/` and the provider request id. Each call line also gives its attempt number and HTTP status or error class, and a line with no valid answer says so. `--full` adds each call's user message and raw response. A header line says that no model call was made. `--item` also takes a line id; an unknown item exits 2. `--json` prints the same as one JSON object with sorted keys. It reads the run folder and the input and library files the manifest names, and refuses either if its bytes changed.

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

## Traced cases

[`docs/traced-cases.md`](docs/traced-cases.md) walks four lines end to end with `oris explain`: a correct match, a plausible wrong match, an abstention, and an API failure that ends in review.


## Known weaknesses

- **French coverage pays for French precision.** On the lockbox, B3 matches 79 of 113 labelled French lines correctly, against 99 for the single-pass baseline (McNemar p = .0002). Every line it declines goes to `needs_review` with a reason code and a top-2 suggestion; none is matched wrongly. In English, B3 keeps the baseline's coverage (p = 1.0).
- **Usage confusions inside the right material type survive.** Both lockbox errors are of this kind:
  - railway sub-ballast was matched to ballast aggregates;
  - French _couche de fondation_, the sub-base, was matched to the base layer, although the glossary states the meaning.

  The E-08 verifier reduces this error class but does not remove it.

- **The verifier costs coverage once the enrichment is in place.** On dev, the same enriched votes without the verifier give 218 correct matches against 194, at P .972 and .958. The verifier was kept because that comparison was not registered before its result was seen (`docs/gates/G2.md` O24).
- **The FR library has no certified threshold.** Unseen BoQs on `data/oris_materials_fr.csv` run at the strictest threshold, which is safe but matches little. The FR smoke set that would certify it (A10) was moved after the freeze.
- **The fallback model is not certified.** If the primary model is unreachable, gpt-4o-mini decides at the strictest threshold, so almost every line goes to review (G2 O26).
- **The lockbox is a same-project holdout.** It is section-held-out and blind to tuning, but it was explored before the split (§10.8). The unseen live BoQ is the only fully blind test.
- **One triggered arm was not run.** `header_context` (6 English errors) did not run before the freeze (G2 O25).

## Scaling to large libraries

The whole library is shown to the model (D-01), because lexical recall@20 is only .869 in English and .690 in French: a shortlist would cap accuracy before the model is called.

- **Size handling:** each rendered library is measured with `count_tokens`, and the D-01 tiers apply:
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

## With more time

- Run the triggered `header_context` arm and the FR-library smoke set (A10), so French BoQs on the French library get a certified threshold.
- Register and measure "verifier off, given the enrichment" blind, on fresh votes. It may recover about 24 correct dev matches.
- A carbon-weighted error metric, once ORIS CO₂ factors are available, so that a wrong match is weighted by the size of the carbon mistake.
- The deferred G3 hardening: the full fault matrix with a 429 storm, CLI/API parity over whole files, and the doctor measuring the enriched prompt for budget reservations.
- Build the `HybridRetriever` and measure its recall@k for libraries above 30k tokens, and add and benchmark a local ≤ 8B adapter.
- The operator UI with asynchronous jobs (`docs/ui-spec.md`).
