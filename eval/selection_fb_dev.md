# Threshold selection (§10.6)

Rendered from the selection JSON. Mean cost: identical across thresholds (same calls). Review load: needs_review per 100 output lines, N basis (evaluation-protocol.md:87). H₂₆₅: diagnostic, never used for selection.

Selected: **T5**, below the dev bar (loosest qualifying: none).

| threshold | minimum (v, b, confidence) | Σ correct | qualifies |
|---|---|---|---|
| T1 | 2, agree, >=90 | 1 | False |
| T2 | 2, agree, 80-89 | 1 | False |
| T3 | 2, agree, 70-79 | 1 | False |
| T4 | 2, agree, <70 | 1 | False |
| T5 | 2, no_evidence, >=90 | 23 | False |
| T6 | 2, no_evidence, 80-89 | 23 | False |
| T7 | 2, no_evidence, 70-79 | 23 | False |
| T8 | 2, no_evidence, <70 | 23 | False |

Sensitivity rows (never shipped): en alone T5 (below the dev bar), fr alone T5 (below the dev bar)

D5a false rejects (A60.12): EN 0 (none), FR 0 (none); total 0, §10.9 not re-triggered.

## EN

| row | matched | correct | P | CP-LB | C₂₅₂ | H₂₆₅ | review /100 | not_a_material | F_NM | mean $/line |
|---|---|---|---|---|---|---|---|---|---|---|
| T1 | 0 | 0 | n/a | 0.000 | 0.000 | 1.000 | 78.9 | 5 | 0 | 0.000403 |
| T2 | 0 | 0 | n/a | 0.000 | 0.000 | 1.000 | 78.9 | 5 | 0 | 0.000403 |
| T3 | 0 | 0 | n/a | 0.000 | 0.000 | 1.000 | 78.9 | 5 | 0 | 0.000403 |
| T4 | 0 | 0 | n/a | 0.000 | 0.000 | 1.000 | 78.9 | 5 | 0 | 0.000403 |
| T5 | 14 | 10 | 0.714 | 0.460 | 0.072 | 0.973 | 71.9 | 5 | 0 | 0.000403 |
| T6 | 14 | 10 | 0.714 | 0.460 | 0.072 | 0.973 | 71.9 | 5 | 0 | 0.000403 |
| T7 | 15 | 10 | 0.667 | 0.423 | 0.072 | 0.966 | 71.4 | 5 | 0 | 0.000403 |
| T8 | 15 | 10 | 0.667 | 0.423 | 0.072 | 0.966 | 71.4 | 5 | 0 | 0.000403 |
| match-all | 117 | 23 | 0.197 | 0.138 | 0.165 | 0.385 | 20.1 | 5 | 0 | 0.000403 |
| B2 (R-10.9-evidence) | 139 | 110 | 0.791 | 0.727 | 0.791 | 0.804 | 11.6 | 0 | 0 | 0.000953 |

## FR

| row | matched | correct | P | CP-LB | C₂₅₂ | H₂₆₅ | review /100 | not_a_material | F_NM | mean $/line |
|---|---|---|---|---|---|---|---|---|---|---|
| T1 | 1 | 1 | 1.000 | 0.050 | 0.007 | 1.000 | 77.9 | 6 | 0 | 0.000410 |
| T2 | 1 | 1 | 1.000 | 0.050 | 0.007 | 1.000 | 77.9 | 6 | 0 | 0.000410 |
| T3 | 1 | 1 | 1.000 | 0.050 | 0.007 | 1.000 | 77.9 | 6 | 0 | 0.000410 |
| T4 | 1 | 1 | 1.000 | 0.050 | 0.007 | 1.000 | 77.9 | 6 | 0 | 0.000410 |
| T5 | 21 | 13 | 0.619 | 0.417 | 0.094 | 0.946 | 67.8 | 6 | 0 | 0.000410 |
| T6 | 21 | 13 | 0.619 | 0.417 | 0.094 | 0.946 | 67.8 | 6 | 0 | 0.000410 |
| T7 | 21 | 13 | 0.619 | 0.417 | 0.094 | 0.946 | 67.8 | 6 | 0 | 0.000410 |
| T8 | 21 | 13 | 0.619 | 0.417 | 0.094 | 0.946 | 67.8 | 6 | 0 | 0.000410 |
| match-all | 141 | 36 | 0.255 | 0.196 | 0.259 | 0.311 | 7.5 | 6 | 0 | 0.000410 |
| B2 (R-10.9-evidence) | 139 | 113 | 0.813 | 0.750 | 0.813 | 0.824 | 11.6 | 0 | 0 | 0.000916 |
