# Design: ORIS material matching service

> **Status: pre-registration.** Sections 1–15 were written and committed **before any language-model call was made on this data**. The following are fixed by the commit that introduced this file, and the git history is the proof:
>
> - the split;
> - the metrics;
> - the acceptance rule;
> - the policy grid;
> - the lockbox procedure;
> - the decision semantics.
>
> From here on, this document changes only in two append-only places: §16 _Amendments_ (dated: what changed, why, and what results existed at the time) and §17 _Results_ (dated, never edited after writing).
>
> - Frozen split: `eval/split_v1.json`, SHA-256 `c9a6d1f0aaafdd9f3b7499bf50f59f786f00270c3350e7d5cad7982b855039bd`.
> - Companion documents: `docs/evaluation-protocol.md` (full statistical protocol), `docs/data-analysis.md` (verified data findings), `docs/ui-spec.md` (operator UI), and `docs/requirements-traceability.md` (every brief requirement mapped to its evidence).

---

## 1. TL;DR and rubric map

**What it does.** For every line of a Bill of Quantities, the service returns exactly one decision:

- `matched`, with a `type / usage / subtype` triple copied verbatim from the selected library;
- `not_a_material`;
- `needs_review`.

Matching is done by Claude Haiku 4.5. It reads the line, its section context and the whole library rendered as a coded tree, and returns row codes. Deterministic code then does three things:

- gates headers and confirmed services;
- vetoes matches whose extracted attributes conflict with the row (strength class, exposure class, CEM type, recycled content, process, EWC code…);
- validates that every emitted triple exists in the loaded library.

A pre-registered abstention policy turns the model's answer into `matched` or `needs_review`. Every line carries an audit trail, and every run can be replayed at $0.

**Targets**

| Target                                     | Value                                                                              |
| ------------------------------------------ | ---------------------------------------------------------------------------------- |
| Matched precision (all three levels right) | ≥ 90% **in each language** (I select on ≥ 93% on dev to absorb selection optimism) |
| Coverage                                   | Maximise, subject to the precision target                                          |
| Material lines decided `not_a_material`    | **0**                                                                              |
| Cost                                       | ≤ $2 per 100 lines                                                                 |
| Latency                                    | ≤ 2 s per line, averaged                                                           |
| Library                                    | Nothing hard-wired to one library                                                  |

**Rubric map.** This table is also the project's grader: each phase gate in §14 is checked against it.

| What ORIS evaluates                                                                                 | Where it is answered | Evidence file                                                                        | Gate          |
| --------------------------------------------------------------------------------------------------- | -------------------- | ------------------------------------------------------------------------------------ | ------------- |
| Understanding of the data: where hard lines concentrate, by category and level; EN vs FR; surprises | §4, §6               | `docs/data-analysis.md`, `docs/evaluation.md` (error taxonomy)                       | Sun 4 / Tue 6 |
| Baseline, how much better, how confident                                                            | §7, §10              | `eval/experiments.md`, `docs/evaluation.md` (B0–B3 ladder, Wilson CIs, paired tests) | Mon 5 / Wed 7 |
| Explicit trade-offs: precision vs coverage vs review load vs cost vs latency                        | §2, §5, §7, §10.6    | risk–coverage curves, decision register                                              | Tue 6         |
| Engineered: boundaries, tests, failures, reproducibility                                            | §9, §11              | `tests/`, `eval/check_requirements.py`, `runs/<id>/manifest.json`                    | Wed 7         |
| Defending choices live                                                                              | §12, §15             | rehearsal script, `oris explain`, `oris replay`                                      | Thu 8         |
| Design document: approach, first steps, cuts                                                        | §5, §7, §8           | this file                                                                            | Sun 4         |
| Service: CLI, API, tests, outputs on both files                                                     | §9, §11              | `outputs/improved_output_{en,fr}.csv`                                                | Wed 7         |
| Evaluation script reusable on any reference                                                         | §10.1, §11.7         | `eval/score.py`                                                                      | Mon 5         |
| README: run, weaknesses, more time                                                                  | —                    | `README.md`                                                                          | Thu 8         |
| Optional UI                                                                                         | §13                  | `/ui`                                                                                | Thu 8         |

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

| Activity                                                                 | Assumed time                                                  |
| ------------------------------------------------------------------------ | ------------------------------------------------------------- |
| Manual entry of a line                                                   | about 50 s                                                    |
| Review of a `needs_review` line with a pre-filled top-1/top-2 suggestion | about 15 s                                                    |
| Skimming a skipped line                                                  | about 2 s                                                     |
| Wrong auto-fill                                                          | priced at k × manual entry, because it is found late or never |

The report converts each candidate operating point into **engineer-minutes saved per 300-line BoQ**, so the precision/coverage choice can be read in ORIS's own units. These figures are labelled assumptions, never measurements.

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

These are condensed from 90 atomic requirements. The full matrix, with the evidence and verification method for each, is in `docs/requirements-traceability.md`.

### 3.1 Functional

| ID  | Requirement                                                                                                            | Verified by                                                                  |
| --- | ---------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| F1  | Exactly one decision per input line. Same row count and same order. Decision ∈ {matched, not_a_material, needs_review} | row-conservation property test; `check_requirements.py` RQ2–RQ3              |
| F2  | `matched` ⇒ the triple is a row of the **selected** library, emitted verbatim, trailing spaces included                | closed-world property test; RQ4                                              |
| F3  | `not_a_material` only for lines with no material: headers, labour, services                                            | decision truth table; RQ5                                                    |
| F4  | Material lines with no library equivalent → `needs_review`, labels blank                                               | decision truth table; annotation-based metric                                |
| F5  | CLI: `oris match --input … --library … --output …` on any BoQ with the same columns                                    | CLI tests on synthetic and real files                                        |
| F6  | HTTP API: batch of lines in, decisions out, same service as the CLI                                                    | API contract test; CLI/API parity test                                       |
| F7  | Library selectable at runtime; nothing hard-wired to the global library                                                | run on the FR library under FakeLLM; grep guard for global strings in `src/` |
| F8  | Offline enrichment script writes `semantic_description` for any library                                                | script test; service runs on unenriched libraries too                        |
| F9  | Outputs for both input files against the global library                                                                | `outputs/` + RQ1–RQ6                                                         |
| F10 | Scorer: any output vs any reference with the three label columns                                                       | scorer fixture tests, including synthetic FR-library references              |
| F11 | Optional UI: upload CSV/XLSX, run, table, `needs_review` easy to spot                                                  | `docs/ui-spec.md` U1–U4                                                      |

### 3.2 Non-functional

| ID  | Requirement       | Target and measure                                                                                              |
| --- | ----------------- | --------------------------------------------------------------------------------------------------------------- |
| N1  | Matched precision | ≥ .90 per language on the lockbox (point estimate, Wilson CI reported)                                          |
| N2  | Coverage          | maximised: C₂₅₂ headline, plus C₂₆₅ and H₂₆₅ (§10.1)                                                            |
| N3  | Safety            | false `not_a_material` on material lines = 0                                                                    |
| N4  | Cost              | ≤ $2 / 100 lines, **measured** from API `usage`, retries included                                               |
| N5  | Latency           | ≤ 2 s / line, run wall-clock ÷ lines; gated on the stricter LLM-routed denominator                              |
| N6  | Failure handling  | timeouts, rate limits, 5xx, truncation, malformed output, missing items → no lost line, no silent mislabel      |
| N7  | Auditability      | per line: model, prompt version, raw response, cost, latency, the rule that fired, the reason code              |
| N8  | Reproducibility   | run manifest; replay gives a byte-identical output CSV                                                          |
| N9  | Modularity        | parsing, matching, validation and decision logic in separate modules with no cross-imports into the pure domain |
| N10 | Model size        | Claude Haiku 4.5 (allowed); the port allows a local model of 8B parameters or fewer                             |

