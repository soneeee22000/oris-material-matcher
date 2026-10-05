# Evaluation protocol (pre-registered)

> **Status:** pre-registration v2, merged before any model call. Version 1 is the git tag `prereg-v1` (bc5c8a2), which is the proof of the original pre-registration. v2 merges the approved amendments A4–A52 (as resolved by the owner decisions of 2026-10-04, recorded in `DESIGN.md` §16) into the sections they change; each changed paragraph ends with its tag, for example [A-31]. §11 is the index. No model call had been made on the data when v2 was written. From the first model call on, this text is not edited: changes go into a dated **Amendments** log (date, what changed, why, and which results existed at the time) and results go into dated **Results** subsections at the end. [A-30]
>
> **Grader rule:** every phase gate (§9 here, `DESIGN.md` §14) is checked against the README's "How We'll Evaluate" items and engineering requirements. A phase is not done until its evidence exists as a file in the repo.

Numbers marked † were recomputed read-only from the data or from exact binomial arithmetic (split composition, interval bounds, operating characteristics, detectable differences). No model output was involved.

**Terms.** _Dev_ is the 139 labelled items the system is tuned on. _Lockbox_ is the 113 labelled items held out from all tuning and scored once, after the freeze (§2). [A-30]

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
- Cells are read with the `csv` module as raw strings, with no NA inference (no pandas anywhere in `score.py`). In a reference file, label cells matching `^(nan|NaN|None|NULL|<NA>|N/A)$` count as blank, with a printed count (§8). [A-34, A-49]
- A reference row with only some labels filled (none exist in this GT) counts as labelled. Its blank fields are compared exactly, and the scorer warns.

### Primary metrics

All of these are computed per language and per split (dev, lockbox, and the full file marked "partly in-sample").

