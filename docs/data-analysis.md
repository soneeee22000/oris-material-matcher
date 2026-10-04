# Data analysis

This is the full data analysis behind `DESIGN.md` §4. It was done before the split was frozen, so it covers all rows, lockbox included. `DESIGN.md` §10.8 discloses this.

**Analysis passes, cited in brackets:**
- [GL] GT vs library;
- [EF] EN vs FR and hard lines;
- [BL] baselines;
- [FR] the FR library and generalisation;
- [CR] independent re-verification. Where [CR] disagrees with an earlier pass, the [CR] number is used.

A second re-verification on 2026-10-04 confirmed every number below and corrected three. The corrections are marked **(corrected 2026-10-04)**.

**Loading:** all files were read as `utf-8` with `dtype=str, keep_default_na=False` (Python 3.10, pandas, rapidfuzz, sklearn).

---

## 1. Key numbers (verified)

| Fact | Value | Source |
|---|---|---|
| Rows in the GT, EN and FR inputs | 319 each. `Item No.` order is identical, with 0 duplicates. `BoQ Qty` is identical in EN and FR | GL, EF, CR |
| Header rows (L0 + L1) | 37 (10 L0 + 27 L1). All have empty Unit, Qty and Long Description, and none is labelled | GL, CR |
| L2 item rows (`NN.NN.NNNN.`) | 282: 252 labelled, 30 blank | GL, CR |
| Total blank GT rows | 67 (37 headers + 30 L2). 0 rows are partially labelled | CR |
| Label fill pattern | 203 full triples, 49 with a blank subtype, 67 fully blank | GL |
| Distinct GT triples | 244 across 252 lines. Frequencies: 238 appear once, 5 appear twice, 1 appears four times (Prefab / Other prefab / blank) | GL, CR |
| GT triples found in the global library | 252/252, exact raw-string match | GL, CR |
| GT triples found in the FR library | 0. The libraries share 0 material types; only the `Custom` placeholder overlaps (1 type, 2 verbatim triples) **(corrected 2026-10-04)** | GL, CR |
| Global library | 342 rows, 30 types, 108 type+usage pairs, 70 blank subtypes, 0 duplicates | GL |
| Global library coverage by GT | 244/342 triples (71%), 99/108 pairs, 29/30 types (`Custom` is never used) | GL |
| FR library | 70 rows, 24 types, 39 pairs, 7 blank subtypes | FR |
| GT rows by type | Concrete 75, Aggregates 32, Asphalt 28, C&D 24, Excavations 13, Cement 12, Steel 11, Admixture 8, Prefab 7, then 20 types with 1–6 rows | GL, EF |
| Mixed parents (a blank subtype plus specific siblings) | 21 pairs. 80 GT rows sit on them and 0 of those pick the blank | GL, CR |
| Concrete strength class appearing literally in text | 73/73 in EN and 73/73 in FR. 0 lines carry two different classes | CR |
| Non-blank GT subtype appearing literally in text | EN 91/203 case-insensitive (85/203 case-sensitive); FR 85/203 either way **(method stated 2026-10-04)** | GL, BL |
| Lines still ambiguous on usage once type and subtype are known | 185/252 | EF |
| Unit rule `{'',LS,month,day,Ft,mois,j}` | Flags 46 lines (37 headers + 9 service lines) and drops 0 labelled lines, in both EN and FR | BL, CR |
| Units t and m³ | 220/220 labelled | EF |
| Section-majority type prior (L1 section) | **73.3% leave-one-out** (184/251). The reported 74.6% is in-sample and leaks the line's own label. The same prior gets type+usage right only **24.7%** of the time (62/251). Using the L0 section instead: 48.8% for type | CR |

## 2. Data quirks and gotchas

**Encoding**
- Every file is UTF-8 with no BOM and LF line endings.
- cp1252 also decodes every file without error but turns accented characters into mojibake, so an encoding mistake will not raise an error.
- The global library and the GT are pure ASCII. The FR library, the inputs and the sample output contain non-ASCII characters.
- On Windows, set `PYTHONIOENCODING=utf-8`, otherwise printing `∅` (U+2205) crashes.