### 3.3 Ambiguities in the brief, and the interpretation I chose

| Ambiguity                                                 | My interpretation, and why                                                                                                                                                                                                                       |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Coverage denominator, "material lines"                    | **Report three numbers:** C₂₅₂ (correct ÷ labelled lines, reproducible from the labels alone), C₂₆₅ (correct ÷ all material-bearing lines, the literal definition, ceiling .951), H₂₆₅ (correct matches + correct reviews ÷ 265). Headline: C₂₅₂ |
| Specialist products with no equivalent                    | `needs_review`, not `not_a_material`. They carry a material, and the sample output agrees (SBS membrane → `needs_review`)                                                                                                                        |
| Lines with both labour and supply ("cut, bent and fixed") | A line that supplies a material **is** a material line. The sample output matches such a line                                                                                                                                                    |
| Precision: pooled or per language                         | Per language. "Both languages count"                                                                                                                                                                                                             |
| Latency definition                                        | Mean wall-clock per line over the whole run (headline, ÷ all rows), and the same over LLM-routed lines (the gate, stricter). Per-call p50/p95 as diagnostics                                                                                     |
| Cost scope                                                | All attempts, retries and failures included, ÷ all rows. One-off enrichment reported separately                                                                                                                                                  |
| "Accuracy at each hierarchy level"                        | Cumulative prefixes (type, type+usage, triple), because usage strings repeat across types. Computed over matched lines **and** over all labelled lines using the suggested row                                                                   |
| Labels on `needs_review` rows                             | Canonical label columns blank, as in the sample. The model's suggestion goes in extra `suggested_*` columns after the sample's columns                                                                                                           |
| Blank subtype                                             | `''` is a real leaf. Exact equality, so `''` vs `C30/37` is wrong both ways                                                                                                                                                                      |
| Using the EN file to help FR                              | Not allowed at inference: the live session has one file. EN↔FR agreement is only a reported diagnostic                                                                                                                                           |

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

1. **The ground truth is a synthetic sweep.** It has 244 distinct triples over 252 lines, and 238 of them appear exactly once. Together they cover 71% of the library at about one line per leaf. Any few-shot or retrieval memory built from labels would leak its own answer, so I use none (§10.8).
2. **No lexical score gives a usable confidence.** The best TF-IDF operating point at P ≥ .90 reaches 4% coverage in EN. That point is overfit: held-out precision is .76–.82. FR never reaches .90.
3. **13 blank-labelled lines carry real materials.** A naive "blank GT = not a material" reading would train the system to delete materials.
4. **The unit rule is almost free precision.** Empty/LS/day/month units flag 46 rows and drop 0 labelled lines in either language. It still cannot be a hard rule on an unseen file (hire items can carry a material).
5. **The L0 headers are bare digits (`0`..`9`).** A header test written as "`NN.` shape" misses all ten of them.
6. **Two diameter characters** (∅ in the FR library, Ø in the BoQ) and **two decimal conventions** (66 decimal commas vs 4 dots in the FR BoQ; dots in the library).

### 4.6 Data traps and the tests that guard them

| Trap                                                          | Guard                                                                     |
| ------------------------------------------------------------- | ------------------------------------------------------------------------- |
| Blank cells read as NaN                                       | `keep_default_na=False`, `dtype=str`; scorer raises on NaN                |
| cp1252 decodes UTF-8 silently into mojibake                   | strict UTF-8 (with BOM tolerated); round-trip test on accented rows       |
| Trailing spaces in canonical labels                           | never strip; key everything by row id; test with `'bitumen for coating '` |
| Usage strings repeat across types (one repeats under 7 types) | usage always qualified by type; per-level accuracy cumulative             |
| Header rows with dotless L0 IDs                               | G1 regex test on the real `0`, `00.01.` rows; parent-path derivation test |
| Decimal comma, Ø/∅/⌀                                          | normalisation unit tests (matching only, never output)                    |

---

## 5. Approach in one page `[README: "What's your approach?"]`

### 5.1 Architecture

```mermaid
flowchart LR
  subgraph Adapters_in [Driving adapters]
    CLI[Typer CLI<br/>match · score · replay · explain]
    API[FastAPI<br/>POST /v1/match · /v1/jobs]
    UI[TS single-page UI<br/>served at /ui]
  end
  subgraph Core [Application core]
    SVC[MatchService<br/>gate → batch → call → validate → decide → audit]
    subgraph Domain [Pure domain, no I/O]
      BOQ[boq: lines, sections]
      LIB[library: row ids, structure]
      NORM[normalize]
      ATTR[attributes: extractors]
      DEC[decision: gates + policy]
    end
  end
  subgraph Adapters_out [Driven adapters]
    LLMP{{LLM port}}
    ANT[AnthropicLLM<br/>Haiku 4.5]
    FAKE[FakeLLM<br/>fault injection]
    REP[ReplayLLM<br/>calls.jsonl, $0]
    IO[io: CSV/XLSX reader,<br/>CSV writer, JSONL audit]
  end
  CLI --> SVC
  API --> SVC
  UI --> API
  SVC --> Domain
  SVC --> LLMP
  LLMP --> ANT
  LLMP --> FAKE
  LLMP --> REP
  SVC --> IO
```

The domain is pure: no network and no file access. That makes every decision rule unit-testable, and lets the same rules re-decide a recorded run with no API calls.

### 5.2 The path of one line

1. **Parse.** Read the row by column name. Keep the raw strings. Derive the section path (L0 > L1 header text).
2. **Gate G1.** Empty Unit **and** empty Qty **and** a header-shaped Item No. → `not_a_material` (`HEADER`). No model call.
3. **Batch.** Group up to 10 lines from the same L1 section.
4. **Call.** One structured-output call to Haiku 4.5. The prompt holds the cached system blocks (decision vocabulary, glossary, the whole library as a coded tree) and the batch, wrapped as data.
5. **Validate.**
   - The response must parse, cover every line id, and use codes that exist.
   - Codes are mapped back to library rows.
   - Extracted line attributes are checked against the row's attributes.
6. **Decide.** The first rule that fires in the decision table (§9.5) wins: gates, failures, no-equivalent, vetoes, then the frozen abstention policy.
7. **Audit.** One output row, one audit record (the rule that fired, the reason code, suggestions), and one call record per API attempt (raw response, usage, cost, latency).

### 5.3 Decision register: what was decided by data

The choices below needed no experiment, because the data already settles them. The ones that do need evidence are pre-registered in §7.

