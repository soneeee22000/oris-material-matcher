# Changelog

Each release is an annotated git tag. The lockbox claim belongs to `v1.0`, evaluated at `eval-freeze`. Later releases leave it unchanged.

## v1.3.0 (2026-10-09)

Docs and project page only. No code, configuration or number changed.

- `site/`: a static project page built from committed output by `scripts/export_site_data.py`, with a CI drift check (`--check`) that fails when the page data no longer matches the committed files.
- README rewritten in portfolio shape; the development-results ladder and the other long tables it dropped moved verbatim to `docs/development-results.md`.
- Adds `docs/WHY.md`: the problem, the layer-by-layer answer, and what this project is not.
- `DESIGN.md`: a dated post-submission orientation note under the title, outside the pre-registered text.

## v1.2.1 (2026-10-09)

Docs only. No code, configuration or number changed.

- `docs/gates/G6.md` is closed, with the release record and a fresh-clone check of `v1.2.0`.
- Adds this changelog.

## v1.2.0 (2026-10-09)

- The FR library is certified for the pinned model by the A10 smoke rule (A70). It runs `T8` with the sibling verifier and `data/enrichment/fr.yaml`, and the brief's FR command now prints `policy_resolution: exact (policy T8)`. The smoke set is 68 builder-labelled lines frozen before one live run; the verdict was a pass, with 0 closed-world violations, 0 false `not_a_material`, 0 decoy matches and 13 of 18 base positives correct. It is readiness evidence, not a held-out claim (`docs/gates/G6.md`).
- `eval/smoke_a10.py`: the A10 scorer. It refuses unlabelled or duplicate lines, a label set without 18 base positives, and a run whose configuration differs from the frozen one.
- Fix: the replay guard now compares the policy of the model that decided, so a run the fallback rescued replays.
- `docs/evaluation.md` reports both full output files: per-level accuracy, decision shares, cost and latency.

## v1.1.0 (2026-10-08)

G5 release: `oris explain` and `oris demo`, the four traced cases in `docs/traced-cases.md`, and the final README. The lockbox claim is unchanged.

## v1.0 (2026-10-08)

G4, the lockbox claim, scored once at `eval-freeze`:

- B3 matched precision EN .989 (88/89, one-sided 95% lower bound .948), FR .988 (79/80, .942).
- Certified at the 90% bar in both languages.
- F_NM 0.

## eval-freeze (2026-10-08)

The configuration the lockbox scores (DESIGN.md §10.3), after the G3 readiness set.

## v0.2 (2026-10-08)

G2 selection: the threshold, the sibling verifier and the enrichment were chosen on dev items only. Provisional and pre-freeze.

## v0.1 (2026-10-05)

G1: the first submittable service, with the CLI, the API, the validators and the audit trail. Provisional and pre-freeze.

## prereg-v1 (2026-10-04)

Pre-registration: the design, the evaluation protocol and the frozen split, committed before any model call.