**Blank values**
- Read with `keep_default_na=False`. Otherwise blank subtypes turn into NaN and triple lookups break.
- A blank subtype is `''` in both the library and the GT.

**Trailing spaces in canonical strings**
- Global library: usage `'bitumen for coating '` (3 rows). The GT reproduces the trailing space.
- FR library: `'Déchets métal non-ferreux '` and `'Déchets dangereux stockage '`.
- Normalise only for matching, and emit the verbatim string (key everything by row id). Stripping on one side only breaks exact match.

**Headers**
- Empty Unit is an exact header test, identical to "Item No. does not match `NN.NN.NNNN.`" (37 rows, both languages).
- Item numbers have gaps: 01.02.0050–0070 do not exist, yet 01.03.0130 refers to "Planings from 01.02.0070".

**Units are localised but map 1:1**
- pcs↔U, LS↔Ft, month↔mois, day↔j.
- t, m³, m² and m are shared. Both languages write `m²`/`m³` as Unicode characters; there is no `m2`/`m3`.

**Number and symbol formats**
- The FR BoQ uses decimal commas (66 comma vs 4 dot). The FR library uses dots.
- The FR library writes `∅` (U+2205); the BoQ writes `Ø` (U+00D8).
- Normalise with NFKC plus folding of Ø/∅/⌀ and `,`→`.`.

**Usage and subtype strings are not unique**
- Usage strings repeat across types: "For use in hydraulically bound mixtures" appears under 7 types, "for use in concrete mixtures" under 6. A usage only means something together with its type.
- 46 subtype strings repeat across type+usage pairs. C30/37 appears under 20 pairs.

**Typos and casing in the libraries**
- Global: `harderning accelerator`; 19 of 93 usages start lowercase.
- FR: `Métakaloin`, `Mat de candélabre`.
- FR contains English fragments, such as `Custom material (Carbon impact in tonne)`.

**Duplicate usage under two types**
- `Precast Concrete sleeper for railway` exists under both Concrete and Prefab. The GT picks Prefab.

**Baseline configuration is sensitive**
- TF-IDF top-1 moves between .349 and .405 depending on `sublinear_tf` and on what the vectoriser is fit on [CR].

## 3. Where hard lines concentrate

**By level** (TF-IDF top-1 on the 252 labelled lines) [CR]

| Lang | Type | Type+Usage | Full triple | P(usage right given type right) | P(subtype right given type+usage right) |
|---|---|---|---|---|---|
| EN | .722 | .524 | .405 | .725 | .773 |
| FR | .560 | .210 | .159 | **.376** | .755 |

Usage is the bottleneck in both languages, and it collapses in FR. Subtype accuracy given the right type+usage stays flat at about .76.

**By category** [CR]
- **Usage-hard families:** the subtype is easy, the structural element or layer is the decision.
  - Concrete: 75 lines, 30% of all labelled lines. About 13.6 candidate usages remain after type and subtype are known. EN full-triple accuracy .53, FR .19.
  - Aggregates: 15 usages, and the subtype does not narrow them.
  - Steel: 8 usages.
  - Prefab.
- **Subtype-hard families:**
  - Asphalt: about 21 subtypes per course. EN full-triple accuracy .25. Fully rule-solvable: family, HMA/WMA and RAP% agree with the GT on 28/28 lines in EN and FR [EF].
  - Cement: 11 subtypes, but CEM types are 100% literal.
  - C&D: the EWC code is literal in only 3/24 lines and needs reasoning about whether the material is hazardous. EN full-triple accuracy .21.
  - Excavations: quality grade has to be inferred from RMR class or weathering. EN full-triple .15, FR .00.