| #    | Decision                                                    | Alternatives considered                     | Deciding evidence                                                                                                                                                                                                                                                                                                                                                        | Cost / latency effect                                                                                                                          |
| ---- | ----------------------------------------------------------- | ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| D-01 | **Show the whole library to the model; no top-k retrieval** | embedding top-k; TF-IDF top-k               | Lexical recall@20 is .869 EN but **.690 FR**, and ≤ .47 on Aggregates, C&D, Excavations and Steel. A shortlist caps accuracy before the model is even called. The global library is about 6.3k tokens and the FR one about 1.1k, so both fit easily. Recall is then 1.0 by construction. A retrieval stage is the documented switch for libraries above about 30k tokens | Global prompt is cache-eligible; FR (about 3k tokens) is below Haiku's 4,096-token cache minimum, a negligible cost that I will not pad around |
| D-02 | **No examples taken from the labels**                       | GT few-shot; retrieval memory               | 238/244 triples are singletons, so any example leaks its own answer                                                                                                                                                                                                                                                                                                      | —                                                                                                                                              |
| D-03 | **Header gate G1**                                          | empty Unit only; shape only                 | Empty Unit **and** empty Qty **and** Item No. matching `^\d{1,2}$\|^\d{2}\.\d{2}\.$` → 37/37 headers, 0 false positives. An unknown numbering scheme degrades to `needs_review` (`HEADER_UNCONFIRMED`), never to a silent skip                                                                                                                                           | 37 lines at $0                                                                                                                                 |
| D-04 | **Strict non-material gate G2**                             | trust the model's "non-material"; two votes | The model says non-material **and** the unit is a service unit (LS, Ft, day, j, month, mois, week, h) **and** no hard attribute was extracted. A measured unit (t, m³, m², m, kg, pcs, U, l) can never auto-skip                                                                                                                                                         | Accepted cost: 7 of 16 services go to review (they carry pcs/m/m² units). Benefit: no material can vanish                                      |
| D-05 | **No library equivalent → `needs_review`**                  | `not_a_material`                            | The brief's definition plus the sample output                                                                                                                                                                                                                                                                                                                            | +13 review lines on this file                                                                                                                  |
| D-06 | **Supply + labour is a material**                           | treat as labour                             | The sample matches "Reinforcing steel B500B, cut and bent"                                                                                                                                                                                                                                                                                                               | —                                                                                                                                              |
| D-07 | **Closed-world output through row ids**                     | free text + fuzzy snapping; schema enums    | `row_id = sha256(type ␟ usage ␟ subtype)[:12]` over raw strings, with a collision test. The model sees short, sorted display codes (`T05.U03.S02`) that keep the hierarchy cue and do not depend on file order. The output is always the library's own strings                                                                                                           | —                                                                                                                                              |
| D-08 | **Attribute extractors veto; they do not filter**           | hard candidate filter                       | Re-run on the data: 0/252 GT conflicts (safe), but a median of 342/342 rows survive the filter, so it narrows nothing. It is valuable as a veto and as an agreement signal                                                                                                                                                                                               | $0                                                                                                                                             |
| D-09 | **Section path in the prompt**                              | omit                                        | The L1-section prior alone gets type right 73.3% of the time (leave-one-out), and it explains the 09.01 block. It does not solve usage (24.7%). Still ablated in E-04                                                                                                                                                                                                    | a few tokens                                                                                                                                   |
| D-10 | **Claude Haiku 4.5 behind an LLM port**                     | GPT-4o-mini, Gemini Flash, local ≤ 8B       | Allowed by the constraints; native structured outputs; $1 / $5 per MTok fits the budget by an order of magnitude. A cross-model comparison is cut (§8); the port keeps a local-model escape hatch open                                                                                                                                                                   | measured                                                                                                                                       |
| D-11 | **Temperature 0; the noise floor is measured**              | assume determinism                          | Temperature 0 is not bit-deterministic on hosted APIs, so I measure the decision flip rate (40 lines run twice). Determinism is guaranteed through replay                                                                                                                                                                                                                | —                                                                                                                                              |
| D-12 | **One abstention policy for both languages**                | per-language thresholds                     | The live run is an unseen file on a different library, and per-language thresholds tuned on about 140 lines would overfit. The report shows what a per-language optimum would have added, as a cost made visible                                                                                                                                                         | —                                                                                                                                              |
| D-13 | **Normalise for matching only; output verbatim**            | clean the strings                           | NFKC, casefold, Ø/∅/⌀ → ø, decimal comma → dot between digits. Trailing spaces are canonical                                                                                                                                                                                                                                                                             | —                                                                                                                                              |
| D-14 | **Three coverage numbers**                                  | one                                         | See §3.3                                                                                                                                                                                                                                                                                                                                                                 | —                                                                                                                                              |
| D-15 | **Suggestions in separate columns**                         | put the guess in the label columns          | ORIS's scorer must not count guesses. Per-level proposal accuracy stays computable from the CSV alone                                                                                                                                                                                                                                                                    | —                                                                                                                                              |
| D-16 | **Sync API up to 500 lines; async jobs for the UI**         | sync only; jobs only                        | A whole 320-line BoQ must fit in one documented call; the UI needs progress                                                                                                                                                                                                                                                                                              | both call `MatchService`                                                                                                                       |

---

## 6. English and French: one pipeline, language-aware resources

**Principle.** There is no language-specific branch in the decision logic. The language difference is handled by **resources**, and the choice between handling strategies is an experiment (E-01), not an opinion.

**Language-aware resources (all deterministic, all tested):**

- **Normaliser:** decimal comma, Ø/∅/⌀, NFKC, casefold.
- **Unit map:** pcs ↔ U, LS ↔ Ft, month ↔ mois, day ↔ j. Bilingual `SERVICE_UNITS`.
- **Extractor lexicons in both languages:**
  - process: WMA / _tiède_ / _température abaissée_ vs HMA / _chaud_;
  - recycled content: RAP / RA / AE / _agrégats d'enrobés_;
  - virgin material: _sans AE_, _matériaux neufs_, virgin.
- **Header aliases** for the input columns: _N° article_, _Désignation_, _Unité_, _Quantité_.
- **A small glossary** of domain terms with a source tag on each entry (see §10.8 for provenance rules). Examples:
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

| Arm | What changes                                                                                                                                        | Extra cost          |
| --- | --------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------- |
| A   | Native text; normaliser, unit map and lexicons only                                                                                                 | $0                  |
| B   | Haiku translates each FR line to EN before matching                                                                                                 | +1 call per line    |
| C   | Deterministic bilingual `semantic_description` per library row (path translation map, acronym glossary, structural nouns, false friends) + glossary | $0 at inference     |
| D   | LLM-generated `semantic_description`, made offline once per library, cached by library hash                                                         | one-off per library |

**Decision rule:** the keep rule of §7. C is the default over B unless B adds ≥ 3 more correct matches across both languages at equal precision.

**What is reported per language, always:**

- precision, all three coverage numbers, per-level accuracy;
- decision shares, cost, latency;
- the error taxonomy;
- the EN↔FR agreement rate (same decision and same row), as a diagnostic only.

**The French library at the live session.** Because there are no labels for it, I hand-label a **smoke set of about 25 lines** against `oris_materials_fr.csv`:

- about 10 lines from this BoQ that have an exact FR equivalent;
- about 8 lines hitting FR-only leaves (pot bearings, fenders, EME/GB with 20/40% AE, ductile pipe, detonating cord);
- about 7 no-match decoys, 3 of them lexically close to a real row.

The labels are frozen and hashed before the first run and reported as builder-labelled.

**Pre-registered rule for a library without labels:** ship the frozen policy only if the smoke set shows **0** closed-world violations, **0** false `not_a_material` and **0** decoy matches. Otherwise run the strictest policy and say so.

---

## 7. What I will try first `[README: "What will you try first?"]`

### 7.1 Order of work

Each step has a hypothesis, the metric that answers it, and the decision the answer triggers.

