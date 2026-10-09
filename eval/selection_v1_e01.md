# Threshold selection (§10.6)

Rendered from the selection JSON. Mean cost: identical across thresholds (same calls). Review load: needs_review per 100 output lines, N basis (evaluation-protocol.md:87). H₂₆₅: diagnostic, never used for selection.

Selected: **T8**, meets the dev bar (loosest qualifying: T8).

| threshold | minimum (v, b, confidence) | Σ correct | qualifies |
|---|---|---|---|
| T1 | 2, agree, >=90 | ≤ 12 | unmeasured |
| T2 | 2, agree, 80-89 | ≤ 60 | unmeasured |
| T3 | 2, agree, 70-79 | ≤ 75 | unmeasured |
| T4 | 2, agree, <70 | ≤ 75 | unmeasured |
| T5 | 2, no_evidence, >=90 | ≤ 90 | unmeasured |
| T6 | 2, no_evidence, 80-89 | ≤ 157 | unmeasured |
| T7 | 2, no_evidence, 70-79 | 187 | True |
| T8 | 2, no_evidence, <70 | 194 | True |

Sensitivity rows (never shipped): en alone T7 (meets the dev bar), fr alone T8 (meets the dev bar)

Unmeasured: T1, T2, T3, T4, T5, T6. unmeasured at $0: no given run answers every verifier request at the threshold; the ceiling is the verifier-off replay of the first run, and none of these could be selected. Sensitivity rows: computed over the thresholds measured in both languages.

D5a false rejects (A60.12): EN 0 (none), FR 1 (01.04.0010.); total 1, §10.9 not re-triggered.

## EN

| row | matched | correct | P | CP-LB | C₂₅₂ | H₂₆₅ | review /100 | not_a_material | F_NM | mean $/line |
|---|---|---|---|---|---|---|---|---|---|---|
| T1 (unmeasured) | ≤ 9 | ≤ 9 |  |  |  |  |  |  |  |  |
| T2 (unmeasured) | ≤ 27 | ≤ 27 |  |  |  |  |  |  |  |  |
| T3 (unmeasured) | ≤ 35 | ≤ 35 |  |  |  |  |  |  |  |  |
| T4 (unmeasured) | ≤ 35 | ≤ 35 |  |  |  |  |  |  |  |  |
| T5 (unmeasured) | ≤ 41 | ≤ 41 |  |  |  |  |  |  |  |  |
| T6 (unmeasured) | ≤ 73 | ≤ 73 |  |  |  |  |  |  |  |  |
| T7 | 93 | 92 | 0.989 | 0.950 | 0.662 | 0.993 | 32.2 | 5 | 0 | 0.002967 |
| T8 | 94 | 92 | 0.979 | 0.935 | 0.662 | 0.986 | 31.7 | 5 | 0 | 0.003000 |
| match-all | 134 | 121 | 0.903 | 0.850 | 0.871 | 0.919 | 11.6 | 5 | 0 | 0.003000 |
| B2 (R-10.9-evidence) | 139 | 110 | 0.791 | 0.727 | 0.791 | 0.804 | 11.6 | 0 | 0 | 0.000953 |

## FR

| row | matched | correct | P | CP-LB | C₂₅₂ | H₂₆₅ | review /100 | not_a_material | F_NM | mean $/line |
|---|---|---|---|---|---|---|---|---|---|---|
| T1 (unmeasured) | ≤ 3 | ≤ 3 |  |  |  |  |  |  |  |  |
| T2 (unmeasured) | ≤ 34 | ≤ 33 |  |  |  |  |  |  |  |  |
| T3 (unmeasured) | ≤ 41 | ≤ 40 |  |  |  |  |  |  |  |  |
| T4 (unmeasured) | ≤ 41 | ≤ 40 |  |  |  |  |  |  |  |  |
| T5 (unmeasured) | ≤ 50 | ≤ 49 |  |  |  |  |  |  |  |  |
| T6 (unmeasured) | ≤ 87 | ≤ 84 |  |  |  |  |  |  |  |  |
| T7 | 96 | 95 | 0.990 | 0.952 | 0.683 | 0.993 | 29.6 | 7 | 0 | 0.002886 |
| T8 | 103 | 102 | 0.990 | 0.955 | 0.734 | 0.993 | 26.1 | 7 | 0 | 0.002897 |
| match-all | 141 | 127 | 0.901 | 0.849 | 0.914 | 0.905 | 7.0 | 7 | 0 | 0.002897 |
| B2 (R-10.9-evidence) | 139 | 113 | 0.813 | 0.750 | 0.813 | 0.824 | 11.6 | 0 | 0 | 0.000916 |