- **Specific confusers:**
  - Piers vs piles: ratio 94, and FR "pile" means EN pier.
  - Base vs binder course.
  - Quick vs Slow visco-elastic.
  - Pile caps → footings.
  - Capping beam → excavation beam.
  - Approach slabs → transition slabs.
  - Segmental rings → tunnel concrete vs Prefab.
  - "RC" means recycled concrete in aggregate lines but rock class in tunnel lines 05.01.0010–0070.
  - Limestone filler → `Limestone/for all usage`, not `Filler`.
  - Admixture synonyms, e.g. Early-strength → `harderning accelerator`, PCE → Plasticiser [EF].

**By L0 section** (full-triple top-1, EN / FR) [CR]
- 01 Demolition/earthworks: .28 / .08
- 03 Pavement: .31 / .13
- 06 Railway underpass: .18 / .00
- 09 Buildings: .22 / .00. This is the ready-mix trap: the lines say "columns" or "transfer beams", but the GT puts all 9 under General Ready mixed.
- 04 River bridge: .70 / .17
- 08 Materials to batching plants: .46 / .26

**EN vs FR**
- FR is a **localised rewrite, not a translation** [EF]:
  - Codes change: AC→GB/BBSG, PA→BBDr, RA→AE, WMA→"tiède", 0/32→0/31,5, crushed stone→GNT, Ready-mix→BPE.
  - Some technical values change too: binder 50/70 in EN becomes 35/50 in FR (03.03.0010).
  - False friend: "couche de fondation" means subbase.
- Vocabulary overlap with the English library: 3.17 shared words of 4+ letters per EN line vs 0.37 per FR line. 168/252 FR lines share no such word with the library (9 in EN) [CR].
- EN and FR fail on different lines: 30 correct in both, 72 in EN only, 10 in FR only. The FR-only wins come from more explicit French wording, e.g. "éléments préfabriqués" or "béton projeté" [CR].
- The phrasing used in the FR library ("enrobé chaud/tiède") appears in 0 lines of the FR BoQ [FR].

## 4. Baselines

**Setup.** Unit rule first, then argmax over the 342 rows. A line is `matched` if its score is at or above the threshold, otherwise `needs_review`. Precision = all-3-correct ÷ matched (a blank-GT line predicted matched counts as wrong). Coverage = all-3-correct matched ÷ 252.

**Pinned configuration for TF-IDF char+word:** `char_wb` 3–5-grams plus word n-grams, cosine scores averaged, `sublinear_tf=True`, fit on library + queries [CR].

| Method | Lang | Threshold | Precision | Coverage | R@1 / R@5 / R@20 / R@50 (median rank) |
|---|---|---|---|---|---|
| TF-IDF char+word | EN | none (match all) | .375 | .405 | .405 / .667 / .869 / .937 (2) |
| TF-IDF char+word | EN | ~0.384–0.390 (tuned in-sample) | .909 (10/11) | **.040** | same as above |
| TF-IDF char+word | EN | 0.275 | .764 | .167 | same as above |
| TF-IDF char | EN | 0.4655 | 1.0 (1/1) | .004 | .341 / .611 / .837 / .929 (3) |
| rapidfuzz token_set | EN | 0.8235 | .900 (9/10) | .036 | .270 / .405 / .571 / .726 (10) |
| TF-IDF char+word | FR | none | .147 | .159 | .159 / .437 / .690 / .786 (9) |
| TF-IDF char+word | FR | no threshold reaches P≥.90 (max .571) | – | **0** | same as above |
| TF-IDF char | FR | 0.2788 | 1.0 (1/1) | .004 | .159 / .377 / .639 / .730 (10) |
| rapidfuzz token_set | FR | none reaches .90 (max .571) | – | 0 | .063 / .115 / .226 / .389 (71) |
| Section-majority prior (L1, leave-one-out) | EN | – | – | – | type .733, type+usage .247 |

**Held-out check.** Tuning the threshold on half the lines and testing on the other half (200 splits): EN held-out precision is about **.76–.82** depending on how the threshold is picked, and falls below .90 in 74–99% of runs. FR is near-useless (precision .09–.33). The 4% EN operating point is overfit [BL, CR].

**Other gates.** Gating on the top1–top2 margin or on the top1/top2 ratio does no better [BL].