| #   | Step                                                                                           | Hypothesis                                                                                                          | Metric                                          | Result → decision                                                          |
| --- | ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- | -------------------------------------------------------------------------- |
| 1   | **B0 rules only**                                                                              | Gates alone are safe                                                                                                | F_NM, G1 37/37                                  | Establishes the floor and the safety baseline                              |
| 2   | **B1 TF-IDF**, threshold chosen on dev                                                         | Lexical matching cannot reach P ≥ .90 held out                                                                      | C₂₅₂ at P ≥ .90 on dev → applied to the lockbox | Reproduces the brief (top-1 EN .405 / FR .159 ±.01) to validate the scorer |
| 3   | **B2 Haiku single pass** on a stratified 40–60-line dev slice, EN + FR, then all 139 dev lines | The model's raw proposal accuracy is far above B1, but its matches are not ≥ .90 precise without abstention         | Proposal accuracy per level, P(match-all)       | If proposal accuracy < .70 in FR, prioritise E-01 before E-02              |
| 4   | **E-02 abstention sweep** (by replay, $0)                                                      | Extractor agreement is the strongest signal on attribute-rich families; two-pass agreement helps on usage confusers | Risk–coverage per signal; coverage at P ≥ .93   | Freezes the policy                                                         |
| 5   | **E-01, E-03, E-04, E-05**                                                                     | See the registry                                                                                                    | Keep rule                                       | Kept or reverted, logged                                                   |
| 6   | **Conditional E-08 / E-09**                                                                    | Only if the error taxonomy points there                                                                             | Keep rule                                       | —                                                                          |

### 7.2 Experiment registry (pre-registered)

**Keep rule (fixed now).** A change is kept only if, on dev:

- the acceptance rule still holds: matched precision ≥ .93 **in each language** with ≥ 30 matched dev lines in each;
- summed C₂₅₂ (EN + FR) rises by **≥ 3 lines**;
- F_NM stays **0**;
- cost and latency stay inside budget;
- the gain exceeds **2× the measured flip rate**.

Gains below the dev detectable difference of about 10 points are labelled _directional_. Every attempt is a row in `eval/experiments.md`: id, hypothesis, change, before/after, kept or reverted, prompt version.

| #    | Question                                                                                     | Arms                                                                                                                                                                        | Decided by                                                                                                     | Extra cost                  |
| ---- | -------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- | --------------------------- |
| E-01 | How to handle EN vs FR                                                                       | A native · B translate · C bilingual deterministic enrichment · D LLM enrichment                                                                                            | §6 rule                                                                                                        | B: +1 call/line; D: one-off |
| E-02 | Which abstention signal                                                                      | (a) self-reported confidence 0–100 + ordinal candidate gap · (b) extractor agreement · (c) two-pass agreement with permuted candidate order — **12 fixed policies** (§10.6) | Max summed C₂₅₂ under the acceptance rule; within 3 lines → the cheaper one; none passes → the strictest ships | (c) ≈ 2× calls              |
| E-03 | How to render the library                                                                    | flat rows vs indented coded tree                                                                                                                                            | Proposal accuracy                                                                                              | ≈ 0                         |
| E-04 | Is the section path worth it                                                                 | with vs without                                                                                                                                                             | C₂₅₂, type accuracy                                                                                            | ≈ 0                         |
| E-05 | Batch size                                                                                   | 1 vs 10 (contiguous in a section), on 30 dev lines                                                                                                                          | \|Δ proposal accuracy\| within the noise floor; Δ$/line; Δs/line                                               | —                           |
| E-06 | Is the extractor veto worth it                                                               | veto on vs off                                                                                                                                                              | ΔP vs ΔC₂₅₂                                                                                                    | $0                          |
| E-07 | Concurrency for latency                                                                      | 1 / 4 / 8 with AIMD                                                                                                                                                         | wall-clock ≤ 2 s per routed line                                                                               | —                           |
| E-08 | _If_ usage confusers are ≥ 20% of dev errors (or ≥ 5 lines): a verifier over confusable rows | on vs off                                                                                                                                                                   | keep rule                                                                                                      | + calls on flagged lines    |
| E-09 | _If_ FR smoke-set errors call for it: a cement-content extractor (`(\d{3})\s*kg`)            | on vs off                                                                                                                                                                   | keep rule + smoke set                                                                                          | $0                          |

---

## 8. What I will cut if I run out of time `[README: "What will you cut?"]`

**Already cut**, with the reasons:

| Cut                                                                                         | Reason                                                                       |
| ------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| Cross-model comparison (GPT-4o-mini, Gemini Flash, local)                                   | One model done well beats three done shallowly; the port keeps the door open |
| LLM enrichment by default                                                                   | Only if E-01 earns it                                                        |
| PDF input                                                                                   | Not provided; out of scope                                                   |
| UI editing and auth                                                                         | Not asked for                                                                |
| Import-linter, generic retrieval/enrichment ports, mutated-library and multi-seed ablations | They add no evidence for this brief                                          |

**Cut order if a day slips.** The first item goes first. Each entry names what it weakens.

1. UI polish (keyboard shortcuts, theme toggle). Weakens: nothing graded.
2. Custom-library upload in the UI (the built-in libraries stay). Weakens: optional UI.
3. XLSX input. Weakens: optional UI; the CLI stays CSV.
4. Retry-failed button in the UI. Weakens: operator convenience.
5. Async jobs (the UI falls back to the sync endpoint). Weakens: the progress bar.
6. Two-pass signal (c). Weakens: one arm of E-02.
7. Enrichment arm D. Weakens: one arm of E-01.
8. Determinism slice reduced from 40 to 20 lines. Weakens: noise-floor precision.
9. FR smoke set reduced from 25 to 15 lines. Weakens: live-session evidence.

**Never cut:**

- the scorer and the requirements check;
- the frozen split and the lockbox-once rule;
- the closed-world validator;
- failure handling;
- audit and replay;
- CLI and API on one service;
- both output files;
- the evaluation report;
- the README.

---

## 9. Pipeline design

### 9.1 Parsing (`io/boq_reader.py`, shared by the CLI, the API and the UI)

- CSV:
  - strict UTF-8, BOM tolerated;
  - delimiter sniffed between `,` and `;`;
  - columns found by name (case- and space-insensitive, with aliases);
  - extra columns are kept and passed through;
  - a missing required column raises a clear error.
- XLSX (openpyxl, read-only, values only, first visible sheet):
  - item numbers are kept as text;
  - `.xls` and `.xlsm` are rejected.
- All cells stay raw strings (`dtype=str`, `keep_default_na=False`, no strip).
- Section path:
  - L0 = the integer prefix of the item number (`00.01.` → `0`);
  - L1 = `NN.NN.`;
  - the text of each comes from its header row;
  - files without codes get no path, and the prompt says so.
- Line identity is a hash of position, item number and text, so duplicate texts stay distinct.

### 9.2 Library (`domain/library.py`)

- Load any CSV with `material_type, material_usage, material_subtype` (quoted headers allowed).
- `row_id` = a short SHA-256 of the three raw strings. The loader checks for collisions and computes the library's own SHA-256.
- **Derived structure**, computed from whatever library is loaded (never from type names):
  - mixed parents (a blank subtype plus specific siblings);
  - sole-child parents;
  - groups of rows that share a subtype within a type, which are confusable;
  - `never_match` rows by **configured patterns**, e.g. the "Custom material (Carbon impact in …)" placeholders, kept in `config/` and not in code.
- Attributes for every row are extracted at load time with the same extractors that run on lines.
- Display codes `Txx.Uyy.Szz` are assigned on **sorted** normalised strings, so they are stable when the file is reordered. `S00` is the blank-subtype leaf.

### 9.3 Attribute extractors (`domain/attributes.py`)

Each extractor applies one function to two inputs: normalised line text and normalised library row text. They are registered in one list, and each has a test template.

