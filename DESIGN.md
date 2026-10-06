# Design: ORIS material matching service

> **Status: pre-registration v2, merged before any model call (v1 = tag `prereg-v1`, commit `bc5c8a2`); from the first model call on, only §16 and §17 change.** [A30]
>
> v1 fixed the items below before any language-model call was made on this data. v2 revises them, still before any call, by merging amendments A4–A52 into the sections they change; each changed paragraph or table row ends with its amendment id, e.g. [A12]. The tag and the git history are the proof. [A30]
>
> - the split;
> - the metrics;
> - the acceptance rule;
> - the operating-point candidates and the selection rule;
> - the lockbox procedure;
> - the decision semantics.
>
> From the first model call on, this document changes only in two append-only places: §16 _Amendments_ (dated: what changed, why, and what results existed at the time) and §17 _Results_ (dated, never edited after writing). [A30, A31]
>
> - Frozen split: `eval/split_v1.json`, SHA-256 `c9a6d1f0aaafdd9f3b7499bf50f59f786f00270c3350e7d5cad7982b855039bd`.
> - Companion documents: `docs/evaluation-protocol.md` (full statistical protocol), `docs/data-analysis.md` (verified data findings), `docs/ui-spec.md` (operator UI), and `docs/requirements-traceability.md` (every brief requirement mapped to its evidence). Where a companion document disagrees with this file, this file wins. [A30]

---

## 0. Front page

**Terms.** _Dev_ is the side of the frozen split that the model may see before the freeze: 139 labelled items per language. _Lockbox_ is the held-out side: 113 labelled items per language, mostly whole L1 sections, sent to the model once, after `eval-freeze`. An item and its translation always sit on the same side. [A30]

**Approach.**

- Claude Haiku 4.5, pinned, reads each line with its section path and the whole selected library rendered as a coded tree, and answers with row codes, never free-text labels.
- Deterministic code gates headers and confirmed services, vetoes matches whose extracted attributes conflict, checks that the quoted evidence is in the line, and refuses any code that is not a row of the loaded library.
- Each routed line gets a score from independent checks: agreement of k = 2 passes over two fixed renderings, attribute agreement judged from the line side, and self-reported confidence. One nested threshold on that score decides `matched`; everything below it is `needs_review` with a reason code.
- Every attempt is logged with OTel GenAI field names, and the committed runs replay the submitted outputs byte for byte at $0. [A31, A35, A36, A43, A45, A46]

**What I try first.**

1. A thin slice: reader, coded-tree renderer, a Haiku adapter that records every call, the closed-world validator, the scorer.
2. B0 (rules only) and B1 (TF-IDF) on dev; then B2 (one Haiku pass) on a stratified 40-line dev slice, then on all dev.
3. E-02: the abstention signals and the nested-threshold selection, on cached votes. Other arms run only when the dev error taxonomy triggers them.
4. A submittable, tagged v0.1 by Tue 6 Oct 12:00. [A4, A32]

**What I will cut or defer.** Nothing is cut in advance. Work beyond the must-haves is deferred and ordered (§8): first the dev diagnostics, the synthetic FR stress set, retrieval recall@k, the local-model benchmark and the cross-model voter if triggered, all before the freeze; then the UI with async jobs, a Phoenix export spike and Docker. A gate that hits its time cap ships what is green, and §8's cut order applies. The freeze is Thu 8 Oct 12:00 at the latest. [A32]

**How the number is trusted.**

- The split, the candidates, the selection rule and the form of the claim were committed before any model call (`prereg-v1`, then this v2).
- Selection uses dev only: the loosest threshold with matched precision ≥ .95 in each language and ≥ 40 matched dev lines in each.
- The lockbox runs once per frozen rung. The claim is the point precision with its one-sided 95% exact (Clopper–Pearson) lower bound, "certified" only if that bound is ≥ .90. Every other interval is display only.
- The scorer is one standard-library file ORIS can run on its own labels, and the evidence behind both output files is committed. [A31, A34, A36]

---

## 1. TL;DR and rubric map

**What it does.** For every line of a Bill of Quantities, the service returns exactly one decision:

- `matched`, with a `type / usage / subtype` triple copied verbatim from the selected library;
- `not_a_material`;
- `needs_review`.

Matching is done by Claude Haiku 4.5, pinned to `claude-haiku-4-5-20251001`, behind an LLM port that accepts only small models from an allowlist. It reads the line, its section context and the whole library rendered as a coded tree, and returns row codes. [A33]

Deterministic code then does four things:

- gates structurally detected headers and confirmed services; [A11, A48]
- vetoes matches whose extracted attributes conflict with the row (strength class, exposure class, CEM type, recycled content, process, EWC code…);
- rejects an answer whose quoted evidence is not in the line; [A43]
- validates that every emitted triple exists in the loaded library.

A pre-registered nested-threshold rule over independent agreement signals turns the model's answer into `matched` or `needs_review`. Every line carries an audit trail, and the committed runs replay the submitted outputs at $0. [A31, A36]

**Targets**

| Target                                     | Value                                                                                                                                                                                 |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Matched precision (all three levels right) | ≥ 90% **in each language** on the lockbox, reported with its one-sided 95% exact lower bound; dev selection requires ≥ 95% in each language with ≥ 40 matched dev lines in each [A31] |
| Coverage                                   | Maximise, subject to the precision target                                                                                                                                             |
| Material lines decided `not_a_material`    | **0**                                                                                                                                                                                 |
| Cost                                       | ≤ $2 per 100 lines                                                                                                                                                                    |
| Latency                                    | ≤ 2 s per line, averaged                                                                                                                                                              |
| Library                                    | Nothing hard-wired to one library; the policy is resolved per (model, library) [A38]                                                                                                  |
| Model                                      | Small models only: Haiku 4.5 pinned, fallbacks from the allowlist, never a larger model [A33]                                                                                         |

**Rubric map.** This table is also the project's grader: each gate in §14 is checked against it.

| What ORIS evaluates                                                                                 | Where it is answered | Evidence file                                                                                                                                               | Gate                                 |
| --------------------------------------------------------------------------------------------------- | -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| Understanding of the data: where hard lines concentrate, by category and level; EN vs FR; surprises | §4, §6               | `docs/data-analysis.md`, `docs/evaluation.md` (error taxonomy)                                                                                              | G0 / G2 [A32]                        |
| Baseline, how much better, how confident                                                            | §7, §10              | `eval/experiments.md` (rendered from `eval/experiments.jsonl`), `docs/evaluation.md` (like-for-like B0–B3 table, one-sided exact lower bound, paired tests) | G1 / G4 [A31, A32, A47]              |
| Explicit trade-offs: precision vs coverage vs review load vs cost vs latency                        | §2, §5, §7, §10.6    | risk–coverage curves, per-threshold trade-off table, decision register                                                                                      | G2 [A32]                             |
| Engineered: boundaries, tests, failures, reproducibility                                            | §9, §11              | `tests/`, `eval/check_requirements.py`, `runs/submission/<run_id>/manifest.json`, CI on Linux and Windows                                                   | G1 / G3 [A32, A36, A49]              |
| Defending choices live                                                                              | §12, §15             | rehearsal script, `oris explain`, `oris select`, `oris doctor`                                                                                              | G5 [A32, A33]                        |
| Design document: approach, first steps, cuts                                                        | §0, §5, §7, §8       | this file                                                                                                                                                   | G0 [A30]                             |
| Service: CLI, API, tests, outputs on both files                                                     | §9, §11              | `output/improved_output_{en,fr}.csv`                                                                                                                        | G1 (B0 placeholders) / G4 [A32, A49] |
| Evaluation script reusable on any reference                                                         | §10.1, §11.7         | `eval/score.py` (standard library only)                                                                                                                     | G1 [A34]                             |
| README: run, weaknesses, more time                                                                  | §12                  | `README.md`                                                                                                                                                 | G1 (provisional) / G5 [A32]          |
| Optional UI                                                                                         | §13                  | `/ui`                                                                                                                                                       | G6 [A32]                             |

---

## 2. Problem statement and objective

**The problem.** A client's BoQ lists hundreds of free-text lines in English or French. ORIS needs each material line mapped to one leaf of a three-level reference library, because each leaf carries its own environmental data (CO₂, availability, sourcing). Today, people do this by hand. Auto-fill was switched off because a wrong auto-filled line is worse than an empty one: a valid-looking but wrong triple silently attaches the wrong CO₂ factor to a project, and nobody notices.

**What each decision costs**

| Outcome                                    | Consequence                                                                   | Who pays                                                         |
| ------------------------------------------ | ----------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| Correct `matched`                          | Line auto-filled                                                              | Nobody; this is the time saving                                  |
| Wrong `matched` (valid triple, wrong leaf) | Wrong CO₂ factor reaches the client's project and possibly an audit report    | ORIS's credibility. This is why precision is the hard constraint |
| `needs_review` on a material line          | A human checks it, helped by the suggestions                                  | An engineer's minute                                             |
| `not_a_material` on a material line        | The material **disappears** from the project, so its carbon is under-reported | Worst case: silent and unrecoverable. Target 0                   |
| `not_a_material` on a header or service    | Skipped correctly                                                             | Nobody                                                           |

**Objective.** I treat precision as a constraint and coverage as the quantity to maximise:

- **maximise** match coverage;
- **subject to** matched precision ≥ .90 per language;
- material lines decided `not_a_material` = 0;
- cost ≤ $2 / 100 lines;
- mean latency ≤ 2 s / line;
- and the system emits only triples that exist in the loaded library.

