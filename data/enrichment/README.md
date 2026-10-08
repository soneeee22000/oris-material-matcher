# Library enrichment sources (DESIGN.md §16 A68 §1)

This folder holds the hand-written inputs of E-01 arm C, the deterministic bilingual library
enrichment (DESIGN.md §6). The generator (`scripts/enrich_library.py`, A68 §2) reads only a
library CSV and the two files below and writes `global.yaml` and `fr.yaml` here. It never reads
BoQ text, labels or annotations.

| File                       | What it holds                                                                               |
| -------------------------- | ------------------------------------------------------------------------------------------- |
| `term_map.yaml`            | Library terms, each with its other-language terms, abbreviations and standard designations  |
| `glossary_supplement.yaml` | General domain facts the default glossary (`prompts/v1/glossary.yaml`) lacks, in its schema |

## term_map.yaml

Each entry has the keys `term`, `lang`, `equivalents`, `source`, `ref`, in that order.

- **`term`** is a phrase exactly as `data/oris_materials_global.csv` (`lang: en`) or
  `data/oris_materials_fr.csv` (`lang: fr`) writes it in a type, usage or subtype label, kept
  even where the library misspells it (`harderning accelerator`, `Métakaloin flashé`). It must occur
  there as a whole phrase, case-insensitive after the §6 normaliser. A node's `also` list is the
  union of the equivalents of every term found in its label, so the terms are chosen to be specific
  enough not to bleed into unrelated rows: `Mastic Asphalt (MA)`, not `Mastic Asphalt`, which would
  also hit Stone Mastic Asphalt; `- WMA`, which hits the warm-mix mixture subtypes but not the
  `technologies (WMA)` additive rows; `Concrete for piles`, not `piles`, which would also hit
  Sheet piles. Matching ignores the term's language, so a French equivalent that is also an English
  word (`piles`) is left out.
- **Known limit.** A bare type label (`Concrete`, `Water`, `Asphalt`) can only be reached by a term
  that also occurs inside longer labels, so its equivalents reach those rows too (`béton` on
  `Asphalt Concrete (AC)`, `eau` on `Water resisting admixtures`, `enrobés` on `Reclaimed Asphalt`).
  Those terms carry only the plain translation of the word, never a product name such as
  `enrobés bitumineux` or `eau de gâchage`. Scoping a term to an exact label needs a generator change.
- **`equivalents`** are short general phrases in the other language, plus abbreviations and
  standard designations (`Quicklime` → `chaux vive`, `CL 90-Q`; `RAP` → `AE`, `RA`). A few English
  synonyms appear on English terms where the dev evidence showed a same-language gap (`- WMA` →
  `reduced temperature`); those entries are tagged `dev_error`.
- **Coverage.** Every type and usage of both libraries is reached by at least one term, except
  where no standard translation exists. Those were left out rather than guessed: `Custom` rows,
  `Building Components`, `Composite`, concrete strength and exposure classes, `B99`,
  `Hot Rolled Asphalt` (French keeps the English name), `Fascicule 65`, `NF EN 206 CN`,
  `granular surfacing`, `base filling` and `Joint à revêtement amélioré`. Waste codes such as
  `17 02 04` need no translation, and their words get the European Waste Catalogue's French terms.
- **Order.** Entries are sorted by `(lang, normalised term)`, normalised by
  `oris_matcher.domain.normalize.normalize` (NFKC, casefold, collapsed whitespace).

## glossary_supplement.yaml

It uses the same fields as `prompts/v1/glossary.yaml`: `term`, `meaning` (one sentence, in English),
`lang`, `source`, `ref` (required for `standard`) and `false_friend` (unused here). It covers the
gaps the dev cause panel (O18, `lexical_gap`) pointed at, written as general facts:

- EN 459-1 lime classes (CL 90-Q quicklime, CL 90-S hydrated lime);
- CBGM, grave-ciment and GTLH as hydraulically bound mixtures;
- warm-mix markers (tiède, température abaissée, reduced temperature) and WMA additive families;
- RA, AE and fraisats as reclaimed asphalt;
- regards and manholes as precast concrete elements, and cable troughs as concrete cable ducts;
- liernes (walings) on excavation support walls;
- fired clay pavers as clay bricks;
- creosoted timber as hazardous wood waste.

## Provenance tags (§10.8)

| Tag         | Meaning                                                                                                    |
| ----------- | ---------------------------------------------------------------------------------------------------------- |
| `library`   | A translation of the library's own vocabulary, or a correspondence between the two libraries' labels       |
| `standard`  | A designation or fact fixed by a public norm, named in `ref` (EN 459-1, EN 14227-1, EN 13108-x, EN 197-1…) |
| `dev_error` | A general fact added because the dev error evidence showed the gap. It still states general domain terms   |

An equivalent or supplement entry that exists because of the dev evidence is tagged `dev_error`, even
where a norm defines the thing it names, so the G4 exclusion below sees it: the warm-mix family
(`- WMA`, `enrobé tiède`), clay pavers filed under `Brick (clay)`, manholes and regards filed under
`Other prefabricated concrete elements`, cable troughs and walings. `standard` is kept for facts the
cited norm itself fixes, never for where this library files a product.

`dev_obs` and `lockbox_obs` are not used. At the lockbox (G4), precision is also reported
excluding lines that contain a `dev_error` term or equivalent (A68 §6).

## Leakage rules and review

- **Evidence used.** Only the 24 dev rows the cause panel labelled `lexical_gap`, read to find which
  general terms were missing. No lockbox line, no ground-truth file and nothing under `eval/` or
  `input/` was opened while writing these files.
- **No item text.** No term, equivalent or meaning copies BoQ wording. Every term and equivalent
  has at most five words, so it cannot share a word 6-gram with a BoQ line. The supplement's
  meanings were checked against the dev evidence lines, with no shared 5-gram. The A68 §5 CI test
  checks all of it against both full BoQ inputs.
- **Review.** `tests/test_enrichment_content.py` checks the schema, key order, sort order, word
  limits and that every term occurs in a library label. It also checks that the dev gaps are
  bridged and that the two lime classes never cross. The translations themselves were
  hand-reviewed by the builder. The generated files record the reviewer in `reviewed_by`
  (A68 §2), and any change to these sources changes their SHA-256 and so the prompt version (§9.3).