| Metric                                | Formula                                                      | Denominator (dev / lockbox / full) | Target                                                                                                                                             |
| ------------------------------------- | ------------------------------------------------------------ | ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Matched precision** P               | \|{i∈M : correct}\| / \|M\|. If \|M\| = 0 → `n/a`, never 1.0 | \|M\| (varies)                     | ≥ .90 on the lockbox (the brief's bar; claim form in §3); ≥ .95 on dev in each language with ≥ 40 matched dev lines in each (selection, §4) [A-31] |
| **Match coverage** C₂₅₂               | \|{i∈M∩L : correct}\| / \|L\|                                | 139 / 113 / 252                    | maximise subject to P (headline)                                                                                                                   |
| **Coverage over material lines** C₂₆₅ | \|{i∈M∩L : correct}\| / \|L∪E\|                              | 148 / 117 / 265                    | reported; the brief's literal definition; ceiling .951                                                                                             |
| **Safety count** F_NM                 | \|{i∈L∪E : d_i = not_a_material}\|                           | count                              | **0**                                                                                                                                              |

**Diagnostic, never a target.** Material handling H₂₆₅ = (\|{i∈M∩L : correct}\| + \|{i∈L∪E : d_i = needs_review}\|) / \|L∪E\| (148 / 117 / 265). It reaches 1.0 by reviewing everything, so it is printed next to review load and never used for selection. [A-7]

The safety count can always be computed on L alone, when the optional material-without-equivalent list is not given (`DESIGN.md` §10.1). This is how it runs on the graders' unseen reference, where only the three label columns exist.

**Selection objective vs tests.** Summed EN + FR counts are a selection objective only. Every test or interval that combines the two languages clusters by Item No., because twins share one GT row. [A-5]

### Secondary metrics

**Per-level accuracy** is computed on three views, each with a stated subset.

1. **Matched-conditional**, over M. acc_type, acc_type+usage and acc_triple are the share of M where that prefix matches. A matched line with a blank GT is wrong at every level. acc_triple ≡ P.
2. **Proposal accuracy (decision-agnostic)**, over L (252). It uses the top-1 row the model proposed, read from the `suggested_*` columns of the output CSV (and mirrored in the audit), _whatever the decision was_. That way the metric is computable from the output file alone. A line with no valid proposal (LLM failure, or an explicit "no equivalent") counts as wrong. This separates the quality of the matcher from the abstention policy, and it is the headline "accuracy at each hierarchy level" the README asks for.
3. **Cascade conditionals**, over L:
   - P(usage ✓ | type ✓) = \|type+usage ✓\| / \|type ✓\|
   - P(subtype ✓ | type+usage ✓)

   These are directly comparable with the brief's TF-IDF numbers: EN .725 / .773, FR .376 / .755.

**Suggestion quality on review lines.** hit@1 and hit@2 over `needs_review` lines that have a labelled reference, per language: the share whose top-1 (or top-1 or top-2) suggested row is the GT row. It measures how much a reviewer is helped and never changes a decision. [A-21]

**Decisions**

- **Decision shares:** count and share of each decision over N (319) and over I (282).
- **Decision confusion matrix:** reference class {H, L, E, S, A} × decision. This is the most informative single table, because every safety and precision failure shows up as a cell.
- **G1 header accuracy:** 37/37 expected.
- **Service skip rate:** \|{i∈S : not_a_material}\| / 16. Expected about 9/16, because G2 sends the rest to review by design.
- **Review load:** needs_review per 100 lines (N basis).
- **EN↔FR agreement:** share of items where both languages give the same decision _and_ the same row ID.

**Cost**

- Cost is measured from the API `usage` field and never estimated. It uses a date-stamped price table in `config/pricing.toml`, keyed by provider and model [A-44]:
  - Haiku 4.5: input $1/MTok, output $5/MTok, cache write ×1.25, cache read ×0.1.
  - The fallback model (A33) has its own row.
  - The table is verified against the docs on the day it is used.
- **Cost per line** = (Σ cost of every attempt in `calls.jsonl`, _including retries and failed attempts_) / N.
- **Cost per 100 lines** = cost per line × 100. Budget ≤ $2.00.
- **Per-line attribution** in the output CSV, over every attempt a that touched the line, with n_a lines in its request: `cost_usd` = Σ cost(a) / n_a (6 decimals) and `latency_ms` = round(Σ elapsed(a) / n_a) (integer). Rule-decided rows carry 0.000000 / 0 / `model = rules`. The sum over lines equals the run total. Replays and cache hits copy the source values and never re-measure them. [A-45]
- **Gated runs.** RQ7 and RQ8 use only manifests with `mode = live` and `cache_hits = 0`; submitted runs use `--no-cache`. A cached or replayed run can never report $0 into a gated file. [A-45]
- Offline enrichment cost is reported **separately**: once per library, cached by library hash, and also amortised over 319 lines for full disclosure.

**Latency**

- **Mean wall-clock per line** = (t_end − t_start of the whole `oris match` run, including gate, retries and I/O, excluding offline enrichment) / N.
  - It is also reported over N_routed, the lines actually sent to the LLM: **282** on these files, because only the 37 headers are decided with no call. That figure is more conservative. [A-1]
  - **The budget check uses N_routed** on a cold live run. Warm and replay timings are reported separately. Budget ≤ 2.0 s. [A-12]
  - The README states the rate-limit tier the numbers were measured on, as recorded by `oris doctor` in the manifest. [A-39]
- **Per call:** p50 / p95 / max of `latency_ms` from `calls.jsonl`.
- **Attributed per line:** the A45 attribution above. The scorer labels its mean "attributed (conservative under concurrency)"; the gate is wall-clock ÷ N_routed, reported next to it. [A-45]
- **Batch effects:** on a fixed 30-line dev slice (EN), compare B = 1 vs B = 10 at the same concurrency. Report:
  - Δ proposal accuracy, which checks whether batch-mates contaminate each other;
  - Δ cost/line, since the cached-prefix share changes;
  - Δ wall-clock/line.
- **Concurrency effects:** concurrency is a fixed semaphore of 4; AIMD on 429 is added only if a dev run records a 429. Wall-clock at concurrency 1 vs 4 over all dev lines is recorded once in G3 (E-07). No lockbox line is sent before the freeze. [A-12, A-32]

---

## 2. Split protocol

### Rules

1. **Split by item, never by row or by language.** EN and FR are twins: the same `Item No.` carries the same GT. If an item sat in dev in EN and in the lockbox in FR, the lockbox would score a translation of an answer already studied. **Both languages of an item are always on the same side.** Twins are not independent: any test or interval that pools EN and FR resamples or pairs by Item No. (§3). [A-5]
2. **Split by section, not at random.** Lines next to each other in an L1 section share phrasing, spec templates and labelling conventions. For example, the 09.01 block puts all 9 lines under General Ready mixed. A row-level split would put near-duplicates on both sides and inflate the held-out score. Holding out whole sections imitates the real situation: an unseen BoQ brings sections we have never seen.
3. **One documented exception.** Section 03.03 holds all 28 asphalt lines. It is split by alternating labelled lines (odd index → lockbox), so asphalt exists on both sides. This lets some leakage in within that section. Asphalt is rule-solvable, and its lockbox score is reported as a separate row.

### Frozen composition

These were recomputed† from the v1 rule.

|                                 | Dev                                           | Lockbox                                                                                                        |
| ------------------------------- | --------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| L1 sections                     | the other 18 sections plus even-indexed 03.03 | 01.03, 03.01, 04.02, 04.04, 05.02, 06.01, 08.01, 08.03, 09.01, plus odd-indexed 03.03                          |
| Labelled (L)                    | **139**                                       | **113**                                                                                                        |
| E (no equivalent)               | 9                                             | 4 (04.04.0050/60/70, 05.02.0130)                                                                               |
| S (service)                     | 14                                            | 2 (05.02.0140, 06.01.0120)                                                                                     |
| A (ambiguous)                   | 0                                             | 1 (04.04.0080)                                                                                                 |
| Blank subtype in GT             | 36                                            | 13                                                                                                             |
| Concrete / Aggregates / Asphalt | 36 / 17 / 14                                  | 39 / 15 / 14                                                                                                   |
| Lines mostly held out           | —                                             | Admixture 7 of 8, Excavations 9 of 13, Cement 8 of 12, Steel 7 of 11, all 9 lines of the 09.01 ready-mix block |

- **Disclosed risk:** the lockbox is _harder_ than dev on Admixture, Excavations and the ready-mix convention. Dev holds most of C&D (18 of 24). Expect some shrinkage from dev to the lockbox. This is part of why the dev bar is .95 rather than .90. [A-31]
- **What the lockbox is.** A same-project, section-held-out holdout. The builder explored it before the split was frozen (§10), so it is blind only to tuning. Only the unseen live BoQ is a blind test. [A-5]
- **Headers** go to the side of their section and count only in the G1 and decision-matrix rows.

### Freezing

- `eval/split_v1.json` holds `{rule, item_ids_dev[], item_ids_lockbox[], created}`. It is committed **before the first LLM call**.
- Its SHA-256 is `c9a6d1f0aaafdd9f3b7499bf50f59f786f00270c3350e7d5cad7982b855039bd` (also in `DESIGN.md`, and unchanged at the tag `prereg-v1`).
- `tests/test_split_frozen.py` asserts the hash.
- Any change needs an Amendment entry and a new file (`split_v2.json`). It never edits v1.

### Lockbox-once rule

1. Before the freeze, the experiment runner only routes dev item IDs (from `eval/split_v1.json`) to the LLM. Lockbox lines are never sent, never replayed and never inspected, so even raw responses on lockbox lines do not exist yet. This also holds for the v0.1 skeleton, whose `output/` files are rules-only B0 placeholders until the freeze. [A-32]
2. The freeze is a git tag `eval-freeze`, at the latest Thu 8 Oct 12:00 (G4; at that time the latest green dev configuration is frozen, whatever gates remain open). It pins [A-6, A-32, A-38, A-45]:
   - the code sha, prompt_version, model ID and the policy resolution in `config/policy.yaml`;
   - the library sha, the enrichment sha and the glossary sha (`config_sha256` in the manifest);
   - the split sha;
   - all three rungs: **B1** (threshold fixed on dev), **B2** (pinned prompt and config) and **B3** (the A31 threshold).
3. **One post-freeze lockbox session.** It runs B2 and then B3, once each, as full-file cold live runs (`--no-cache`) for both languages. B1 needs no model call. Scores are broken down by split. Both runs are appended to `eval/lockbox_log.md` (append-only: timestamp, tag, sha, rung, every metric) and committed in full under `runs/submission/` (§8). The once-only rule applies per frozen rung. Nothing is tuned afterwards. [A-6, A-36]
4. **If the lockbox misses .90:** nothing is tuned on it. The number is reported as measured, in the §3 claim form, with error analysis, in the README's known weaknesses.
5. **Bug-only rerun exception.** A rerun is allowed at most once, and only if all of these hold:
   - (a) the defect is a gap between code and this written spec, such as a parser, join or encoding error, and not a prompt, threshold, glossary or policy change;
   - (b) a unit test that reproduces it is written _before_ the fix, and that test does not quote lockbox text;
   - (c) both lockbox scores are reported side by side, with the commit diff linked.

---

## 3. Statistical confidence

### The claim bound

**The one-sided 95% exact (Clopper–Pearson) lower bound is the only bound used for any "≥ 90%" claim.** Every other interval in this document is for display only. [A-18, A-31]

```
L(k, n) = 0                               if k = 0
L(k, n) = the p with P(X ≥ k | n, p) = .05  otherwise   (= Beta⁻¹(.05; k, n−k+1))
```

`eval/score.py` computes L by bisection on the binomial CDF with `math.lgamma`, standard library only (§8). [A-34]

**Values at our sample sizes**†

| Observed | One-sided 95% CP lower bound |
| -------- | ---------------------------- |
| 30/30    | .905                         |
| 40/40    | .928                         |
| 39/40    | .887                         |
| 57/60    | .876                         |
| 59/60    | .923                         |
| 95/100   | .898                         |
| 102/113  | .844                         |
| 133/139  | .917                         |

To reach L ≥ .90 a set of n matched lines may contain **0 errors at n = 29–45, 1 at n = 46–60, 2 at n = 61–75, 3 at n = 76–88, 4 at n = 89–102 and 5 at n = 103–115**†. Below n = 29 the bound can never reach .90.

### Display intervals

**Wilson 95% interval** for any proportion k/n (precision, coverage, handling), with z = 1.96. It is printed for reading, never used for a claim or a decision:

```
centre = (p̂ + z²/2n) / (1 + z²/n)
half   = z·√(p̂(1−p̂)/n + z²/4n²) / (1 + z²/n)
```

**Interval widths at our sample sizes**†

| Observed                | Wilson 95%   |
| ----------------------- | ------------ |
| 28/30 = .933            | [.787, .982] |
| 56/60 = .933            | [.841, .974] |
| 51/55 = .927            | [.827, .971] |
| Coverage .45 on n = 139 | [.373, .536] |
| Coverage .45 on n = 113 | [.363, .543] |

### Paired comparisons (same items, so paired tests)

- **Coverage or correct-match indicator** (rung vs rung, EN vs FR on the same items): McNemar exact binomial test on the discordant pairs (b, c), with p reported. Per language. A test that pools both languages counts discordance per Item No., so twins are never two observations. [A-5]
- **Precision difference:** the matched sets differ between systems, so McNemar does not apply. Use a **paired item bootstrap**, display only:
  - 10,000 resamples of Item No. (both languages of an item move together) [A-5];
  - recompute both systems' precision on each resample;
  - report the 95% percentile interval of ΔP.
- **Sensitivity check:** a section-cluster bootstrap that resamples L1 sections. There are only about 10 lockbox sections, so its interval is wide. It is reported as a robustness check, not as the headline.

### Minimum detectable difference

Two-sided α = .05, power .80†.

| Comparison                            | n                     | Minimum detectable difference |
| ------------------------------------- | --------------------- | ----------------------------- |
| Coverage, McNemar, 3–5% reverse flips | dev 139               | **≈ 10–11 points**            |
| Coverage, McNemar, 3–5% reverse flips | lockbox 113           | **≈ 11–13 points**            |
| Precision between two variants        | about 40 matched each | **≈ 25 points**               |
| Precision between two variants        | about 60 matched each | **≈ 19 points**               |

Consequences:

- **Baseline vs final** (the expected coverage gap is about 35–50 points) is easy to detect.
- **Variant vs variant precision is not.** We therefore never claim that one variant is "more precise" than another. Variants are selected on coverage under the precision bar.
- Dev gains smaller than about 10 points are called _directional_.

### Why the bound gates the lockbox claim and not dev selection

On dev, gating on L ≥ .90 would allow 0 errors at n = 29–45 and 1 error at n = 46–60. A policy with true precision .96 would then pass one language only 30–60% of the time, and both languages jointly much less often. Optimising for that bound pushes coverage towards zero, which is the overfit the TF-IDF baseline shows: its P = .909 at 4% coverage falls to .76–.82 on held-out halves. [A-31]

So the two uses are split [A-31]:

- **Dev selection** uses a point estimate: **matched precision ≥ .95 in each language, with ≥ 40 matched dev lines in each** (§4). The 5-point margin absorbs the winner's curse from choosing among the nested thresholds, plus the dev-to-lockbox shift. L is used on dev only to order the fallback when no threshold qualifies.
- **The lockbox claim** uses L, once, on the one frozen threshold.

### Operating characteristic of the dev bar

Exact binomial probability that a threshold shows observed precision ≥ .95 on n matched lines in **one** language, given its true precision†. Both languages must pass, so the joint pass rate is lower.

| True precision | n = 40 (needs ≥ 38) | n = 50 (≥ 48) | n = 60 (≥ 57) | n = 80 (≥ 76) | n = 100 (≥ 95) |
| -------------- | ------------------- | ------------- | ------------- | ------------- | -------------- |
| .85            | .05                 | .01           | .01           | .00           | .00            |
| .90            | .22                 | .11           | .14           | .09           | .06            |
| .93            | .46                 | .31           | .39           | .33           | .29            |
| .95            | .68                 | .54           | .65           | .63           | .62            |
| .96            | .79                 | .68           | .78           | .78           | .79            |
| .97            | .88                 | .81           | .89           | .91           | .92            |

How to read it:

- A true-.90 threshold passes one language 9–22% of the time at n = 40–80 (6% at n = 100); a true-.96 threshold about 78% (68% at n = 50). This is the pre-registered operating characteristic, as stated in `DESIGN.md` §10.6. [A-31]
- The bar mostly rejects anything at true .90 or worse, which is the point: the lockbox claim needs room above .90.

**Claim wording, fixed now** [A-31]: _"Lockbox matched precision X% on n lines; one-sided 95% exact lower bound L; meets the 90% point bar: yes / no; certified at 95% confidence: yes only if L ≥ .90."_ A Wilson interval may be printed next to it for display; it is never the bound.

---

## 4. Abstention and selective-prediction evaluation

**Population.** Dev item rows not settled by G1 or G2, meaning every line where a match is possible: L, E, the unresolved part of S, and A.

- Error event: a line decided `matched` whose triple is wrong, including any match on a blank-GT line.
- Lines that a gate vetoes (decision-table rows D0–D8, including D1b `LLM_FAILURE:partial_signal` and D5a `EVIDENCE_NOT_IN_LINE`) never match, whatever their score. `ATTR_CONFLICT` lines never match. [A-31, A-43]

**Signals.** No logprobs are available, so all three are verbalised, code-derived or agreement-based.

| Id  | Signal                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | Values                                                                                            | Extra cost     |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- | -------------- |
| a   | Self-reported confidence (integer 0–100, anchored in the prompt, range validated in code) plus the ordinal `self_reported_candidate_gap` (`decisive` / `clear` / `narrow` / `tossup` between the model's top-1 and top-2). A verbal judgement, never a probability                                                                                                                                                                                                                                                                                                                                               | confidence bucket {≥ 90, 80–89, 70–79, < 70}; the gap is recorded and reported, not scored [A-31] | 0              |
| b   | Attribute-extractor agreement, defined from the **line side** (below)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | {conflict, no_evidence, agree} [A-35]                                                             | 0 (code)       |
| c   | Vote count v ∈ {0..k}: the number of passes whose top-1 equals the plurality top-1. Pass 1 uses the canonical rendering; passes 2..k each use one fixed, pre-registered alternative rendering (for example codes in reverse sorted order; display codes unchanged), each with its own cache breakpoint and prompt_version component. Per-call random permutations are forbidden. All passes at temperature 0. A tie for plurality → `needs_review`. **k = 2 by default**; k = 3 is an E-02 arm, adopted only under the keep rule (§6) and the cold-run latency gate at the tier recorded by `oris doctor` [A-46] | v                                                                                                 | about k× calls |

**Signal (b), defined once** [A-35]:

- **conflict:** some hard family is stated on both sides with incompatible values. Always a veto (D7).
- **agree:** no conflict, **and** at least one hard family is stated on both sides and compatible, **and** every family the line states is compatible with the row or absent from it (the §9.3 absence rule).
- **no_evidence:** everything else.
- A row is never required to have all its families present in the line. FR library rows bundle full specs (`XC3-XC4-…-XA1 C30/37 330kg CEM I`) while FR lines state only part (`C30/37 XC4/XA1`); under a row-side definition b would almost never fire on FR concrete. D8 uses "a sibling is agree".
- A2's dev table (literal / neutral / conflict per language) is recomputed under this definition and logged.
- Unit test: `Semelles … C30/37 XC4/XA1` against `…XC3-XC4-…-XA1 C30/37 330kg CEM I` gives agree. FR smoke fixture: b fires on at least one FR concrete positive (§7).

**Score and candidate operating points** [A-31]

- Every routed line that no gate vetoes gets a lexicographic score s = (v, b, confidence bucket), each component compared in order: higher v first, then b = agree before no_evidence, then the higher confidence bucket.
- The operating points are the thresholds on s. A line matches at threshold T when s ≥ T. Each threshold accepts a superset of the next stricter one, so the order is fixed before any call. With k = 2 (v = 2 for every non-tied line) there are 8 thresholds, from (2, agree, ≥ 90), the strictest, to (2, no_evidence, < 70), the loosest.
- Match-all (every valid in-library answer matched) is kept only as a reference and is never a candidate.
- If A17's cross-model votes are adopted under their own gate, they enter as extra passes in v. The threshold is then defined per available-voter set; a missing voter gives an `ENSEMBLE_DEGRADED:` reason (`DESIGN.md` §9.5 enum) and the Haiku-only threshold, which is validated separately. [A-17]
- One threshold covers both languages (D-12). A per-language optimum is reported as a sensitivity row and never shipped. The live run uses an unseen file with a different library.

**Dev selection** [A-31]

1. Pick the **loosest** threshold whose dev matched precision is **≥ .95 in each language, with ≥ 40 matched dev lines in each**.
2. If two thresholds are within 3 summed correct matches, pick the **stricter**.
3. If none qualifies, ship the threshold with the highest minimum-over-languages one-sided 95% CP lower bound, labelled "below the dev bar", and report the shortfall.
4. `oris select --target T` re-runs this rule on cached votes at $0 (for example T = .85 or .95 in the live session). [A-27]
5. On the lockbox, report only the frozen threshold, in the §3 claim form. A lockbox risk–coverage curve may be shown afterwards, labelled "post-hoc, not used for selection".

This rule replaces both the v1 selection rule and A18's certified walk; neither applies anywhere in this document. A18's one-sided CP bound is kept for the lockbox claim (§3). [A-31]

**Reported with the selection**

- Dev precision by v, and the share of errors with v = k (systematic errors that agreement cannot catch). [A-46]
- A precision / coverage / review-load / cost table for every threshold, produced by $0 replay. This is the explicit trade-off curve.
- D5a's false-reject rate on dev, by $0 replay. If it rejects more than 3 correct matches, the evidence length is widened and the prompt version bumped (logged). [A-43]

**Risk–coverage curves** (reported with the selection, G2)

- Sort the population by s, descending. Equal-score groups are a single step.
- For each prefix k: risk(k) = errors in the top k / k; selective coverage = k / \|pop\|.
- Each curve point is also plotted as **task coverage C₂₅₂** against P, with B1, B2, B3 and the A31 thresholds marked.
- Plots are per language, with 1,000-resample bootstrap bands that resample Item No. [A-31, A-5]

### Dev diagnostics (deferred: after must-haves, before the freeze)

These run after the G2 must-haves, on dev only. None of them chooses the threshold; the A31 rule above does. They stay in scope as diagnostics (A41's cut is not adopted). [A-22, A-32]

**AURC and E-AURC**

- AURC = (1/n) Σₖ risk(k).
- E-AURC = AURC − AURC_oracle, where the oracle ranks every correct line first.
- Summaries only.

**Selection stability** [A-22]

- Repeated grouped 2-fold resampling over **dev replay votes only** (groups = Item No. within L1 section). Within each repeat the A31 selection runs on one half and is scored on the other. The spread of the chosen threshold and of its held-out precision is reported as a dev diagnostic.
- No resampling ever touches lockbox items.
- Label-noise candidates (unanimous model disagreement with GT) are adjudicated on dev only, before the freeze, with a written reason, and tagged `gt_suspect`. Headline metrics are always "as labelled".

**Calibration battery** of the self-reported confidence (dev diagnostic):

- Event: the top-1 triple is correct, over the whole population (not only M).
- Reliability diagram with **5 equal-mass bins** (about 30 lines per bin per language).
- ECE = Σ_b (n_b/n)·\|acc_b − conf_b\|, plus the Brier score.
- For the ordinal gap rating: precision at each gap level.
- Expected result: overconfidence. That is the reason confidence is the last component of s, behind agreement and attribute evidence, and is not trusted by default. [A-31]

**Comparing the signals** (dev diagnostic)

- Paired item bootstrap on ΔAURC and on Δ coverage at the dev bar, for each pair of signals. Display only. [A-5]
- Report the extra cost of passes 2..k per 100 lines. [A-46]
- **Expected finding:** b dominates on attribute-rich families (Concrete, Asphalt, Cement), and c helps on usage confusers. Whatever the result, it is reported.

---

## 5. Baseline ladder

All rungs are scored by the same `eval/score.py` on the same splits.

| Rung                      | Configuration (pinned)                                                                                                                                                                                                                                                                                                                                | Answers                                                                                                       |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| **B0: rules only**        | G1 headers → not_a_material; every other line → needs_review. No unit-only skip [A-15]                                                                                                                                                                                                                                                                | The floor: C₂₅₂ = 0, P = n/a. Safety and review load with zero intelligence.                                  |
| **B1: TF-IDF**            | `char_wb` 3–5-grams plus word 1–2-grams, cosine scores averaged, `sublinear_tf=True`, argmax over the 342 rows. The vectoriser is fit on library rows + **dev lines only**; lockbox lines are only transformed [A-29]. Threshold on the cosine score chosen **on dev** by the A31 rule (§4) [A-47]. Frozen at eval-freeze; needs no model call [A-6]. | "The simplest reasonable approach", measured held out.                                                        |
| **B2: Haiku single pass** | One structured-output call per batch of 10, raw library hierarchy, no section path, no extractors, no glossary, pinned prompt and config. Every valid in-library answer → matched. Run once on the lockbox in the A6 session [A-6].                                                                                                                   | Raw model capability. The gap between B1 and B2 is the value of the LLM; P(B2) shows the need for abstention. |
| **B3: final**             | Frozen system: section path, extractors, the A31 threshold, and any evidence-gated additions kept under §6. Run once on the lockbox in the A6 session, after B2 [A-6].                                                                                                                                                                                | The gap between B2 and B3 is the value of the engineering.                                                    |

**Scorer check.** The brief's full-set match-all top-1 (EN .405, FR .159) is reproduced within ±.01 by a TF-IDF fit on library + all queries, the brief's own configuration. That fit is label-free, run once to validate the scorer, and never thresholded or reported as B1. [A-29]

**Like-for-like baseline table** (lockbox, per language) [A-47]:

|     | What                                                                                                                                                                                  | Test                              |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------- |
| (a) | Top-1 full-triple accuracy over all labelled lines: argmax for B1, the answer for B2, the suggested row for B3. Abstention plays no part                                              | McNemar exact B1 → B2 and B2 → B3 |
| (b) | Per-level cumulative top-1 accuracy (type, type+usage, triple) for each rung                                                                                                          | descriptive                       |
| (c) | Coverage at each rung's own dev-selected point (the A31 rule applied to B1's cosine score, to B2's confidence and to B3's s), with lockbox precision and its one-sided CP lower bound | §3 claim form                     |

- Every rung also reports P, C₂₅₂, F_NM, H₂₆₅ (diagnostic), per-level proposal accuracy and cost per 100 lines.
- The B2 → B3 precision difference is shown with the paired item bootstrap, display only (§3). It mixes in abstention, so (a) is the like-for-like comparison. [A-47]
- After the freeze, a descriptive lockbox error table: material family (top 8 + other) × first wrong level / match-on-blank / missed, labelled "post-hoc, not used for any decision". [A-47]
- Ablations of B3 (one component removed at a time) run on dev only.

**Candidate set and recall@k** [A-15, A-24, A-37]

- The default `CandidateProvider` is `WholeLibrary`: the whole library goes into the prompt, so candidate inclusion is 1.0 by construction (not "recall"). [A-15]
- **Library size tiers**, on the measured rendered prompt: ≤ 30k tokens, whole library; 30k–150k, whole library with a printed warning and a cache check; > 150k, an explicit error naming the retrieval switch. The loader never truncates. [A-15, A-37]
- A second adapter, `HybridRetriever` (BGE-M3 dense + BM25, optional reranker), lives behind the same port. Its dependencies (torch, BGE-M3) are in the optional `[retrieval]` extra, so the core install and Docker stay light. It is deferred: after must-haves, before the freeze. [A-24, A-37]
- recall@k = \|{i∈L : GT row ∈ candidates(i)}\| / \|L\|, for k ∈ {10, 20, 30, 50}, per language, **on dev only**. Report the distribution of candidate-set sizes. [A-24]
- Gates:
  - switching to retrieval requires the rendered library to exceed 30k tokens **and** dev recall@k ≥ .98 per language [A-24];
  - a **hard attribute filter** needs recall = 1.00 on dev. Any miss is treated as a parser bug, gets a unit test, and is fixed.
- Reference point from the brief: TF-IDF recall@20 is .869 in EN and .690 in FR. That is why no lexical top-k shortlist is used on these libraries, and why the full list of 108 type+usage pairs goes into the prompt.

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
- `usage_split`, for usage errors only: `element_right_usage_wrong` or `element_wrong`, judged against the model's `element_or_application` field [A-43]
- `gt_suspect`: set only after the A22 dev adjudication, with its written reason [A-22]
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
- Lockbox errors are tagged _after_ the lockbox run, for reporting only.

**From taxonomy to experiment** (evidence-gated additions)

Order of work: the B2 thin slice runs first (a stratified 40-line dev slice in EN and FR, then all 139 dev lines); E-02 (§4) is then mandatory. Every other arm (E-01, E-03 to E-09, the A17 cross-model voter, retrieval, the verifier) runs only when the taxonomy below triggers it, which limits how many configurations are scored on dev. [A-4]

1. Build a Pareto chart of causes per language, by count. `false_match`, `match_on_blank` and `false_nm` are weighted ×2, because precision and safety errors cost more than missed coverage.
2. An intervention is considered only if its cause accounts for ≥ 5 dev lines or ≥ 20% of dev errors in one language. The pre-mapped responses:

| Cause          | Response                                                                                                                                   |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| lexical_gap    | Bilingual enrichment / glossary A/B                                                                                                        |
| usage_confuser | Contrast set, or the E-08 sibling verifier over confusable rows (disagreement → `VERIFIER_DISAGREES`) [A-20]                               |
| subtype_parse  | Fix or add an extractor, test first                                                                                                        |
| header_context | Change how the section path is rendered                                                                                                    |
| not_in_library | An explicit no-equivalent option, plus a forced-match guard                                                                                |
| llm_failure    | Engineering: retry, batch size                                                                                                             |
| gt_convention  | **Not fixed with an item-specific rule.** Documented as a known weakness, or addressed only with a rule that generalises and is disclosed. |

3. **Keep rule.** A change is **kept** only if, on dev [A-42]:
   - (i) summed correct matches (EN + FR) rise by **≥ max(6, 2·√d)**, where d = discordant items between the two runs, pooled across languages and counted per Item No.;
   - (ii) neither language loses more than 2 correct matches;
   - (iii) the A31 dev bar still holds (≥ .95 in each language, ≥ 40 matched in each);
   - (iv) F_NM stays at 0, and cost and latency stay within budget.

   Otherwise the simpler or cheaper arm is kept. Both runs are scored by replay where possible. The rule replaces v1's "+3 lines" and "2× the flip rate", which let run-to-run noise ratchet in. [A-42]

4. Every attempt is a row in the experiment ledger. The experiment runner (never `eval/score.py`) appends one record per scored configuration to `eval/experiments.jsonl`, and `eval/experiments.md` is rendered from it: id, hypothesis, cause targeted, change, dev before/after (P, C, F_NM, $/100), d, the one-sided sign-test p (a note, not a gate), kept or reverted, prompt_version. Every configuration scored on dev is logged, kept or not. [A-4, A-42]
5. Gains are labelled _directional_ when they are below the dev detectable difference of about 10 points.

---

## 7. Robustness and generalisation

| Check                                                                              | Method                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | Pass criterion                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| ---------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **FR-library smoke set** [A-10, A-35, A-37, A-38]                                  | Base set of about 25 builder-labelled lines against `oris_materials_fr.csv`: about 10 FR lines from this BoQ that have an exact FR equivalent, about 8 synthetic lines hitting FR-only leaves (pot bearings, fenders, EME / GB with 20/40% AE, ductile pipe, detonating cord), and about 7 **no-match decoys**, 3 of which are lexically close to a real row. It gains mixed supply + labour lines, a material priced in LS/Ft, a close-wrong-usage decoy, an explicit subtype conflict and a line missing its spec (A10). **Plus about 20 hand-written FR lines** (mangled formats, LS-priced materials, close decoys), added to the same set (A37, kept). All labels are frozen and hashed before the first run. | The frozen policy ships on the FR library (`certified_by: smoke_A10` in `config/policy.yaml`) only if: closed-world violations 0; F_NM 0; decoy matches 0; **at least 9 of the 18 base positive lines correctly matched** (floor fixed before the first run); signal b fires on at least one FR concrete positive (A35). Report correct/positive, wrong/matched, decoy matches and false skips as k/n, base and hand-written lines on separate rows. FR-taxonomy results are reported apart from FR→global. Labelled "builder-labelled, unofficial". Run before submission and again before the live session.                                                                                                      |
| **Synthetic FR stress set** [A-23] (deferred: after must-haves, before the freeze) | `eval/stress_fr_synth_v1.csv`: about 150 FR-library leaves × 1–2 generated lines, + 30 non-material + 15 no-equivalent. Generated by an allowlisted small model from a family not used as a voter (A33); 50 lines hand-checked; the file is hashed before first use.                                                                                                                                                                                                                                                                                                                                                                                                                                               | Never affects thresholds, prompts or the policy. Reported separately as "synthetic stress test (optimistic)", with the smoke-set metrics as k/n. Whether it appears in the README is decided after the result.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| **Mangled-format parser fixtures** [A-15, A-23, A-30]                              | `;` delimiter, decimal comma, Latin-1 / cp1252 export, merged headers.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | The parser tries UTF-8 (BOM tolerated) first; a cp1252 file decodes as cp1252 with a warning and `encoding: cp1252` in the manifest. Every other variant gives an explicit error or `needs_review`, never a silent mislabel.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| **Library-order shuffle**                                                          | Permute the library rows (3 seeds). With FakeLLM: compare the outputs. Live: one shuffle on 30 dev lines.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | Prompt rendering is canonical-sorted, so `request_sha256` must be **identical**, and FakeLLM outputs must be byte-identical. The live flip rate must be at or below the rerun noise floor (determinism row below).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| **Row-ID stability**                                                               | ID = sha256(type ‖ usage ‖ subtype, raw)[:12]. Tests: IDs are unchanged when the library is reordered or when a row is added; `'x'` and `'x '` get different IDs; 0 collisions on both libraries. Line IDs = sha256(item_no ‖ position ‖ text), so duplicate texts stay distinct.                                                                                                                                                                                                                                                                                                                                                                                                                                  | All tests pass, offline.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| **Failure injection** (FakeLLM) [A-8, A-12, A-39, A-43, A-50]                      | A fault matrix (timeout, 429 then success, permanent 429, 5xx, 529, malformed JSON, truncation / `max_tokens`, refusal, missing ID, byte-identical duplicate ID, **conflicting duplicate ID**, unknown ID, out-of-library triple, **ids swapped within a batch**, **pass 2 times out**, **a line containing `</line><line id="L3">` plus a JSON-breaking quote**, **replay cache miss**) × fault position (first, middle, last batch). A sustained 429 storm with retry-after 20 s over 300 lines. A `hypothesis` property test adds random fault schedules × batch sizes 1–20.                                                                                                                                    | For every case: output length = input length, Item No. order preserved, every affected line is `needs_review` with its reason (`LLM_FAILURE:<kind>`, `INVALID_ROW_ID`, `LLM_FAILURE:duplicate_conflict` after one re-ask, `LLM_FAILURE:partial_signal` when a pass the policy needs is missing, `LLM_FAILURE:replay_miss` with exit code 3), no `matched` derived from a failed or partial response, raw error in the audit, at most 6 model calls and 90 s of in-flight time per line, budget stop respected. Ids swapped → 0 matched; the injected line changes no other line. The 429 storm ends with 0 lost and 0 `LLM_UNAVAILABLE` lines: throttling never counts toward the §11.3 breaker. **0 lost lines.** |
| **Determinism / noise floor** [A-42]                                               | temperature 0. A 40-line dev slice (20 EN + 20 FR) is run twice live.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | Report the decision flip rate, the row flip rate (expected ≤ 5%) and d, the discordant items. Variant differences are judged by the A42 keep rule (§6), not by a multiple of the flip rate. Replay of a run must be **byte-identical** (offline test).                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| **Batch contamination**                                                            | B = 1 vs B = 10 on 30 dev lines (§1).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | \|Δ proposal accuracy\| within the noise floor. Otherwise B is reduced and the change recorded as an Amendment.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| **Structural headers** [A-48]                                                      | The FR input renumbered `1 / 1.1 / 1.1.1`, and the FR input with codes removed.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | Same header decisions and section paths as the original. A file with no derivable path runs the strictest threshold unless the E-04 no-path ablation meets the dev bar; `path_mode` is recorded in the manifest.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| **CLI / API parity** [A-50]                                                        | The whole EN file through POST /v1/match and through the CLI, under ReplayLLM.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | Identical decisions and request hashes.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| **Policy resolution** [A-38]                                                       | FakeLLM: the brief's literal command on the FR library; the FR library with one byte changed; the global library; the fallback model on the global library.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | The A10 entry; strictest; the selected threshold; strictest. `policy_resolution` is recorded in the manifest.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |

---

## 8. Evaluation as CI

### Scorer contract (`eval/score.py`) [A-7, A-34]

The scorer is the deliverable ORIS reruns on its own labels, so it is lenient on input by default and strict on our own files.

- One standard-library-only file that imports nothing from `src/`.
- Usage: `python eval/score.py --output OUT.csv --reference REF.csv [--label en] [--key COL] [--strict] [--join key|row-order] [--split eval/split_v1.json --side dev|lockbox|all] [--json report.json]`. Repeat the `--output`/`--label` pair to score EN and FR side by side.
- **Key and columns.** The key is auto-detected (case- and space-insensitive) from `Item No.`, `Item No`, `item_no`, `N° article`; `--key` overrides. Label columns resolve by alias. The output needs the key, `decision` and the three labels; `reason`, `cost_usd` and `latency_ms` are optional and print n/a when absent.
- **Join (default, lenient).** Rows join on (key, occurrence index). Duplicate or blank keys produce a warning with a count. A reference row missing from the output counts as a failure and is listed. An output row missing from the reference is unscored, listed, and excluded from every denominator. A row-order join needs `--join row-order`.
- **`--strict`** restores A7's hard errors: missing, extra or duplicate keys in either file; a row-order join with unequal row counts; a decision outside the enum; `matched` with an all-blank triple; a non-matched row carrying labels. A blank subtype is always a valid leaf. `check_requirements.py` always runs the scorer with `--strict` on our own files.
- **Cells.** Label cells matching `^(nan|NaN|None|NULL|<NA>|N/A)$` count as blank, with a printed count. The headline uses exact raw-string match (§1). A "lenient (NFC + trim + whitespace collapse)" line is always printed, with a warning when it differs.
- **Report:** matched precision with its one-sided 95% CP lower bound (§3); C₂₅₂ (C_labelled); cumulative per-level accuracy over matched rows and over all labelled rows; decision shares over all rows and over item rows; the false not_a_material count; hit@1/2 on review lines (A21); mean `cost_usd` and attributed `latency_ms`. Bootstrap, McNemar, risk–coverage and plots stay in `eval/report.py`.
- The scorer never writes the experiment ledger (§6).

### Offline in pytest (no network: pytest-socket, loopback only [A53], runs in CI on every commit)

- **Scorer fixtures** [A-34]:
  - a hand-computed reference/output pair of about 15 rows: blank subtype in both directions; a match on a blank-GT line; a `whitespace_only_mismatch`; `n/a` precision when nothing is matched; both denominators; CP lower bounds and Wilson values to 3 decimals;
  - GT scored against itself gives P = 1; the provided sample output; a labels-only reference; duplicate codes; `nan` literals; an NFD reference; a renamed key header; a key-less file with `--join row-order`;
  - under `--strict`: duplicate, missing or extra keys → error.
- **Split hash** test.
- **Leakage guard:** an AST/grep test that `src/` never references the GT path or `eval/` and contains no literal GT type or usage strings, plus a 6-gram overlap check between glossary entries, enrichment output and the BoQ inputs. [A-19]
- **Invariant and property tests** (§7).
- **LLM port contract tests** (`tests/contract/test_llm_port.py`), parametrised over Fake, Replay and Anthropic; the Anthropic case injects an HTTP MockTransport serving 200, 429 with retry-after, 529, `max_tokens` and refusal. [A-44]
- **Model allowlist:** the port refuses to construct an adapter for any model outside `config/models.toml` (unit test). [A-33]
- **Replay golden:** a committed 20-line dev `calls.jsonl` fixture must replay to a committed CSV, byte-identical. [A-36]
- **Submission replay (RQ11):** `oris replay runs/submission/<b3_run> --check output/improved_output_{en,fr}.csv` must be byte-identical, once the G4 runs exist. [A-36]
- **Requirements check** on the committed outputs.
- CI runs on ubuntu-latest and windows-latest; the golden replay and the prompt-hash snapshot must match byte-for-byte on both. [A-49]

### Need the API (`@pytest.mark.live`, skipped without a key, never in CI)

- `oris doctor` (in G1, on Thu 8 Oct and 30 minutes before the session): keys present; pinned model served; one 2-line call with the production schema accepted; count_tokens per library and cache eligibility; rate-limit tier from the response headers; fallback adapter answers. Saved to `evidence/doctor_<date>.json`. [A-33, A-39]
- the live smoke run;
- dev experiments;
- the determinism and batch checks;
- the lockbox session (A6);
- cost and latency measurement (cold, `--no-cache`).

Each produces a `runs/<id>/` folder with `manifest.json`, so the results can be replayed at $0. **The runs behind the submission are committed** under `runs/submission/<run_id>/{manifest.json, calls.jsonl, audit.jsonl, prompts/<sha256>.txt}`: the B2 and B3 lockbox runs for EN and FR, the dev selection runs, and the FR smoke-set run. Runs on any other input stay local (`.gitignore`: `runs/*` plus `!runs/submission/**`). [A-36]

### Audit records [A-45, A-52]

- **`calls.jsonl`, one record per attempt**, with field names from the OpenTelemetry GenAI semantic conventions (semconv version pinned in the manifest): call_id, parent_call_id, attempt_no, reason_for_call ∈ {first, retry, reask_missing, reask_conflict, split}, started_at / ended_at (UTC), `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.response.id`, provider_request_id, `gen_ai.response.finish_reasons`, `gen_ai.usage.{input_tokens, output_tokens, cache_read, cache_creation}`, request_sha256, user_message, system_blocks_sha256 (system blocks stored once at `prompts/<sha>.txt`), http_status, error_class, raw_response, cost_usd, latency_ms, line_ids, cache_hit, source_call_id.
- **`audit.jsonl`, one record per line**, holds `raw_line_response` (the line's verbatim JSON object from its batch response) and its call_id(s). [A-36]
- No tracing server is a dependency. The records load into Phoenix or Langfuse unchanged (D-18). A 1-hour Phoenix / OpenInference export spike is G6 work, only if G5 is green.

### `eval/check_requirements.py --output X --input Y --library Z --run runs/<id>`

Exits non-zero on any failure and prints JSON:

| ID   | Check                                                                                                                                                                                                                                                                                                                                                              |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| RQ1  | Columns = input columns in order + `decision, material_type, material_usage, material_subtype` + audit columns from the sample output (`reason, model, prompt_version, latency_ms, cost_usd`), then `suggested_type, suggested_usage, suggested_subtype, library_row_id, call_ids` (`;`-joined, empty for rule-decided rows) [A-30, A-36]                          |
| RQ2  | Row count, Item No. sequence and input cell values are identical to the input (UTF-8 round trip, no stripping); CRLF line endings, no BOM [A-49]                                                                                                                                                                                                                   |
| RQ3  | `decision` ∈ {matched, not_a_material, needs_review}, with no blanks                                                                                                                                                                                                                                                                                               |
| RQ4  | Every `matched` row's triple ∈ the library's raw triple set (exact, case-sensitive strings); every non-matched row has blank labels                                                                                                                                                                                                                                |
| RQ5  | Every `not_a_material` row has reason ∈ {`HEADER`, `G2_SERVICE`, `EMPTY_ROW`}; `G2_SERVICE` rows have a unit in `SERVICE_UNITS` (after unit aliases) and no supply marker [A-11, A-30, A-48]                                                                                                                                                                       |
| RQ6  | `audit.jsonl` has exactly one record per line (line IDs 1:1), with `raw_line_response` on LLM-derived lines; every LLM-derived decision references a `call_id` whose record has requested and served model, prompt_version, raw_response, usage, cost_usd, latency_ms, and, on every successful call, non-null provider_request_id and finish_reasons [A-36, A-45] |
| RQ7  | Σ cost / N × 100 ≤ $2.00, from a manifest with `mode = live` and `cache_hits = 0`; the per-line `cost_usd` sums to the run total [A-45]                                                                                                                                                                                                                            |
| RQ8  | Run wall-clock / N_routed ≤ 2.0 s, from a cold live manifest (`mode = live`, `cache_hits = 0`) [A-12, A-45]                                                                                                                                                                                                                                                        |
| RQ9  | `manifest.json` pins all present: code sha, model, prompt_version, library sha, split sha, price-table date, `mode`, `config_sha256` (config/\*, glossary, enrichment), enrichment_sha256, semconv version, rate-limit tier, `policy_resolution`, `fallback_model` if used [A-38, A-39, A-45]                                                                      |
| RQ10 | Every distinct served model in `calls.jsonl` is on the allowlist in `config/models.toml`: {claude-haiku-\*, gpt-4o-mini\*, gemini-\*-flash\*, local:≤8B}. No non-small model is configured anywhere [A-33]                                                                                                                                                         |
| RQ11 | Replaying `runs/submission/<b3_run>` reproduces `output/improved_output_{en,fr}.csv` byte-identically [A-36]                                                                                                                                                                                                                                                       |

---

## 9. Grader gate table

Fixed-time gates, variable scope. Each gate ends in a submittable tag; a gate that hits its cap ships what is green and documents the rest. A gate passes only when its evidence exists as files in the repo. [A-32]

Rubric key: **U** data understanding · **B** baseline and confidence · **T** trade-offs · **E** engineered · **D** defend.

| Gate · cap                                                      | Rubric checked          | Evidence that must exist                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| --------------------------------------------------------------- | ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Pre-registration** (done)                                     | U, design-doc questions | tag `prereg-v1` (bc5c8a2); `DESIGN.md` with the split SHA; `eval/split_v1.json`; `docs/data-analysis.md`; `eval/annotations/blank_line_classes.csv`                                                                                                                                                                                                                                                                                                                                                                                          |
| **G0** · spec merge · Mon 5 Oct 10:00                           | design-doc questions    | DESIGN v2 and this protocol v2 committed as "docs: pre-registration v2", before any model call on the data [A-30]                                                                                                                                                                                                                                                                                                                                                                                                                            |
| **G1** · v0.1 skeleton · **Tue 6 Oct 12:00**, tag v0.1          | B (started), E          | CLI + POST /v1/match on one MatchService; reader, validator, G1 37/37 offline; every LLM failure → needs_review with a reason; audit columns + `calls.jsonl`; Haiku and gpt-4o-mini adapters behind the allowlist; `score.py` passing the A34 fixtures; B0 and B1 dev numbers; B2 / v0.1 on a stratified 40-line dev slice, then all 139 dev lines EN + FR; `check_requirements.py`; CI matrix green; `evidence/doctor_<date>.json` with the tier; provisional README; `output/` files are labelled B0 placeholders [A-32, A-33, A-34, A-39] |
| **G2** · selection · Wed 7 Oct 12:00, tag v0.2                  | B, T, U (EN vs FR), D   | E-02 votes at k = 2 on all dev lines; the A31 selection result per language (≥ .95, ≥ 40 matched, F_NM = 0) or the "below the dev bar" fallback; `errors_dev.csv` with causes; `eval/experiments.jsonl` and the rendered `experiments.md`; `config/policy.yaml` (A38); D5a false-reject rate [A-31, A-38, A-42, A-46]                                                                                                                                                                                                                        |
| **G3** · hardening · Wed 7 Oct 22:00, tag v0.3                  | E, constraints          | fault matrix, 429 storm and property tests green; structural-header fixtures; FR smoke set labels hashed and first run against the A10 floor; CLI / API parity; API limits; one cold live run over all dev lines (no lockbox line) for latency at the recorded tier; determinism noise floor [A-10, A-39, A-48, A-50]                                                                                                                                                                                                                        |
| **Deferred: after must-haves, before the freeze**               | B, T                    | A22 selection-stability diagnostic; calibration battery; A23 synthetic set generated and hashed; A24 recall@k on dev; A25 local ≤ 8B benchmark on the 40-line slice; A17 cross-model voter if triggered; AIMD (only after a recorded 429) and the windowed breaker rule; optional ~$0.50 fallback-model dev certification if G2 closed on time. Each starts only after the gates above are green, and none changes a threshold except through the §6 keep rule [A-22, A-23, A-24, A-25, A-33]                                                |
| **G4** · freeze + lockbox · **Thu 8 Oct 12:00, hard**, tag v1.0 | B, E, constraints       | `eval-freeze` tag; the single A6 session (B2 then B3, once each); one entry per rung and language in `lockbox_log.md`; both full-file outputs; `runs/submission/` committed and the RQ11 replay check green in CI; `check_requirements.py` green on both; the A47 baseline table; `docs/evaluation.md` generated by `report.py`; `DESIGN.md` §17 Results dated [A-6, A-36, A-47]                                                                                                                                                             |
| **G5** · release · Thu 8 Oct 18:00                              | all five                | clean clone on Windows and Linux (`uv sync --locked && uv run pytest && uv run oris …` green); `oris doctor`; fallback rehearsal with `ANTHROPIC_API_KEY` unset; final README (run, with more time, known weaknesses, live session); rehearsal of the A27 questions with four traced cases (correct match, plausible wrong match, abstention, API failure → review) [A-16, A-27, A-33]                                                                                                                                                       |
| **G6** · optional · hard stop Fri 9 Oct 12:00                   | D                       | only if G5 is green: UI (U1–U4); Phoenix / OpenInference export spike (1 h); Docker (core dependencies only)                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| **Submission** · Fri 9 Oct 12:00–17:00                          | D                       | README and submission only                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |

**Minimum shippable submission.** Every gate tag is submittable. `eval-freeze` happens at Thu 8 Oct 12:00 at the latest, on the latest green dev configuration, whatever gates remain open, and the single lockbox session follows (`DESIGN.md` §10.3, §14). [A-32]

---

## 10. Honesty and leakage

**No GT at inference**

- Prompts contain only:
  - the selected library;
  - enrichment generated from that library alone, cached by library hash;
  - a small glossary with sourced entries;
  - the line and its L0/L1 headers.
- `data/boq_dataset_matched_GT.csv` and `eval/annotations/*` are read only under `eval/`. CI enforces this (§8).
- There are no few-shot examples taken from GT. Dev examples would not leak lockbox answers under the frozen split. They are still not used: (1) the live run uses the FR library (0 shared triples), so global examples would have to be switched off and the scored system would differ from the live one; (2) with 238 of 244 GT triples singletons, examples transfer conventions, not answers, and those conventions enter as glossary entries tagged `dev_error`. [A-52]
- No conversational or correction memory is used. The classifier is stateless and closed-world; label-derived memory would leak (D-02, R3). [A-52]
- B1's TF-IDF vectoriser is fit on library rows + dev lines only, so no lockbox text shapes its vocabulary. [A-29]

**Enrichment and models**

- If E-01 adopts arm D, the shipped `semantic_description` is generated by Claude Haiku 4.5 from library rows (full path + siblings) and the glossary only, never BoQ text or labels; otherwise it is the deterministic arm C file. Enrichment files carry generator_model, prompt_version and reviewed_by, and the 6-gram overlap check covers their output. [A-19]
- Every model that touches the pipeline, including enrichment, the A23 generator and the fallback, is on the small-model allowlist (RQ10). A larger-model enrichment, if run, is a separate disclosed ablation and is never committed as the shipped file. [A-19, A-33]

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

- The builder profiled all 252 lines, lockbox included, during data analysis, _before_ the split was frozen.
- Mitigations:
  - no item-specific rules;
  - the leakage guards;
  - the dev-only experiment ledger.
- Any rule that encodes a convention seen in that analysis is listed. Lockbox precision is reported both on all lines and **excluding the lockbox lines touched by such a rule**.

**Builder labels**

- The 13 no-equivalent annotations, the FR smoke set (including the hand-written lines) and the `gt_suspect` adjudications are builder judgement. [A-22, A-37]
- The A23 synthetic stress set is model-generated, with 50 lines hand-checked. [A-23]
- All of them are used only in evaluation, published with provenance, and reported separately from GT-based metrics.

**README disclosure** (draft):

> _Labelled data at inference:_ none. The matcher sees only the selected library, an enrichment derived from that library, and a glossary of domain terms, each tagged with its source: a standard, the library, or a dev-set error. Ground truth is read only by the evaluation code, and a CI test enforces that. We froze a section-based split of 139 dev and 113 lockbox items before any model call. EN and FR twins of an item always sit on the same side. Every prompt, glossary and threshold decision was made on dev only. The single-pass baseline and the final system were each scored once on the lockbox, at a tagged commit; the scores and commit are in `eval/lockbox_log.md`, and the runs are committed for replay. The lockbox is a same-project, section-held-out holdout: the author profiled the whole dataset before freezing the split, so it is blind to tuning but not to exploration; only an unseen BoQ is a blind test. Any rule that reflects a convention seen there is listed, and lockbox precision is also reported without the lines it affects. The 13 "material with no library equivalent" annotations and the FR-library smoke set are the author's own labels. They are used only for evaluation and are reported separately. [A-5, A-6, A-36]

---

## 11. Amendment index (pre-registration v2)

Every entry below was merged before any model call on the data. Source texts: A4–A29 from the v3 approval sheet and A30–A52 from the external review, as resolved by the owner decisions recorded in `DESIGN.md` §16; the full texts are kept with the planning records, outside this repository. [A-30]

| Id                     | Sections changed here                                                                                                                                                                                                     |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A1                     | §1 latency (N_routed = 282)                                                                                                                                                                                               |
| A4                     | §6 order of work, ledger                                                                                                                                                                                                  |
| A5                     | §1, §2 rules and lockbox meaning, §3 paired tests, §4 bands, §10 disclosure                                                                                                                                               |
| A6                     | §2 lockbox-once rule, §5 rungs, §9 G4, §10 disclosure                                                                                                                                                                     |
| A7                     | §1 H₂₆₅ as diagnostic, §8 `--strict`                                                                                                                                                                                      |
| A8, A12, A39, A43, A50 | §7 failure injection; §1 concurrency (A12); §4 vetoes and D5a (A43)                                                                                                                                                       |
| A10                    | §7 FR smoke set floor                                                                                                                                                                                                     |
| A11, A48               | §8 RQ5; §7 structural headers (A48)                                                                                                                                                                                       |
| A15                    | §5 B0, candidate inclusion, size tiers; §7 parser fixtures                                                                                                                                                                |
| A16                    | §9 G5 traced cases                                                                                                                                                                                                        |
| A17                    | §4 cross-model votes in v                                                                                                                                                                                                 |
| A18                    | superseded by A31, except the one-sided CP bound (§3)                                                                                                                                                                     |
| A19, A33               | §8 leakage guard and allowlist, §8 RQ10, §10 enrichment and models                                                                                                                                                        |
| A20                    | §6 response table                                                                                                                                                                                                         |
| A21                    | §1 hit@1/2                                                                                                                                                                                                                |
| A22                    | §4 selection stability and `gt_suspect`; §6 field                                                                                                                                                                         |
| A23                    | §7 synthetic stress set (deferred)                                                                                                                                                                                        |
| A24                    | §5 CandidateProvider and recall@k on dev (deferred)                                                                                                                                                                       |
| A25                    | §9 deferred list                                                                                                                                                                                                          |
| A26                    | fallback sentences superseded by A33                                                                                                                                                                                      |
| A27                    | §4 `oris select --target`                                                                                                                                                                                                 |
| A29                    | §5 B1 fit, scorer check; §10                                                                                                                                                                                              |
| A30                    | status note, terms, §8 RQ1/RQ5, this index                                                                                                                                                                                |
| A31                    | §1 targets, §2 risk note, §3 claim bound and operating characteristic, §4 score, thresholds and selection                                                                                                                 |
| A32                    | §1, §2, §9 fixed-time gates and the deferred list (nothing cut)                                                                                                                                                           |
| A34                    | §3 bound computation, §8 scorer contract and fixtures                                                                                                                                                                     |
| A35                    | §4 signal (b), §7 FR smoke fixture                                                                                                                                                                                        |
| A36                    | §2, §8 committed runs, audit, RQ1/RQ6/RQ11                                                                                                                                                                                |
| A37 (kept parts only)  | §5 `[retrieval]` extra and size tiers, §7 hand-written FR lines                                                                                                                                                           |
| A38                    | §2 freeze, §7 policy resolution, §8 RQ9                                                                                                                                                                                   |
| A42                    | §6 keep rule, §7 determinism                                                                                                                                                                                              |
| A44                    | §1 price table, §8 contract tests                                                                                                                                                                                         |
| A45, A52               | §1 attribution and gated runs, §8 audit records, RQ6–RQ9; D-02 and D-18 (A52) in §8 and §10                                                                                                                               |
| A46                    | §4 vote count v, k = 2 default                                                                                                                                                                                            |
| A47                    | §5 baseline table                                                                                                                                                                                                         |
| A49                    | §8 CI matrix, RQ2                                                                                                                                                                                                         |
| Not adopted as cuts    | A37's cuts, A40, A41: A22, the calibration battery, A23, A24, A25, async jobs (with the UI), AIMD (after a recorded 429) and the windowed breaker rule stay, after must-haves; the consecutive-failure breaker is G3 work |
