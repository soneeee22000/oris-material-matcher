# Evaluation protocol (pre-registered)

> **Status:** pre-registered with `DESIGN.md` (§10 there is the condensed version). This file is committed before any LLM call. From then on, edits go into a dated **Amendments** log (date, what changed, why, and which results existed at the time) and never overwrite this text. Results go in dated **Results** subsections at the end.
>
> **Grader rule:** every phase gate (§9 here, `DESIGN.md` §14) is checked against the README's "How We'll Evaluate" items and engineering requirements. A phase is not done until its evidence exists as a file in the repo.

Numbers marked † were recomputed read-only from the data for this draft (split composition, Wilson bounds, detectable differences).

---

## 1. Metric definitions

### Notation

**Rows**
- N = all rows in a file: 319 per language.
- H = header rows (G1 shape): 37.
- I = item rows: 282.

**Reference classes** for item rows. These come from GT plus one annotation file. GT has labels only; it does not distinguish material blanks from service blanks.
- **L**: labelled (any GT label column non-blank). |L| = 252 (dev 139, lockbox 113)†.
- **E**: material with no equivalent in the library. |E| = 13 (dev 9, lockbox 4)†. These come from `eval/annotations/blank_line_classes.csv`, which is builder judgement taken from the data brief and carries a provenance column. The file is only ever read by `eval/`.
- **S**: service or temporary works. |S| = 16 (dev 14, lockbox 2)†.
- **A**: ambiguous. |A| = 1 (04.04.0080, formwork/falsework). It is excluded from E and S. It is reported on its own line, and `needs_review` and `not_a_material` are both accepted for it.

**Decisions**
- d_i is the decision on line i: `matched`, `not_a_material` or `needs_review`.
- M = {i : d_i = matched}.

**Correctness**
- A line is correct when all three of (t̂, û, ŝ) equal (t, u, s), compared as **exact raw strings**.
- Trailing spaces are significant (for example `'bitumen for coating '`).
- The scorer also emits a `whitespace_only_mismatch` diagnostic count. Those lines still count as wrong.

### Blank-subtype semantics

- `''` is a real subtype value. It is the library's "no subtype" leaf: 49 labelled lines, dev 36 and lockbox 13†.
- GT `''` with prediction `''` is correct at the subtype level.
- GT `''` with prediction `C30/37` is wrong, and so is the reverse.
- If a GT row has all three labels blank (a class E, S or A line) and the system decides `matched`, the line is wrong at **every** level and goes into the precision denominator.
- The CSV reader uses `keep_default_na=False`. A NaN reaching the scorer is a hard error, never treated as `''`.
- A reference row with only some labels filled (none exist in this GT) counts as labelled. Its blank fields are compared exactly, and the scorer warns.

### Primary metrics

All of these are computed per language and per split (dev, lockbox, and the full file marked "partly in-sample").

| Metric | Formula | Denominator (dev / lockbox / full) | Target |
|---|---|---|---|
| **Matched precision** P | \|{i∈M : correct}\| / \|M\|. If \|M\| = 0 → `n/a`, never 1.0 | \|M\| (varies) | ≥ .90 (bar); ≥ .93 on dev (acceptance) |
| **Match coverage** C₂₅₂ | \|{i∈M∩L : correct}\| / \|L\| | 139 / 113 / 252 | maximise subject to P (headline) |
| **Coverage over material lines** C₂₆₅ | \|{i∈M∩L : correct}\| / \|L∪E\| | 148 / 117 / 265 | reported; the brief's literal definition; ceiling .951 |
| **Material handling** H₂₆₅ | (\|{i∈M∩L : correct}\| + \|{i∈L∪E : d_i = needs_review}\|) / \|L∪E\| | 148 / 117 / 265 | maximise; ceiling 1.0 |
| **Safety count** F_NM | \|{i∈L∪E : d_i = not_a_material}\| | count | **0** |

The safety count can always be computed on L alone (`--material-ids` not given). This is how it runs on the graders' unseen reference, where only the three label columns exist.

### Secondary metrics

**Per-level accuracy** is computed on three views, each with a stated subset.