| Family           | Pattern (after normalisation)                                                                                                                                           | Comparison                                                      | Role                                                                     |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------ |
| Strength class   | `\bc(\d{1,3})/(\d{1,3})\b`                                                                                                                                              | equal                                                           | hard                                                                     |
| Exposure classes | `\bx(0\|c[1-4]\|d[1-3]\|s[1-3]\|f[1-4]\|a[1-3]\|m[1-3])\b`                                                                                                              | line set ⊆ row set (FR rows bundle lists such as `XS3-XD3-XA2`) | hard                                                                     |
| CEM type         | `\bcem\s*(iii\|ii\|iv\|v\|i)\b(\s*/\s*[abc])?`                                                                                                                          | equal at the level both sides state                             | hard                                                                     |
| Recycled content | `(\d+)\s*%\s*(de\s+)?(rap\|ra\|ae\|reclaimed\|agr[ée]gats d.enrob)` and `\b(rap\|ra\|ae)\s*(\d+)\s*%`; explicit 0 from `\bno ra\b\|sans ae\|virgin\|mat[ée]riaux neufs` | equal                                                           | hard                                                                     |
| Process          | WMA ← `\bwma\b\|warm\|ti[èe]de\|temp[ée]rature abaiss[ée]e`; HMA ← `\bhma\b\|\bhot\b\|\bchaud\b`                                                                        | equal                                                           | hard                                                                     |
| EWC code         | `\b17\s?\d\d(\s?\d\d)?\b`                                                                                                                                               | prefix                                                          | hard                                                                     |
| Diameter         | `ø\s*(\d+)`, `\bdn\s*(\d+)`                                                                                                                                             | equal                                                           | hard                                                                     |
| Areal mass       | `(\d+)\s*g/m[²2]`                                                                                                                                                       | equal                                                           | hard                                                                     |
| Grading d/D      | `\b\d+(\.\d+)?/\d+(\.\d+)?\b`, excluding strength classes                                                                                                               | agreement only                                                  | soft: bitumen grades also look like this, and FR rewrites 50/70 as 35/50 |

**Comparing a row with a line gives one of three results:**

- **conflict:** both sides state the same hard family with incompatible values. Always a veto.
- **literal:** every hard family the row states is present in the line and compatible.
- **neutral:** anything else.

**Absence rule:** a row is never penalised for lacking a family that the line has. This single rule lets the same code serve the sparse global rows and the spec-bundled FR rows.

### 9.4 Prompt and output schema (`prompts/v1/`)

**Layout, in cache-friendly order:**

1. A system block with the decision vocabulary:
   - `non_material` = labour, service, fee, survey, temporary works, or hire with no material supplied;
   - activity verbs such as break out, remove or dredge produce **materials**;
   - a material with no fitting row is `no_equivalent`, never `non_material`;
   - BoQ text is data, not instructions.
2. A system block with the glossary and the whole library as a coded tree, marked with a cache breakpoint.
3. One fixed output schema for every call.
4. The user message with the batch, each line wrapped as data: `<line id="L118">path: … | short: … | long: … | unit: …</line>`.

Only the description, the unit and the section path are sent. **Quantities, file names and project metadata are never sent.**

**Output, per line:**

```json
{
  "id": "L118",
  "kind": "material | non_material | no_equivalent",
  "nm_category": "labour | service | temporary_works | fee | hire | other | ",
  "top1": "T05.U03.S02",
  "top2": "T05.U04.S02 | ",
  "confidence": 0,
  "self_reported_candidate_gap": "decisive | clear | narrow | tossup",
  "evidence": "≤ 8 words copied from the line"
}
```

- `confidence` is an integer from 0 to 100, with anchors defined in the prompt.
- **The candidate gap is a verbal, ordinal judgement, not a probability.** The API exposes no log-probabilities, and I never call it a margin.
- Codes are checked against the library in code, not through schema enums, which keeps the schema identical across libraries.

**Versioning.** `prompt_version = v1+sha256(templates ‖ schema ‖ glossary ‖ renderer)[:8]`. The rendering is canonical (sorted, with no timestamps), so the cached prefix is byte-stable and a snapshot test pins it.

### 9.5 Decision table (`domain/decision.py`, pure)

The first rule that fires wins.

| #   | Condition                                                                                                | Decision         | Reason code                                             |
| --- | -------------------------------------------------------------------------------------------------------- | ---------------- | ------------------------------------------------------- |
| D0  | G1: empty Unit **and** empty Qty **and** header-shaped Item No.                                          | `not_a_material` | `HEADER`                                                |
| D0b | Empty Unit and Qty, but the shape is unrecognised                                                        | `needs_review`   | `HEADER_UNCONFIRMED`                                    |
| D1  | No valid answer after the retry policy                                                                   | `needs_review`   | `LLM_FAILURE:<kind>`, `LLM_UNAVAILABLE` or `BUDGET_CAP` |
| D2  | G2: `kind = non_material` **and** the unit is in `SERVICE_UNITS` **and** no hard attribute was extracted | `not_a_material` | `G2_SERVICE`                                            |
| D3  | `kind = non_material`, any other case                                                                    | `needs_review`   | `NM_UNCONFIRMED`                                        |
| D4  | `kind = no_equivalent`                                                                                   | `needs_review`   | `NO_LIBRARY_EQUIVALENT`                                 |
| D5  | top1 unknown, malformed or inconsistent with its prefix                                                  | `needs_review`   | `INVALID_ROW_ID`                                        |
| D6  | top1 is a configured never-match row                                                                     | `needs_review`   | `NEVER_MATCH_ROW`                                       |
| D7  | Attribute comparison of top1 = conflict                                                                  | `needs_review`   | `ATTR_CONFLICT`                                         |
| D8  | top1 is the blank leaf of a mixed parent and a sibling is `literal`                                      | `needs_review`   | `GENERIC_PARENT`                                        |
| D9  | The frozen abstention policy passes                                                                      | `matched`        | `SIGNAL:<policy>`                                       |
| D10 | Anything else                                                                                            | `needs_review`   | `LOW_SIGNAL:<failed signals>`                           |

The **reason-code enum is frozen** and the UI shows it verbatim: `HEADER, HEADER_UNCONFIRMED, G2_SERVICE, NM_UNCONFIRMED, NO_LIBRARY_EQUIVALENT, INVALID_ROW_ID, NEVER_MATCH_ROW, ATTR_CONFLICT, GENERIC_PARENT, SIGNAL:*, LOW_SIGNAL:*, LLM_FAILURE:{timeout, rate_limited, api_error, truncated, malformed, missing_item, refusal}, LLM_UNAVAILABLE, BUDGET_CAP, INTERNAL_INVARIANT`.

**Safety by construction:** no measured unit can reach `not_a_material`, because only D0 and D2 emit it, and D2 requires a service unit. A truth-table test pins this.

### 9.6 Output and audit

**Output CSV:**

- the input columns, unchanged;
- `decision, material_type, material_usage, material_subtype`;
- the sample's audit columns `reason, model, prompt_version, latency_ms, cost_usd`;
- then `suggested_type, suggested_usage, suggested_subtype, library_row_id`.

Rule-decided lines carry `model = rules`, cost 0 and latency 0.

**Run folder `runs/<run_id>/`:**

- `manifest.json`: the model snapshot ID, library SHA-256, prompt version, glossary hash, policy version, code SHA plus a dirty flag, split SHA, price-table date, temperature, max tokens, and SDK version;
- `calls.jsonl`: one record per API attempt (request hash, raw response, usage including cache reads and writes, cost, latency, status, retry count);
- `audit.jsonl`: one record per line (rule fired, reason, signals, top-1/top-2, attribute result, call id).

Run folders are git-ignored, because they contain client text. The committed outputs and the report are derived from them.

---

## 10. Evaluation protocol `[Rubric: Baseline and confidence]`

This is the condensed version. Formulas, split membership, the power table and the requirements check are given in full in `docs/evaluation-protocol.md`.

### 10.1 Metrics

