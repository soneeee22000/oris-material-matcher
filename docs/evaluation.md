# Evaluation: the lockbox claim

This is the single post-freeze lockbox session (`DESIGN.md` §10.3), run once at the `eval-freeze` tag (`c8cd80a`) on Thu 8 Oct 2026, 04:36–04:46.

- **What ran:** B2 then B3, on both full files, live, with `--no-cache`.
- **What was scored:** the 120 lockbox items per language, 113 of them labelled. FR is scored against the global library.
- **Afterwards:** nothing was tuned.
- **Evidence:** `eval/lockbox_log.md`, `runs/submission/20261008T02*`, and `output/improved_output_{en,fr}.csv`, which are the B3 outputs.

## The claim (§10.3, A31)

The frozen configuration is Haiku 4.5, k = 2 renderings, threshold `T8`, the E-08 sibling verifier, and the E-01 enrichment.

|                                                     | EN                                 | FR                                 |
| --------------------------------------------------- | ---------------------------------- | ---------------------------------- |
| Matched precision                                   | **98.9% on 89 lines** (88 correct) | **98.8% on 80 lines** (79 correct) |
| One-sided 95% exact (Clopper–Pearson) lower bound   | **.948**                           | **.942**                           |
| Meets the 90% point bar                             | yes                                | yes                                |
| **Certified at 95% confidence** (lower bound ≥ .90) | **yes**                            | **yes**                            |
| Coverage C₂₅₂ (correct / labelled)                  | .779 (88/113)                      | .699 (79/113)                      |
| False `not_a_material` (F_NM)                       | 0                                  | 0                                  |
| Cost per 100 lines; wall clock per routed line      | $0.22; 0.63 s                      | $0.21; 0.68 s                      |

## Like-for-like on the lockbox

| Rung                                           | EN P (correct/matched) | EN CP-LB | EN C₂₅₂  | FR P (correct/matched) | FR CP-LB | FR C₂₅₂  |
| ---------------------------------------------- | ---------------------- | -------- | -------- | ---------------------- | -------- | -------- |
| B2, one Haiku pass, every valid answer matched | .809 (89/110)          | .737     | .788     | .876 (99/113)          | .813     | .876     |
| **B3, shipped**                                | **.989 (88/89)**       | **.948** | **.779** | **.988 (79/80)**       | **.942** | **.699** |

Coverage is compared on the same items, B3 against B2, with McNemar's exact test (§10.4):

- **EN:** 10 items are correct only under B3 and 11 only under B2 (p = 1.0). B3 keeps B2's coverage, and makes 1 wrong match where B2 made 21.
- **FR:** 4 items are correct only under B3 and 24 only under B2 (p = .0002). In French, abstention costs about 18 points of coverage. Those lines go to `needs_review` with a reason, never to a wrong match.

B0 and B1 have no lockbox rows. The session was pre-registered as B2 then B3. Their dev figures are in DESIGN.md §17.

## Robustness

**Lines touched by dev-derived knowledge (§10.8, A68.6).** Some lines contain a term or equivalent tagged `dev_error` in the enrichment's term map or glossary supplement. Without those lines, B3's figures are:

|                               | EN           | FR           |
| ----------------------------- | ------------ | ------------ |
| Precision without those lines | .989 (86/87) | .987 (74/75) |
| Lower bound                   | .947         | .938         |
| Matched lines excluded        | 2            | 5            |

So the claim does not rest on the entries added from dev errors.

**Dev and lockbox agree.** At the same configuration, dev was EN .979 (92/94) and FR .990 (102/103). The lockbox is a same-project, section-held-out holdout (§10.8). It was blind to tuning but not to the exploration done before the split.

## The two wrong matches

| Item             | Line                                                        | Matched                                                   | Reference                                              | Kind                                                                                                      |
| ---------------- | ----------------------------------------------------------- | --------------------------------------------------------- | ------------------------------------------------------ | --------------------------------------------------------------------------------------------------------- |
| EN `06.01.0040.` | Sub-ballast 0/45 from processed track spoil                 | Aggregates / Unbound aggregates for Ballast / Recycled    | Aggregates / Crushed rock for railway / Recycled       | usage confuser (railway aggregates)                                                                       |
| FR `03.01.0020.` | Couche de fondation en GNT recyclée 0/45, 300 mm, bretelles | Aggregates / unbound aggregates for base layer / Recycled | Aggregates / unbound aggregates for subbase / Recycled | usage confuser: _couche de fondation_ is the sub-base, as the glossary states, yet the base-layer row won |

Both are usage confusions inside the right material type, the failure class E-08 targets. Neither was vetoed. That is the known weakness carried into the report: the verifier reduces this error class but does not remove it.

## Cost of the session

| Run                         | Rung  | Spend (USD)                    |
| --------------------------- | ----- | ------------------------------ |
| `20261008T023626Z-ea37e3a1` | B2 EN | 0.2192                         |
| `20261008T023750Z-be2dd33e` | B2 FR | 0.2173                         |
| `20261008T023928Z-56f85fb8` | B3 EN | 0.6861                         |
| `20261008T024244Z-de394c39` | B3 FR | 0.6756                         |
| **Total**                   |       | **1.7982** of the $3.00 G4 cap |

## Provenance note

The manifests of the second, third and fourth runs record `code_dirty: true`. The manifest flag is set when `git status` lists anything, and after the first run the pinned `eval/lockbox_log.md` had been appended to. No tracked file outside `runs/` and that log differed from `eval-freeze` during the session; `git diff --name-only eval-freeze` lists none. The lockbox gate's own clean-tree check exempts both paths and recorded `code_dirty False` in the log. Making the manifest flag exempt the pinned log is a post-freeze fix.

## Reproduce

```bash
uv run oris replay runs/submission/20261008T023928Z-56f85fb8 --check output/improved_output_en.csv
uv run python eval/check_requirements.py --output output/improved_output_en.csv --input input/boq_dataset_input_en.csv --library data/oris_materials_global.csv --run runs/submission/20261008T023928Z-56f85fb8
```

Both are green for EN and FR: STRICT and RQ1–RQ11, with the replay byte-identical.