**Bar to beat:** about 4% coverage at P≥.90 in EN (optimistic) and 0% in FR.

## 5. Blank-GT lines that carry a material, and the decision policy

The [CR] classification of the 30 blank L2 lines supersedes the earlier passes' estimates. The per-line classes are in `eval/annotations/blank_line_classes.csv`.

**16 services or temporary works:**
- 00.01.0010/0020/0030
- 00.02.0010/0020/0030
- 00.03.0010–0050
- 01.01.0010/0020
- 02.01.0040
- 05.02.0140
- 06.01.0120

9 of these are caught by the LS/day/month rule.

**13 lines that name a material with no global equivalent:**
- 01.04.0100 hydroseed
- 02.01.0010 and 02.01.0020 PP carrier drain
- 03.03.0330 hot-poured sealant
- 03.04.0070 PU sealant
- 03.05.0060 thermoplastic markings
- 04.04.0050 pot bearings
- 04.04.0060 modular joint
- 04.04.0070 PMMA waterproofing
- 05.01.0080 slotted liners
- 05.02.0130 PVC-P membrane
- 07.01.0080 aluminium signs
- 07.01.0090 road studs

**1 ambiguous line:** 04.04.0080 soffit formwork and falsework.

**Policy implications**
- The 13 material lines must be **`needs_review`, never `not_a_material`**. The README says marking a material line `not_a_material` is an error, and the sample output's SBS membrane row (99.01.0050) is `needs_review`. Earlier passes suggested `not_a_material` for these lines, which contradicts the brief [CR].
- **Coverage denominator.** The README defines coverage as "share of material lines". That could mean **252** (lines with a GT label) or **265** (252 + 13). With 265, coverage caps at 95.1% on the global library. State which one is used, and make the scorer support both [CR].
- **Decoy cues** in blank lines that will pull a naive retriever toward a real label [EF]:
  - "pile" → Concrete for piles (00.03.0040/0050)
  - "tack coat" → Bitumen emulsion (04.04.0070)
  - "bitumen" → Bitumen (07.01.0090)
  - "formwork" → Wood
  - "aluminium" → C&D 17 04 02
  - PP pipe → PVC-U / HDPE, which are real GT labels elsewhere
- **The reverse trap:** 34 labelled lines start with an activity verb ("Break out", "Remove", "Dredge", "Drill and blast"). In the GT these are C&D waste or Excavations, not services. Never treat verb-first lines as non-material.
- **A blank label depends on the library.** Under the FR library, 04.04.0050 has an exact row (`Appareils d'appui / Appareil d'appui à pot / ''`), and formwork plausibly maps to `Bois/Coffrage`. Never train a non-material classifier on GT blanks [FR].
- **Unit rule.** LS/day/month (Ft/j/mois) loses 0 materials here, but the unseen FR BoQ could contain hire items that carry a material. Treat these units as a strong prior, and treat empty Unit as the only hard rule [CR].

## 6. FR library and generalisation risks

**A different taxonomy, not a translation**
- 0 shared material types or triples with the global library. Only the `Custom` placeholder overlaps (1 type, 2 triples) **(corrected 2026-10-04)**.
- Global puts the application in the *usage* and keeps the *subtype* a short qualifier.
- FR puts a product family or standard in the *usage* (`Béton Fascicule 65`, `Enrobés bitumineux`) and bundles a full spec into the *subtype*: exposure classes + strength + cement content + cement type, or family + temperature process + % AE.
- FR has no asphalt course level; the layer is implied by the code (GB/EME are base courses, BBSG/BBDr wearing or binder).
- FR has no mixed blank parents (all 7 blanks are sole children), no reused subtypes and no reused usages.

**Coverage of this BoQ under the FR library.** These figures are analyst judgement and are not code-verified [CR]:
- About 17 GT lines have an exact FR equivalent, and about 37 have an exact or partial one.
- FR reaches 16 of the 30 global types.
- Only 10 of 75 concrete lines survive matching on strength plus an exposure-class subset (FR has only C16/20, C30/37 and C35/45).
- On a BoQ like this one, most lines would correctly end up as `needs_review`.