| Metric                 | Definition                                                                                                          | Denominator                 | Target                                 |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------- | --------------------------- | -------------------------------------- |
| Matched precision P    | correct triples ÷ lines decided `matched`. A match on a blank-GT line is wrong. `n/a` if nothing is matched         | \|matched\|                 | ≥ .90 (lockbox); ≥ .93 (dev selection) |
| Match coverage C₂₅₂    | correct matches ÷ labelled lines                                                                                    | 252 (dev 139 / lockbox 113) | maximise                               |
| Coverage C₂₆₅          | correct matches ÷ all material-bearing lines                                                                        | 265                         | reported; ceiling .951                 |
| Material handling H₂₆₅ | (correct matches + `needs_review` on material lines) ÷ 265                                                          | 265                         | maximise                               |
| Safety F_NM            | material-bearing lines decided `not_a_material`                                                                     | count                       | **0**                                  |
| Per-level accuracy     | cumulative type, type+usage, triple, (1) over matched lines and (2) over all labelled lines using the suggested row | as stated                   | reported                               |
| Decision shares        | share of each decision over all rows and over item rows; decision × reference-class confusion matrix                | 319 / 282                   | reported                               |
| Cost                   | Σ cost of all attempts ÷ lines × 100, from API `usage` and a dated price table                                      | all rows                    | ≤ $2.00                                |
| Latency                | run wall-clock ÷ all rows (headline) and ÷ LLM-routed lines (gate); per-call p50/p95                                | as stated                   | ≤ 2.0 s                                |

**Exact strings everywhere.** Trailing spaces count, and a `whitespace_only_mismatch` diagnostic is reported separately but still counts as wrong.

**The scorer's contract**, which matters because ORIS will reuse it:

- it takes any output against any reference with the three label columns;
- denominators are derived from the reference;
- the material-without-equivalent list and the split are optional inputs;
- duplicate keys are refused with a clear error, and missing rows count as failures;
- it falls back to a row-order join, with a warning, when there is no key column.

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
   - Disclosed: the lockbox is **harder**. It holds 7/8 Admixture, 9/13 Excavations, 8/12 Cement, 7/11 Steel and the whole 09.01 ready-mix block, while dev holds 18/24 C&D. I expect shrinkage from dev to lockbox, which is one reason dev selection uses .93.

### 10.3 Lockbox procedure

- Before the freeze, only dev items are ever sent to the model. Lockbox lines are never sent, replayed or inspected.
- The freeze is a git tag, `eval-freeze`, pinning code, prompt, model, policy, library, glossary and split.
- After the freeze, both full files run **once**, and the result is appended to `eval/lockbox_log.md`.
- **If the lockbox misses .90:** nothing is tuned on it. The measured number is reported with its error analysis, as a known weakness.
- **One rerun is allowed, only for a demonstrable implementation bug.** That means a gap between the code and this written spec (a parser, join or encoding error), not a prompt, threshold, glossary or policy change. A unit test that reproduces the bug is written first, and both scores are reported side by side.

### 10.4 How confident a number is

**Intervals and tests:**

- Every proportion gets a **Wilson 95% interval**.
- Same-item comparisons are **paired**:
  - McNemar's exact test for coverage, e.g. baseline vs final, EN vs FR;
  - a paired item bootstrap (10,000 resamples) for precision differences.
- A section-cluster bootstrap is reported as a robustness check.

**What the sample size allows:**

|                                                                                 | Value                                         |
| ------------------------------------------------------------------------------- | --------------------------------------------- |
| Detectable coverage difference (α .05, power .8)                                | ≈ 10–11 points on dev, ≈ 11–13 on the lockbox |
| Detectable precision difference between two variants with about 60 matches each | ≈ 19 points                                   |

So I never claim one variant is "more precise" than another. Variants are selected on coverage under the precision bar.

**What the acceptance rule can and cannot prove.** Exact binomial probabilities of passing "≥ .93 observed":

| True precision | n = 30 | n = 60 | n = 100 |
| -------------- | ------ | ------ | ------- |
| .85            | .15    | .04    | .01     |
| .90            | .41    | .27    | .21     |
| .93            | .65    | .59    | .60     |
| .95            | .81    | .82    | .87     |

The rule is a deliberately conservative filter, not a proof. Requiring the Wilson lower bound to exceed .90 is out of reach at these sample sizes: even 30/30 gives a lower bound of .886. Chasing that bound would push coverage towards zero, which is exactly the overfit the TF-IDF baseline shows.

**The claim is pre-committed in this form:** _"lockbox matched precision X% [Wilson 95% CI a–b] on n matched lines; we can / cannot rule out < 90% at 95% confidence."_

### 10.5 Baseline ladder

Every rung is scored by the same scorer on the same split.

| Rung                 | Pinned configuration                                                                                                                                                                                      | What it answers                                                    |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| B0 Rules only        | G1 + G2 unit prior; everything else → `needs_review`                                                                                                                                                      | The floor: coverage 0, safety and review load at zero intelligence |
| B1 TF-IDF            | char_wb 3–5-grams + word 1–2-grams, cosine scores averaged, `sublinear_tf`, fit on library + queries; argmax over all rows; threshold = max coverage with P ≥ .90 **on dev**, then applied to the lockbox | "The simplest reasonable approach", measured held out              |
| B2 Haiku single pass | Raw library, no section path, no extractors, no glossary; every valid answer matched                                                                                                                      | Raw model capability; shows why abstention is needed               |
| B3 Final             | The frozen system                                                                                                                                                                                         | The value of the engineering                                       |

**Rung comparisons:** B1 → B3 coverage by McNemar; B2 → B3 precision by paired bootstrap. Ablations of B3 run on dev only.

**Candidate recall gates.** Any step that narrows candidates must keep recall ≥ .95 per language on dev (a soft shortlist) or 1.00 (a hard filter; any miss is treated as a bug).

### 10.6 Abstention policy selection

**The 12 policies, fixed now:**

| Group            | Policies                                                                                |
| ---------------- | --------------------------------------------------------------------------------------- |
| Signal (a) alone | confidence ≥ τ for τ ∈ {60, 70, 80, 90}, each with gap ∈ {decisive, clear} (4 policies) |
| Signal (b) alone | attribute agreement `literal`                                                           |
| Signal (c) alone | two-pass agreement                                                                      |
| Combinations     | a(80) ∧ b · a(80) ∧ c · b ∧ c · a(80) ∧ b ∧ c                                           |
| Strictest        | a(90) ∧ b ∧ c                                                                           |
| Reference only   | match-all                                                                               |

**Selection:** among the policies that reach dev P ≥ .93 in **both** languages with ≥ 30 matched dev lines each, pick the one with the highest summed C₂₅₂. If two are within 3 lines of each other, pick the cheaper one. If none qualifies, the strictest ships and the shortfall is reported.

**Reported with the selection:**

- risk–coverage curves per language with bootstrap bands;
- AURC and E-AURC;
- calibration of the self-reported confidence: reliability diagram over 5 equal-mass bins, ECE and Brier score;
- precision at each candidate-gap level;
- a precision / coverage / review-load / cost table for every policy, produced by $0 replay. This is the explicit trade-off curve.

### 10.7 Error taxonomy, and how it drives the next experiment

**Each dev error is recorded** in `eval/errors_dev.csv` with:

- family;
- kind: `false_match`, `match_on_blank`, `false_nm`, `missed`, or `wrong_proposal_reviewed`;
- the first wrong level;
- a cause, from: `lexical_gap`, `usage_confuser`, `subtype_parse`, `header_context`, `not_in_library`, `llm_failure`, `gt_convention`.

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
  - enrichment derived from that library;
  - a small glossary;
  - the line and its section headers.
