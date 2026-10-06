# Threshold selection (§10.6)

Rendered from the selection JSON. Mean cost: identical across thresholds (same calls). Review load: needs_review per 100 output lines, N basis (evaluation-protocol.md:87). H₂₆₅: diagnostic, never used for selection.

Selected: **T8**, meets the dev bar (loosest qualifying: T8).

| threshold | minimum (v, b, confidence) | Σ correct | qualifies |
|---|---|---|---|
| T1 | 2, agree, >=90 | ≤ 5 | unmeasured |
| T2 | 2, agree, 80-89 | ≤ 49 | unmeasured |
| T3 | 2, agree, 70-79 | ≤ 60 | unmeasured |
| T4 | 2, agree, <70 | ≤ 60 | unmeasured |
| T5 | 2, no_evidence, >=90 | ≤ 69 | unmeasured |
| T6 | 2, no_evidence, 80-89 | ≤ 125 | unmeasured |
| T7 | 2, no_evidence, 70-79 | 153 | True |
| T8 | 2, no_evidence, <70 | 163 | True |

Sensitivity rows (never shipped): en alone T8 (meets the dev bar), fr alone T8 (meets the dev bar)

Unmeasured: T1, T2, T3, T4, T5, T6. unmeasured at $0: no given run answers every verifier request at the threshold; the ceiling is the verifier-off replay of the first run, and none of these could be selected. Sensitivity rows: computed over the thresholds measured in both languages.

D5a false rejects (A60.12): EN 1 (03.02.0060.), FR 0 (none); total 1, §10.9 not re-triggered.

## EN

| row | matched | correct | P | CP-LB | C₂₅₂ | H₂₆₅ | review /100 | not_a_material | F_NM | mean $/line |
|---|---|---|---|---|---|---|---|---|---|---|
| T1 (unmeasured) | ≤ 2 | ≤ 2 |  |  |  |  |  |  |  |  |
| T2 (unmeasured) | ≤ 29 | ≤ 25 |  |  |  |  |  |  |  |  |
| T3 (unmeasured) | ≤ 36 | ≤ 29 |  |  |  |  |  |  |  |  |
| T4 (unmeasured) | ≤ 36 | ≤ 29 |  |  |  |  |  |  |  |  |
| T5 (unmeasured) | ≤ 43 | ≤ 35 |  |  |  |  |  |  |  |  |
| T6 (unmeasured) | ≤ 75 | ≤ 64 |  |  |  |  |  |  |  |  |
| T7 | 78 | 77 | 0.987 | 0.941 | 0.554 | 0.993 | 39.2 | 6 | 0 | 0.002198 |
| T8 | 83 | 82 | 0.988 | 0.944 | 0.590 | 0.993 | 36.7 | 6 | 0 | 0.002215 |
| match-all | 138 | 105 | 0.761 | 0.694 | 0.755 | 0.777 | 9.0 | 6 | 0 | 0.002215 |
| B2 (R-10.9-evidence) | 139 | 110 | 0.791 | 0.727 | 0.791 | 0.804 | 11.6 | 0 | 0 | 0.000953 |

## FR

| row | matched | correct | P | CP-LB | C₂₅₂ | H₂₆₅ | review /100 | not_a_material | F_NM | mean $/line |
|---|---|---|---|---|---|---|---|---|---|---|
| T1 (unmeasured) | ≤ 3 | ≤ 3 |  |  |  |  |  |  |  |  |
| T2 (unmeasured) | ≤ 30 | ≤ 24 |  |  |  |  |  |  |  |  |
| T3 (unmeasured) | ≤ 39 | ≤ 31 |  |  |  |  |  |  |  |  |
| T4 (unmeasured) | ≤ 39 | ≤ 31 |  |  |  |  |  |  |  |  |
| T5 (unmeasured) | ≤ 42 | ≤ 34 |  |  |  |  |  |  |  |  |
| T6 (unmeasured) | ≤ 69 | ≤ 61 |  |  |  |  |  |  |  |  |
| T7 | 77 | 76 | 0.987 | 0.940 | 0.547 | 0.993 | 39.2 | 7 | 0 | 0.002276 |
| T8 | 82 | 81 | 0.988 | 0.943 | 0.583 | 0.993 | 36.7 | 7 | 0 | 0.002310 |
| match-all | 135 | 114 | 0.844 | 0.784 | 0.820 | 0.858 | 10.1 | 7 | 0 | 0.002310 |
| B2 (R-10.9-evidence) | 139 | 113 | 0.813 | 0.750 | 0.813 | 0.824 | 11.6 | 0 | 0 | 0.000916 |