**Coverage is likely to look different live**
- The GT sweeps 71% of the global library at about one line per leaf.
- The live BoQ will likely sweep the FR-only leaves: maritime fenders and bollards, detonating cord, bearings, CMC drilling-mud additive, ductile iron pipe, EPDM, lighting masts, and GB/EME with 20/40% AE (global offers only 30/50% and has no EME1).
- The FR file may be a sample of a larger library, so do not assume 70 rows.

**Cross-language collapse** (rapidfuzz top-1)
- FR BoQ on the global library: full triple 6.0%.
- EN BoQ on the FR library (proxy, n=56): type 20%, type+usage 14%.
- The ranking flips with library language, so enrichment must be bilingual in both directions.

**What breaks if anything is hard-coded to global**
- Type-name rules (Concrete, `asphalt mixture for * course`, EWC 17 xx, Virgin/Recycled/Reclaimed).
- A hard-coded "never pick the blank" rule.
- Concrete keyed on structural element. FR needs attribute subset logic instead: strength equal, line exposure classes ⊆ the row's list, compatible CEM type and dosage.
- Few-shot or retrieval memory built from GT, which carries global labels.
- A `Custom` exclusion written by type name.

## 7. Design implications (ranked)

> These implications fed the design. Where they differ from `DESIGN.md`, `DESIGN.md` wins:
> - the header gate (§5.3 D-03);
> - the strict service gate G2 (D-04), which replaces the unit-only prior below;
> - extractors as a veto rather than a filter (D-08).

1. **Deterministic gate first.**
   - Empty Unit → `not_a_material` (exact on all 37 headers).
   - LS/day/month (Ft/j/mois) → `not_a_material` as a prior (9/9 here).
   - t or m³ → never `not_a_material`.
   - Every other uncertain case defaults to **`needs_review`**.
2. **Closed-world output.**
   - Load the library raw, key on row id, and emit only exact verbatim tuples, including trailing spaces.
   - Validate every LLM answer against the set of tuples.
   - Exclude `Custom` with a generic rule (usage pattern or config flag), not by type name.
3. **Decide hierarchically, conditioning usage on type.**
   - Usage-hard families (Concrete, Aggregates, Steel, Prefab): regex out the subtype, then have the LLM choose among only the usages valid for that (type, subtype). That is up to about 20 for Concrete and 15 for Aggregates.
   - Subtype-hard families: code parsers for Asphalt (family, HMA/WMA, RAP/AE %, in both EN and FR codes), CEM, EWC and grading, plus LLM reasoning for hazardous status and quality grade.
4. **Library-agnostic attribute extractors** that run on both line text and library text:
   - strength `C\d+/\d+`
   - exposure `X[0CDSFAM]\d?`
   - `CEM [IV]+(/[ABC])?`
   - RAP/AE %
   - tiède / WMA / chaud / HMA
   - grading d/D, diameter, g/m²
   
   Use these as hard filters, not just as text.
5. **Bilingual, per-library offline enrichment** (`semantic_description`), cached by library hash:
   - an EN↔FR translation of the path
   - an acronym glossary: AE↔RAP, tiède↔WMA, GB/BBSG/EME/BBDr↔AC/EME2/PA, GNT, BPE, PEHD↔HDPE
   - structural nouns: culée, pile = pier, semelle, tablier, hourdis, radier, voussoir
   - false friends, and how the row differs from its siblings
   - typo-corrected aliases
6. **Pass the header hierarchy** (L0 and L1 short descriptions) as context. It is worth about 73% on type (leave-one-out) and resolves the 09.01 ready-mix block, but it **does not** solve usage (24.7%).
7. **Do not rely on top-k retrieval for FR.** Recall@20 is .69 overall and .47 or lower for Aggregates, C&D, Excavations and Steel. Either show the full list of 108 type+usage pairs (it fits a small-model prompt), or translate before retrieving. Target recall@k ≥ .95 before any reranking.
8. **Derive structural rules from the loaded library:**
   - "a blank subtype is valid only when the parent has no specific siblings" (holds in the global GT, 0/80)
   - sole-child parents
   - catch-all detection
