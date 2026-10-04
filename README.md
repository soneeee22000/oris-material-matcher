# ORIS material matcher

This service maps each line of a Bill of Quantities to an exact `type / usage / subtype` row of an ORIS material library, or decides `not_a_material` or `needs_review`. It reports the evidence for its precision, coverage, cost and latency.

> **Status: design pre-registered, implementation in progress.** Start with [`DESIGN.md`](DESIGN.md).

| Document | Purpose |
|---|---|
| [`DESIGN.md`](DESIGN.md) | Approach, decision register, what is tried first, what is cut, and the evaluation and engineering contracts |
| [`docs/evaluation-protocol.md`](docs/evaluation-protocol.md) | Full, pre-registered evaluation protocol |
| [`docs/data-analysis.md`](docs/data-analysis.md) | Verified data findings |
| [`docs/requirements-traceability.md`](docs/requirements-traceability.md) | Every requirement in the brief, mapped to its evidence |
| [`docs/ui-spec.md`](docs/ui-spec.md) | Operator UI specification |
| [`docs/exercise-brief.md`](docs/exercise-brief.md) | The exercise brief as received |

How to run it, the results, the known weaknesses and what I would do with more time will be added here as the implementation lands.