1. **Matched-conditional**, over M. acc_type, acc_type+usage and acc_triple are the share of M where that prefix matches. A matched line with a blank GT is wrong at every level. acc_triple ≡ P.
2. **Proposal accuracy (decision-agnostic)**, over L (252). It uses the top-1 row the model proposed, read from the `suggested_*` columns of the output CSV (and mirrored in the audit), *whatever the decision was*. That way the metric is computable from the output file alone. A line with no valid proposal (LLM failure, or an explicit "no equivalent") counts as wrong. This separates the quality of the matcher from the abstention policy, and it is the headline "accuracy at each hierarchy level" the README asks for.
3. **Cascade conditionals**, over L:
   - P(usage ✓ | type ✓) = \|type+usage ✓\| / \|type ✓\|
   - P(subtype ✓ | type+usage ✓)

   These are directly comparable with the brief's TF-IDF numbers: EN .725 / .773, FR .376 / .755.

**Decisions**
- **Decision shares:** count and share of each decision over N (319) and over I (282).
- **Decision confusion matrix:** reference class {H, L, E, S, A} × decision. This is the most informative single table, because every safety and precision failure shows up as a cell.
- **G1 header accuracy:** 37/37 expected.
- **Service skip rate:** \|{i∈S : not_a_material}\| / 16. Expected about 9/16, because G2 sends the rest to review by design.
- **Review load:** needs_review per 100 lines (N basis).
- **EN↔FR agreement:** share of items where both languages give the same decision *and* the same row ID.

**Cost**
- Cost is measured from the API `usage` field and never estimated. It uses a date-stamped price table in `config/pricing.toml`:
  - Haiku 4.5: input $1/MTok, output $5/MTok, cache write ×1.25, cache read ×0.1.
  - The table is verified against the docs on the day it is used.
- **Cost per line** = (Σ cost of every attempt in `calls.jsonl`, *including retries and failed attempts*) / N.
- **Cost per 100 lines** = cost per line × 100. Budget ≤ $2.00.
- Per-line cost in the output CSV is the line's share of its call cost, by token share.
- Offline enrichment cost is reported **separately**: once per library, cached by library hash, and also amortised over 319 lines for full disclosure.

**Latency**
- **Mean wall-clock per line** = (t_end − t_start of the whole `oris match` run, including gate, retries and I/O, excluding offline enrichment) / N.
  - It is also reported over N_routed, the lines actually sent to the LLM (about 273). That figure is more conservative.
  - **The budget check uses N_routed.** Budget ≤ 2.0 s.
- **Per call:** p50 / p95 / max of `latency_ms` from `calls.jsonl`.
- **Amortised per line:** call latency / batch size.
- **Batch effects:** on a fixed 30-line dev slice (EN), compare B = 1 vs B = 10 at the same concurrency. Report:
  - Δ proposal accuracy, which checks whether batch-mates contaminate each other;
  - Δ cost/line, since the cached-prefix share changes;
  - Δ wall-clock/line.
- **Concurrency effects:** wall-clock at concurrency 1 / 4 / 8 on the full file, recorded once on Wed 7.

---

## 2. Split protocol

### Rules

1. **Split by item, never by row or by language.** EN and FR are twins: the same `Item No.` carries the same GT. If an item sat in dev in EN and in the lockbox in FR, the lockbox would score a translation of an answer already studied. **Both languages of an item are always on the same side.**
2. **Split by section, not at random.** Lines next to each other in an L1 section share phrasing, spec templates and labelling conventions. For example, the 09.01 block puts all 9 lines under General Ready mixed. A row-level split would put near-duplicates on both sides and inflate the held-out score. Holding out whole sections imitates the real situation: an unseen BoQ brings sections we have never seen.
3. **One documented exception.** Section 03.03 holds all 28 asphalt lines. It is split by alternating labelled lines (odd index → lockbox), so asphalt exists on both sides. This lets some leakage in within that section. Asphalt is rule-solvable, and its lockbox score is reported as a separate row.

### Frozen composition

These were recomputed† from the v1 rule.