9. **Confidence comes from agreement, not lexical scores.** Combine the LLM's choice, the validator, and extracted codes agreeing with the row's attributes. Low margin on a known confuser pair → `needs_review`.
10. **Honest evaluation.**
    - With 238 singleton triples, any GT few-shot example leaks its own answer. Use leave-one-section-out, or library-only prompts.
    - Report results per family, per level and per language, with confidence intervals (a single error moves precision several points at small n).
    - State the coverage denominator (252 vs 265).
    - Pin the baseline configuration.
    - Hand-label an FR dev set of about 30 lines that covers FR-only leaves.

## 8. Surprises

- **The GT is a synthetic sweep.** 244 triples over 252 lines, one line per leaf, covering 71% of the library. This kills few-shot retrieval and suggests the live BoQ sweeps the FR leaves.
- **Concrete lines rarely say "concrete".** 3/75 in EN (26/75 "béton" in FR). The strength class is the type signal, and usage decides 30% of coverage.
- **The FR library shares nothing with the global library**, and the FR BoQ is a localised rewrite that changes codes and even some numeric values.
- **13 blank-GT lines are real materials,** and two profilers advised `not_a_material` for them, which contradicts the README.
- **The unit rule is free precision:** 46 flagged lines, 0 materials lost.
- **No lexical score gives a usable confidence signal.** Even the top 14 EN lines are only 86% correct, and the P≥.90 point does not survive held-out testing.
- **The 74.6% section prior was leaky.** Leave-one-out gives 73.3% for type and only 24.7% for type+usage.
- **FR beats EN on 10 lines** where the French wording is more explicit.
- **Canonical label strings contain trailing spaces,** and the GT reproduces them.
- **Two different diameter characters** (∅ in the library vs Ø in the BoQ) and two decimal conventions.

## 9. Re-verification (2026-10-04) and distributions for charts

**Header test (corrected).**
- L0 header rows have bare item numbers `0`..`9`, and L1 header rows look like `00.01.`.
- A test written as "`NN.` / `NN.NN.` shape" catches only the 27 L1 rows.
- The header gate therefore uses: empty Unit **and** empty Qty **and** Item No. matching `^\d{1,2}$|^\d{2}\.\d{2}\.$`. That catches 37/37, and no non-header row has an empty Qty.

**Strength-class regex (corrected).** Use `c(\d{1,3})/(\d{1,3})`. A two-digit pattern misses C8/10 and reports 72 instead of 73.

**Expected `not_a_material` load on this file.** At most 46: 37 headers plus the 9 service lines with LS/day/month units. The other 7 service lines carry pcs/m/m² units and go to `needs_review` by design.

**Distributions (verified):**