- Ground truth and annotations are read only by `eval/`, and a CI test enforces it.
- **Glossary provenance.** Every entry is tagged `standard`, `library`, `dev_obs`, `dev_error` or `lockbox_obs`.
  - Entries must state general domain facts, never item text.
  - A 6-gram overlap check against both BoQ inputs runs in CI.
  - `lockbox_obs` entries are excluded. If any are kept, lockbox precision is reported with and without them.
- **Disclosed contamination.** I profiled all 252 labelled lines, lockbox included, during data analysis, _before_ freezing the split. The lockbox is therefore blind to tuning but not to exploration. Mitigations:
  - no item-specific rules;
  - provenance tags;
  - a dev-only experiment ledger;
  - lockbox precision also reported excluding lines touched by any convention-derived rule.
- **The EN and FR lockboxes are correlated.** They are the same items, not two independent confirmations. The unseen live BoQ is the only fully blind test.
- **Builder labels.** The 30 blank-line classes and the FR smoke set are my own judgement. They are published with provenance and used only for evaluation.

### 10.9 Robustness checks

| Check                                                               | Pass criterion                                                                                                            |
| ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| FR smoke set                                                        | 0 closed-world violations, 0 false `not_a_material`, 0 decoy matches; precision reported as k/n with Wilson CI            |
| Library row-order shuffle (3 seeds)                                 | Identical rendered prompt hash; byte-identical FakeLLM output                                                             |
| Row-id stability                                                    | ids unchanged under reordering and row addition; `'x'` ≠ `'x '`; 0 collisions on both libraries                           |
| Failure injection (FakeLLM fault matrix + property-based schedules) | 0 lost lines; every affected line `needs_review` with its failure code; nothing matched from a failed or partial response |
| Determinism                                                         | 40 lines run twice live → decision and row flip rate reported; replay byte-identical                                      |
| Batch contamination                                                 | B = 1 vs B = 10: \|Δ accuracy\| within the noise floor                                                                    |

---

## 11. Engineering contract `[Rubric: Engineered]`

### 11.1 Module boundaries

```
src/oris_matcher/
  domain/      boq.py  library.py  normalize.py  attributes.py  decision.py   pure, no I/O
  llm/         base.py (port)  anthropic_llm.py  fake_llm.py  replay_llm.py
  prompts/v1/  templates, schema, glossary.yaml (with provenance)
  service.py   MatchService: the only orchestrator
  io/          boq_reader.py  writer.py  audit.py
  cli.py       Typer: match · score · replay · explain · serve
  api/         app.py  jobs.py  static/ui/ (committed build)
config/        policy.yaml  pricing.toml  never_match.yaml  service_units.yaml
eval/          score.py  check_requirements.py  report.py  baseline_tfidf.py  make_split.py
tests/
```

Two rules hold throughout:

- **The domain never imports from `llm/`, `io/` or `api/`.**
- **Decision logic exists in one place.** The CLI, API and UI all call `MatchService`.

### 11.2 Invariants, each enforced by a named test

| Invariant                                                           | Test                                                  |
| ------------------------------------------------------------------- | ----------------------------------------------------- |
| Output length and order equal the input, with unique line ids       | property test under random FakeLLM corruption         |
| Every `matched` triple is a library row, verbatim                   | property test; RQ4                                    |
| `not_a_material` only from D0 or D2; never on a measured unit       | decision truth table                                  |
| Every line has an audit record; every API attempt has a call record | RQ6                                                   |
| Replay of a run yields a byte-identical CSV                         | golden replay test                                    |
| Nothing global-library-specific in `src/`                           | grep guard + full run on the FR library under FakeLLM |
| No ground truth in `src/`                                           | leakage guard                                         |

### 11.3 Runtime contract

| Concern                           | Contract                                                                                                                                         |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Per-call timeout                  | 30 s; SDK retries off, so every attempt is logged                                                                                                |
| Retries                           | 3 attempts, 1/2/4 s backoff plus jitter; honour `retry-after`                                                                                    |
| Concurrency                       | semaphore, default 8, AIMD on rate limits (halve on 429, +1 per 10 successes)                                                                    |
| Truncation / malformed output     | bisect the batch down to single lines, then `LLM_FAILURE:truncated` / `malformed`                                                                |
| Missing / duplicate / unknown ids | re-ask only the missing ones; first duplicate wins (logged); unknown ignored (logged)                                                            |
| Circuit breaker                   | ≥ 10 consecutive failures or > 30% failures over 20 calls → stop calling; pending lines `LLM_UNAVAILABLE`; output still written; CLI exit code 3 |
| Budget                            | projected > $1.80 / 100 lines → stop; remaining lines `BUDGET_CAP`                                                                               |
| API partial failure               | always HTTP 200 with every line present and a per-line reason; never a 5xx for model failures                                                    |
| API limits                        | `POST /v1/match` ≤ 500 lines (413 above); the CLI and UI chunk automatically                                                                     |

### 11.4 API contract (`/v1`, additive-only)

```
POST /v1/match
  request:  {library: "global" | "fr" | <path configured server-side>,
             lines: [{item_no, short_description, long_description, unit, qty}]}
  response: {run_id, versions: {model, prompt, library_sha256, policy},
             decisions: [{line_index, item_no, decision, material_type, material_usage,
                          material_subtype, reason, suggestions: [top1, top2],
                          model, prompt_version, cost_usd, latency_ms}],
             summary: {counts, cost_usd, mean_latency_ms, failures}}
POST /v1/jobs, GET /v1/jobs/{id}, GET /v1/jobs/{id}/result[.csv]   (UI progress path)
GET  /health  (liveness)    GET /ready (library loaded, key present, model reachable)
```

- Validation errors return a 422 envelope.
- New fields are optional; anything breaking goes to `/v2`.
- Labels are verbatim library strings.

### 11.5 Reproducibility and determinism

**Reproducibility:**

- Python 3.10 with uv and a lock file.
- The model ID is pinned to a dated snapshot.
- Each run writes a manifest (§9.6).
- The response cache is keyed on (normalised line text, library SHA, prompt version, model, policy version).

**Determinism contract:** the same manifest plus the same `calls.jsonl` produce a byte-identical output. Live runs are _not_ claimed to be deterministic; their flip rate is measured instead.

### 11.6 Observability

- Structured JSON logs carry `run_id` and `line_index`.
- Each run prints a summary to stdout, the manifest and the API summary:
  - decision shares;
  - reason-code histogram;
  - retries and cache hits;
  - p50/p95 latency;
  - $ per 100 lines.
- **Three metrics ORIS should alert on:**
  - the `needs_review` share jumping more than 20 points above a client's baseline (drift or new vocabulary);
  - an `LLM_FAILURE` rate above 5%;
  - cost per 100 lines above $1.

### 11.7 Tests

All of these run offline in CI with FakeLLM and ReplayLLM:

- `ruff`, `mypy --strict`;
- the decision truth table;
- property tests (`hypothesis`) for the invariants;
- the fault matrix (timeout, 429, 5xx, 529, malformed, truncation, refusal, missing/duplicate/unknown ids);
- a scorer fixture with hand-computed metrics;
- the API contract;
- CLI/API parity;
- a full run on the FR library;
- the split-hash test;
- leakage and global-string guards;
- library shuffle and row-id stability;
- normalisation and encoding round trips;
- golden replay.

Tests that need the live API are marked and never run in CI.

`eval/check_requirements.py` gates the committed outputs on ten checks (RQ1–RQ10):

- columns;
- length and order;
- the decision enum;
- closed-world labels;
- `not_a_material` provenance;
- one audit record per line;
- cost;
- latency;
- manifest completeness;
- the model allowlist.