| | Dev | Lockbox |
|---|---|---|
| L1 sections | the other 18 sections plus even-indexed 03.03 | 01.03, 03.01, 04.02, 04.04, 05.02, 06.01, 08.01, 08.03, 09.01, plus odd-indexed 03.03 |
| Labelled (L) | **139** | **113** |
| E (no equivalent) | 9 | 4 (04.04.0050/60/70, 05.02.0130) |
| S (service) | 14 | 2 (05.02.0140, 06.01.0120) |
| A (ambiguous) | 0 | 1 (04.04.0080) |
| Blank subtype in GT | 36 | 13 |
| Concrete / Aggregates / Asphalt | 36 / 17 / 14 | 39 / 15 / 14 |
| Lines mostly held out | — | Admixture 7 of 8, Excavations 9 of 13, Cement 8 of 12, Steel 7 of 11, all 9 lines of the 09.01 ready-mix block |

- **Disclosed risk:** the lockbox is *harder* than dev on Admixture, Excavations and the ready-mix convention. Dev holds most of C&D (18 of 24). Expect some shrinkage from dev to the lockbox. This is part of why the dev bar is .93 rather than .90.
- **Headers** go to the side of their section and count only in the G1 and decision-matrix rows.

### Freezing

- `eval/split_v1.json` holds `{rule, item_ids_dev[], item_ids_lockbox[], created}`. It is committed **before the first LLM call**.
- Its SHA-256 is written into this document.
- `tests/test_split_frozen.py` asserts the hash.
- Any change needs an Amendment entry and a new file (`split_v2.json`). It never edits v1.

### Lockbox-once rule

1. Before the freeze, the harness only routes dev item IDs to the LLM (`oris experiment --split dev`). Lockbox lines are never sent, never replayed and never inspected, so even raw responses on lockbox lines do not exist yet.
2. The freeze is a git tag `eval-freeze` that pins:
   - the code sha, prompt_version, model ID and policy config;
   - the library sha, the enrichment key and the glossary sha;
   - the split sha.
3. After the freeze, run the full-file outputs for both languages once. Scores are broken down by split. The result is appended to `eval/lockbox_log.md` (append-only: timestamp, tag, sha, every metric).
4. **If the lockbox misses .90:** nothing is tuned on it. The number is reported as measured, with error analysis, in the README's known weaknesses.
5. **Bug-only rerun exception.** A rerun is allowed at most once, and only if all of these hold:
   - (a) the defect is a gap between code and this written spec, such as a parser, join or encoding error, and not a prompt, threshold, glossary or policy change;
   - (b) a unit test that reproduces it is written *before* the fix, and that test does not quote lockbox text;
   - (c) both lockbox scores are reported side by side, with the commit diff linked.

---

## 3. Statistical confidence

**Wilson 95% interval** for any proportion k/n (precision, coverage, handling), with z = 1.96:

```
centre = (p̂ + z²/2n) / (1 + z²/n)
half   = z·√(p̂(1−p̂)/n + z²/4n²) / (1 + z²/n)
```

**Interval widths at our sample sizes**†

| Observed | Wilson 95% |
|---|---|
| 28/30 = .933 | [.787, .982] |
| 56/60 = .933 | [.841, .974] |
| 51/55 = .927 | [.827, .971] |
| Coverage .45 on n = 139 | [.373, .536] |
| Coverage .45 on n = 113 | [.363, .543] |

### Paired comparisons (same items, so paired tests)

- **Coverage or correct-match indicator** (baseline vs final, EN vs FR on the same items): McNemar exact binomial test on the discordant pairs (b, c), with p reported.
- **Precision difference:** the matched sets differ between systems, so McNemar does not apply. Use a **paired item bootstrap**:
  - 10,000 resamples of item IDs;
  - recompute both systems' precision on each resample;
  - report the 95% percentile interval of ΔP.
- **Sensitivity check:** a section-cluster bootstrap that resamples L1 sections. There are only about 10 lockbox sections, so its interval is wide. It is reported as a robustness check, not as the headline.

### Minimum detectable difference

Two-sided α = .05, power .80†.

| Comparison | n | Minimum detectable difference |
|---|---|---|
| Coverage, McNemar, 3–5% reverse flips | dev 139 | **≈ 10–11 points** |
| Coverage, McNemar, 3–5% reverse flips | lockbox 113 | **≈ 11–13 points** |
| Precision between two variants | about 40 matched each | **≈ 25 points** |
| Precision between two variants | about 60 matched each | **≈ 19 points** |