- L2 lines per L0 section (total L2 / labelled / blank-GT): 00 Preliminaries and general items 11/0/11; 01 Site clearance, demolition and earthworks 43/40/3; 02 Drainage 9/6/3; 03 Pavement 78/75/3; 04 River bridge BW-3 and structures 50/46/4; 05 Tunnel Kalkberg 22/19/3; 06 Railway underpass EÜ-7 and track works 12/11/1; 07 Road equipment, retaining systems and ducts 9/7/2; 08 Materials supplied to batching and mixing plants 39/39/0; 09 Buildings and minor structures 9/9/0. Sums 282/252/30.
- Labelled lines per L1 section (L2 total/labelled): 00.01 3/0, 00.02 3/0, 00.03 5/0, 01.01 2/0, 01.02 18/18, 01.03 13/13, 01.04 10/9, 02.01 4/1, 02.02 5/5, 03.01 11/11, 03.02 21/21, 03.03 33/32, 03.04 7/6, 03.05 6/5, 04.01 12/12, 04.02 15/15, 04.03 13/13, 04.04 10/6, 05.01 8/7, 05.02 14/12, 06.01 12/11, 07.01 9/7, 08.01 13/13, 08.02 5/5, 08.03 7/7, 08.04 14/14, 09.01 9/9.
- Type x L0 heatmap (labelled lines): Concrete {03:11, 04:38, 05:14, 06:3, 09:9}; Aggregates {01:6, 02:2, 03:13, 06:4, 08:7}; Asphalt {03:28}; C&D {01:22, 06:2}; Excavations {01:9, 05:4}; Cement {03:4, 08:8}; Steel {04:8, 05:1, 06:1, 07:1}; Admixture {03:1, 08:7}; Prefab {02:2, 03:3, 06:1, 07:1}; Bitumen {03:4, 08:2}; Additives {08:6}; Filler {03:1, 08:2}; Limestone {03:1, 08:2}; Recycled Plastic {02:1, 07:1}; Composite {03:1, 07:1}; Cold Recycled Bound Material {03:2}; Fly Ash, Blast furnace slag, Hydrated lime, Silica Fume each {03:1, 08:1}; Building Components {03:2}; Quicklime {01:1}; Geotextile {01:1}; Soil {01:1}; Drainage Elements {02:1}; Wood {07:1}; Gabion Systems {07:1}; Cabling Components {07:1}; Water {08:1}.
- Top-4 types = 159/252 labelled lines (63.1%); Concrete alone 75/252 = 29.8%.
- Unit histogram, all 319 rows. EN: t 112, m³ 108, (empty) 37, m² 23, m 20, pcs 10, LS 3, month 3, day 3. FR: t 112, m³ 108, (empty) 37, m² 23, m 20, U 10, Ft 3, mois 3, j 3.
- Unit histogram, 252 labelled lines (EN / FR): t 112, m³ 108, m² 18, m 12, pcs/U 2.
- Unit histogram, 30 blank-GT L2 lines (EN): pcs 8, m 8, m² 5, LS 3, month 3, day 3 (FR: U 8, m 8, m² 5, Ft 3, mois 3, j 3). Zero blank lines are t or m³.
- Blank L2 split by L0: 00 11, 01 3, 02 3, 03 3, 04 4, 05 3, 06 1, 07 2, 08 0, 09 0. Analyst classes: 16 services/temporary works (9 caught by LS/day/month; 7 have pcs/m/m² units), 13 material-without-global-equivalent (must be needs_review), 1 ambiguous (04.04.0080 soffit formwork).
- Funnel for a chart: 319 rows -> 37 headers (decided by rules, no model call) -> 282 items, **all sent to the model** (DESIGN.md §16 A1). Of these, 9 carry LS/day/month units and can end as not_a_material through G2 if the model confirms; the rest split into 252 labelled + 13 materials with no equivalent + 7 services with physical units + 1 ambiguous.
- Label-fill donut: 203 full triple / 49 blank subtype / 67 fully blank (37 headers + 30 L2).
- Blank-subtype labelled rows by type (49): Steel 11, Prefab 7, Filler 3, Limestone 3, then 2 each Recycled Plastic, Composite, Cold Recycled Bound Material, Fly Ash, Blast furnace slag, Hydrated lime, Silica Fume, Building Components, and 1 each Quicklime, Geotextile, Soil, Drainage Elements, Concrete, Wood, Gabion Systems, Cabling Components, Water. All 49 sit on sole-blank parents (49 sole-blank parents in global library); 21 mixed parents, GT never picks the blank on them (0/80).
- Global library branching: Concrete has 24 usage pairs, Aggregates 15, Steel 8, C&D 8, Custom 6; max subtypes under one pair = 26. 93 distinct usage strings, 130 distinct subtype strings.
- GT triple frequency: 238 singletons, 5 doubles, 1 quadruple (Prefab / Other prefabricated concrete elements / '') -> 244 distinct = 71.3% of 342 library rows; 99/108 pairs; 29/30 types.
- Concrete signal: strength class is literal in 73/73 class-subtyped Concrete lines in both languages, but the word 'concrete' appears in only 3/75 EN lines vs 'béton' in 26/75 FR lines.