**Business translation (assumptions, to be replaced by ORIS's own numbers).**

| Activity                                                                 | Assumed time                                                                                                     |
| ------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------- |
| Manual entry of a line                                                   | about 50 s                                                                                                       |
| Review of a `needs_review` line with a pre-filled top-1/top-2 suggestion | about 15 s                                                                                                       |
| Skimming a skipped line                                                  | about 2 s                                                                                                        |
| Wrong auto-fill                                                          | priced at w × manual entry, because it is found late or never; w is unrelated to the vote count k of §10.6 [A46] |

The report converts each candidate operating point into **engineer-minutes saved per 300-line BoQ**, so the precision/coverage choice can be read in ORIS's own units. These figures are labelled assumptions, never measurements, and they appear in the report only, never in the UI. [A14]

**Scope.** In scope:

- CSV and XLSX BoQs with the five input columns (matched by name, with header aliases);
- any library CSV with the three label columns;
- CLI, HTTP API and an operator UI.

**Non-goals:**

- PDF BoQs (the brief mentions them, but none are provided);
- editing decisions in the UI;
- multi-tenant authentication;
- training a model.

---

## 3. Requirements

These are condensed from 87 atomic requirements. The full matrix, with the evidence and verification method for each, is in `docs/requirements-traceability.md`. [A3]

### 3.1 Functional

| ID  | Requirement                                                                                                                                                                                                              | Verified by                                                                                                 |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------- |
| F1  | Exactly one decision per input line. Same row count and same order. Decision ∈ {matched, not_a_material, needs_review}                                                                                                   | row-conservation property test; `check_requirements.py` RQ2–RQ3                                             |
| F2  | `matched` ⇒ the triple is a row of the **selected** library, emitted verbatim, trailing spaces included                                                                                                                  | closed-world property test; RQ4                                                                             |
| F3  | `not_a_material` only for lines with no material: headers, empty rows, labour, services                                                                                                                                  | decision truth table; RQ5 [A48]                                                                             |
| F4  | Material lines with no library equivalent → `needs_review`, labels blank                                                                                                                                                 | decision truth table; annotation-based metric                                                               |
| F5  | CLI: `oris match --input … --library … --output …` on any BoQ with the same columns. `match` is the default command, so the brief's literal flags work as `uv run oris --input …` and `python -m oris_matcher --input …` | CLI tests on synthetic and real files; literal-command test [A38]                                           |
| F6  | HTTP API: batch of lines in, decisions out, through the same async `MatchService` and the same pure batch planner as the CLI                                                                                             | API contract test; CLI/API parity test: same decisions and request hashes under ReplayLLM [A50]             |
| F7  | Library selectable at runtime; nothing hard-wired to the global library; the policy is resolved per (model, library), and an unknown pair runs the strictest threshold                                                   | run on the FR library under FakeLLM; grep guard for global strings in `src/`; policy-resolution tests [A38] |
| F8  | Offline enrichment script writes `semantic_description` for any library                                                                                                                                                  | script test; service runs on unenriched libraries too                                                       |
| F9  | Outputs for both input files against the global library                                                                                                                                                                  | `output/improved_output_{en,fr}.csv` + RQ1–RQ11 [A36, A49]                                                  |
| F10 | Scorer: any output vs any reference with the three label columns; lenient on input, strict on our own files                                                                                                              | scorer fixture tests (§10.1), including synthetic FR-library references [A34]                               |
| F11 | Optional UI: upload CSV/XLSX, run, table, `needs_review` easy to spot                                                                                                                                                    | `docs/ui-spec.md` U1–U4, with U4 as in §13 [A14]                                                            |

### 3.2 Non-functional

| ID  | Requirement       | Target and measure                                                                                                                                                                                                                                              |
| --- | ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| N1  | Matched precision | ≥ .90 per language on the lockbox (point estimate), with the one-sided 95% exact (Clopper–Pearson) lower bound reported; "certified" only if that bound is ≥ .90 [A31]                                                                                          |
| N2  | Coverage          | maximised: C₂₅₂ headline, plus C₂₆₅; H₂₆₅ is a diagnostic only (§10.1) [A7]                                                                                                                                                                                     |
| N3  | Safety            | false `not_a_material` on material lines = 0                                                                                                                                                                                                                    |
| N4  | Cost              | ≤ $2 / 100 lines, **measured** from API `usage`, retries included, attributed per line (§9.6); gated only on live, uncached manifests [A45]                                                                                                                     |
| N5  | Latency           | ≤ 2 s / line on a cold live run. The gate is wall-clock ÷ LLM-routed lines (N_routed = 282 on these files); the headline is wall-clock ÷ all rows; per-line attributed latency is reported as conservative; the rate-limit tier is recorded [A1, A12, A39, A45] |
| N6  | Failure handling  | timeouts, rate limits, 5xx, truncation, malformed output, missing, duplicate or swapped items, replay misses → no lost line, no silent mislabel. Throttling is not a failure [A8, A39, A43, A50]                                                                |
| N7  | Auditability      | per line: requested and served model, prompt version, the line's raw response, call ids and provider request ids, attributed cost and latency, the rule that fired, the reason code [A33, A36, A45]                                                             |
| N8  | Reproducibility   | run manifest; the committed submission runs replay to byte-identical output CSVs on Linux and Windows [A36, A49]                                                                                                                                                |
| N9  | Modularity        | parsing, matching, validation and decision logic in separate modules with no cross-imports into the pure domain                                                                                                                                                 |
| N10 | Model size        | Claude Haiku 4.5, pinned; fallbacks only from the small-model allowlist, enforced at the port; a local model of 8B parameters or fewer is admitted [A33]                                                                                                        |

### 3.3 Ambiguities in the brief, and the interpretation I chose

| Ambiguity                                                 | My interpretation, and why                                                                                                                                                                                                                                                                                                |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Coverage denominator, "material lines"                    | **Report three numbers:** C₂₅₂ (correct ÷ labelled lines, reproducible from the labels alone), C₂₆₅ (correct ÷ all material-bearing lines, the literal definition, ceiling .951), H₂₆₅ (correct matches + correct reviews ÷ 265, a diagnostic printed next to review load, never used for selection). Headline: C₂₅₂ [A7] |
| Specialist products with no equivalent                    | `needs_review`, not `not_a_material`. They carry a material, and the sample output agrees (SBS membrane → `needs_review`)                                                                                                                                                                                                 |
| Lines with both labour and supply ("cut, bent and fixed") | A line that supplies a material **is** a material line. The sample output matches such a line                                                                                                                                                                                                                             |
| Precision: pooled or per language                         | Per language. "Both languages count"                                                                                                                                                                                                                                                                                      |
| Latency definition                                        | Mean wall-clock per line on a cold live run: over LLM-routed lines (the gate, stricter) and over the whole run (headline, ÷ all rows). Per-line attributed latency (§9.6) is shown as conservative under concurrency. Warm, cached and replay timings are reported separately. Per-call p50/p95 as diagnostics [A12, A45] |
| Cost scope                                                | All attempts, retries and failures included, ÷ all rows, and attributed to lines by the §9.6 rule. Cache hits and replays copy the source cost; they are never $0 evidence. One-off enrichment reported separately [A45]                                                                                                  |
| "Accuracy at each hierarchy level"                        | Cumulative prefixes (type, type+usage, triple), because usage strings repeat across types. Computed over matched lines **and** over all labelled lines using the suggested row                                                                                                                                            |
| Labels on `needs_review` rows                             | Canonical label columns blank, as in the sample. The model's suggestion goes in extra `suggested_*` columns after the sample's columns, followed by `library_row_id` and `call_ids` [A36]                                                                                                                                 |
| Blank subtype                                             | `''` is a real leaf. Exact equality, so `''` vs `C30/37` is wrong both ways                                                                                                                                                                                                                                               |
| Using the EN file to help FR                              | Not allowed at inference: the live session has one file. EN↔FR agreement is only a reported diagnostic                                                                                                                                                                                                                    |

---

## 4. Data understanding `[Rubric: Data]`

All numbers below were recomputed from the CSVs (UTF-8, blanks kept as `''`, no stripping). Charts and the full breakdown are in `docs/data-analysis.md`.

### 4.1 Shape of the data

```
319 rows per language (EN and FR share Item No. order and quantities)
├── 37 headers ............ 10 L0 ("0".."9") + 27 L1 ("00.01."); empty Unit and Qty; never labelled
└── 282 items (NN.NN.NNNN.)
    ├── 252 labelled ...... 203 full triples + 49 blank-subtype leaves; 244 distinct triples
    └── 30 blank in GT
        ├── 16 services / temporary works   (9 with LS/day/month units; 7 with pcs/m/m²)
        ├── 13 materials with no global-library equivalent  (pot bearings, PMMA, PVC-P, PP drains…)
        └──  1 ambiguous (soffit formwork incl. falsework)
```

- Every one of the 252 GT triples exists **verbatim** in the global library, including a canonical trailing space (`bitumen for coating `).
- None exists in the French library. The two libraries share **no material type**; only the `Custom` placeholder overlaps (1 type, 2 triples).
- The global library has 342 rows, 30 types, 108 type+usage pairs and 70 blank-subtype leaves.
- The FR library has 70 rows, 24 types and 39 pairs.

### 4.2 Where the hard lines concentrate

**By hierarchy level.** Usage is the bottleneck. Measured with a TF-IDF top-1 match over the 342 rows:

| Language | Type | Type+usage | Triple | P(usage ✓ \| type ✓) | P(subtype ✓ \| type+usage ✓) |
| -------- | ---- | ---------- | ------ | -------------------- | ---------------------------- |
| EN       | .722 | .524       | .405   | .725                 | .773                         |
| FR       | .560 | .210       | .159   | **.376**             | .755                         |

The subtype is roughly flat across languages once usage is right. Usage collapses in French.

**By category**

| Family             | Lines    | Why it is hard                                                                                                                                                                                    | Design response                                                             |
| ------------------ | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| Concrete           | 75 (30%) | The strength class is literal in 73/73 class-subtyped lines, but **"concrete" appears in only 3/75 EN lines**. About 14 usages (piers, piles, footings, slabs…) stay open once the class is known | Extract strength as a hard veto; the LLM decides usage with section context |
| Aggregates         | 32       | 15 usages; the subtype does not narrow them                                                                                                                                                       | LLM + section context                                                       |
| Asphalt            | 28       | About 21 subtypes per course, but family, HMA/WMA and RAP % fully determine the leaf (28/28 in both languages)                                                                                    | Extractors for process and RAP/AE %, in EN and FR codes                     |
| C&D waste          | 24       | The EWC code is literal in only 3/24; hazardous vs non-hazardous must be inferred                                                                                                                 | LLM; EWC extractor where present                                            |
| Excavations        | 13       | Quality grade inferred from rock class or weathering                                                                                                                                              | LLM; known weakness                                                         |
| Cement / Admixture | 12 / 8   | CEM types are 100% literal; admixture synonyms (PCE → plasticiser, early-strength → "harderning accelerator")                                                                                     | CEM extractor; sourced glossary                                             |

**Specific confusers seen in the data:**

- piers vs piles;
- base vs binder course;
- quick vs slow visco-elastic;
- pile caps → footings;
- segmental rings (tunnel concrete vs precast);
- "RC", which means _recycled concrete_ in aggregate lines but _rock class_ in tunnel lines.

### 4.3 English vs French

French is a **localised rewrite, not a translation**.

- **Codes change:** AC → GB/BBSG, PA → BBDr, RA → AE, WMA → _tiède_, 0/32 → 0/31,5, crushed stone → GNT, ready-mix → BPE.
- **Some technical values change too:** the binder grade 50/70 becomes 35/50 on 03.03.0010.
- **False friends:** _couche de fondation_ means sub-base, and French _pile_ is an English bridge _pier_.

The lexical bridge to the English library is almost gone:

- 3.17 shared words (4+ letters) per EN line vs 0.37 per FR line;
- 168 of 252 FR lines share no such word with the library.

EN and FR also fail on **different** lines: 72 lines are right only in EN, and 10 only in FR, where the French wording is more explicit. §6 describes how the design handles this.

### 4.4 Conventions in the labels I follow (not errors)

- **09.01 block:** all 9 in-situ building lines ("columns", "transfer beams") are labelled `General Ready mixed`, not an element-specific usage.
- **Sleepers:** precast sleepers are labelled under `Prefab`, although an identical usage string exists under `Concrete`.
- **Limestone filler** → `Limestone / for all usage`, not `Filler`.
- **Lines starting with an activity verb** ("Break out", "Remove", "Dredge", "Drill and blast"; 34 lines) are **materials** (waste, spoil), not services.
- **Mixed parents:** where a type+usage pair has both a blank-subtype row and specific siblings, the GT never picks the blank (0/80 lines).

These conventions are tagged `gt_convention` in the error taxonomy, so they are not mistaken for model errors, and no item-specific rule encodes them.

### 4.5 Surprises

1. **The ground truth is a synthetic sweep.** It has 244 distinct triples over 252 lines, and 238 of them appear exactly once. Together they cover 71% of the library at about one line per leaf. Examples drawn from the labels would transfer conventions rather than answers, and could not be used on the live FR library, so I use none (D-02, §10.8). [A52]
2. **No lexical score gives a usable confidence.** The best TF-IDF operating point at P ≥ .90 reaches 4% coverage in EN. That point is overfit: held-out precision is .76–.82. FR never reaches .90.
3. **13 blank-labelled lines carry real materials.** A naive "blank GT = not a material" reading would train the system to delete materials.
4. **The unit rule is almost free precision.** Empty/LS/day/month units flag 46 rows and drop 0 labelled lines in either language. It still cannot be a hard rule on an unseen file (hire items can carry a material).
5. **The L0 headers are bare digits (`0`..`9`).** A header test written as "`NN.` shape" misses all ten of them.
6. **Two diameter characters** (∅ in the FR library, Ø in the BoQ) and **two decimal conventions** (66 decimal commas vs 4 dots in the FR BoQ; dots in the library).

### 4.6 Data traps and the tests that guard them

| Trap                                                                                 | Guard                                                                                                                                                                         |
| ------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Blank cells read as NaN                                                              | `csv` module, raw string cells, no NaN conversion; the scorer counts `nan`-like literals as blank, with a printed count [A34, A49]                                            |
| cp1252 decodes UTF-8 silently into mojibake                                          | UTF-8 (BOM tolerated) is always tried first; only on failure is cp1252 decoded, with a warning and `encoding: cp1252` in the manifest; round-trip test on accented rows [A15] |
| Trailing spaces in canonical labels                                                  | never strip; key everything by row id; test with `'bitumen for coating '`                                                                                                     |
| Usage strings repeat across types (one repeats under 7 types)                        | usage always qualified by type; per-level accuracy cumulative                                                                                                                 |
| Header rows with dotless L0 IDs, or an unseen numbering scheme                       | structural header detection (§9.1); G1 regex test on the real `0`, `00.01.` rows; renumbered and code-less fixtures; parent-path derivation test [A48]                        |
| Decimal comma, Ø/∅/⌀                                                                 | normalisation unit tests (matching only, never output)                                                                                                                        |
| Every provided CSV is CRLF with no BOM; OS-default line endings and encodings differ | `csv` writer with `\r\n`, explicit encodings everywhere (ruff PLW1514), golden replay on Linux and Windows [A49]                                                              |
| A reference file with a renamed key column, duplicate codes or no key                | lenient scorer join with warnings; `--key` and `--join row-order`; one fixture each (§10.1) [A34]                                                                             |

---

## 5. Approach in one page `[README: "What's your approach?"]`

### 5.1 Architecture

```mermaid
flowchart LR
  subgraph Adapters_in [Driving adapters]
    CLI[Typer CLI<br/>match, the default · score · replay · explain<br/>select · doctor · demo · serve]
    API[FastAPI, async routes<br/>POST /v1/match · /v1/jobs]
    UI[TS single-page UI<br/>served at /ui]
  end
  subgraph Core [Application core]
    SVC[async MatchService<br/>gate → plan batches → call → validate → decide → audit]
    subgraph Domain [Pure domain, no I/O]
      BOQ[boq: lines, structural headers, sections]
      BAT[batching: plan_batches]
      LIB[library: row ids, structure]
      NORM[normalize]
      ATTR[attributes: extractors]
      DEC[decision: gates + nested threshold]
    end
  end
  subgraph Adapters_out [Driven adapters]
    WRAP[LLM wrapper<br/>retry · budget · cost · record · cache]
    LLMP{{LLM port}}
    ANT[AnthropicLLM<br/>Haiku 4.5, pinned]
    OAI[OpenAIChatLLM<br/>gpt-4o-mini, fallback]
    FAKE[FakeLLM<br/>fault injection]
    REP[ReplayLLM<br/>calls.jsonl, $0, fails closed]
    CAND{{CandidateProvider}}
    WHOLE[WholeLibrary<br/>default]
    HYB[HybridRetriever<br/>optional retrieval extra]
    IO[io: CSV/XLSX reader,<br/>CSV writer, JSONL audit]
  end
  CLI --> SVC
  API --> SVC
  UI --> API
  SVC --> Domain
  SVC --> WRAP
  WRAP --> LLMP
  LLMP --> ANT
  LLMP --> OAI
  LLMP --> FAKE
  LLMP --> REP
  SVC --> CAND
  CAND --> WHOLE
  CAND --> HYB
  SVC --> IO
```

The domain is pure: no network and no file access. That makes every decision rule unit-testable, and lets the same rules re-decide a recorded run with no API calls.

Retry, budget, cost, recording and the response cache live in one wrapper outside the adapters, so the Anthropic and OpenAI adapters cannot drift apart (§11.9). Candidates come through a `CandidateProvider` port whose default is the whole library (D-01); the retrieval dependencies sit in an optional `[retrieval]` extra, so the core install and the Docker image stay light. [A24, A33, A37, A44, A50]

### 5.2 The path of one line

1. **Parse.** Read the row by column name: UTF-8 first, cp1252 only as a recorded fallback. Keep the raw strings. Detect headers structurally and derive the section path over the whole input, before any chunking. [A15, A48, A50]
2. **Gate G1.** Empty Unit **and** empty Qty **and** a header (a configured code pattern, or a code that is a strict prefix of the next code) → `not_a_material` (`HEADER`). A fully empty row → `not_a_material` (`EMPTY_ROW`). No model call. [A48]
3. **Batch.** `plan_batches` (pure) groups up to 10 contiguous lines under the same nearest header, in input order. The CLI, the API and the UI all use it. [A48, A50]
4. **Call.** k = 2 structured-output calls to Haiku 4.5 per batch: pass 1 on the canonical library rendering, pass 2 on one fixed alternative rendering, each with its own cached prefix. The prompt holds the cached system blocks (decision vocabulary, glossary, the whole library as a coded tree) and the batch as one JSON object marked as data. The first batch of a run goes alone; the rest then fan out. [A43, A46]
5. **Validate**, in this order:
   - `stop_reason` (refusal, truncation);
   - strict schema validation (ranges, lengths, enums);
   - every line id answered once, with codes that exist (case-sensitive), mapped back to library rows;
   - the quoted evidence is a substring of the line;
   - extracted line attributes are checked against the row's attributes. [A43, A44]
6. **Decide.** The first rule that fires in the decision table (§9.5) wins: gates, failures, no-equivalent, evidence, vetoes, then the frozen threshold, which is resolved from (model, library) (§10.6). [A31, A38, A43]
7. **Audit.** One output row; one audit record (the rule that fired, the reason code, suggestions, the line's raw response, its call ids); one call record per API attempt (§9.6). [A36, A45]

### 5.3 Decision register: what was decided by data

The choices below needed no experiment, because the data already settles them. The ones that do need evidence are pre-registered in §7.

| #    | Decision                                                               | Alternatives considered                                                               | Deciding evidence                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | Cost / latency effect                                                                                                                                                                                                                                                                                                              |
| ---- | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| D-01 | **Show the whole library to the model; no top-k retrieval by default** | embedding top-k; TF-IDF top-k; hybrid retrieval                                       | Lexical recall@20 is .869 EN but **.690 FR**, and ≤ .47 on Aggregates, C&D, Excavations and Steel. A shortlist caps accuracy before the model is even called. Each library's rendered prompt is measured with `count_tokens` and the value is recorded in the manifest; the tier each library falls in is reported from that measurement, not assumed. With the whole library, candidate inclusion is 1.0 by construction. Tiers: ≤ 30k tokens, whole library; 30k–150k, whole library with a printed warning and a cache check; > 150k, an explicit error naming the retrieval switch; never truncate. Retrieval is a `CandidateProvider` port with `WholeLibrary` (default) and `HybridRetriever` (§10.5) | Whether each rendered prompt reaches Haiku's 4,096-token cache minimum is measured and reported, not assumed [A15, A24, A28, A37]                                                                                                                                                                                                  |
| D-02 | **No examples taken from the labels**                                  | GT few-shot; retrieval memory                                                         | Dev examples would not leak lockbox answers under the frozen split. They are still not used: (1) the live run uses the FR library (0 shared triples), so global examples would have to be switched off and the scored system would differ from the live one; (2) with 238/244 singleton triples, examples transfer conventions, not answers, and those conventions enter as glossary entries tagged `dev_error`                                                                                                                                                                                                                                                                                             | — [A52]                                                                                                                                                                                                                                                                                                                            |
| D-03 | **Header gate G1, structural**                                         | empty Unit only; shape only                                                           | Empty Unit **and** empty Qty **and** (the Item No. matches a configured pattern, by default `^\d{1,2}$\|^\d{2}\.\d{2}\.$`, **or**, after normalising `.`, `-` and spaces, it is a strict prefix of the next non-empty code) → 37/37 headers, 0 false positives. A fully empty row → `EMPTY_ROW`. Any other row with empty Unit and Qty degrades to `needs_review` (`HEADER_UNCONFIRMED`), never to a silent skip                                                                                                                                                                                                                                                                                            | 37 lines at $0 [A48]                                                                                                                                                                                                                                                                                                               |
| D-04 | **Strict non-material gate G2**                                        | trust the model's "non-material"; two votes                                           | The model says non-material **and** the unit is a service unit (LS, Ft, day, j, month, mois, week, h, Ens, Forfait, FF, PM…) **and** no hard attribute was extracted **and** the line contains no supply marker from `config/supply_markers.yaml` (supply, fourniture, fourni, provide, deliver…). A measured unit (t, m³, m², m, kg, pcs, U, l) can never auto-skip                                                                                                                                                                                                                                                                                                                                        | Accepted cost: 7 of 16 services go to review (they carry pcs/m/m² units). Benefit: no line with a measured unit can be auto-skipped. Residual risk, measured by fixture: a material priced in a service unit, with no extracted attribute and no supply marker, can still be skipped if the model calls it non-material [A11, A48] |
| D-05 | **No library equivalent → `needs_review`**                             | `not_a_material`                                                                      | The brief's definition plus the sample output                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | +13 review lines on this file                                                                                                                                                                                                                                                                                                      |
| D-06 | **Supply + labour is a material**                                      | treat as labour                                                                       | The sample matches "Reinforcing steel B500B, cut and bent"                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | —                                                                                                                                                                                                                                                                                                                                  |
| D-07 | **Closed-world output through row ids**                                | free text + fuzzy snapping; schema enums                                              | `row_id = sha256(type ␟ usage ␟ subtype)[:12]` over raw strings, with a collision test. The model sees short, sorted display codes (`T05.U03.S02`) that keep the hierarchy cue and do not depend on file order. Inside a batch, line transport ids are `L<position>`. The output is always the library's own strings                                                                                                                                                                                                                                                                                                                                                                                        | — [A8]                                                                                                                                                                                                                                                                                                                             |
| D-08 | **Attribute extractors veto; they do not filter**                      | hard candidate filter                                                                 | Re-run on the data: 0/252 GT conflicts (safe), but a median of 342/342 rows survive the filter, so it narrows nothing. It is valuable as a veto and as an agreement signal                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | $0                                                                                                                                                                                                                                                                                                                                 |
| D-09 | **Section path in the prompt**                                         | omit                                                                                  | The L1-section prior alone gets type right 73.3% of the time (leave-one-out), and it explains the 09.01 block. It does not solve usage (24.7%). Still ablated in E-04                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | a few tokens                                                                                                                                                                                                                                                                                                                       |
| D-10 | **Claude Haiku 4.5 behind an LLM port, small models only**             | GPT-4o-mini, Gemini Flash, local ≤ 8B                                                 | Allowed by the constraints; native structured outputs; $1 / $5 per MTok fits the budget by an order of magnitude. Pinned to `claude-haiku-4-5-20251001`. The fallback chain holds only allowlisted small models (§11.3), and no larger model is ever configured. A cross-model voter is a gated E-02 arm (§7.2); a local ≤ 8B adapter is benchmarked once, deferred (§8)                                                                                                                                                                                                                                                                                                                                    | measured [A17, A25, A33]                                                                                                                                                                                                                                                                                                           |
| D-11 | **Temperature 0; the noise floor is measured**                         | assume determinism                                                                    | Temperature 0 is not bit-deterministic on hosted APIs, so I measure the decision flip rate (40 lines run twice). Determinism is guaranteed through replay                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | —                                                                                                                                                                                                                                                                                                                                  |
| D-12 | **One threshold for both languages**                                   | per-language thresholds                                                               | The live run is an unseen file on a different library, and per-language thresholds tuned on about 140 lines would overfit. A per-language optimum is reported as a sensitivity row, as a cost made visible, and is never shipped                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | — [A31]                                                                                                                                                                                                                                                                                                                            |
| D-13 | **Normalise for matching only; output verbatim**                       | clean the strings                                                                     | NFKC, casefold, Ø/∅/⌀ → ø, decimal comma → dot between digits. Trailing spaces are canonical                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | —                                                                                                                                                                                                                                                                                                                                  |
| D-14 | **Three coverage numbers**                                             | one                                                                                   | See §3.3                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | —                                                                                                                                                                                                                                                                                                                                  |
| D-15 | **Suggestions in separate columns**                                    | put the guess in the label columns                                                    | ORIS's scorer must not count guesses. Per-level proposal accuracy stays computable from the CSV alone                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | —                                                                                                                                                                                                                                                                                                                                  |
| D-16 | **Sync API up to 500 lines; async jobs for the UI**                    | sync only; jobs only                                                                  | A whole 320-line BoQ must fit in one documented call (soft limit 100 lines, hard limit 500); the UI needs progress. Jobs are in-memory, single worker, at most 4 queued, results dropped after 1 h, cancelled on shutdown. Jobs are built with the UI in G6 (§8); A40's proposal to drop them was not adopted                                                                                                                                                                                                                                                                                                                                                                                               | both await the same async `MatchService` [A14, A50]                                                                                                                                                                                                                                                                                |
| D-17 | **No LLM framework or gateway**                                        | LangChain, LlamaIndex, LiteLLM or another gateway; instructor                         | Every attempt must be logged with provider-native fields (request id, served model, finish reason, cache tokens). Two thin adapters behind one port and one wrapper do that with fewer dependencies (§11.9)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | — [A44, A49]                                                                                                                                                                                                                                                                                                                       |
| D-18 | **No LLMOps platform dependency**                                      | Langfuse, Phoenix as a server, MLflow, W&B Weave, promptfoo, Inspect AI, LLM gateways | Tracing: `calls.jsonl` with OTel GenAI names, exportable to Phoenix or Langfuse, with no server. Prompt registry: content-addressed `prompt_version` in git. Evaluation: our own scorer, which the brief requires ORIS to reuse. Each rejected tool adds a service, account or dependency to a run that must reproduce from a clean clone, for no change in any graded number. In production the same `gen_ai.*` records go to ORIS's OTel backend                                                                                                                                                                                                                                                          | — [A52]                                                                                                                                                                                                                                                                                                                            |

---

## 6. English and French: one pipeline, language-aware resources

**Principle.** There is no language-specific branch in the decision logic. The language difference is handled by **resources**, and the choice between handling strategies is an experiment (E-01), not an opinion.

**Language-aware resources (all deterministic, all tested):**

- **Normaliser:** decimal comma, Ø/∅/⌀, NFKC, casefold.
- **Unit map:** pcs ↔ U, LS ↔ Ft, month ↔ mois, day ↔ j; aliases in `config/unit_aliases.yaml` (ml → m, m2 → m², m3 → m³, u → U, T → t). Bilingual `SERVICE_UNITS` adds Ens, ensemble, Forfait, F, FF, PM, jour, sem, semaine and h. [A48]
- **Supply markers** in both languages (`config/supply_markers.yaml`: supply, fourniture, fourni, provide, deliver…), used by gate G2. [A11]
- **Extractor lexicons in both languages:**
  - process: WMA / _reduced temperature_ / _tiède_ / _température abaissée_ vs HMA / _chaud_; [A2]
  - recycled content: RAP / RA / AE / _agrégats d'enrobés_;
  - virgin material: _sans AE_, _matériaux neufs_, virgin.
- **Header aliases** for the input columns: _N° article_, _Désignation_, _Unité_, _Quantité_.
- **A small glossary** of domain terms with a source tag on each entry (see §10.8 for provenance rules). False-friend entries cite a source and are checked against the library vocabulary before inclusion. Examples: [A19]
  - GNT = unbound granular mixture;
  - BPE = ready-mixed concrete;
  - GB/BBSG/BBDr/EME = asphalt families;
  - _pile_ (of a bridge) = pier;
  - _couche de fondation_ = sub-base.

**The language pair matters more than the language.** The cross-lingual problem depends on _both_ sides:

| BoQ language → library | Situation                             | Where it occurs           |
| ---------------------- | ------------------------------------- | ------------------------- |
| EN → global (EN)       | monolingual                           | scored file 1             |
| FR → global (EN)       | **cross-lingual**                     | scored file 2             |
| FR → FR library        | monolingual, but a different taxonomy | **the live session**      |
| EN → FR library        | cross-lingual                         | possible for ORIS clients |

This is why translating French lines into English (arm B below) is a trap. It helps FR → global but hurts FR → FR, which is exactly the setting I will be judged on live. Enriching the **library** in both languages (arm C) helps every pair, because it adds the missing side to the library rather than rewriting the line. I therefore pre-state a prior for C. B must beat it clearly to be adopted, and if B wins it is applied only when the BoQ language differs from the library language.

**E-01 arms.** Run on FR dev lines, with EN as a non-regression check.

| Arm | What changes                                                                                                                                                                                                                                                                                                                                                                                        | Extra cost          |
| --- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------- |
| A   | Native text; normaliser, unit map and lexicons only                                                                                                                                                                                                                                                                                                                                                 | $0                  |
| B   | Haiku translates each FR line to EN before matching                                                                                                                                                                                                                                                                                                                                                 | +1 call per line    |
| C   | Deterministic bilingual `semantic_description` per library row (path translation map, acronym glossary, structural nouns, false friends) + glossary                                                                                                                                                                                                                                                 | $0 at inference     |
| D   | `semantic_description` generated by Claude Haiku 4.5, offline, once per library, from library rows (full path + siblings) and the glossary only, never BoQ text or labels; cached by library hash; the file records generator_model, prompt_version and reviewed_by. A larger-model enrichment may appear only as a disclosed ablation and is never committed as the shipped description [A19, A33] | one-off per library |

**Decision rule:** the keep rule of §7.2. C is the default over B unless B passes that keep rule against C. [A42]

**What is reported per language, always:**

- precision, all three coverage numbers, per-level accuracy;
- decision shares, cost, latency;
- the error taxonomy;
- the EN↔FR agreement rate (same decision and same row), as a diagnostic only.

**The French library at the live session.** Because there are no labels for it, I hand-label a **smoke set** against `oris_materials_fr.csv`. Its base of about 25 lines:

- 10 lines from this BoQ that have an exact FR equivalent;
- 8 lines hitting FR-only leaves (pot bearings, fenders, EME/GB with 20/40% AE, ductile pipe, detonating cord);
- about 7 no-match decoys, 3 of them lexically close to a real row and one with a close but wrong usage.

It also holds mixed supply + labour lines, a material priced in LS/Ft, a line with an explicit subtype conflict and a line missing its spec [A10], plus about 20 hand-written French lines (mangled formats, LS-priced materials, close decoys) [A37]. The labels are frozen and hashed before the first run and reported as builder-labelled. [A10, A37]

**Pre-registered rule for a library without labels:** ship the frozen threshold only if the smoke set shows **0** closed-world violations, **0** false `not_a_material`, **0** decoy matches, **and** at least 9 of the 18 base positive lines correctly matched (fixed before the first run). A pass writes the `(model_id, library_sha256)` entry with `certified_by: smoke_A10` into `config/policy.yaml` (§10.6). Otherwise the strictest threshold runs, and the run summary says so. Correct/positive, wrong/matched, decoy matches and false skips are reported as k/n, and FR-taxonomy results are reported apart from FR → global. A fixture checks that signal (b) fires on at least one FR concrete positive. [A10, A35, A38]

**Synthetic French stress set (deferred, §8).** `eval/stress_fr_synth_v1.csv`: about 150 French-library leaves × 1–2 generated lines, plus 30 non-material and 15 no-equivalent lines. It is generated by an allowlisted model family that is not used as a voter; 50 lines are hand-checked; the file is hashed before first use; it never affects thresholds or prompts; it is reported separately as "synthetic stress test (optimistic)". Mangled-format variants (`;`, decimal comma, Latin-1, merged headers) go into the parser tests: Latin-1 fixtures expect a cp1252 decode, a warning and `encoding: cp1252` in the manifest; the others must produce explicit errors or `needs_review`. [A23, A30, A33]

---

## 7. What I will try first `[README: "What will you try first?"]`

### 7.1 Order of work

Each step has a hypothesis, the metric that answers it, and the decision the answer triggers.

| #   | Step                                                                                                                                          | Hypothesis                                                                                                      | Metric                                                                                 | Result → decision                                                                                                                                                                                             |
| --- | --------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0   | **Thin slice**: CSV reader, coded-tree renderer, a minimal Haiku adapter that records every call, the closed-world validator and the scorer   | The end-to-end path works before any other component is built                                                   | the scorer runs on a recorded B2 slice                                                 | Opens the rest of G1 [A4]                                                                                                                                                                                     |
| 1   | **B0 rules only**: G1 headers → `not_a_material`, everything else → `needs_review`; no unit-only skip                                         | Gates alone are safe                                                                                            | F_NM, G1 37/37                                                                         | Establishes the floor and the safety baseline [A15]                                                                                                                                                           |
| 2   | **B1 TF-IDF**, fit on library rows + dev lines only, threshold chosen on dev                                                                  | Lexical matching cannot reach P ≥ .90 held out                                                                  | C₂₅₂ at the §10.6 dev-selected cosine threshold → applied to the lockbox [A47]         | A separate label-free TF-IDF fit on library + all queries (the brief's configuration, never thresholded or reported as B1) reproduces the brief's top-1 (EN .405 / FR .159 ±.01) to validate the scorer [A29] |
| 3   | **B2 Haiku single pass** on a stratified 40-line dev slice, EN + FR, then all 139 dev lines                                                   | The model's raw proposal accuracy is far above B1, but its matches are not ≥ .90 precise without abstention     | Proposal accuracy per level, P(match-all)                                              | If FR proposal accuracy < .70, E-01 is the first triggered arm after E-02 [A4]                                                                                                                                |
| 4   | **E-02 abstention, mandatory**: signals (a), (b), (c) and the nested-threshold selection, by replay, $0                                       | Vote agreement and line-side attribute agreement carry most of the signal; self-reported confidence adds little | Risk–coverage per signal; the dev bar (P ≥ .95 in each language, ≥ 40 matched in each) | Freezes the threshold [A4, A31, A35, A46]                                                                                                                                                                     |
| 5   | **Triggered arms**: E-01, E-03 to E-09, E-02(d)                                                                                               | Each runs only when the §10.7 taxonomy triggers it (for E-05 and E-07, a measured cost or latency miss)         | Keep rule                                                                              | Kept or reverted, logged [A4, A42]                                                                                                                                                                            |
| 6   | **Deferred, after the must-haves, before the freeze**: A22 resampling, calibration battery, A23 stress set, A24 recall@k, A25 local benchmark | Diagnostics; none changes the frozen threshold                                                                  | as specified in §6, §10.5, §10.6                                                       | Reported; see §8 [A32]                                                                                                                                                                                        |

### 7.2 Experiment registry (pre-registered)

**Keep rule (fixed now).** A change is kept only if, on dev:

- summed correct matches (EN + FR) rise by **≥ max(6, 2·√d)**, where d is the number of discordant items between the two runs, pooled across languages and counted per item;
- neither language loses more than 2 correct matches;
- the dev bar of §10.6 still holds;
- F_NM stays **0**, and cost and latency stay inside budget.

Otherwise the simpler or cheaper arm is kept. Both runs are scored by replay where possible. [A42]

Gains below the dev detectable difference of about 10 points are labelled _directional_. Summed EN + FR counts are a selection objective only; any test that combines the languages clusters by Item No. (§10.4). [A5]

**Ledger.** Every configuration scored on dev is a row: id, hypothesis, change, before/after, d, the one-sided sign-test p (a note, not a gate), kept or reverted, prompt version. The experiment runner appends it to `eval/experiments.jsonl`, and `eval/experiments.md` is rendered from that file. `eval/score.py` never writes to it. [A4, A42]

| #       | Question                                                                          | Arms                                                                                                                                                                                                                                         | Decided by                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | Extra cost                        |
| ------- | --------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------- |
| E-01    | How to handle EN vs FR                                                            | A native · B translate · C bilingual deterministic enrichment · D LLM enrichment                                                                                                                                                             | §6 rule                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | B: +1 call/line; D: one-off       |
| E-02    | Which abstention score                                                            | (a) self-reported confidence bucket {≥ 90, 80–89, 70–79, < 70}, with the ordinal candidate gap reported · (b) attribute agreement judged from the line side (§9.3) · (c) vote count v over k = 2 passes on fixed renderings; k = 3 as an arm | the nested-threshold rule (§10.6); k = 3 only if it passes the keep rule and the cold-run latency gate at the tier recorded by `oris doctor`                                                                                                                                                                                                                                                                                                                                | (c) ≈ k × calls [A31, A35, A46]   |
| E-02(d) | Is a cross-model vote worth it                                                    | GPT-4o-mini votes through the fallback adapter (a Gemini Flash voter optional)                                                                                                                                                               | Adopted only if no Haiku-only threshold meets the dev bar (§10.6), or if (d) adds ≥ 3 summed correct matches at equal precision. If adopted, the threshold is defined per available-voter set; a missing voter → `ENSEMBLE_DEGRADED:<SIGNAL or LOW_SIGNAL reason>`, decided by the Haiku-only threshold, validated separately. The service runs Haiku-only whenever only `ANTHROPIC_API_KEY` is set. GPT-4o-mini logprobs are an optional logged feature; null means absent | +1 call per batch [A17, A33, A46] |
| E-03    | How to render the library                                                         | flat rows vs indented coded tree                                                                                                                                                                                                             | Proposal accuracy                                                                                                                                                                                                                                                                                                                                                                                                                                                           | ≈ 0                               |
| E-04    | Is the section path worth it                                                      | with vs without                                                                                                                                                                                                                              | C₂₅₂, type accuracy                                                                                                                                                                                                                                                                                                                                                                                                                                                         | ≈ 0                               |
| E-05    | Batch size                                                                        | 1 vs 10 (contiguous under one header), on 30 dev lines                                                                                                                                                                                       | \|Δ proposal accuracy\| within the noise floor; Δ$/line; Δs/line                                                                                                                                                                                                                                                                                                                                                                                                            | —                                 |
| E-06    | Is the extractor veto worth it                                                    | veto on vs off                                                                                                                                                                                                                               | ΔP vs ΔC₂₅₂                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | $0                                |
| E-07    | Concurrency for latency                                                           | 1 vs a fixed 4; AIMD only if a dev run records a 429                                                                                                                                                                                         | wall-clock ≤ 2 s per routed line on a cold run                                                                                                                                                                                                                                                                                                                                                                                                                              | — [A12]                           |
| E-08    | _If_ usage confusers are ≥ 20% of dev errors (or ≥ 5 lines): a sibling verifier   | on vs off. The verifier sees the line, its path and only the sibling usages of the agreed type, and returns a code or NONE plus an evidence span; the code is validated in code (case-sensitive) even when an enum is used                   | keep rule; correct→wrong and wrong→correct flips reported per family; disagreement → `VERIFIER_DISAGREES`                                                                                                                                                                                                                                                                                                                                                                   | + calls on flagged lines [A20]    |
| E-09    | _If_ FR smoke-set errors call for it: a cement-content extractor (`(\d{3})\s*kg`) | on vs off                                                                                                                                                                                                                                    | keep rule + smoke set                                                                                                                                                                                                                                                                                                                                                                                                                                                       | $0                                |

---

## 8. What I will cut if I run out of time `[README: "What will you cut?"]`

**Nothing is cut in advance.** Work that the brief does not require is **deferred**: it runs after the must-haves, in the order below, and anything not done by its cap is listed in the README as not done. [A32]

**Deferred: after must-haves**

| #   | Deferred item                                                                                        | When                               | What it adds                                                                                                      |
| --- | ---------------------------------------------------------------------------------------------------- | ---------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| 1   | Selection-stability resampling on dev replay votes (§10.6)                                           | pre-freeze                         | spread of the selection, as a dev diagnostic [A22]                                                                |
| 2   | Calibration battery: AURC, E-AURC, reliability diagram, ECE, Brier (§10.6)                           | pre-freeze                         | dev diagnostics, never used for the claim [A32, A41]                                                              |
| 3   | Synthetic FR stress set: generation and hash (§6)                                                    | pre-freeze                         | optimistic evidence for FR on the FR library; never affects thresholds [A23]                                      |
| 4   | `HybridRetriever` (BGE-M3 dense + BM25, optional reranker) and recall@{10,20,30,50} on dev (§10.5)   | pre-freeze                         | the measured scaling path, in the `[retrieval]` extra only [A24, A37]                                             |
| 5   | Local ≤ 8B adapter, benchmarked once on the 40-line dev slice (accuracy per level, seconds per line) | pre-freeze                         | the answer for clients who forbid external APIs; it joins E-02(d) as a voter only under the A17 gate (§7.2) [A25] |
| 6   | Cross-model voter E-02(d), if triggered                                                              | pre-freeze                         | one more independent signal [A17]                                                                                 |
| 7   | AIMD (only after a recorded 429) and the windowed breaker rule (§11.3)                               | pre-freeze, after the above        | throughput and protection under sustained errors [A12, A39]                                                       |
| 8   | UI, with async `/v1/jobs` (§13)                                                                      | after G5 (G6)                      | the optional deliverable [A14, A50]                                                                               |
| 9   | Phoenix/OpenInference export spike, 1 h (§11.6)                                                      | after G5 (G6), only if G5 is green | the observability answer, shown [A45, A52]                                                                        |
| 10  | Dockerfile, core dependencies only (§11.10)                                                          | after G5 (G6)                      | a one-command container [A26, A49]                                                                                |

**Out of scope** (non-goals, §2): PDF input (not provided); UI editing and auth (not asked for; an optional bearer token exists, §11.4); import-linter, mutated-library and multi-seed ablations (they add no evidence for this brief). LLM enrichment is the default only if E-01 earns it. [A32]

**Cut order if a gate hits its cap.** The first item goes first. Each entry names what it weakens.

1. The UI. Weakens: the optional deliverable.
2. The cross-model voter E-02(d). Weakens: one arm of E-02.
3. k = 3 votes. Weakens: one point on the vote axis.
4. The E-03 and E-05 arms. Weakens: the rendering and batch-size answers.
5. Enrichment arm D. Weakens: one arm of E-01.
6. Determinism slice reduced from 40 to 20 lines. Weakens: noise-floor precision.
7. FR smoke-set base reduced from 25 to 15 lines. Weakens: live-session evidence.
8. The second agreement pass. Weakens: signal (c); the score keeps (b) and (a).

[A10, A32]

**Never cut:**

- CLI and API on one service;
- the closed-world validator;
- failure handling;
- audit and replay;
- the scorer, its contract (§10.1) and the requirements check;
- committed evidence (§9.6);
- the fallback adapter (§11.3);
- the frozen split and the lockbox-once rule;
- both output files;
- the evaluation report;
- the README. [A32, A33, A34, A36]

---

## 9. Pipeline design

### 9.1 Parsing (`io/boq_reader.py`, shared by the CLI, the API and the UI)

- CSV (the `csv` module; pandas is never used in `src/`):
  - UTF-8, BOM tolerated, is tried first; only if that fails is the file decoded as cp1252, with an explicit warning and `encoding` recorded in the manifest. A UTF-8 file can never be mis-decoded, because UTF-8 is always tried first; [A15, A49]
  - delimiter sniffed between `,` and `;`;
  - columns found by name (case- and space-insensitive, with aliases);
  - extra columns are kept and passed through;
  - a missing required column raises a clear error.
- XLSX (openpyxl, read-only, values only, first visible sheet; `[xlsx]` extra with defusedxml; built only if the UI ships):
  - item numbers are kept as text, and numeric item codes are rendered with their cell `number_format`;
  - `.xls` and `.xlsm` are rejected. [A49, A51]
- All cells stay raw strings: no NaN conversion, no strip. [A49]
- Field caps: item_no ≤ 64, short ≤ 1,000, long ≤ 4,000 characters. The CLI truncates longer text for the prompt only and sets a `TRUNCATED` audit flag; the API returns 422 (§11.4). [A51]
- **Headers.** A row is a header if Unit and Qty are empty **and** (its code matches a configured pattern **or**, after normalising `.`, `-` and spaces, its code is a strict prefix of the next non-empty code). Fully empty rows are `EMPTY_ROW`. Other rows with empty Unit and Qty are `HEADER_UNCONFIRMED` (§9.5). [A48]
- Section path:
  - the stack of the most recent header rows whose codes prefix the line's code, with the text of each header (on these files, L0 > L1);
  - computed over the whole input before any chunking; an API line may also carry it explicitly (§11.4);
  - a file where no path can be derived gets none: the prompt says "no section context", the audit records `context: none`, and the manifest records `path_mode`. Such a file runs the strictest threshold unless the E-04 no-path ablation meets the dev bar. [A48, A50]
- Line identity is a hash of position, item number and text, so duplicate texts stay distinct. The UI uses the same id. Inside a batch, the transport id is `L<position>`. [A8, A15]

### 9.2 Library (`domain/library.py`)

- Load any CSV with `material_type, material_usage, material_subtype` (quoted headers allowed).
- `row_id` = a short SHA-256 of the three raw strings. The loader checks for collisions and computes the library's own SHA-256.
- **Rendered size.** The loader measures the rendered prompt with `count_tokens` and records it in the manifest. ≤ 30k tokens: whole library; 30k–150k: whole library with a printed warning and a cache check; > 150k: an explicit error naming the retrieval switch. A library is never truncated. [A15, A28, A37]
- **Derived structure**, computed from whatever library is loaded (never from type names):
  - mixed parents (a blank subtype plus specific siblings);
  - sole-child parents;
  - groups of rows that share a subtype within a type, which are confusable;
  - `never_match` rows by **configured patterns**, e.g. the "Custom material (Carbon impact in …)" placeholders, kept in `config/` and not in code.
- Attributes for every row are extracted at load time with the same extractors that run on lines.
- Display codes `Txx.Uyy.Szz` are assigned on **sorted** normalised strings, so they are stable when the file is reordered. `S00` is the blank-subtype leaf. The alternative rendering of §9.4 reorders rows, never codes. [A46]
- Enrichment files live in `config/enrichment/`, keyed by library SHA-256; a library with no entry runs unenriched. [A38]

### 9.3 Attribute extractors (`domain/attributes.py`)

Each extractor applies one function to two inputs: normalised line text and normalised library row text. They are registered in one list, and each has a test template.

| Family           | Pattern (after normalisation)                                                                                                                                                                                                                                                                                                                                                                                   | Comparison                                                      | Role                                                                     |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------ |
| Strength class   | `\bc(\d{1,3})/(\d{1,3})\b`                                                                                                                                                                                                                                                                                                                                                                                      | equal                                                           | hard                                                                     |
| Exposure classes | `\bx(0\|c[1-4]\|d[1-3]\|s[1-3]\|f[1-4]\|a[1-3]\|m[1-3])\b`                                                                                                                                                                                                                                                                                                                                                      | line set ⊆ row set (FR rows bundle lists such as `XS3-XD3-XA2`) | hard                                                                     |
| CEM type         | `\bcem\s*(iii\|ii\|iv\|v\|i)\b(\s*/\s*[abc])?`                                                                                                                                                                                                                                                                                                                                                                  | equal at the level both sides state                             | hard                                                                     |
| Recycled content | `(\d+)\s*%\s*(de\s+)?(rap\|ra\|ae\|reclaimed\|agr[ée]gats d.enrob)` and `\b(rap\|ra\|ae)\s*(\d+)\s*%`; explicit 0 from `\bno ra\b\|sans ae\|virgin\|mat[ée]riaux neufs`. Implicit 0: a row that states no percentage counts as 0 only when a sibling in its type+usage pair states a positive percentage (a sibling that says only "virgin" does not trigger it), and only on catalogues where this was checked | equal                                                           | hard [A2, A11]                                                           |
| Process          | WMA ← `\bwma\b\|warm\|ti[èe]de\|temp[ée]rature abaiss[ée]e\|reduced temperature`; HMA ← `\bhma\b\|\bhot\b\|\bchaud\b`                                                                                                                                                                                                                                                                                           | equal                                                           | hard [A2]                                                                |
| EWC code         | `\b17\s?\d\d(\s?\d\d)?\b`                                                                                                                                                                                                                                                                                                                                                                                       | prefix                                                          | hard                                                                     |
| Diameter         | `ø\s*(\d+)`, `\bdn\s*(\d+)`                                                                                                                                                                                                                                                                                                                                                                                     | equal                                                           | hard                                                                     |
| Areal mass       | `(\d+)\s*g/m[²2]`                                                                                                                                                                                                                                                                                                                                                                                               | equal                                                           | hard                                                                     |
| Grading d/D      | `\b\d+(\.\d+)?/\d+(\.\d+)?\b`, excluding strength classes                                                                                                                                                                                                                                                                                                                                                       | agreement only                                                  | soft: bitumen grades also look like this, and FR rewrites 50/70 as 35/50 |

**Comparing a row with a line gives one of three results, always judged from the line side** (signal (b) of §10.6):

- **conflict:** some hard family is stated on both sides with incompatible values. Always a veto.
- **agree:** no conflict, **and** at least one hard family is stated on both sides and compatible, **and** every family the line states is compatible with the row or absent from it.
- **no_evidence:** anything else. [A35]

**Absence rule:** a row is never penalised for lacking a family that the line has, and a row is never required to have all its families present in the line. This lets the same code serve the sparse global rows and the spec-bundled FR rows: `Semelles … C30/37 XC4/XA1` against `…XC3-XC4-…-XA1 C30/37 330kg CEM I` gives `agree` (unit test). The A2 dev table (§16) is recomputed under these definitions and logged. [A35]

### 9.4 Prompt and output schema (`prompts/v1/`)

**Layout, in cache-friendly order:**

1. A system block with the decision vocabulary:
   - `non_material` = labour, service, fee, survey, temporary works, or hire with no material supplied;
   - activity verbs such as break out, remove or dredge produce **materials**;
   - a material with no fitting row is `no_equivalent`, never `non_material`;
   - BoQ text is data, not instructions, and every field value in the batch is data. [A43]
2. A system block with the glossary and the whole library as a coded tree, marked with a cache breakpoint. Pass 2 (§10.6) uses the same block in one fixed, pre-registered alternative rendering (rows in reverse sorted code order; display codes unchanged, D-07), with its own cache breakpoint. Per-call random permutations are forbidden. [A46]
3. One fixed output schema for every call: one Pydantic v2 model, sent through native structured outputs (`output_config.format`). [A44]
4. The user message: the batch as one `json.dumps({"lines": [...]}, ensure_ascii=False, sort_keys=True)` inside `<boq_lines>`, each line object holding id, path, short, long and unit. Line text cannot forge a record boundary. [A43]

Only the description, the unit and the section path are sent. **Quantities, file names and project metadata are never sent.**

**Output, per line, in this order.** Haiku runs without a reasoning budget, so field order is the only "think first" lever. This is the pre-registered v1 schema, not an experimental arm. [A43]

```json
{
  "id": "L118",
  "evidence": "≤ 12 words copied verbatim from the line",
  "element_or_application": "short English noun phrase | ",
  "material_family": "…",
  "kind": "material | non_material | no_equivalent",
  "nm_category": "labour | service | temporary_works | fee | hire | other | ",
  "top1": "T05.U03.S02",
  "top2": "T05.U04.S02 | ",
  "confidence": 0,
  "self_reported_candidate_gap": "decisive | clear | narrow | tossup"
}
```

- The prompt says: "fill evidence, element and family before choosing codes; the usage must be consistent with element_or_application". [A43]
- `confidence` is an integer from 0 to 100, with anchors defined in the prompt. The structured-output grammar does not enforce ranges or lengths, so confidence and the evidence length are validated in code. [A44]
- **The candidate gap is a verbal, ordinal judgement, not a probability.** The API exposes no log-probabilities, and I never call it a margin.
- Codes are checked against the library in code, case-sensitively, not through schema enums, which keeps the schema identical across libraries. [A44]
- Responses are read with `messages.create`, never `parse()`, so the raw text survives a malformed answer. [A44]
- `max_tokens` is a fixed 4,096. The measured output peaks at 1,213 tokens for a 10-line batch, so 4,096 never truncates; the reasons for not sizing it from p99 × 1.3 are given in A63. [A39, A63]

**Versioning.** `prompt_version = v1+sha256(templates ‖ generated JSON schema ‖ glossary ‖ renderer ‖ rendering variant)[:8]`, with one component per pass. The rendering is canonical (sorted, with no timestamps), so each cached prefix is byte-stable and a snapshot test pins it. [A44, A46]

### 9.5 Decision table (`domain/decision.py`, pure)

The first rule that fires wins. "top1" is the plurality top-1 across the passes. [A46]

| #   | Condition                                                                                                                                    | Decision         | Reason code                                                              |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------- | ---------------- | ------------------------------------------------------------------------ |
| D0  | G1: empty Unit **and** empty Qty **and** a structural header (§9.1)                                                                          | `not_a_material` | `HEADER` [A48]                                                           |
| D0a | Fully empty row                                                                                                                              | `not_a_material` | `EMPTY_ROW` [A48]                                                        |
| D0b | Empty Unit and Qty, but not a header                                                                                                         | `needs_review`   | `HEADER_UNCONFIRMED` [A48]                                               |
| D1  | No valid answer within the line's budget (6 calls, 90 s in flight, §11.3), a conflicting duplicate, or a replay miss                         | `needs_review`   | `LLM_FAILURE:<kind>`, `LLM_UNAVAILABLE` or `BUDGET_CAP` [A8, A12, A50]   |
| D1b | A pass or voter that the frozen threshold needs is missing after retries                                                                     | `needs_review`   | `LLM_FAILURE:partial_signal`, with the call id; never `LOW_SIGNAL` [A43] |
| D2  | G2: `kind = non_material` **and** the unit is in `SERVICE_UNITS` **and** no hard attribute was extracted **and** no supply marker is present | `not_a_material` | `G2_SERVICE` [A11]                                                       |
| D3  | `kind = non_material`, any other case                                                                                                        | `needs_review`   | `NM_UNCONFIRMED`                                                         |
| D4  | `kind = no_equivalent`                                                                                                                       | `needs_review`   | `NO_LIBRARY_EQUIVALENT`                                                  |
| D5  | top1 unknown, malformed or inconsistent with its prefix (case-sensitive check)                                                               | `needs_review`   | `INVALID_ROW_ID` [A44]                                                   |
| D5a | The evidence has no word, or a word of it is not a word of normalise(short + ' ' + long + ' ' + path) (NFKC, casefold, whitespace collapse, decimal comma; words are `\w+` runs, any order) | `needs_review`   | `EVIDENCE_NOT_IN_LINE` [A43, A62]                                        |
| D6  | top1 is a configured never-match row                                                                                                         | `needs_review`   | `NEVER_MATCH_ROW`                                                        |
| D7  | Attribute comparison of top1 = conflict                                                                                                      | `needs_review`   | `ATTR_CONFLICT`                                                          |
| D8  | top1 is the blank leaf of a mixed parent and a sibling is `agree`                                                                            | `needs_review`   | `GENERIC_PARENT` [A35]                                                   |
| D8a | Only if E-08 is adopted: the verifier disagrees with top1                                                                                    | `needs_review`   | `VERIFIER_DISAGREES` [A20]                                               |
| D9  | The line's score s is at or above the frozen threshold (§10.6)                                                                               | `matched`        | `SIGNAL:<threshold id>` [A31]                                            |
| D10 | Anything else, including a vote tie                                                                                                          | `needs_review`   | `LOW_SIGNAL:<failed signals>` [A31, A46]                                 |

The **reason-code enum is frozen**, issued here as one list, and the UI shows it verbatim:

`HEADER, EMPTY_ROW, HEADER_UNCONFIRMED, G2_SERVICE, NM_UNCONFIRMED, NO_LIBRARY_EQUIVALENT, INVALID_ROW_ID, EVIDENCE_NOT_IN_LINE, NEVER_MATCH_ROW, ATTR_CONFLICT, GENERIC_PARENT, VERIFIER_DISAGREES, SIGNAL:*, LOW_SIGNAL:*, ENSEMBLE_DEGRADED:SIGNAL:*, ENSEMBLE_DEGRADED:LOW_SIGNAL:*, LLM_FAILURE:{timeout, rate_limited, overloaded, api_error, truncated, malformed, refusal, missing_item, duplicate_conflict, partial_signal, replay_miss}, LLM_UNAVAILABLE, BUDGET_CAP, INTERNAL_INVARIANT`.

`VERIFIER_DISAGREES` occurs only if E-08 is adopted. The `ENSEMBLE_DEGRADED:` forms occur only if E-02(d) is adopted and a voter is missing, so the Haiku-only threshold decided the line. `TRUNCATED` is an audit flag, not a reason code. [A17, A20, A30, A43, A48, A50]

**Safety by construction:** no measured unit can reach `not_a_material`, because only D0, D0a and D2 emit it; D0 and D0a need empty Unit and Qty, and D2 requires a service unit. RQ5 checks that every `not_a_material` row has reason ∈ {HEADER, EMPTY_ROW, G2_SERVICE}. A truth-table test pins this. [A30, A48]

### 9.6 Output and audit

**Output CSV**, written with the `csv` module: UTF-8 without BOM, `\r\n` line endings, QUOTE_MINIMAL (`--excel-bom` is optional and recorded); never pandas in `src/`. [A49]

- the input columns, unchanged;
- `decision, material_type, material_usage, material_subtype`;
- the sample's audit columns `reason, model, prompt_version, latency_ms, cost_usd`;
- then `suggested_type, suggested_usage, suggested_subtype, library_row_id, call_ids` (`;`-joined, empty for rule-decided rows). [A36]
- then `suggested2_type, suggested2_usage, suggested2_subtype` (the valid top-2 row, else blank). [A56]

The `model` column holds the served model, which is the fallback model when it decided the line. [A33]

**Attribution of cost and latency.** Over every attempt a that touched the line, with n_a lines in its request: `cost_usd = Σ cost(a) / n_a` and `latency_ms = round(Σ elapsed(a) / n_a)`. Rule-decided rows carry `0.000000`, `0` and `model = rules`. `cost_usd` has 6 decimals; `latency_ms` is an integer. The sum over lines equals the run total (RQ7). Replays and cache hits copy the source values and never re-measure them. [A45]

**Run folder `runs/<run_id>/`:**

- `manifest.json`: mode ∈ {live, cached, replay} and source_run_id; requested model and fallback_model; library SHA-256, and the measured `count_tokens` of each rendered library with its cache eligibility; prompt version; enrichment_sha256; `config_sha256 {path: sha}` for `config/*`, the glossary and the enrichment file; policy id and `policy_resolution`; `path_mode`; input `encoding`; code SHA plus a dirty flag; split SHA; price-table date; temperature and max_tokens; SDK versions; the OTel GenAI semconv version; the rate-limit tier and `anthropic-ratelimit-*` headers; spend_usd, attributed_cost_usd, cache_hits, cache read and write tokens; settings_effective (non-secret). Paths are repo-relative POSIX. [A15, A28, A33, A38, A39, A45, A46, A48, A49]
- `calls.jsonl`, one record per attempt: call_id, parent_call_id, attempt_no, reason_for_call ∈ {first, retry, reask_missing, reask_conflict, split}, started_at and ended_at (UTC), `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.response.id`, provider_request_id, `gen_ai.response.finish_reasons`, `gen_ai.usage.{input_tokens, output_tokens, cache_read, cache_creation}`, request_sha256, user_message, system_blocks_sha256 (the system blocks are stored once at `prompts/<sha256>.txt`), http_status, error_class, raw_response, cost_usd, latency_ms, line_ids, cache_hit, source_call_id. Field names follow the OTel GenAI conventions; no tracing server is a dependency. [A45]
- `audit.jsonl`, one record per line: rule fired, reason, signals (v, b, confidence bucket), top-1/top-2, attribute result, `raw_line_response` (the line's verbatim JSON object from each pass's batch response), call_ids, `context`, and flags such as `TRUNCATED`. [A36, A50, A51]

**Committed evidence.** `runs/` is ignored by git except `runs/submission/`. Committed in full under `runs/submission/<run_id>/{manifest.json, calls.jsonl, audit.jsonl, prompts/<sha256>.txt}`: the B2 and B3 lockbox runs (§10.3) for EN and FR, the dev selection runs, and the FR smoke-set run. These hold ORIS's exercise files, not client data. Runs on any other input stay local and ignored. CI (RQ11) replays the B3 runs and checks them byte for byte against `output/improved_output_{en,fr}.csv`; a 20-line dev fixture backs the fast golden unit test. [A36]

---

## 10. Evaluation protocol `[Rubric: Baseline and confidence]`

This is the condensed version. Formulas, split membership, the power table and the requirements check are given in full in `docs/evaluation-protocol.md`.

### 10.1 Metrics

| Metric                   | Definition                                                                                                                                                                                | Denominator                 | Target                                                                                                                        |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| Matched precision P      | correct triples ÷ lines decided `matched`. A match on a blank-GT line is wrong. `n/a` if nothing is matched                                                                               | \|matched\|                 | dev selection: ≥ .95 in each language with ≥ 40 matched; lockbox: ≥ .90 point, with the one-sided 95% exact lower bound [A31] |
| Match coverage C₂₅₂      | correct matches ÷ labelled lines                                                                                                                                                          | 252 (dev 139 / lockbox 113) | maximise                                                                                                                      |
| Coverage C₂₆₅            | correct matches ÷ all material-bearing lines                                                                                                                                              | 265                         | reported; ceiling .951                                                                                                        |
| Material handling H₂₆₅   | (correct matches + `needs_review` on material lines) ÷ 265                                                                                                                                | 265                         | diagnostic, printed next to review load; never used for selection [A7]                                                        |
| Safety F_NM              | material-bearing lines decided `not_a_material`                                                                                                                                           | count                       | **0**                                                                                                                         |
| Per-level accuracy       | cumulative type, type+usage, triple, (1) over matched lines and (2) over all labelled lines using the suggested row (abstention-free)                                                     | as stated                   | reported [A47]                                                                                                                |
| Suggestion hit@1 / hit@2 | the reference triple equals the first / either suggestion, over `needs_review` lines with a labelled reference                                                                            | per language                | reported [A21]                                                                                                                |
| Decision shares          | share of each decision over all rows and over item rows; decision × reference-class confusion matrix                                                                                      | 319 / 282                   | reported                                                                                                                      |
| Cost                     | Σ cost of all attempts ÷ lines × 100, from API `usage` and `config/pricing.toml` (dated, keyed by provider and model); attributed per line (§9.6); gated on live, uncached manifests only | all rows                    | ≤ $2.00 [A45]                                                                                                                 |
| Latency                  | cold live run: wall-clock ÷ LLM-routed lines (gate; N_routed = 282 here) and ÷ all rows (headline); `latency_ms` labelled "attributed (conservative under concurrency)"; per-call p50/p95 | as stated                   | ≤ 2.0 s [A1, A12, A45]                                                                                                        |

**Exact strings everywhere.** The headline uses exact match, and trailing spaces count. A "lenient (NFC + trim + whitespace collapse)" line is always printed, with a warning when it differs; it never changes the headline. [A34]

**The scorer's contract**, which matters because ORIS will run it on its own labels:

- `eval/score.py` is one standard-library-only file that imports nothing from `src/`.
- Usage: `python eval/score.py --output OUT.csv --reference REF.csv [--label en] [--key COL] [--strict] [--join key|row-order] [--split eval/split_v1.json --side dev|lockbox|all] [--json report.json]`. Repeating the `--output`/`--label` pair scores EN and FR side by side.
- **Key and columns.** The key is auto-detected (case- and space-insensitive) from `Item No.`, `Item No`, `item_no` and `N° article`; `--key` overrides. Label columns resolve by alias. The output needs the key, `decision` and the three labels; `reason`, `cost_usd` and `latency_ms` are optional and print n/a when absent.
- **Join.** Rows join on (key, occurrence index); duplicate or blank keys produce a warning with a count. A reference row missing from the output counts as a failure and is listed. An output row missing from the reference is unscored, listed, and excluded from every denominator. A row-order join needs `--join row-order`.
- **Denominators** come from the reference; the material-without-equivalent list and the split are optional inputs.
- **Cells.** Label cells matching `^(nan|NaN|None|NULL|<NA>|N/A)$` count as blank, with a printed count.
- **Strict mode** (`--strict`, always used by `check_requirements.py` on our own files): missing, extra or duplicate keys are hard errors; a row-order join needs equal row counts; a row is rejected if its decision is outside the enum, if `matched` has an all-blank triple, or if a non-matched row carries canonical labels. A blank subtype is a valid leaf. [A7]
- **Report:** matched precision with its one-sided 95% CP lower bound (bisection on the binomial CDF via `math.lgamma`); C_labelled; cumulative per-level accuracy over matched rows and over all labelled rows; decision shares over all rows and over item rows; the false `not_a_material` count; hit@1/2; mean `cost_usd` and attributed `latency_ms`. Bootstrap, McNemar and plots stay in `eval/report.py`.
- **Fixture tests:** GT against itself gives P = 1; the provided sample output; a labels-only reference; duplicate codes; `nan` literals; an NFD reference; a renamed key header; a key-less file with `--join row-order`. [A34]

### 10.2 Split

1. **Split by item, never by row or by language.** The EN and FR twins of an item always sit on the same side. Otherwise the lockbox would score translations of answers already studied.
2. **Hold out whole L1 sections**, because neighbouring lines share templates and conventions. This imitates an unseen BoQ.
   - **Lockbox sections:** 01.03 Excavation · 03.01 Unbound layers · 04.02 Substructure and retaining walls · 04.04 Reinforcement, steelwork and bridge equipment · 05.02 Final lining and portal structures · 06.01 Track and track bed · 08.01 Cements and additions · 08.03 Admixtures and fibres · 09.01 In-situ concrete, buildings and minor works.
   - **One exception:** 03.03 _Bituminous layers_ holds all 28 asphalt lines, so its labelled lines alternate between the sides.
3. **Composition**, verified against the frozen file:

   | Side    | Labelled | No-equivalent materials | Services | Ambiguous | Blank-subtype |
   | ------- | -------- | ----------------------- | -------- | --------- | ------------- |
   | Dev     | 139      | 9                       | 14       | 0         | 36            |
   | Lockbox | 113      | 4                       | 2        | 1         | 13            |
   - Concrete / Aggregates / Asphalt: 36/17/14 dev vs 39/15/14 lockbox.
   - Disclosed: the lockbox is **harder**. It holds 7/8 Admixture, 9/13 Excavations, 8/12 Cement, 7/11 Steel and the whole 09.01 ready-mix block, while dev holds 18/24 C&D. I expect shrinkage from dev to lockbox, which is one reason the dev bar is .95. [A31]

4. **What the lockbox is.** A same-project, section-held-out holdout, explored before the split (§10.8) and blind only to tuning. Only the unseen live BoQ is a blind test. [A5]

### 10.3 Lockbox procedure

- Before the freeze, only dev items are ever sent to the model. Lockbox lines are never sent, replayed or inspected.
- The freeze is a git tag, `eval-freeze`, pinning code, prompt, model, threshold, library, glossary and split. It happens on Thu 8 Oct 12:00 at the latest: at that time the latest green dev configuration is frozen, whatever gates remain open. [A32]
- **Three rungs are frozen and tagged together:** B1 (threshold fixed on dev), B2 (pinned prompt and config) and B3. B1 needs no model call. [A6]
- After the freeze, a single lockbox session runs B2 and then B3 on both full files, **once each**, with `--no-cache`. Both runs are appended to `eval/lockbox_log.md` and committed under `runs/submission/` (§9.6). The once-only rule applies per frozen rung, and nothing is tuned afterwards. [A6, A36, A45]
- **The claim**, per language, for the one frozen threshold: _"matched precision X% on n lines; one-sided 95% exact lower bound L; meets the 90% point bar: yes/no; certified at 95% confidence: yes only if L ≥ .90."_ [A31]
- **If the lockbox misses .90:** nothing is tuned on it. The measured number is reported with its error analysis, as a known weakness.
- **One rerun is allowed, only for a demonstrable implementation bug.** That means a gap between the code and this written spec (a parser, join or encoding error), not a prompt, threshold, glossary or policy change. A unit test that reproduces the bug is written first, and both scores are reported side by side.

### 10.4 How confident a number is

**Intervals and tests:**

- The only bound used for any "≥ 90%" claim is the **one-sided 95% exact (Clopper–Pearson) lower bound**. Every other interval, including the Wilson 95% interval shown next to each proportion, is display only. [A18, A31]
- Same-item comparisons are **paired**:
  - McNemar's exact test for coverage, e.g. baseline vs final, EN vs FR;
  - a paired item bootstrap (10,000 resamples) for precision differences.
- Any test or interval that combines EN and FR clusters by Item No., because the twins are the same items. [A5]
- A section-cluster bootstrap is reported as a robustness check.

**What the sample size allows:**

|                                                                                 | Value                                         |
| ------------------------------------------------------------------------------- | --------------------------------------------- |
| Detectable coverage difference (α .05, power .8)                                | ≈ 10–11 points on dev, ≈ 11–13 on the lockbox |
| Detectable precision difference between two variants with about 60 matches each | ≈ 19 points                                   |

So I never claim one variant is "more precise" than another. Variants are selected on coverage under the precision bar.

**What the bounds can and cannot prove.** [A18, A31]

- A one-sided 95% CP lower bound ≥ .90 allows 0 errors at n = 29–45 matched lines and 1 error at n = 46–60; 30/30 gives .905. Used as a dev selection rule, it would certify nothing in most runs, so it is the lockbox claim only.
- The dev bar (≥ .95 in each language, ≥ 40 matched in each) is a conservative filter, not a proof. Its pre-registered operating characteristic: a policy with true precision .90 passes it 9–22% of the time; a true-.96 policy passes about 78% of the time.
- The lockbox bound is the proof, and at these sample sizes it may honestly say "not certified".

**The claim is pre-committed in the form given in §10.3.** [A31]

### 10.5 Baseline ladder

Every rung is scored by the same scorer on the same split.

| Rung                 | Pinned configuration                                                                                                                                                                                                                                                                                                                                     | What it answers                                                          |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| B0 Rules only        | G1 headers → `not_a_material`; everything else → `needs_review`; no unit-only skip                                                                                                                                                                                                                                                                       | The floor: coverage 0, safety and review load at zero intelligence [A15] |
| B1 TF-IDF            | char_wb 3–5-grams + word 1–2-grams, cosine scores averaged, `sublinear_tf`; the vectoriser is fit on library rows + dev lines only, and lockbox lines are only transformed; argmax over all rows; threshold on the cosine score chosen **on dev** by the §10.6 rule, frozen at `eval-freeze` (no model call), then applied to the lockbox [A6, A29, A47] | "The simplest reasonable approach", measured held out [A29]              |
| B2 Haiku single pass | Raw library, no section path, no extractors, no glossary; every valid answer matched; prompt and config pinned, frozen at `eval-freeze` and run once on the lockbox (§10.3)                                                                                                                                                                              | Raw model capability; shows why abstention is needed [A6]                |
| B3 Final             | The frozen system                                                                                                                                                                                                                                                                                                                                        | The value of the engineering                                             |

**Like-for-like baseline table** (lockbox, per language), because B2 matches every line and B3 abstains: [A47]

- (a) top-1 full-triple accuracy over all labelled lines (the suggested row for B3, the argmax for B1), with McNemar exact B1 → B2 and B2 → B3;
- (b) per-level cumulative top-1 accuracy for each rung;
- (c) coverage at each rung's own dev-selected point (the §10.6 rule applied to B1's score and to B2's confidence), with lockbox precision and its one-sided CP bound.

After the freeze, a descriptive lockbox error table is added: material family (top 8 + other) × first wrong level / match-on-blank / missed, labelled "post-hoc, not used for any decision". Ablations of B3 run on dev only. [A47]

**Candidate recall gate.** The whole library is the default candidate set, so inclusion is 1.0. The `HybridRetriever` (BGE-M3 dense + BM25, optional reranker; deferred, §8) is measured on dev only, recall@{10,20,30,50} per language, and may replace it only if the rendered library exceeds 30k tokens **and** dev recall@k ≥ .98. [A24]

### 10.6 Abstention: score, candidates and selection

**The score, fixed now.** Every routed line that no gate or veto stops (D0–D8) gets a lexicographic score s: [A31, A35, A46]

1. v = the number of the k passes whose top-1 equals the plurality top-1. k = 2 by default, so a split vote is a tie and goes to review (D10); k = 3 is an E-02 arm. Pass 1 uses the canonical rendering and each further pass one fixed alternative rendering (§9.4), all at temperature 0;
2. signal (b) ∈ {agree > no_evidence}, from the line side (§9.3);
3. the self-reported confidence bucket {≥ 90, 80–89, 70–79, < 70}.

`ATTR_CONFLICT` lines never match.

**Candidates.** The operating points are the thresholds on s, in lexicographic order. Each is a superset of the next stricter one, so the order is fixed before any call; with k = 2 there are 8. The **strictest** threshold is (v = k, agree, ≥ 90), and that is what "strictest" means everywhere in this document. Match-all is a reference row only. [A31]

**Dev selection.** Pick the loosest threshold whose dev matched precision is ≥ .95 in **each** language, with ≥ 40 matched dev lines in each. If two thresholds are within 3 summed correct matches, pick the stricter. If none qualifies, ship the threshold with the highest minimum-over-languages one-sided 95% CP lower bound, labelled "below the dev bar". One threshold covers both languages (D-12); a per-language optimum is reported as a sensitivity row and never shipped. `oris select --target T` re-runs this rule on cached votes at $0. [A31]

**Operating characteristic, pre-registered:** a true-.90 policy passes the dev bar 9–22% of the time; a true-.96 policy about 78%. [A31]

**CTO version:** _"We rank lines by how strongly independent checks agree, accept down the list while dev precision stays ≥ 95% in both languages, and report the untouched lockbox with an exact bound."_ [A31]

**Policy resolution.** `config/policy.yaml` maps (model_id, library_sha256) → {policy_id, certified_by: dev_selection | smoke_A10}. An exact hit uses its entry. Any unknown pair uses the strictest threshold, runs unenriched, logs a WARNING and records `policy_resolution: fallback_strictest` in the manifest and the run summary. This covers a library with one byte changed and the fallback model, unless an optional ~$0.50 dev run certifies the fallback under this rule. `--policy` overrides, and the override is recorded. [A33, A38]

**Reported with the selection:**

- risk–coverage curves per language (C₂₅₂ vs precision) with B1, B2, B3 and the candidate thresholds marked, and bootstrap bands;
- precision by v, and the share of errors with v = k (systematic errors); [A46]
- precision at each candidate-gap level;
- a precision / coverage / review-load / cost table for every threshold, produced by $0 replay. This is the explicit trade-off curve.

**Dev diagnostics, deferred until after the must-haves (§8):** [A22, A31]

- AURC and E-AURC; calibration of the self-reported confidence (reliability diagram over 5 equal-mass bins, ECE and Brier score). None of them enters the claim.
- Selection stability: repeated grouped 2-fold resampling (groups = Item No. within L1 section) over dev replay votes only. In each repeat the selection rule runs on one half and is scored on the other; the spread is reported. No resampling touches lockbox items.
- Label-noise candidates (unanimous model disagreement with the GT) are adjudicated on dev only, before the freeze, with a written reason, and tagged `gt_suspect`. Headline metrics are always "as labelled".

### 10.7 Error taxonomy, and how it drives the next experiment

**Each dev error is recorded** in `eval/errors_dev.csv` with:

- family;
- kind: `false_match`, `match_on_blank`, `false_nm`, `missed`, or `wrong_proposal_reviewed`;
- the first wrong level;
- a cause, from: `lexical_gap`, `usage_confuser`, `subtype_parse`, `header_context`, `not_in_library`, `llm_failure`, `gt_convention`.

Usage errors are split into "element right, usage wrong" and "element wrong", using `element_or_application` (§9.4). Lines adjudicated as label noise carry `gt_suspect` (§10.6). [A22, A43]

**From taxonomy to intervention.** An intervention is considered only if its cause covers ≥ 5 dev lines or ≥ 20% of dev errors in one language. Each cause has a pre-mapped response:

| Cause          | Pre-mapped response                                |
| -------------- | -------------------------------------------------- |
| lexical_gap    | E-01 enrichment                                    |
| usage_confuser | E-08 verifier                                      |
| subtype_parse  | fix or add an extractor, test first                |
| header_context | path rendering                                     |
| not_in_library | no-equivalent guard                                |
| llm_failure    | engineering                                        |
| gt_convention  | never fixed with an item-specific rule; documented |

### 10.8 Honesty and leakage

- **No labelled data at inference.** The model sees only:
  - the selected library;
  - enrichment derived from that library (its generator sees only library rows and the glossary, §6);
  - a small glossary;
  - the line and its section headers. [A19]
- **No examples and no memory.** Nothing is drawn from the labels as examples (D-02). No conversational or correction memory exists; reviewer-correction memory keyed by library hash is a production next step (§12). [A52]
- Ground truth and annotations are read only by `eval/`, and a CI test enforces it.
- **Glossary provenance.** Every entry is tagged `standard`, `library`, `dev_obs`, `dev_error` or `lockbox_obs`.
  - Entries must state general domain facts, never item text.
  - A 6-gram overlap check against both BoQ inputs runs in CI, on glossary entries and on enrichment output. [A19]
  - `lockbox_obs` entries are excluded. If any are kept, lockbox precision is reported with and without them.
- **Disclosed contamination.** I profiled all 252 labelled lines, lockbox included, during data analysis, _before_ freezing the split. The lockbox is therefore a same-project, section-held-out holdout: blind to tuning but not to exploration. Mitigations: [A5]
  - no item-specific rules;
  - provenance tags;
  - a dev-only experiment ledger (`eval/experiments.jsonl`, §7.2);
  - lockbox precision also reported excluding lines touched by any convention-derived rule.
- **The EN and FR lockboxes are correlated.** They are the same items, not two independent confirmations, so every combined test clusters by Item No. (§10.4). The unseen live BoQ is the only fully blind test. [A5]
- **Builder labels.** The 30 blank-line classes, the FR smoke set and its hand-written lines are my own judgement. They are published with provenance and used only for evaluation. The synthetic stress set (§6) is reported separately and never used for thresholds or prompts. [A23, A37]

### 10.9 Robustness checks

| Check                                                               | Pass criterion                                                                                                                                                                                                                                                                                                                                                                                                     |
| ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| FR smoke set                                                        | 0 closed-world violations, 0 false `not_a_material`, 0 decoy matches, ≥ 9 of the 18 base positives correctly matched; k/n reported (Wilson CI for display); signal (b) fires on at least one FR concrete positive [A10, A35]                                                                                                                                                                                       |
| Library row-order shuffle (3 seeds)                                 | Identical rendered prompt hash for each rendering; byte-identical FakeLLM output [A46]                                                                                                                                                                                                                                                                                                                             |
| Row-id stability                                                    | ids unchanged under reordering and row addition; `'x'` ≠ `'x '`; 0 collisions on both libraries                                                                                                                                                                                                                                                                                                                    |
| Structural headers                                                  | The FR input renumbered `1 / 1.1 / 1.1.1`, and the FR input with codes removed, give the same header decisions and paths as the original [A48]                                                                                                                                                                                                                                                                     |
| Failure injection (FakeLLM fault matrix + property-based schedules) | 0 lost lines; every affected line `needs_review` with its failure code; nothing matched from a failed or partial response. Includes: ids swapped within a batch → 0 matched; pass 2 times out → `partial_signal`; a line containing `</line><line id="L3">` plus a JSON-breaking quote → no other line changes; a 429 storm with retry-after 20 s over 300 lines → 0 lost and 0 `LLM_UNAVAILABLE` lines [A39, A43] |
| Evidence check false rejects                                        | D5a measured on dev by $0 replay; if it rejects more than 3 correct matches, the validator's evidence tolerance is widened (A59.1); the prompt and prompt_version are unchanged because the limit is not part of the generated schema (logged) [A43]                                                                                                                                                               |
| CLI/API parity                                                      | The whole EN file through the API and through the CLI under ReplayLLM gives identical decisions and request hashes [A50]                                                                                                                                                                                                                                                                                           |
| Determinism                                                         | 40 lines run twice live → decision and row flip rate reported; replay byte-identical on Linux and Windows [A49]                                                                                                                                                                                                                                                                                                    |
| Batch contamination                                                 | B = 1 vs B = 10: \|Δ accuracy\| within the noise floor                                                                                                                                                                                                                                                                                                                                                             |
| Synthetic FR stress set (deferred, §8)                              | Reported separately as "synthetic stress test (optimistic)"; never a gate [A23]                                                                                                                                                                                                                                                                                                                                    |

---

## 11. Engineering contract `[Rubric: Engineered]`

### 11.1 Module boundaries

```
src/oris_matcher/
  domain/      boq.py  library.py  normalize.py  attributes.py  decision.py  batching.py   pure, no I/O
  llm/         base.py (port, LLMRequest, LLMResult)  wrapper.py (retry, budget, cost, record, cache)
               anthropic_llm.py  openai_llm.py  fake_llm.py  replay_llm.py
  candidates/  base.py (CandidateProvider)  whole_library.py  hybrid.py ([retrieval] extra)
  prompts/v1/  templates, schema.py (one Pydantic v2 model), glossary.yaml (with provenance)
  service.py   async MatchService: the only orchestrator
  settings.py  pydantic-settings, prefix ORIS_
  io/          boq_reader.py  writer.py  audit.py
  cli.py       Typer: match (default) · score · replay · explain · select · doctor · demo · serve
  api/         app.py  jobs.py  static/ui/ (committed build)
config/        policy.yaml  models.toml  pricing.toml  never_match.yaml  service_units.yaml
               unit_aliases.yaml  supply_markers.yaml  enrichment/
eval/          score.py (standard library only)  check_requirements.py  report.py  baseline_tfidf.py
               make_split.py  experiments.jsonl
runs/submission/   committed evidence (§9.6)
evidence/      doctor_<date>.json
tests/         … contract/test_llm_port.py
.github/workflows/ci.yml
```

[A11, A24, A33, A36, A37, A44, A45, A48, A49, A50]

Three rules hold throughout:

- **The domain never imports from `llm/`, `io/` or `api/`.**
- **Decision logic exists in one place.** The CLI, API and UI all call `MatchService`.
- **The port refuses to construct any adapter whose model is not in `config/models.toml`.** [A33]

### 11.2 Invariants, each enforced by a named test

| Invariant                                                                                                                     | Test                                                                          |
| ----------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| Output length and order equal the input, with unique line ids                                                                 | property test under random FakeLLM corruption                                 |
| Every `matched` triple is a library row, verbatim                                                                             | property test; RQ4                                                            |
| `not_a_material` only from D0, D0a or D2; never on a measured unit                                                            | decision truth table; RQ5 [A48]                                               |
| Every line has an audit record; every API attempt has a call record with provider request id, served model and finish reasons | RQ6 [A45]                                                                     |
| Attributed line costs sum to the run total                                                                                    | RQ7 [A45]                                                                     |
| Replay of a run yields a byte-identical CSV on Linux and Windows                                                              | golden replay test in the CI matrix; RQ11 on the committed outputs [A36, A49] |
| A replay miss never reaches a live call                                                                                       | ReplayLLM test: raises in tests, `replay_miss` at runtime [A50]               |
| CLI and API give the same decisions and request hashes                                                                        | parity test [A50]                                                             |
| Every served model is on the allowlist                                                                                        | port unit test; RQ10 [A33]                                                    |
| Nothing global-library-specific in `src/`                                                                                     | grep guard + full run on the FR library under FakeLLM                         |
| No ground truth in `src/`                                                                                                     | leakage guard                                                                 |

### 11.3 Runtime contract

| Concern                           | Contract                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Per-call timeout                  | 30 s; SDK retries off, so every attempt is logged                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| Attempts and retries              | 1 call + up to 2 retries, hand-rolled. Retryable: connection errors, timeouts, 408, 409, 429, 500–599 including 529, `x-should-retry: true`. Not retryable: 400, 401, 403, 404, 413, `x-should-retry: false`, refusal. Wait = max(retry-after, exponential backoff with jitter) [A12, A44]                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| Per-line budget                   | 6 model calls and 90 s of in-flight time across retries, re-asks and bisection; semaphore and retry-after waits do not count. Then `needs_review` with the failure code [A12, A39]                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Concurrency                       | fixed semaphore of 4 (also in `docs/ui-spec.md`). The first batch of a run goes alone so the cache is written once, then the rest fan out. AIMD (halve on 429, +1 per 10 successes) is added only if a dev run records a 429 (deferred, §8) [A12, A46]                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Throttling                        | a 429, or a 529 carrying retry-after, is throttling: it is retried and never counts toward the breaker. `oris doctor` records the account tier and the `anthropic-ratelimit-*` headers; if the projected cold run for 282 routed lines at the frozen pass count exceeds 2 s/line at that tier, the account moves up a tier before G4, and this is logged [A39]                                                                                                                                                                                                                                                                                                                                                                 |
| Response checks                   | in order: `stop_reason` (refusal → `LLM_FAILURE:refusal`; max_tokens → truncated, then bisection) → Pydantic strict validation (ranges, lengths, enums compared case-insensitively) → case-sensitive code check against the library [A44]                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| Truncation / malformed output     | bisect the batch down to single lines within the per-line budget, then `LLM_FAILURE:truncated` / `malformed` [A12]                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Missing / duplicate / unknown ids | missing ids are re-asked within the per-line budget; byte-identical duplicates collapse; conflicting duplicates are re-asked once, then `LLM_FAILURE:duplicate_conflict`; unknown ids are ignored and logged. Every raw response is kept in `calls.jsonl` [A8]                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| Circuit breaker                   | ≥ 10 consecutive non-throttling failures, or (deferred rule, §8) > 30% non-throttling failures over 20 calls → stop calling the model; pending lines `LLM_UNAVAILABLE`, unless the fallback adapter takes over (below); output still written; CLI exit code 3 [A33, A39]                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Model and fallback                | pinned `claude-haiku-4-5-20251001`; each call record stores the requested and the served model. The fallback chain holds only allowed models: (1) a newer claude-haiku snapshot, if published; (2) gpt-4o-mini (pinned snapshot) through `OpenAIChatLLM` behind the same port (official openai SDK, strict json_schema, same Pydantic schema), built in G1. Selected by `--llm openai:gpt-4o-mini`, or automatically after the breaker trips; it runs the strictest threshold unless certified (§10.6); `fallback_model` is recorded in the manifest and the per-line model column. Allowlist in `config/models.toml`: {claude-haiku-\*, gpt-4o-mini\*, gemini-\*-flash\*, local:≤8B}. No other model is ever configured [A33] |
| Budget                            | before each dispatch, reserve input cost (the prefix at the cache-write rate until a cache read is observed, then at the read rate) + max_tokens × output price; dispatch when spent + reserved ≤ cap ($1.80 / 100 lines); otherwise wait for reservations in flight to settle, and refuse only when spent + reservation > cap [A61]; reconcile on usage; undispatched rows become `BUDGET_CAP` [A12, A46]                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Replay miss                       | raises in tests; at runtime the line becomes `needs_review` with `LLM_FAILURE:replay_miss`, the exit code is 3, and nothing falls through to a live call [A50]                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| API partial failure               | always HTTP 200 with every line present and a per-line reason; never a 5xx for model failures                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| API limits                        | `POST /v1/match`: soft limit 100 lines (documented), hard limit 500 (413 above); field caps and auth in §11.4. There is no request-level deadline. The CLI and UI chunk automatically [A50, A51]                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |

### 11.4 API contract (`/v1`, additive-only)

```
POST /v1/match
  request:  {library: <id from Settings.libraries, e.g. "global" | "fr">,
             lines: [{item_no, short_description, long_description, unit, qty, section_path?}]}
  response: {run_id, versions: {model, prompt, library_sha256, policy, policy_resolution},
             decisions: [{line_index, item_no, decision, material_type, material_usage,
                          material_subtype, reason, suggestions: [top1, top2], committed_levels?,
                          model, prompt_version, cost_usd, latency_ms, call_ids}],
             summary: {counts, cost_usd, mean_latency_ms, failures}}
POST /v1/jobs, GET /v1/jobs/{id}, GET /v1/jobs/{id}/result[.csv]   (UI progress path, deferred with the UI)
GET  /health  (liveness)    GET /ready (library loaded, API key configured; no model call)
GET  /v1/diagnostics/llm    (one explicit, logged connectivity call, on demand)
```

[A13, A21, A36, A38, A50, A51]

- Routes are `async def` and await `MatchService.match` directly. [A50]
- `library` is an id from `Settings.libraries`; anything else → 422. Clients never send paths. Request models use `extra='forbid'`. Field caps: item_no ≤ 64, short ≤ 1,000, long ≤ 4,000 characters (→ 422). One test per limit. [A51]
- `section_path` is optional. When it is absent, the server derives it from header rows in the request; when that is impossible, the prompt says "no section context". [A50]
- `committed_levels` (type, or type+usage) appears only when that prefix is unanimous across passes. It never changes the decision. [A21]
- If `ORIS_API_TOKEN` is set, `/v1/*` requires a Bearer token; `/health` and `/ready` stay open. Every response carries `X-Request-ID` = run_id. No CORS middleware. [A51]
- Jobs are in-memory, single worker, at most 4 queued, results dropped after 1 h, cancelled on shutdown. [A14]
- Validation errors return a 422 envelope.
- New fields are optional; anything breaking goes to `/v2`.
- Labels are verbatim library strings.

### 11.5 Reproducibility and determinism

**Reproducibility:**

- Python 3.12 with uv and `uv.lock` (§11.10). [A49]
- The model is pinned to `claude-haiku-4-5-20251001` and recorded in the manifest; the README states its published retirement floor (not before 2026-10-15). [A26, A33]
- Each run writes a manifest (§9.6).
- The response cache key is the SHA-256 of the canonical serialised request: model snapshot, system blocks (rendered library, enrichment, glossary), schema, temperature, max_tokens and the full user message, so batch neighbours and section paths are part of it. The policy is not in the key, so decisions are re-derived from cached responses when a threshold changes. Submitted runs use `--no-cache`. [A9, A45]
- The committed submission runs (§9.6) let a clean clone replay both outputs. `uv run oris demo` replays the committed runs on both files (no key, $0, byte-identical), then runs `eval/score.py` and `eval/check_requirements.py` on the result; `oris demo --live` makes a cold paid run, whose numbers differ from the committed ones by up to the measured flip rate. [A26, A36, A49]
- A Dockerfile (core dependencies only, non-root, 1 worker) is optional and deferred (§8). [A26, A37, A49]

**Determinism contract:** the same manifest plus the same `calls.jsonl` produce a byte-identical output, on Linux and on Windows. Live runs are _not_ claimed to be deterministic; their flip rate is measured instead. [A9, A49]

### 11.6 Observability

- Structured JSON logs carry `run_id` and `line_index`.
- `calls.jsonl` uses the OTel GenAI field names (`gen_ai.*`), with the semconv version pinned in the manifest, so the log loads into Phoenix or Langfuse unchanged. No tracing server sits on the grading path (D-18). [A45, A52]
- Each run prints a summary to stdout, the manifest and the API summary:
  - decision shares;
  - reason-code histogram;
  - retries and cache hits, with cache read and write tokens; [A46]
  - the policy resolution; [A38]
  - p50/p95 latency;
  - $ per 100 lines.
- **Three metrics ORIS should alert on:**
  - the `needs_review` share jumping more than 20 points above a client's baseline (drift or new vocabulary);
  - an `LLM_FAILURE` rate above 5%;
  - cost per 100 lines above $1.
- **G6 spike (1 h, only if G5 is green):** export one committed run's `calls.jsonl` to Phoenix through OpenInference, to show how the records plug into an observability backend. Nothing on the grading path depends on it. [A45, A52]

### 11.7 Tests

All of these run offline in CI, on Linux and Windows, with FakeLLM and ReplayLLM and pytest-socket restricted to loopback (`--allow-hosts=127.0.0.1,::1`), so no test can reach an external host: [A49, A53]

- `ruff check`, `ruff format --check`, `mypy --strict src`; coverage ≥ 80% on the domain and the service; [A49]
- the decision truth table;
- property tests (`hypothesis`) for the invariants;
- the fault matrix: timeout, 429 with retry-after, 5xx, 529, malformed, truncation, refusal, missing / duplicate / conflicting / unknown ids, ids swapped within a batch, a timed-out second pass, record-boundary forgery, a 429 storm; [A8, A39, A43]
- LLM port contract tests (§11.9); [A44]
- the allowlist test: the port refuses a model outside `config/models.toml`; [A33]
- the scorer fixtures (§10.1); [A34]
- the API contract, with one test per limit; [A51]
- CLI/API parity under ReplayLLM; [A50]
- policy-resolution tests: the literal brief command on the FR library → the smoke-certified entry; the FR library with one byte changed → strictest; the global library → the selected threshold; the fallback model on the global library → strictest; [A38]
- a full run on the FR library;
- the structural-header fixtures (§10.9); [A48]
- the split-hash test;
- leakage and global-string guards;
- library shuffle and row-id stability;
- normalisation and encoding round trips, including the cp1252 fallback; [A15]
- golden replay and the prompt-hash snapshot, byte for byte on both operating systems. [A49]

Tests that need the live API are marked and never run in CI.

`eval/check_requirements.py` gates the committed outputs on eleven checks, always through the scorer's `--strict` mode: [A34, A36]

- RQ1 columns;
- RQ2 length and order;
- RQ3 the decision enum;
- RQ4 closed-world labels;
- RQ5 `not_a_material` provenance: reason ∈ {HEADER, EMPTY_ROW, G2_SERVICE}; [A30, A48]
- RQ6 one audit record per line, and non-null provider_request_id, served model and finish_reasons on every successful call; [A45]
- RQ7 cost, from manifests with mode = live and cache_hits = 0, with attributed line costs summing to the run total; [A45]
- RQ8 latency per routed line on a cold live run (N_routed = 282 on these files); [A1, A12, A45]
- RQ9 manifest completeness;
- RQ10 every distinct served model in `calls.jsonl` is on the allowlist; [A33]
- RQ11 `oris replay runs/submission/<b3_run> --check output/improved_output_{en,fr}.csv` is byte-identical. [A36]

### 11.8 Data handling and threat model

**Data minimisation**

- Only descriptions, the unit and the section path leave the machine. Vendors that may receive line text: Anthropic (always); OpenAI (only when the fallback adapter or an adopted GPT-4o-mini voter runs); Google (only if a Gemini Flash voter is adopted). [A17, A33]
- Run folders are local and ignored by git, except `runs/submission/`, which holds runs on ORIS's exercise files only (§9.6). [A36]
- The API key comes from the environment through `Settings` (as a `SecretStr`) and is never logged. [A45]
- A client who forbids external APIs can be served through the LLM port with a local model of 8B parameters or fewer, at an accuracy cost measured once if A25 runs (§8). [A25]

**Prompt injection (text inside a BoQ line):**

|                | What happens                                                                                                                                                                                                                                                                                                  |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Guaranteed     | No invented triple (closed-world validation). No line with a measured unit is auto-skipped, because G2 requires a service unit. No lost line [A11]                                                                                                                                                            |
| Not guaranteed | A wrong-but-valid match: mitigated, not prevented, by attribute vetoes, the evidence check (D5a) and abstention. A material priced in a service unit, with no extracted attribute and no supply marker, skipped because the model calls it non-material: this residual risk is measured by fixture [A11, A43] |

Lines are sent as one JSON object, so line text cannot forge a record boundary, and long fields are capped with a `TRUNCATED` flag (§9.1). Adversarial fixtures: a material priced in LS; an injected instruction asking for a valid but wrong row; a line containing `</line><line id="L3">` plus a JSON-breaking quote. [A11, A43, A51]

### 11.9 LLM port contract

- `class LLMPort(Protocol): async def complete(req: LLMRequest) -> LLMResult`.
- `LLMRequest`: model, system blocks, schema, user payload, max_tokens, temperature, and a canonical `sha256()` that is the cache key (§11.5).
- `LLMResult`: status ∈ {ok, timeout, rate_limited, overloaded, api_error, truncated, refusal, malformed}, raw_text, parsed (or None), usage {input, output, cache_read, cache_write}, latency_ms, served_model, provider_request_id, retry_after_s.
- Adapters never raise for API failures. They call `messages.create` (never `parse`) and always return `raw_text`.
- Response checks run in the order of §11.3. The schema is one Pydantic v2 model; its generated JSON schema is hashed into `prompt_version`.
- Retry, budget, cost (`config/pricing.toml`, keyed by provider and model), recording and the cache live in one wrapper outside the adapters. ReplayLLM is that cache with miss → `replay_miss`.
- `tests/contract/test_llm_port.py` is parametrised over Fake, Replay and Anthropic. The Anthropic case injects an HTTP `MockTransport` through the SDK's `http_client`, serving 200, 429 with retry-after, 529, max_tokens and refusal. No instructor.

[A44]

### 11.10 Stack and CI

- **Runtime:** Python 3.12 (`>=3.12,<3.13`), uv + `uv.lock`, hatchling, package `oris_matcher` everywhere, console script `oris`.
- **Core:** anthropic (minor pinned), openai, pydantic ≥ 2, pydantic-settings, fastapi, uvicorn, python-multipart, typer, pyyaml.
- **Extras:** `[xlsx]` openpyxl + defusedxml; `[eval]` pandas, numpy, scipy, scikit-learn, matplotlib (report and baselines only; `score.py` stays standard library); `[retrieval]` the dense and BM25 dependencies of the `HybridRetriever`, never needed by the core install or the Docker image.
- **Dev:** ruff, mypy, pytest, pytest-asyncio, hypothesis, pytest-socket, pytest-cov.
- **Not used:** LangChain, LlamaIndex, LiteLLM or any gateway, instructor, Celery/Redis, any database (D-17).
- **Settings:** one pydantic-settings `Settings` (prefix `ORIS_`, keys as `SecretStr`). Domain YAML loads with `extra='forbid'` and fails at startup if invalid.
- **Portability:** explicit encodings enforced by ruff PLW1514; the CLI calls `sys.stdout.reconfigure(encoding='utf-8', errors='replace')`; manifests use repo-relative POSIX paths; outputs as in §9.6.
- **CI** (ubuntu-latest + windows-latest): `uv sync --locked --all-extras --no-extra retrieval` → `ruff check` → `ruff format --check` → `mypy --strict src` → `pytest --cov` (pytest-socket, loopback only [A53]), with no keys; `HybridRetriever` tests are skipped when the `[retrieval]` extra is absent. The golden replay and the prompt-hash snapshot must match byte for byte on both operating systems.
- **Dockerfile:** core dependencies only, non-root, 1 worker; optional and deferred. `make demo` is replaced by `uv run oris demo`.

[A24, A37, A45, A49]

---

## 12. Extension points and the live session `[Rubric: Defend]`

| Likely request                                     | How it is done                                                                                                                                                                                                                                                        | Time               |
| -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------ |
| Run an unseen BoQ on the FR library                | the brief's literal command, `uv run oris --input new.csv --library data/oris_materials_fr.csv --output out.csv` (`match` is the default). The run prints its policy resolution: the smoke-certified FR entry for the pinned model, otherwise the strictest threshold | run time [A38]     |
| "Favour coverage" / "target 85% or 95%"            | `oris select --target 0.95` re-runs the §10.6 rule on cached votes → new P / coverage / review load in seconds, $0. Or edit `config/policy.yaml` → `oris replay runs/<id> --policy policy.yaml`                                                                       | < 2 min [A27, A31] |
| "Why did line X get this?"                         | `oris explain --run <id> --item 03.03.0010.`: the stored prompt (`prompts/<sha256>.txt` + user message), the raw response and the line's slice of it, validator checks, rule fired, attributed cost and latency, provider request id                                  | < 1 min [A45]      |
| "What about a 10k-row library?"                    | the D-01 tiers apply; switch to the `HybridRetriever` provider and show its dev recall@k (§10.5)                                                                                                                                                                      | ~5 min [A24, A27]  |
| Add an attribute (e.g. steel grade, pipe SN class) | add a pure extractor to the registry + copy the test template; it runs on lines and library rows alike                                                                                                                                                                | ~5 min             |
| Swap the model                                     | `--llm` selects the adapter, e.g. `--llm openai:gpt-4o-mini`; a new adapter implements `LLMPort.complete`; the port refuses any model outside the allowlist                                                                                                           | ~10 min [A33, A44] |
| A new unit or service unit                         | `config/unit_aliases.yaml` or `config/service_units.yaml` + truth-table test                                                                                                                                                                                          | ~3 min [A48]       |
| A new reason code                                  | enum + decision-table row + test                                                                                                                                                                                                                                      | ~5 min             |

**Rehearsed before the session:**

- each row above;
- four traced cases: a correct match, a plausible wrong match, an abstention, and an API failure that ends in review; [A16]
- a run on a deliberately mangled copy of the FR input (no item codes, renumbered `1 / 1.1 / 1.1.1`, `;` delimiter, renamed columns, cp1252, XLSX); [A15, A48]
- `oris doctor`, on Thursday and 30 min before the session. It checks: keys present; pinned model served; one 2-line call with the production schema accepted; `count_tokens` per library and whether it is cache-eligible; the rate-limit tier from response headers; the fallback adapter answers. Its output is a one-screen pass/fail table saved to `evidence/doctor_<date>.json`; [A33, A39]
- one run with `ANTHROPIC_API_KEY` unset, to rehearse the fallback adapter. ReplayLLM is only the offline demo of the provided files; it cannot serve an unseen BoQ. [A33]

**README requirements**, beyond how to run it, the results, the known weaknesses and what I would do with more time:

- a "Live session" section with the exact command and the expected policy-resolution line; [A38]
- the memory note: "No conversational or correction memory is used. The classifier is stateless and closed-world; label-derived memory would leak (D-02, R3). Reviewer-correction memory keyed by library hash is a production next step."; [A52]
- "Scaling to large libraries": the measured lexical recall@20, the D-01 tiers, and the `HybridRetriever` with its dev recall@k if it ran; [A24, A37]
- the local ≤ 8B adapter: its benchmark if A25 ran, otherwise "admitted by the port, not benchmarked"; [A25, A37]
- the rate-limit tier the latency numbers were measured on; [A39]
- the pinned model and its published retirement floor (not before 2026-10-15); [A26]
- that `uv run oris demo` replays the committed runs, and that live numbers differ from them by up to the measured flip rate; [A36]
- the labelled-data disclosure (§10.8);
- under "with more time": a carbon-weighted error metric, once ORIS CO₂ factors are available; [R2]
- from v0.1 on, a provisional version marked "provisional, pre-freeze". [A32]

---

## 13. Operator UI (optional deliverable)

**What it does.** A single page served by the same FastAPI process at `/ui`, built from strict TypeScript with esbuild. The build output is committed, so reviewers need no Node.

| #   | The brief asks                          | The UI does                                                                                                                                                                                          | Proved by                                 |
| --- | --------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------- |
| U1  | Upload a CSV or Excel BoQ               | Drop zone; the server parses it with the shared reader; uploads ≤ 2 MB / 2,000 rows; row identity is the §9.1 line id                                                                                | CSV/XLSX row-id parity test [A15, A51]    |
| U2  | Run the matching                        | One button; a job with a progress bar (§11.4); the job runs the whole file through `plan_batches` with whole-file section paths                                                                      | job lifecycle test (FakeLLM) [A14, A50]   |
| U3  | Show the table with decision and labels | Results table with the verbatim labels, reason, suggestions and an audit drawer; the drawer shows the reason code and evidence first, then the confidence labelled "model self-report, uncalibrated" | row count and order equal the input [A14] |
| U4  | `needs_review` easy to spot             | Amber chip with a triangle shape **and** text, amber row border, one-click filter, count in the summary. The table keeps input order, so section context is never broken                             | DOM assertion + manual check [A14]        |

**Design principles:**

- **No business logic in the browser.** The downloaded CSV is the server's file, byte-identical to the CLI output.
- No time-saved figure appears in the UI; the report keeps the one assumptions table (§2). [A14]
- Mobile-first, keyboard accessible, light and dark.
- Five colour tokens, system fonts, inline SVG icons.
- No ORIS branding.

**Schedule:** built only after `eval-freeze`, both outputs and the clean-clone check, in G6 with a hard stop at Fri 12:00. It is first in the cut order (§8). The full specification, with wireframe, states and tokens, is in `docs/ui-spec.md`. [A14, A32]

---

## 14. Schedule: fixed-time gates

Work started on Sunday 4 October. Submission is Friday 9 October, 17:00 Paris, a hard stop. A gate opens only when the previous gate's evidence files exist, with one exception: G4's freeze runs at its cap (Thu 8 Oct 12:00) on the latest green dev configuration, whatever gates remain open (§10.3). Each gate also has a time cap: at the cap it ships what is green, documents the rest as in progress, and ends in a submittable tag. Fri 12:00–17:00 is for the README and the submission only. [A16, A32]

| Gate                 | Cap                           | Work                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | Gate evidence                                                                                                                                                     | Tag  | Rubric checked                                                                        |
| -------------------- | ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- | ------------------------------------------------------------------------------------- |
| G0 spec              | Mon 5, 10:00                  | Pre-registration v2 (this file); `prereg-v1` is already tagged                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | this file; split SHA in the status note                                                                                                                           | —    | Data; design-doc questions [A30]                                                      |
| G1 v0.1 skeleton     | **Tue 6, 12:00**              | Thin slice first (§7.1). Then: the `oris` CLI and `POST /v1/match` on one async `MatchService` with `plan_batches`; reader, closed-world validator, G1 header gate, decision table with D1b and D5a; every LLM failure mapped to `needs_review` with a reason code; LLM port, wrapper and contract tests; Haiku and GPT-4o-mini adapters with the allowlist; audit columns, `calls.jsonl` and manifest; `eval/score.py`; `check_requirements.py`; B0, B1 and B2 (a single Haiku pass) on a stratified 40-line dev slice, then all 139 dev lines, **dev only**; the CI matrix; `oris doctor` with the tier check; the 20-line golden replay fixture; a README marked "provisional, pre-freeze". `output/` holds B0 placeholders (rules only, no model call), labelled as such | B0 / B1 / v0.1 dev numbers; G1 37/37; validator property test and CI green                                                                                        | v0.1 | Baseline (started); engineered [A4, A32, A33, A34, A36, A39, A43, A44, A45, A49, A50] |
| G2 selection         | Wed 7, 12:00                  | The E-02 signals (b from the line side, v with k = 2, confidence); the §10.6 selection on cached votes; error taxonomy; policy resolution; triggered arms, only within the cap; the optional ~$0.50 fallback certification if G2 closes on time                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | the ledger and `eval/experiments.md`; the selected threshold and the dev-bar result per language (or the documented shortfall); risk–coverage and trade-off table | v0.2 | Trade-offs; data (EN vs FR) [A31, A33, A35, A38, A46]                                 |
| G3 hardening         | Wed 7, 22:00                  | Fault matrix including the 429 storm; consecutive-failure breaker; structural headers; FR smoke set (base + hand-written lines) labelled, hashed and run; CLI/API parity; API limits; replay; one cold live run over all dev lines (no lockbox line) for cost, latency at the recorded tier and the noise floor; the full-file cold run for 282 routed lines is projected from it (A39) and measured only in the post-freeze lockbox session                                                                                                                                                                                                                                                                                                                                 | fault matrix green; smoke-set k/n; cold-run cost and latency                                                                                                      | v0.3 | Engineered [A10, A16, A37, A39, A48, A50, A51]                                        |
| Deferred, pre-freeze | within G4's cap               | If G3 closes early, in order: A22 resampling, the calibration battery, A23 set generation + hash, A24 recall@k on dev, A25 local benchmark on the 40-line slice, the A17 voter if triggered, then AIMD (only after a recorded 429) and the windowed breaker rule (§8)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | each as a dated dev diagnostic                                                                                                                                    | —    | Baseline + confidence [A32]                                                           |
| G4 freeze + lockbox  | **Thu 8, 12:00, hard latest** | `eval-freeze` on the latest green dev configuration, whatever remains open; the single lockbox session (B2 then B3, §10.3); both outputs; `runs/submission/` committed; RQ11 in CI; the like-for-like baseline table; `docs/evaluation.md`; §17 Results                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | `check_requirements.py` green on both outputs; one session in `eval/lockbox_log.md`                                                                               | v1.0 | Engineered; baseline + confidence [A6, A32, A36, A47]                                 |
| G5 release           | Thu 8, 18:00                  | Clean clone on Windows and Linux; `oris doctor`; rehearsal with the key unset; the four traced cases; the A27 rehearsals; final README (run, weaknesses, more time, live session)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | fresh clone: `uv sync --locked && uv run pytest && uv run oris --input … --library … --output …` green                                                            | —    | Defend; all [A16, A27, A33]                                                           |
| G6 optional          | Fri 9, 12:00, hard stop       | Only if G5 is green: UI with `/v1/jobs`; Phoenix/OpenInference export spike (1 h); Dockerfile                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | U1–U4                                                                                                                                                             | —    | Optional UI [A14, A32]                                                                |
| Submission           | Fri 9, 12:00–17:00            | README polish; submit by 17:00                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | submitted                                                                                                                                                         | —    | — [A32]                                                                               |

---

## 15. Risks

| Risk                                                                             | Likelihood | Mitigation                                                                                                                                                                                                                          |
| -------------------------------------------------------------------------------- | ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Lockbox precision below .90 in one language                                      | medium     | Reported honestly per §10.3, with its one-sided exact bound. Never tuned on the lockbox. If no threshold meets the dev bar, the shipped one is labelled "below the dev bar" (§10.6) [A31]                                           |
| French usage accuracy stays low (localised rewrite)                              | high       | E-01 arms; the `element_or_application` field; abstention routes uncertain FR lines to review rather than guessing [A43]                                                                                                            |
| Uncalibrated policy on the FR library over-matches live                          | medium     | Policy resolution: an unknown (model, library) pair runs the strictest threshold; the smoke-set rule with 0 decoy matches and a useful-match floor; signal (b) judged from the line side, so it can fire on FR rows [A10, A35, A38] |
| Unseen BoQ structure (no codes, renumbering, `;`, XLSX, renamed columns, cp1252) | medium     | Shared reader with aliases and the cp1252 fallback; structural headers; `HEADER_UNCONFIRMED` degrades to review; rehearsed on a mangled copy [A15, A48]                                                                             |
| Rate limits or a network outage during the session                               | low–medium | `oris doctor` records the tier; throttling never trips the breaker; the allowlisted fallback adapter, rehearsed with the key unset; AIMD after a recorded 429; `--limit` for a subset [A12, A33, A39]                               |
| The pinned model snapshot is no longer served                                    | low        | `oris doctor` checks it before the session; the fallback chain holds only allowlisted small models [A26, A33]                                                                                                                       |
| ORIS's reference file breaks the scorer                                          | medium     | Lenient join and cell rules; fixtures for a labels-only reference, a renamed key, duplicates, `nan` literals and NFD text (§10.1) [A34]                                                                                             |
| A schedule slip                                                                  | medium     | Fixed-time gates with submittable tags; freeze at Thu 12:00 at the latest; the deferred list and the cut order (§8) [A32]                                                                                                           |
| A wrong-but-valid match forced by injected text                                  | low        | Not prevented, only mitigated (§11.8): JSON-wrapped batches and the evidence check; disclosed [A43]                                                                                                                                 |

---

## 16. Amendments

_Append-only. Each entry gives the date, what changed, why, and which results existed at the time._

_v2 note: A1–A3 keep their full text below and are also merged into the body (§3, §3.2, §6, §9.3, §10.1, §11.7). A4–A52 were merged into the sections listed in the index that follows; their full texts are kept with the planning records, outside this repository._ [A30]

### A1 · 2026-10-04 · Lines routed to the model: 282, not 273

**Results existing at the time:** none. No model call had been made.

**What changes:**

- `docs/evaluation-protocol.md` §1 gives N_routed as "about 273", which subtracts the 9 service-unit lines. Under D-04 / §9.5 D2, gate G2 _needs the model's_ non-material vote, so those lines are sent to the model too.
- Only the 37 headers are decided by rules with no call.
- **N_routed = 282 on these files**, and RQ8 (latency per routed line) uses it.

**Why:** an internal inconsistency found in review.

### A2 · 2026-10-04 · Process lexicon: add "reduced temperature"; recycled-content implicit zero

**Results existing at the time:** none.

**What changes:**

1. **The English BoQ never writes "WMA".** Warm-mix lines say "reduced temperature" (9 lines; "warm" 1, "WMA" 0). The §9.3 WMA pattern becomes `\bwma\b|warm|ti[èe]de|temp[ée]rature abaiss[ée]e|reduced temperature`.
2. **Implicit zero.** A library row that states no recycled percentage is treated as recycled content 0 **only when a sibling in its type+usage pair states a positive percentage**. Example: `Asphalt Concrete (AC) - HMA` next to `… - HMA 30% RAP`. A sibling that says only "virgin" does not trigger it.

**Checked on the data (no model involved), on dev only (lockbox lines not checked):** with these rules, across all 139 labelled dev lines, the ground-truth row is never in `conflict`.

| Language | `literal` | `neutral` | `conflict` |
| -------- | --------- | --------- | ---------- |
| EN       | 55        | 84        | 0          |
| FR       | 57        | 82        | 0          |

The veto leaves a median of 342 and a minimum of 266 candidate rows per line, which confirms D-08: the extractors veto, they do not filter.

**Why:** a lexicon gap found while reading dev lines (provenance `dev_obs`, a general domain fact rather than item text). The zero rule makes explicit a sibling rule that §9.3 left unstated.

### A3 · 2026-10-04 · Requirement count

§3 says the requirements are condensed from "90 atomic requirements". `docs/requirements-traceability.md` holds **87**. The count is corrected here; no requirement was dropped.

**Results existing at the time:** none.

### Index of A4–A52 · 2026-10-04 · pre-registration v2

**Results existing at the time:** none. No model call had been made; v1 is tag `prereg-v1` (bc5c8a2). Format: A-n · section(s) changed · title · status.

- A4 · §0, §7.1, §7.2, §14 · Evidence-gated order of work · merged before any model call
- A5 · §7.2, §10.2, §10.4, §10.8 · Twins and the meaning of the holdout · merged before any model call
- A6 · §10.3, §10.5, §14 · How B2 legitimately reaches the lockbox · merged before any model call
- A7 · §3.2, §3.3, §10.1 · A hostile scorer, and H₂₆₅ demoted to a diagnostic (its hard errors survive as the scorer's `--strict` mode under A34) · merged before any model call
- A8 · §5.3 D-07, §9.1, §9.5, §11.3 · Conflicting duplicates fail closed · merged before any model call
- A9 · §11.5 · Cache the full request · merged before any model call
- A10 · §6, §8, §10.9, §14 · A useful-match floor for the French smoke set · merged before any model call
- A11 · §1, §5.3 D-04, §6, §9.3, §9.5, §11.8 · Safety claims narrowed to what the code proves · merged before any model call
- A12 · §3.2, §3.3, §7.2, §8, §10.1, §11.3 · Attempts, budget and latency defined precisely · merged before any model call
- A13 · §11.4 · /ready makes no paid call · merged before any model call
- A14 · §2, §5.3 D-16, §8, §11.4, §13 · UI ordering and honesty · merged before any model call
- A15 · §4.6, §5.2, §5.3 D-01, §7.1, §9.1, §9.2, §10.5, §12, §13 · One parser contract and cross-document fixes · merged before any model call
- A16 · §12, §14 · Gates replace the day schedule · merged before any model call
- A17 · §5.3 D-10, §7.2, §8, §9.5, §11.8 · Cross-model agreement, gated on evidence · merged before any model call
- A18 · §10.4 · Certified selection replaces the .93 heuristic · superseded by A31 (its one-sided exact bound is kept for the lockbox claim)
- A19 · §6, §10.8 · Enrichment rules, with one wrong example removed · merged before any model call
- A20 · §7.2, §9.5 · Sibling verifier stays gated · merged before any model call
- A21 · §10.1, §11.4 · Suggestion quality on review lines · merged before any model call
- A22 · §8, §10.6, §10.7 · Selection stability and label audit, dev only · merged before any model call
- A23 · §6, §8, §10.8, §10.9 · Synthetic French stress set for the live condition · merged before any model call
- A24 · §5.1, §5.3 D-01, §8, §10.5, §12 · Retrieval as a measured scaling path · merged before any model call
- A25 · §5.3 D-10, §8, §11.8, §12 · Local ≤ 8B model, benchmarked once · merged before any model call
- A26 · §8, §11.5, §12, §15 · Reproducible demo and model pinning · merged before any model call; fallback sentences superseded by A33
- A27 · §12, §14 · Two more live-session rehearsals · merged before any model call
- A28 · §5.3 D-01, §9.2, §9.6 · Measured token sizes · merged before any model call
- A29 · §7.1, §10.5 · Baseline fitted without lockbox text · merged before any model call
- A30 · status note, §0, §9.5, §11.7, §16, all changed sections · Consolidate the spec before any code · merged before any model call
- A31 · §0, §1, §3.2, §5.3 D-12, §7.1, §10.1–§10.6, §15 · Nested thresholds, a dev bar of .95, an exact bound on the lockbox only · merged before any model call
- A32 · §0, §1, §8, §10.3, §12, §13, §14, §15 · Fixed-time gates, a submittable v0.1, a hard eval-freeze, a deferred list · merged before any model call (owner decision: nothing is cut in advance)
- A33 · §1, §3.2, §5.1, §5.3 D-10, §6, §10.6, §11.1–§11.3, §11.5, §11.7, §11.8, §12 · Fallback models from the allowlist only, an enforced allowlist, `oris doctor` · merged before any model call
- A34 · §0, §3.1, §4.6, §10.1, §11.7, §15 · Scorer contract that tolerates ORIS's reference file · merged before any model call
- A35 · §0, §6, §9.3, §9.5, §10.6, §10.9 · Signal (b) defined once, from the line side · merged before any model call
- A36 · §0, §1, §3.2, §9.6, §10.3, §11.2, §11.5, §11.7, §11.8, §12, `.gitignore` · Commit the evidence behind the submitted outputs · merged before any model call
- A37 · §5.1, §5.3 D-01, §6, §9.2, §10.8, §11.10, §12 · Cut A24, A25 and A23; soften A15(4) · considered; cuts not adopted, kept parts merged (see decisions): the `[retrieval]` extra, the A15(4) tiering, the hand-written FR smoke lines
- A38 · §1, §3.1, §5.2, §6, §9.2, §10.6, §11.4, §11.6, §11.7, §12 · Library- and model-aware policy resolution · merged before any model call
- A39 · §3.2, §9.4, §9.6, §10.9, §11.3, §12 · Rate limiting is throttling, not failure · merged before any model call
- A40 · §8, §11.3 · Simplify runtime failure handling and drop async jobs · considered; cuts not adopted, kept parts merged (see decisions): the A12 bisection cap, AIMD after a recorded 429, the §11.3 breaker (throttling never counts, A39) and `/v1/jobs` stay, with AIMD and the windowed breaker rule deferred
- A41 · §10.4, §10.6 · Trim the evaluation statistics · considered; cuts not adopted, kept parts merged (see decisions): A22 and the calibration battery stay as deferred dev diagnostics; the one-sided CP lower bound is the only bound for any "≥ 90%" claim, other intervals display only
- A42 · §6, §7.2 · Keep rule above run-to-run noise · merged before any model call
- A43 · §1, §5.2, §9.4, §9.5, §10.7, §10.9, §11.7, §11.8, §15 · Prompt input/output integrity · merged before any model call
- A44 · §5.1, §5.2, §9.4, §9.5, §11.3, §11.7, §11.9, §12 · LLM port contract, structured-output usage and retry classification · merged before any model call
- A45 · §3.2, §3.3, §9.6, §10.1, §10.3, §11.2, §11.5–§11.7, §12 · Audit and manifest contract (OTel GenAI field names) · merged before any model call
- A46 · §0, §2, §5.2, §7.2, §9.2, §9.4–§9.6, §10.6, §11.3 · Agreement signal as a vote count, with cache-aware dispatch · merged before any model call
- A47 · §1, §10.1, §10.5 · Like-for-like baseline table · merged before any model call
- A48 · §4.6, §5.2, §5.3 D-03/D-04, §6, §9.1, §9.5, §10.9, §11.7, §12 · Structural header detection and French units · merged before any model call
- A49 · §1, §3.1, §4.6, §9.1, §9.6, §11.1, §11.5, §11.7, §11.10 · Concrete stack, CI and portable output format · merged before any model call
- A50 · §3.1, §5.1, §5.2, §5.3 D-16, §9.1, §9.5, §11.2–§11.4, §13 · Service core: async, deterministic batching, replay that fails closed · merged before any model call
- A51 · §9.1, §11.4, §11.8, §13 · Minimal API hardening · merged before any model call
- A52 · §4.5, §5.3 D-02 and new D-18, §10.8, §11.6, §12 · Decision-register corrections · merged before any model call

### A53 · 2026-10-05 · Offline test guard: loopback only, not `--disable-socket`

`--disable-socket` breaks every async test on Windows, because the Proactor event loop creates a loopback socket pair at start-up (`AttributeError: 'ProactorEventLoop' object has no attribute '_ssock'`, seen on the empty scaffold). The guard becomes pytest-socket with `--allow-hosts=127.0.0.1,::1`: sockets can be created, and any connection to a non-loopback host fails the test. The guarantee that no test reaches a paid API is unchanged. Changed: §11.7, §11.10. Results existing at the time: none; no model call had been made.

### A54 · 2026-10-05 · The 40-item dev slice is fixed before the first model call

§7.1 step 3 and §14 G1 name "a stratified 40-line dev slice" without defining it. It is now `eval/slice_v1.json`, written by `eval/make_slice.py`:

- 40 items, drawn from `item_ids_dev` of `eval/split_v1.json` only (population 162: 139 labelled + 23 blank-GT), with the same ids in EN and FR.
- Strata: L1 section × reference class (labelled with a subtype, labelled with a blank subtype, blank-GT service, no-equivalent material from `eval/annotations/blank_line_classes.csv`). There are 33 strata, and places are allocated proportionally by largest remainder.
- Within a stratum, items are ranked by `sha256("oris-material-matcher/slice_v1" + item_no)`, with no RNG.
- `ids_sha256 = 248da575dde907de7df6e6e112b22f4930a3cafe3e5b2526cebc64aad6d565a7`; the file's SHA-256 is `b41cf9512032cfbd35ccce660ca03e18bc3b3c251b899d1e9cc229327be51f2f`. A test pins both.

The slice serves smoke runs and the A25 benchmark only. Selection (§10.6) always uses all dev items. Changed: §7.1, §14. Results existing at the time: B1 dev numbers and the label-free TF-IDF reproduction (no model call).

### A55 · 2026-10-05 · A file without item codes never auto-skips a header

§10.9 asked that the FR input with its codes removed give "the same header decisions" as the original. That would need a header rule based on text shape alone, which D-03 rejects, because a material line with an empty unit could then reach `not_a_material`. The criterion becomes: with codes removed, the **section paths** are identical to the original, and every former header is `HEADER_UNCONFIRMED` (D0b, `needs_review`); no row is `HEADER`. The renumbered `1 / 1.1 / 1.1.1` fixture still gives identical decisions and paths. Changed: §9.1, §10.9. Results existing at the time: none from any model.

### A56 · 2026-10-05 · Second suggestion in the output CSV

§10.1 reports hit@2, but the §9.6 columns carry only the first suggestion, so the scorer could not compute it from the CSV. Three columns are **appended after `call_ids`**: `suggested2_type, suggested2_usage, suggested2_subtype`, holding top-2 when it is a valid library row, otherwise blank. The A36 column order is unchanged. The scorer reads them when present, and prints hit@2 as n/a when they are absent. Changed: §9.6, §10.1. Results existing at the time: none from any model.

### A57 · 2026-10-05 · A per-run floor on the budget cap

The §11.3 cap of $1.80 per 100 lines scales with the run. For a run of a few lines it is smaller than one call's reservation: the cached library prefix at the cache-write rate plus `max_tokens` of output. So a one-line API request could never dispatch and always returned `BUDGET_CAP`. The cap becomes `max($1.80 × n_lines / 100, $0.10)`, and the effective cap is recorded in the manifest as `budget_cap_usd`. The $2.00 per 100 lines target (§10.1) is unchanged and is still measured on cold live runs. Changed: §11.3. Results existing at the time: none from any model.

### A58 · 2026-10-05 · Interpretations made while building G1

This records how the code resolves points that this document leaves open, so the spec and the code can be read side by side. None of them changes the split, the candidates, the selection rule or the form of the claim. Results existing at the time: the doctor and the B2 smoke and slice runs on dev (`docs/gates/G1.md`). None of the items below was chosen to change a measured number.

1. **B0's reason code.** B0 sends nothing to a model, so its non-header lines get `LOW_SIGNAL:v+b+confidence`: all three signals failed, because none exists. Changed: §10.5.
2. **Section paths compare code segments.** Codes are split on `.`, `-` and spaces, leading zeros are dropped, and a strict segment prefix is required. A plain string prefix cannot place `01.01.0010.` under L0 header `1`. The header rule itself stays exactly as in §9.1. Changed: §9.1.
3. **Hard attributes that cannot be compared never veto.** A line that states several values of one family ("DN 125 and DN 100", "CEM I or CEM III") or a decimal value is recorded as stating that family, so G2 still sees a hard attribute, but it has no comparable value, so it never vetoes. EWC codes after an exclusion phrase ("other than", "autres que") are dropped. Changed: §9.3.
4. **Units are matched case-sensitively.** `Ft` (forfait, a lump sum) is a service unit; `ft` and `FT` (feet) are measured units and never reach G2. Changed: §9.5 D2.
5. **Answers are validated line by line.** The response envelope is parsed once. Each line object is then validated on its own: valid lines are accepted, and an invalid one fails only its own line as `LLM_FAILURE:malformed`, with its raw JSON kept. §11.3 bisection still applies when the envelope is broken (truncation or invalid JSON). Before this, one over-long evidence quote failed all ten lines of its batch (`docs/gates/G1.md` O5). An invalid line is not re-asked, because the same prompt would produce the same violation. An invalid object with no usable id counts as missing and is re-asked within the line's budget. A valid and an invalid object for the same id count as a conflicting duplicate. A run recorded before this change that contains split calls caused by one invalid line would not replay byte for byte; no such run exists. The live doctor now passes its call check only on schema-valid answers. Changed: §11.3.
6. **Run modes.** A run's manifest mode is `fake` (FakeLLM), `replay`, `cached` (at least one cache hit, with `source_run_id` set), `live`, or `rules` (B0, no model call). The response cache is seeded only from earlier `live` runs of the same provider and model. Changed: §9.6, §11.5.
7. **The fallback model decides at the strictest threshold.** When the breaker trips and the gpt-4o-mini fallback engages, the whole run resolves to the strictest threshold unless the fallback is certified, and the manifest records `fallback_engaged`. Changed: §10.6, §11.3.
8. **Before the freeze, the CLI refuses a live run over a whole exercise input.** The only routes to the model before `eval-freeze` are `eval/run_experiment.py --side dev` and offline modes (`fake`, `replay`, B0). The runner also refuses to score a library that shares no row with the reference labels (`docs/gates/G1.md` O2). Changed: §10.3.
9. **B2 cannot fire G2.** B2 runs without extractors or supply markers (§10.5), so a line the model calls non-material is `NM_UNCONFIRMED` in B2, never `G2_SERVICE`. Changed: §10.5.
10. **`calls.jsonl` holds transport ids.** `line_ids` in a call record are the batch's transport ids (`L<position>`), and cost attribution maps them back to lines. They are not part of the request hash. Changed: §9.6.

### A59 · 2026-10-06 · G2 rules fixed before any G2 model call

**Results existing at the time:** B0, B1 and B2 on dev (§17, `docs/gates/G1.md`). No B3 run has been made. None of the choices below uses a B3 result.

1. **The evidence remedy (§10.9) widens the validator, not the prompt.** On the B2 dev runs, the 12-word evidence check rejected 17 answers, and 14 of them were correct (`docs/gates/G1.md` O10). §10.9's trigger is more than 3. The validator tolerance becomes 25 words (`EVIDENCE_MAX_WORDS`). The prompt instruction stays "at most 12 words", so request bytes and cache keys do not change, and the B2 dev runs are re-scored by replay at $0, which gives an exact before and after. D5a still requires the evidence to be a substring of the line, so a longer quote is still grounded. If the schema module is part of `prompt_version`, the version moves, and the prompt pins and the golden fixture are regenerated as a recorded change. The before/after is a ledger row. Changed: §9.4, §10.9.
2. **Error causes are labelled by a panel.** Every dev error at the selected threshold gets its computed fields (family, kind, first wrong level) from `eval/errors_dev.py`. Two labeller agents then assign the §10.7 cause, each without seeing the other's labels, and a third agent adjudicates every disagreement with a written reason. Raw agreement and Cohen's κ are reported. The owner reviews every disagreement and every `gt_suspect` call. Provenance on each row: "builder labels (assistant panel)" and the date. Causes decide which arms trigger (§10.7) and are never used at inference. Changed: §10.7, §10.8.
3. **Selection never calls a model.** `oris select` reads the B3 dev run folders and re-decides every line under each of the thresholds T1–T8 by replay (the policy is not part of the cache key, §11.5). It scores each language with `eval/score.py --side dev` and applies the §10.6 rule. `--target` only changes the bar for a sensitivity row; the shipped threshold always uses .95 and 40 matched in each language. The result is written to `eval/selection_v1.json`. Changed: §10.6.
4. **G2 spend cap: $3**, with one pause for the owner after the first B3 live call. Changed: §14.

**Owner decisions on the contested items (2026-10-04):** experiment ledger adopt-lite, written by the runner, never by `score.py` (§7.2); no request deadline (§11.3); k = 2 by default (§10.6); XLSX `number_format` note only (§9.1); fallback certification optional, ~$0.50 if G2 closes on time (§10.6, §14); a 1-hour Phoenix/OpenInference export spike in G6 if G5 is green (§11.6).

**Rejected (not merged):**

- R1 · Grouped cross-validation over all 252 labelled lines · it needs model outputs on lockbox lines before the freeze; the valid part survives as A22 (dev only).
- R2 · Carbon-weighted error metric · the provided libraries carry no CO₂ factors; listed under "with more time" (§12).
- R3 · Correction memory from reviewer fixes · singleton triples make it near-useless, and ground truth as "corrections" is labelled data at inference; a README production note (§12).
- R4 · Keyboard-first queue with self-timed minutes saved · a self-graded benchmark whose sort conflicts with A14.
- R5 · Cut the UI before evidence because of time · its ordering survives in A14 and A16; time caps return with A32.

### A60 · 2026-10-06 · G2 interpretations and owner decisions, fixed before any B3 run

**Results existing at the time:** B0, B1 and B2 on dev (§17, `docs/gates/G1.md`). No B3 run has been made. None of the items below uses a B3 result. They come from a read-only audit of the G2 ticket sheet (four lenses, each adversarially verified). They are interpretations only: none changes the split, the candidates, the selection rule or the form of the claim. Items 15 and 16 record owner decisions.

1. **Ledger ids.** Rows of one configuration share a language-neutral id and baseline id. The B2 baseline for the evidence remedy is re-recorded at $0 as `B2-dev-w12` (a replay of the two B2 dev runs under the 12-word cap). `R-10.9-evidence` is adopted under §10.9 (A59.1). Its §7.2 `kept` field is informational, because B2 has no threshold and so cannot meet the dev bar.
2. **§10.9 wording.** "The prompt version bumped" reads as "the validator's evidence tolerance is widened; the prompt and prompt_version are unchanged because the limit is not part of the generated schema".
3. **§10.6 rule, as already implemented for B1.**
   - Qualifies ⇔ matched ≥ 40 and P ≥ .95 in each language (strict scoring, all 162 dev items, so a matched blank-GT line counts as wrong). Qualifiers need not be contiguous.
   - "Within 3 summed correct" is measured once against the loosest qualifier, and the strictest qualifier within that window ships.
   - Fallback: maximise the min-over-languages one-sided CP lower bound, with 0 when matched = 0 and ties to the stricter. The within-3 rule does not apply in the fallback.
   - Sensitivity rows (per language; `--target`) are never shipped.
   - Candidates are forced by a temporary override policy file. Selection never reads `config/policy.yaml`.
4. **Trade-off columns.** Review load follows evaluation-protocol.md:87 (needs_review per 100 lines, N basis). Cost per threshold is the source run's attributed cost and is identical across thresholds. Match-all and B2 are reference rows, never candidates.
5. **Below the dev bar.** The policy entry is `certified_by: dev_selection` in both outcomes. The "below the dev bar" label lives in `eval/selection_v1.json` (`dev_bar_met`), G2.md, the §17 entry and the README.
6. **Arm baseline.** Arms are compared with `B3-dev-sel`, a $0 replay of the T5 votes decided at the selected threshold. They are never compared with the T5 rows, which were decided at `T1` (fallback_strictest). A kept arm is followed by a $0 re-selection, logged as a new ledger row.
7. **Errors.** Kinds and fields are those of evaluation-protocol.md §6.
   - A blank-GT line in needs_review or not_a_material is not an error.
   - The family is the GT type, else the predicted type. The "top 8 + other" grouping belongs only to the §10.5 lockbox table.
   - A §10.7 trigger counts primary causes, unweighted, per language: ≥ 5 rows in one language or ≥ 20% of one language's error rows. The ×2 weighting is for the Pareto display only.
8. **gt_suspect.** A candidate is an error row where both passes returned kind material with the same library top1 (v = k) and that triple ≠ GT, blank GT included. The adjudicator rules once per Item No., and the tag goes on both language rows.
9. **κ.** Unweighted Cohen's κ on the primary cause, pooled and per language, with n. It is "undefined" when p_e = 1.
10. **Precision by v and by gap.**
    - The population is lines where every pass answered validly and the plurality is a material with a valid top1. Matched lines alone would make the measure degenerate at k = 2.
    - The systematic-error share = wrong with v = k / wrong in that population.
    - A line's gap is the weakest gap among the passes that support the plurality top1, mirroring the min-confidence rule.
11. **Risk–coverage bands.** 1,000 Item No. resamples (protocol §4). The 10,000 figure applies only to paired precision differences. Per-signal curves are deferred diagnostics.
12. **D5a false rejects.** A D5a false reject is a line with reason `EVIDENCE_NOT_IN_LINE` whose plurality top1 triple equals GT. It is measured on the B3 dev runs. More than 3 in total re-triggers §10.9, and any further change is written as its own amendment before it runs.
13. **Undefined responses.** A §10.7 response that is not a specified E-arm (not_in_library guard, header_context rendering) has its exact change and ledger id written as an amendment before its first run, citing the error rows that triggered it.
14. **Latency.** In the keep rule, latency is checked only where a cold live run measured it. Otherwise it is recorded as "not measured" and checked at the G3 cold run.
15. **Owner decisions D1 and D2 (Tue 6 Oct, before any B3 result).**
    - D1, missed rows: a `missed` row (a correct proposal sent to review) gets the cause of the signal that failed: `llm_failure` for D1/D1b, otherwise the panel's best fit for why v or b failed. It counts in the trigger denominator.
    - D2, keep rule under a shortfall: clause (iii) is read literally, as `run_experiment.py` codes it. If no threshold meets the dev bar, an arm cannot be kept; it is reverted and its gain is logged as directional.
16. **Owner decision D3: no time cut on triggered arms.** §14's "triggered arms, only within the cap" and "the optional ~$0.50 fallback certification if G2 closes on time" no longer bind scope. Every triggered arm is built and run, and the fallback certification is in scope. The $3 G2 spend cap and the `eval-freeze` boundary (§10.3, Thu 8 Oct 12:00) are unchanged. If G2 runs past its Wed 7 Oct 12:00 cap, the slip is recorded in `docs/gates/G2.md`.

### A61 · 2026-10-06 · A held reservation delays a dispatch; it never refuses one

**Results existing at the time:** the B3 live smoke `20261006T001017Z-354cd333` (5 EN dev slice items; `docs/gates/G2.md` O12) and the G1 runs. No selection result exists.

§11.3 said "dispatch only if spent + reserved ≤ cap". In the smoke run, 4 pass-1 calls in flight held about $0.084 of worst-case reservations (mostly `max_tokens` at the output rate). Each pass-2 call was then refused as `BUDGET_CAP` while spend was about $0.015 (the run finished at $0.0215) of the $0.10 cap. That refusal is a defect: the cap bounds spend, and it rejected calls that would have fit.

The rule becomes:

- A dispatch whose reservation fits the spend (spent + reservation ≤ cap) **waits** until reservations in flight settle, then reserves.
- A dispatch is refused as `BUDGET_CAP` only when spent + reservation > cap. Spend only grows, so such a dispatch can never fit.
- The reservation is recomputed after every wait, because a cache read observed meanwhile lowers it.
- This cannot deadlock: with nothing in flight, a reservation that fits the spend always fits. The reserved total is set to exactly 0 when the last reservation settles, so float residue cannot park a waiter.
- After a wait, the circuit breaker is asked again: a dispatch whose wait ended after the breaker tripped is refused as `LLM_UNAVAILABLE`, and never reaches the model.
- Time spent waiting is not part of `latency_ms`, attributed latency or the per-line in-flight budget (§11.3); it shows only in wall-clock. Under a small cap, the fan-out degrades toward one call at a time.
- A dispatch within about one worst-case reservation (~$0.02–0.03 while `max_tokens` is 4,096; G2.md O13) of the cap is still refused.

Replay is unchanged. It reads each run's recorded `declined_attempts`, and runs recorded before this change keep their refusals. The cap, its A57 floor and the $2.00 per 100 lines target are unchanged.

Changed: §11.3. Owner decision Tue 6 Oct (pause #1, option A).

### A62 · 2026-10-06 · D5a checks that every quoted word is a word of the line

**Results existing at the time:** the B3 dev runs `20261006T004009Z-2fb88775` (EN) and `20261006T004134Z-242c4109` (FR), decided at `T1` (`docs/gates/G2.md` O15). No threshold has been selected.

The amended rule is fixed before the $0 replay that measures it, as A60.12 requires.

**Why.** D5a required each supporting quote to be a contiguous substring of the normalised line, and on the B3 dev runs it rejected 52 EN and 71 FR lines. 43 EN and 60 FR of those carried the GT triple, against a §10.9 / A60.12 trigger of more than 3. Every rejected quote was classified against the haystack:
- no quote introduced a word absent from the line;
- the model stitches its quote from the short and long descriptions, skipping words and punctuation, and sometimes reordering them.

The check is meant to test grounding: the answer must rest on this line's text, not on another line or on injected text (§11.8). The contiguous span was an implementation choice, not that purpose.

**The rule.**
- A supporting quote passes when it has at least one word and **every word of it is a word of the normalised haystack** (short + long + section path). A word is a `\w+` run, compared whole, in any order.
- Normalisation is unchanged: NFKC, casefold, whitespace collapse, decimal comma.
- A quote that brings in any word not in this line is still refused, so text injected from a neighbouring line or invented by the model still fails D5a.

**What it does not change.** It does not touch the prompt or `prompt_version` (D5a lives in the decision table, not in the prompt), B2 (which has no D5a), the split, the candidates, the selection rule or the form of the claim.

**Measured** by a $0 replay of the two runs, ledger id `R-D5a-words` against `B3-dev`, decided at the same `T1`. The alternative "same words, in order" (owner option B) would have recovered EN 28 of 52 and FR 36 of 71; it was not adopted.

Changed: §9.5 D5a. Owner decision Tue 6 Oct (pause #1b, option A).

### A63 · 2026-10-06 · `max_tokens` stays 4,096; budget reservations count the measured prefix

**Results existing at the time:** the G2 B3 dev runs, the selection (`T8`, below the dev bar) and the analysis `evidence/o13-max-tokens-brief.md` (`docs/gates/G2.md` O13). Neither decision below uses a quality result.

**1. `max_tokens` stays 4,096, and §9.4 now says so.** §9.4 said `max_tokens` is "sized from measured output (p99 × 1.3)"; the code has always sent 4,096.

The measurement: across 505 fresh Haiku calls (G1 and G2), output peaks at 1,213 tokens (10 lines) and at 138 per line. There were 0 truncations.

The rule is not adopted, for four reasons:
- `max_tokens` is part of the request hash. Changing it would stop every recorded run from replaying: the golden fixture, the selection evidence and `runs/submission/`. It would force about $0.58–0.90 of re-runs and re-derive the selection on new votes before the freeze.
- A39's reason, output-token rate limits, does not bind at the recorded tier: 1,000,000 output tokens per minute, against about 16k in flight.
- Since A61, the slack costs only waiting time on small runs.
- The slack keeps every reservation a true worst case (item 2).

Owner decision (O13, option A).

**2. The budget reservation counts the cached prefix with its measured token count.** The reservation estimated the prefix as characters ÷ 4: about 5.3k tokens for the global library, against 10,066 billed cache-write tokens. With the 4,096-token output term the reservation was still above the true cost, but only by accident.

- The prefix term now uses the `oris doctor --live` measurement for that rendering (`rendered_tokens`, keyed by library SHA-256, model and prompt version) whenever one exists.
- Otherwise it falls back to characters ÷ 4, recorded as estimated.
- Reservations change only in size; they never enter a request hash. Replays that recorded their `declined_attempts` are unaffected.

Owner decision (O13 follow-up, option A). Changed: §9.4, §11.3.

### A64 · 2026-10-06 · G2-T8 arm `E-subtype-drop`: a pass whose top1 conflicts with a stated hard attribute casts no vote

**Results existing at the time:** the G2 selection (`T8`, below the dev bar), the `B3-dev-sel` runs and the accepted cause panel (`docs/gates/G2.md` O18). This arm is a §10.7 response that is not a pre-specified E-arm, so it is written here before its first run (A60.13).

**Why.** `subtype_parse` triggered in EN (7 rows; FR 1). In all 8 rows the extractor read the attribute correctly: C35/45, CEM V/A, RA 20 %. One pass picked a sibling row of the same type and usage whose attribute contradicts the line.
- In 4 rows that pass carried the plurality, and D7 sent the line to review (`ATTR_CONFLICT`).
- In 4 rows the other pass was right, and the split vote left v = 1 (`LOW_SIGNAL:v`).

The pre-mapped response (fix or add an extractor) has nothing to fix.

**The arm (owner decision, option C).**
- With the setting `drop_conflicting_votes` on, a `material` answer whose library top1 conflicts with the line's extracted hard attributes (`compare(...) == CONFLICT`, the D7 test) is removed from the vote before the plurality is taken.
- If no answer remains, the line is decided by D7 exactly as today.
- Otherwise the vote, the supporters and the signals are computed over the remaining answers. With k = 2, one dropped pass leaves v ≤ 1, so the line cannot match at `T8`. The arm can move lines between review reasons and improve the suggested row, but it is not expected to add matches.
- Defaults are unchanged: off.
- The run's manifest records the setting, and replay reads it back, so a replay re-decides exactly as the run did.
- D-08 still holds: an extractor only removes a vote, it never chooses a row.
- The alternative "repair the conflicting pass to the agreeing sibling" (owner option A) was not adopted.

**Measured** by a $0 replay of the `B3-dev-sel` votes, ledger id `E-subtype-drop`, `--baseline-id B3-dev-sel`, `--cause-targeted subtype_parse`. It is kept only under §7.2. Changed: §9.5 (D7 note).

---

## 17. Results

_Append-only, dated. Filled as each gate produces measured numbers. Nothing here is an estimate._

_(none yet: no model call has been made at the time of pre-registration)_

### 2026-10-05 · G1 (v0.1), dev only

Dev only, before `eval-freeze`; 162 items per language, 139 labelled; FR input scored against the global library. Record: `docs/gates/G1.md` §5–§6.

- **B0** (rules only, both full files, runs `20261005T151248Z-d669eb87` EN, `20261005T151251Z-74b773b1` FR): 37 `not_a_material` (HEADER), 282 `needs_review`, 0 calls, F_NM 0, dev C₂₅₂ 0.
- **B1** (TF-IDF, threshold .1126, below the dev bar): EN P .413 (43/104), CP-LB .332, C₂₅₂ .309; FR P .429 (12/28), CP-LB .269, C₂₅₂ .086; F_NM 0.
- **B1 label-free scorer check:** top-1 triple EN .405, FR .159 (type .722 / .560, type+usage .524 / .210), matching the brief.
- **B2 slice** (40 items; `20261005T144408Z-72765ec5` EN, `20261005T144524Z-f2fd2efa` FR): EN P .788 (26/33), CP-LB .638; FR P .690 (20/29), CP-LB .521; both replay byte-identical at $0.
- **B2 all dev** (`20261005T151020Z-33c38c05` EN, `20261005T151104Z-0c9b50d6` FR): EN P .780 (103/132), CP-LB .713, C₂₅₂ .741; FR P .803 (106/132), CP-LB .737, C₂₅₂ .763; F_NM 0 both.
- **B2 per level over matched** (type / type+usage / triple): EN .939 / .818 / .780; FR .947 / .856 / .803.
- **B2 cost and latency:** mean attributed cost per line EN $0.00095, FR $0.00092; wall clock 40.9 s EN and 40.4 s FR for 162 routed lines; 40 calls per run.
- **Live spend at G1:** $0.6713 billed, of the $3 G1 cap (`docs/gates/G1.md` §5).