Consequences:
- **Baseline vs final** (the expected coverage gap is about 35–50 points) is easy to detect.
- **Variant vs variant precision is not.** We therefore never claim that one variant is "more precise" than another. Variants are selected on coverage under the precision bar.
- Dev gains smaller than about 10 points are called *directional*.

### Why the lower CI bound is not required to exceed .90

The bound is mathematically out of reach without near-perfect observed precision, at least on the bound check†:
- 30/30 gives a lower bound of .886, so it can never pass at n = 30;
- n = 50 needs 50/50;
- n = 60 needs 59/60;
- n = 139 needs 133/139.

Optimising for that bound pushes coverage towards zero. That is exactly the overfit the TF-IDF baseline shows: its P = .909 at 4% coverage falls to .76–.82 on held-out halves.

Instead, the rule uses a **point estimate of ≥ .93 on dev** in *each* language, with **≥ 30 matched dev lines** in each. The 3-point margin absorbs the winner's curse from picking among up to 12 policies, plus the dev-to-lockbox shift. Wilson intervals are always printed next to the point estimate.

---

### Operating characteristic of the acceptance rule

This table gives the exact binomial probability that a policy shows observed precision ≥ .93 on n matched lines, given its true precision:

| True precision | n = 30 (needs ≥ 28) | n = 40 (≥ 38) | n = 60 (≥ 56) | n = 100 (≥ 93) |
|---|---|---|---|---|
| .80 | .04 | .01 | .00 | .00 |
| .85 | .15 | .05 | .04 | .01 |
| .90 | .41 | .22 | .27 | .21 |
| .93 | .65 | .46 | .59 | .60 |
| .95 | .81 | .68 | .82 | .87 |
| .97 | .94 | .88 | .97 | .99 |

How to read it:

- The rule mostly rejects policies whose true precision is .85 or worse, and accepts most policies at .95 or better.
- A true-.90 policy passes only 21–41% of the time. That is intended: the margin absorbs selection among 12 policies and the dev → lockbox shift.
- Wilson examples: 28/30 [.787, .982]; 30/30 [.886, 1.000]; 56/60 [.841, .974]; 93/100 [.863, .966]; 102/113 [.834, .945].

**Claim wording, fixed now:** *"lockbox matched precision X% [Wilson 95% CI a–b] on n matched lines; we can / cannot rule out < 90% at 95% confidence."*

---

## 4. Abstention and selective-prediction evaluation

**Population.** Dev item rows not settled by G1 or G2, meaning every line where a match is possible: L, E, the unresolved part of S, and A.
- Error event: a line decided `matched` whose triple is wrong, including any match on a blank-GT line.

**Signals.** No logprobs are available, so all three are verbalised or agreement-based.