### 11.8 Data handling and threat model

**Data minimisation**

- Only descriptions, the unit and the section path leave the machine.
- Run folders are local and git-ignored.
- The API key comes from the environment and is never logged.
- A client who forbids external APIs can be served through the LLM port with a local model of 8B parameters or fewer, at a measured accuracy cost.

**Prompt injection (text inside a BoQ line):**

|                | What happens                                                                                                                                                                               |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Guaranteed     | No invented triple (closed-world validation). No material silently skipped: a non-material vote on a measured unit still lands in review, because G2 requires a service unit. No lost line |
| Not guaranteed | A wrong-but-valid match. This is mitigated, not prevented, by attribute vetoes, the evidence-span check (quoted evidence must be a substring of the line) and abstention                   |

Lines are delimited as data and truncated at a maximum length, with a flag. One adversarial fixture per guarantee is in the test suite.

---

## 12. Extension points and the live session `[Rubric: Defend]`

| Likely request                                     | How it is done                                                                                                                              | Time     |
| -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| Run an unseen BoQ on the FR library                | `oris match --input new.csv --library data/oris_materials_fr.csv --output out.csv`; the strict policy applies if the smoke-set rule says so | run time |
| "Favour coverage" / "change the threshold"         | edit `config/policy.yaml` → `oris replay runs/<id> --policy policy.yaml` → new P / coverage / review load in seconds, $0                    | < 2 min  |
| "Why did line X get this?"                         | `oris explain --run <id> --item 03.03.0010.`: prompt excerpt, raw response, validator checks, rule fired, cost, latency                     | < 1 min  |
| Add an attribute (e.g. steel grade, pipe SN class) | add a pure extractor to the registry + copy the test template; it runs on lines and library rows alike                                      | ~5 min   |
| Swap the model                                     | `--llm` selects the adapter; a new adapter implements one method of the LLM port                                                            | ~10 min  |
| A new unit or service unit                         | `config/service_units.yaml` + truth-table test                                                                                              | ~3 min   |
| A new reason code                                  | enum + decision-table row + test                                                                                                            | ~5 min   |

**Rehearsed before the session:**

- each row above;
- a run on a deliberately mangled copy of the FR input (no item codes, `;` delimiter, renamed columns, XLSX);
- a ReplayLLM fallback in case the network fails.

---

## 13. Operator UI (optional deliverable)

**What it does.** A single page served by the same FastAPI process at `/ui`, built from strict TypeScript with esbuild. The build output is committed, so reviewers need no Node.

| #   | The brief asks                          | The UI does                                                                                                                | Proved by                           |
| --- | --------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- | ----------------------------------- |
| U1  | Upload a CSV or Excel BoQ               | Drop zone; the server parses it with the shared reader                                                                     | CSV/XLSX row-id parity test         |
| U2  | Run the matching                        | One button; a job with a progress bar                                                                                      | job lifecycle test (FakeLLM)        |
| U3  | Show the table with decision and labels | Results table with the verbatim labels, reason, suggestions and audit drawer                                               | row count and order equal the input |
| U4  | `needs_review` easy to spot             | Amber chip with a triangle shape **and** text, amber row border, sorted to the top, one-click filter, count in the summary | DOM assertion + manual check        |

**Design principles:**

- **No business logic in the browser.** The downloaded CSV is the server's file, byte-identical to the CLI output.
- Mobile-first, keyboard accessible, light and dark.
- Five colour tokens, system fonts, inline SVG icons.
- No ORIS branding.

**Schedule:** built Thursday, with a hard stop at 15:00. The full specification, with wireframe, states and tokens, is in `docs/ui-spec.md`.

---

## 14. Schedule and grader gates

Work started on Sunday 4 October. Submission is Friday 9 October, 17:00 Paris. A phase is done only when its evidence exists as files in the repo.

| Day   | Work                                                                                                                                                                                          | Gate evidence                                                                                                    | Rubric checked                    |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- | --------------------------------- |
| Sun 4 | This pre-registration; `docs/`; `eval/split_v1.json` frozen; blank-line annotations                                                                                                           | This commit; split SHA in §0                                                                                     | Data; design-doc questions        |
| Mon 5 | Scaffold; reader, library, validator, G1/G2; scorer + fixture; B0; B1 on dev; Haiku adapter with audit and failure handling (test-first); B2 on a slice, then all dev                         | B1/B2 dev numbers in `runs/`; G1 37/37; validator property test green                                            | Baseline (started); engineered    |
| Tue 6 | Section path; extractors; schema with confidence and gap; replay; E-02, E-01, E-03, E-04, E-05; error taxonomy; FR smoke set labelled, hashed and run; **policy frozen**                      | `eval/experiments.md`; acceptance result per language (or the documented shortfall); risk–coverage + calibration | Trade-offs; data (EN vs FR)       |
| Wed 7 | Hardening (fault matrix, API, CLI parity, budget stop, manifest); measure cost, latency and noise floor; **`eval-freeze` → lockbox once** → both outputs → `docs/evaluation.md` → §17 Results | `check_requirements.py` green on both outputs; one entry in `eval/lockbox_log.md`                                | Engineered; baseline + confidence |
| Thu 8 | UI 09:00–15:00 (hard stop); README (run, known weaknesses, with more time, labelled-data disclosure); clean-clone check; live rehearsal                                                       | Fresh clone: `uv sync && uv run pytest && uv run oris match …` green; U1–U4                                      | Defend; all                       |
| Fri 9 | Buffer; final review; submit by 17:00                                                                                                                                                         | Submitted                                                                                                        | —                                 |

---

## 15. Risks

| Risk                                                        | Likelihood | Mitigation                                                                                       |
| ----------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------ |
| Lockbox precision below .90 in one language                 | medium     | Reported honestly per §10.3. The strictest-policy fallback is ready. Never tuned on the lockbox  |
| French usage accuracy stays low (localised rewrite)         | high       | E-01 arms; abstention routes uncertain FR lines to review rather than guessing                   |
| Uncalibrated policy on the FR library over-matches live     | medium     | Pre-registered smoke-set rule (§6); a decoy-match count of 0 is required                         |
| Unseen BoQ structure (no codes, `;`, XLSX, renamed columns) | medium     | Shared reader with aliases; `HEADER_UNCONFIRMED` degrades to review; rehearsed on a mangled copy |
| Rate limits or a network outage during the session          | low–medium | Measured throughput, AIMD, a prepared replay run, `--limit` for a subset                         |
| A schedule slip                                             | medium     | The ordered cut list (§8); the UI has a hard stop                                                |
| A wrong-but-valid match forced by injected text             | low        | Not prevented, only mitigated (§11.8); disclosed                                                 |

---

## 16. Amendments

_Append-only. Each entry gives the date, what changed, why, and which results existed at the time._

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
|---|---|---|---|
| EN | 55 | 84 | 0 |
| FR | 57 | 82 | 0 |

The veto leaves a median of 342 and a minimum of 266 candidate rows per line, which confirms D-08: the extractors veto, they do not filter.

**Why:** a lexicon gap found while reading dev lines (provenance `dev_obs`, a general domain fact rather than item text). The zero rule makes explicit a sibling rule that §9.3 left unstated.

### A3 · 2026-10-04 · Requirement count

§3 says the requirements are condensed from "90 atomic requirements". `docs/requirements-traceability.md` holds **87**. The count is corrected here; no requirement was dropped.

**Results existing at the time:** none.

---

## 17. Results

_Append-only, dated. Filled as each gate produces measured numbers. Nothing here is an estimate._

_(none yet: no model call has been made at the time of pre-registration)_
