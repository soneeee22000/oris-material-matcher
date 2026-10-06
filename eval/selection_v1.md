# Threshold selection (§10.6)

Rendered from the selection JSON. Mean cost: identical across thresholds (same calls). Review load: needs_review per 100 output lines, N basis (evaluation-protocol.md:87). H₂₆₅: diagnostic, never used for selection.

Selected: **T8**, below the dev bar (loosest qualifying: none).

| threshold | minimum (v, b, confidence) | Σ correct | qualifies |
|---|---|---|---|
| T1 | 2, agree, >=90 | 5 | False |
| T2 | 2, agree, 80-89 | 49 | False |
| T3 | 2, agree, 70-79 | 60 | False |
| T4 | 2, agree, <70 | 60 | False |
| T5 | 2, no_evidence, >=90 | 69 | False |
| T6 | 2, no_evidence, 80-89 | 125 | False |
| T7 | 2, no_evidence, 70-79 | 169 | False |
| T8 | 2, no_evidence, <70 | 185 | False |

Sensitivity rows (never shipped): en alone T8 (below the dev bar), fr alone T8 (below the dev bar)

## EN

| row | matched | correct | P | CP-LB | C₂₅₂ | H₂₆₅ | review /100 | not_a_material | F_NM | mean $/line |
|---|---|---|---|---|---|---|---|---|---|---|
| T1 | 2 | 2 | 1.000 | 0.224 | 0.014 | 1.000 | 77.4 | 6 | 0 | 0.001746 |
| T2 | 29 | 25 | 0.862 | 0.712 | 0.180 | 0.973 | 63.8 | 6 | 0 | 0.001746 |
| T3 | 36 | 29 | 0.806 | 0.666 | 0.209 | 0.953 | 60.3 | 6 | 0 | 0.001746 |
| T4 | 36 | 29 | 0.806 | 0.666 | 0.209 | 0.953 | 60.3 | 6 | 0 | 0.001746 |
| T5 | 43 | 35 | 0.814 | 0.689 | 0.252 | 0.946 | 56.8 | 6 | 0 | 0.001746 |
| T6 | 75 | 64 | 0.853 | 0.769 | 0.460 | 0.926 | 40.7 | 6 | 0 | 0.001746 |
| T7 | 96 | 85 | 0.885 | 0.817 | 0.612 | 0.926 | 30.2 | 6 | 0 | 0.001746 |
| T8 | 101 | 90 | 0.891 | 0.826 | 0.647 | 0.926 | 27.6 | 6 | 0 | 0.001746 |
| match-all | 138 | 105 | 0.761 | 0.694 | 0.755 | 0.777 | 9.0 | 6 | 0 | 0.001746 |
| B2 (R-10.9-evidence) | 139 | 110 | 0.791 | 0.727 | 0.791 | 0.804 | 11.6 | 0 | 0 | 0.000953 |

## FR

| row | matched | correct | P | CP-LB | C₂₅₂ | H₂₆₅ | review /100 | not_a_material | F_NM | mean $/line |
|---|---|---|---|---|---|---|---|---|---|---|
| T1 | 3 | 3 | 1.000 | 0.368 | 0.022 | 1.000 | 76.4 | 7 | 0 | 0.001818 |
| T2 | 30 | 24 | 0.800 | 0.643 | 0.173 | 0.959 | 62.8 | 7 | 0 | 0.001818 |
| T3 | 39 | 31 | 0.795 | 0.660 | 0.223 | 0.946 | 58.3 | 7 | 0 | 0.001818 |
| T4 | 39 | 31 | 0.795 | 0.660 | 0.223 | 0.946 | 58.3 | 7 | 0 | 0.001818 |
| T5 | 42 | 34 | 0.810 | 0.682 | 0.245 | 0.946 | 56.8 | 7 | 0 | 0.001818 |
| T6 | 69 | 61 | 0.884 | 0.801 | 0.439 | 0.946 | 43.2 | 7 | 0 | 0.001818 |
| T7 | 92 | 84 | 0.913 | 0.849 | 0.604 | 0.946 | 31.7 | 7 | 0 | 0.001818 |
| T8 | 104 | 95 | 0.913 | 0.854 | 0.683 | 0.939 | 25.6 | 7 | 0 | 0.001818 |
| match-all | 135 | 114 | 0.844 | 0.784 | 0.820 | 0.858 | 10.1 | 7 | 0 | 0.001818 |
| B2 (R-10.9-evidence) | 139 | 113 | 0.813 | 0.750 | 0.813 | 0.824 | 11.6 | 0 | 0 | 0.000916 |