| Id | Signal | Score | Extra cost |
|---|---|---|---|
| a | Self-reported confidence (integer 0–100, anchored in the prompt) plus the ordinal `self_reported_candidate_gap` (`decisive` / `clear` / `narrow` / `tossup` between the model's top-1 and top-2). A verbal judgement, never a probability | conf, then gap as a tiebreak | 0 |
| b | Attribute-extractor agreement: every attribute parsed from the line (strength, exposure, CEM, RAP/AE %, WMA/HMA, EWC, Ø, g/m²) is consistent with the chosen row | {conflict, no-evidence, agree} | 0 (code) |
| c | Two-pass agreement: a second independent pass with permuted candidate order gives the same row ID | {disagree, agree} | about 2× calls |

**Risk–coverage curves**
- Sort the population by score, descending. Equal-score groups are a single step, which matters for b and c because they are discrete.
- For each prefix k: risk(k) = errors in the top k / k; selective coverage = k / \|pop\|.
- Each curve point is also plotted as **task coverage C₂₅₂** against P, because that is the axis the acceptance rule uses.
- Plots are per language, with 1,000-resample bootstrap bands.

**AURC and E-AURC**
- AURC = (1/n) Σₖ risk(k).
- E-AURC = AURC − AURC_oracle, where the oracle ranks every correct line first.
- These are summaries only. **The decision metric is coverage at P ≥ .93.**

**Pre-registered candidate policies.** There are 12 and they are fixed now, which limits multiple comparisons:
- a with τ ∈ {60, 70, 80, 90} combined with gap ∈ {decisive, clear} (4 policies);
- b = agree;
- c = agree;
- a(80) ∧ b, a(80) ∧ c, b ∧ c, a(80) ∧ b ∧ c;
- the strictest: a(90) ∧ b ∧ c;
- match-all, kept only as a reference.

All policies are shared across languages. Per-language thresholds would overfit, and the live run uses an unseen file with a different library.

**Choosing the operating point.** Among the policies that reach dev P ≥ .93 in both EN and FR with ≥ 30 matched lines in each, pick the one with the highest summed C₂₅₂.
- If two are within 3 lines of each other, pick the cheaper one.
- If none qualifies, ship the strictest policy and report the shortfall.
- On the lockbox, report only the frozen point. A lockbox risk–coverage curve may be shown afterwards, labelled "post-hoc, not used for selection".

**Calibration** of the self-reported confidence:
- Event: the top-1 triple is correct, over the whole population (not only M).
- Reliability diagram with **5 equal-mass bins** (about 30 lines per bin per language).
- ECE = Σ_b (n_b/n)·\|acc_b − conf_b\|, plus the Brier score.
- For the ordinal gap rating: precision at each gap level.
- Expected result: overconfidence. That is the reason signal a has to earn its place against b and c and is not trusted by default.

**Comparing the signals**
- Paired bootstrap on ΔAURC and on Δ coverage at P ≥ .93, for each pair of signals.
- Report the extra cost of c per 100 lines.
- **Expected finding:** b dominates on attribute-rich families (Concrete, Asphalt, Cement), and c helps on usage confusers. Whatever the result, it is reported.

---

## 5. Baseline ladder

All rungs are scored by the same `eval/score.py` on the same splits.

| Rung | Configuration (pinned) | Answers |
|---|---|---|
| **B0: rules only** | G1 + G2 unit prior; every other line → needs_review | The floor: C₂₅₂ = 0, P = n/a. Safety and review load with zero intelligence. |
| **B1: TF-IDF** | `char_wb` 3–5-grams plus word 1–2-grams, cosine scores averaged, `sublinear_tf=True`, fit on library + queries, argmax over the 342 rows. Threshold chosen **on dev** as the maximum coverage with P ≥ .90; if no threshold qualifies, coverage = 0. Scored on the lockbox. | "The simplest reasonable approach", measured held out. It should reproduce the brief's full-set match-all top-1 (EN .405, FR .159) within ±.01, which checks that the scorer is right. |
| **B2: Haiku single pass** | One structured-output call per batch of 10, raw library hierarchy, no section path, no extractors, no glossary. Every valid in-library answer → matched. | Raw model capability. The gap between B1 and B2 is the value of the LLM; P(B2) shows the need for abstention. |
| **B3: final** | Frozen system: section path, extractors, the selected signal policy, and any evidence-gated additions from §6 | The gap between B2 and B3 is the value of the engineering. |

- Every rung reports P, C₂₅₂, H₂₆₅, F_NM, per-level proposal accuracy and cost per 100 lines.
- **B1 → B3 coverage** is tested with McNemar.
- **B2 → B3 precision** is tested with the paired bootstrap.
- Ablations of B3 (one component removed at a time) run on dev only.

**Recall@k diagnostic** for any step that narrows the candidate set (top-k shortlist, attribute hard filter, type-first routing):
- recall@k = \|{i∈L : GT row ∈ candidates(i)}\| / \|L\|.
- Report the distribution of candidate-set sizes.
- Gates:
  - a **soft shortlist** needs recall ≥ .95 per language on dev;
  - a **hard attribute filter** needs recall = 1.00 on dev. Any miss is treated as a parser bug, gets a unit test, and is fixed.
- Reference point from the brief: TF-IDF recall@20 is .869 in EN and .690 in FR. That is why no lexical top-k shortlist is planned for FR, and why the full list of 108 type+usage pairs goes into the prompt.

---

## 6. Error taxonomy, and how it drives the next experiment

**One record per dev error** in `eval/errors_dev.csv`, with these fields:
- `item_id`, `lang`, `split`
- `family`: GT type, or predicted type if GT is blank
- `kind`: one of
  - `false_match` (wrong triple, matched)
  - `match_on_blank` (E, S or A line matched)
  - `false_nm` (safety)
  - `missed` (correct proposal sent to review)
  - `wrong_proposal_reviewed` (proposal wrong, safely abstained)
- `level_first_wrong`: type / usage / subtype / decision
- `cause` (primary, required) and `cause_2` (optional), from:
  - `lexical_gap`: the term is not bridged between line and library (FR acronyms such as GNT, BPE, GB/BBSG, "tiède")
  - `usage_confuser`: a known confusable pair (piers/piles, base/binder, pile caps → footings, segmental rings, ready-mix vs element)
  - `subtype_parse`: an attribute was mis-extracted or not extracted (strength, CEM, RAP/AE %, EWC, grade)
  - `header_context`: the answer needs L0/L1 context that was missing or ignored
  - `not_in_library`: there is no valid row, but the system forced one
  - `llm_failure`: timeout, malformed output, missing ID, truncation
  - `gt_convention`: the GT follows a project convention the text does not support, such as 09.01 ready-mix or limestone filler → `Limestone/for all usage`. This category is added so these cases are not mislabelled as model errors.
- `evidence`: one line, written by the builder.

**Tagging**
- Dev errors are tagged by the builder.
- Lockbox errors are tagged *after* the lockbox run, for reporting only.

**From taxonomy to experiment** (evidence-gated additions)
1. Build a Pareto chart of causes per language, by count. `false_match`, `match_on_blank` and `false_nm` are weighted ×2, because precision and safety errors cost more than missed coverage.
2. An intervention is considered only if its cause accounts for ≥ 5 dev lines or ≥ 20% of dev errors in one language. The pre-mapped responses:

| Cause | Response |
|---|---|
| lexical_gap | Bilingual enrichment / glossary A/B |
| usage_confuser | Contrast set or verifier over confusable rows |
| subtype_parse | Fix or add an extractor, test first |
| header_context | Change how the section path is rendered |
| not_in_library | An explicit no-equivalent option, plus a forced-match guard |
| llm_failure | Engineering: retry, batch size |
| gt_convention | **Not fixed with an item-specific rule.** Documented as a known weakness, or addressed only with a rule that generalises and is disclosed. |

3. A change is **kept** only if, on dev:
   - the acceptance rule still passes;
   - summed C₂₅₂ rises by ≥ 3 lines;
   - F_NM stays at 0;
   - cost stays inside budget.

   Otherwise it is reverted.
4. Every attempt is a row in the ledger `eval/experiments.md`: id, hypothesis, cause targeted, change, dev before/after (P, C, F_NM, $/100), kept or reverted, prompt_version.
5. Gains are labelled *directional* when they are below the dev detectable difference of about 10 points.

---

## 7. Robustness and generalisation

| Check | Method | Pass criterion |
|---|---|---|
| **FR-library smoke set** | About 25 builder-labelled lines against `oris_materials_fr.csv`: about 10 FR lines from this BoQ that have an exact FR equivalent, about 8 synthetic lines hitting FR-only leaves (pot bearings, fenders, EME / GB with 20/40% AE, ductile pipe, detonating cord), and about 7 **no-match decoys**, 3 of which are lexically close to a real row. Labels are frozen and hashed before the first run. | Closed-world violations 0; F_NM 0; forced matches on decoys 0; matched precision reported as k/n with Wilson interval. Labelled "builder-labelled, unofficial". Run before submission and again before the live session. |
| **Library-order shuffle** | Permute the library rows (3 seeds). With FakeLLM: compare the outputs. Live: one shuffle on 30 dev lines. | Prompt rendering is canonical-sorted, so `request_sha256` must be **identical**, and FakeLLM outputs must be byte-identical. The live flip rate must be at or below the rerun noise floor (determinism row below). |
| **Row-ID stability** | ID = sha256(type ‖ usage ‖ subtype, raw)[:12]. Tests: IDs are unchanged when the library is reordered or when a row is added; `'x'` and `'x '` get different IDs; 0 collisions on both libraries. Line IDs = sha256(item_no ‖ position ‖ text), so duplicate texts stay distinct. | All tests pass, offline. |
| **Failure injection** (FakeLLM) | A fault matrix (timeout, 429 then success, permanent 429, 5xx, 529, malformed JSON, truncation / `max_tokens`, refusal, missing ID, duplicate ID, unknown ID, out-of-library triple) × fault position (first, middle, last batch). A `hypothesis` property test adds random fault schedules × batch sizes 1–20. | For every case: output length = input length, Item No. order preserved, every affected line is `needs_review` with `LLM_FAILURE:<kind>` (or `INVALID_ROW_ID`), no `matched` derived from a failed or partial response, raw error in the audit, retries ≤ the configured cap, budget stop respected. **0 lost lines.** |
| **Determinism / noise floor** | temperature 0. A 40-line dev slice (20 EN + 20 FR) is run twice live. | Report the decision flip rate and the row flip rate (expected ≤ 5%). Any difference between variants smaller than 2× the flip rate is treated as noise. Replay of a run must be **byte-identical** (offline test). |
| **Batch contamination** | B = 1 vs B = 10 on 30 dev lines (§1). | \|Δ proposal accuracy\| within the noise floor. Otherwise B is reduced and the change recorded as an Amendment. |

---

## 8. Evaluation as CI

**Offline in pytest** (no network, runs in CI on every commit)
- **Scorer fixture.** A hand-computed reference/output pair of about 15 rows covering:
  - blank subtype in both directions;
  - a match on a blank-GT line;
  - a `whitespace_only_mismatch`;
  - `n/a` precision when nothing is matched;
  - both denominators;
  - Wilson values to 3 decimals;
  - refusal of duplicate keys;
  - missing or extra Item No. → error.
- **Split hash** test.
- **Leakage guard:** an AST/grep test that `src/` never references the GT path or `eval/` and contains no literal GT type or usage strings, plus a 6-gram overlap check between glossary entries and the BoQ inputs.
- **Invariant and property tests** (§7).
- **Replay golden:** a committed `calls.jsonl` fixture of about 10 real lines must replay to a committed CSV, byte-identical.
- **Requirements check** on the committed outputs.

**Need the API** (`@pytest.mark.live`, skipped without `ANTHROPIC_API_KEY`, never in CI)
- the live smoke run;
- dev experiments;
- the determinism and batch checks;
- the lockbox run;
- cost and latency measurement.

Each produces a `runs/<id>/` folder with `manifest.json`, so the results can be replayed at $0.

**`eval/check_requirements.py --output X --input Y --library Z --run runs/<id>`** exits non-zero on any failure and prints JSON:

| ID | Check |
|---|---|
| RQ1 | Columns = input columns in order + `decision, material_type, material_usage, material_subtype` + audit columns from the sample output (`reason, model, prompt_version, latency_ms, cost_usd`), then `suggested_type, suggested_usage, suggested_subtype, library_row_id` |
| RQ2 | Row count, Item No. sequence and input cell values are identical to the input (UTF-8 round trip, no stripping) |
| RQ3 | `decision` ∈ {matched, not_a_material, needs_review}, with no blanks |
| RQ4 | Every `matched` row's triple ∈ the library's raw triple set (exact strings); every non-matched row has blank labels |
| RQ5 | Every `not_a_material` row carries rule code G1 or G2, and G2 rows have a unit in `SERVICE_UNITS` |
| RQ6 | `audit.jsonl` has exactly one record per line (line IDs 1:1); every LLM-derived decision references a `call_id` whose record has model, prompt_version, raw_response, usage, cost_usd, latency_ms |
| RQ7 | Σ cost / N × 100 ≤ $2.00 |
| RQ8 | Run wall-clock / N_routed ≤ 2.0 s |
| RQ9 | `manifest.json` pins all present: code sha, model, prompt_version, library sha, split sha, price-table date |
| RQ10 | The model ID is on the small-model allowlist (Haiku family) |

---

## 9. Grader gate table

The schedule is re-baselined because Saturday was lost. A gate passes only when its evidence exists as files in the repo.

Rubric key: **U** data understanding · **B** baseline and confidence · **T** trade-offs · **E** engineered · **D** defend.

| Day | Rubric checked | Evidence that must exist |
|---|---|---|
| **Sun 4** · pre-registration | U, design-doc questions | `DESIGN.md` committed with the split SHA; `eval/split_v1.json`; `docs/data-analysis.md`; `eval/annotations/blank_line_classes.csv` |
| **Mon 5** · vertical slice and baselines | B (started), E (closed world) | `score.py` passing the scorer fixture; G1 37/37 offline; validator property test green; B0 and B1 dev numbers (B1 reproduces top-1 EN .405 / FR .159 ±.01); B2 on a stratified 40–60-line slice, then all 139 dev lines EN+FR, in `runs/<id>/` |
| **Tue 6** · signals, experiments, policy freeze | B, T, U (EN vs FR), D | risk–coverage per signal + AURC + calibration; replay sweep reproducing thresholds at $0; `errors_dev.csv` with causes; `experiments.md` ledger; acceptance result per language (P ≥ .93, ≥ 30 matched, F_NM = 0), or the documented shortfall; FR smoke set labels hashed and first run; policy config committed |
| **Wed 7** · hardening, measurement, **freeze + lockbox** | E, constraints | fault-matrix and property tests green; API test (3 in → 3 out, in order); measured $/100 and s/line in a manifest; determinism noise floor; `eval-freeze` tag; **one** entry in `lockbox_log.md`; both full-file outputs; `check_requirements.py` green on both; `docs/evaluation.md` generated by `report.py` |
| **Thu 8** · README, UI, rehearsal | all five | README with known weaknesses and "with more time"; DESIGN.md §17 Results dated; clean clone `uv sync && uv run pytest && uv run oris match …` green; UI U1–U4; rehearsal (switch library, replay a policy, add an extractor test-first, explain one lockbox error) |
| **Fri 9** · buffer | D | submitted by 17:00 |

---

## 10. Honesty and leakage

**No GT at inference**
- Prompts contain only:
  - the selected library;
  - enrichment generated from that library alone, cached by library hash;
  - a small glossary with sourced entries;
  - the line and its L0/L1 headers.
- `data/boq_dataset_matched_GT.csv` and `eval/annotations/*` are read only under `eval/`. CI enforces this (§8).
- There are no few-shot examples taken from GT. 238 of the 244 GT triples are singletons, so any GT example would leak its own answer.

**Glossary provenance**
- Each entry is tagged with one of:
  - `standard`: EN 206, EN 13108, EN 197, the EWC list, NF norms;
  - `library`: derived from row text;
  - `dev_obs`: a general domain fact noticed while reading dev lines;
  - `dev_error`: added after a dev error;
  - `lockbox_obs`: anything traceable to a lockbox-section line. These entries are excluded. If one is ever kept, lockbox precision is reported with and without it.
- `dev_error` entries must state general domain facts (for example "GNT = graves non traitées = unbound granular mixture"), never item text or answers.
- They must pass the 6-gram overlap check against both BoQ inputs.
- Entry counts per tag are recorded in the manifest.
- No entry comes from a lockbox error.

**Disclosed contamination by the analyst**
- The builder profiled all 252 lines, lockbox included, during data analysis, *before* the split was frozen.
- Mitigations:
  - no item-specific rules;
  - the leakage guards;
  - the dev-only experiment ledger.
- Any rule that encodes a convention seen in that analysis is listed. Lockbox precision is reported both on all lines and **excluding the lockbox lines touched by such a rule**.

**Builder labels**
- The 13 no-equivalent annotations and the FR smoke set are builder judgement.
- They are used only in evaluation, published with provenance, and reported separately from GT-based metrics.

**README disclosure** (draft):

> *Labelled data at inference:* none. The matcher sees only the selected library, an enrichment derived from that library, and a glossary of domain terms, each tagged with its source: a standard, the library, or a dev-set error. Ground truth is read only by the evaluation code, and a CI test enforces that. We froze a section-based split of 139 dev and 113 lockbox items before any model call. EN and FR twins of an item always sit on the same side. Every prompt, glossary and threshold decision was made on dev only. The lockbox was scored once, at a tagged commit; the score and commit are in `eval/lockbox_log.md`. The author profiled the whole dataset before freezing the split. Any rule that reflects a convention seen there is listed, and lockbox precision is also reported without the lines it affects. The 13 "material with no library equivalent" annotations and the FR-library smoke set are the author's own labels. They are used only for evaluation and are reported separately.