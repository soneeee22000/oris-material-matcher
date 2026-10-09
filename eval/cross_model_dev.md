# Cross-model agreement on dev (post-freeze analysis)

Rendered by `eval/cross_model_dev.py` from `eval/cross_model_dev_votes.json`. Dev items only, after the lockbox was scored: no claim changes. The second model ran the B3 profile on the primary's prompt, without the verifier or the enrichment (G2 O26).

Policies: `rescue_any`/`rescue_both` turn a primary `needs_review` line into a match when one/both second-model passes name the primary's top-1 row; `veto_any` keeps a primary match only when at least one second-model pass agrees.

## EN

| role | model | mode | run | threshold | enrichment | attributed cost (USD) |
|---|---|---|---|---|---|---|
| primary | claude-haiku-4-5-20251001 | replay | `20261007T221351Z-fe10b4d3` | T8 | 057f062221ae | 0.4860 |
| second | gpt-4o-mini-2024-07-18 | live | `20261007T225635Z-36f5a2c5` | T1 | none | 0.0652 |

Top-1 suggestion correct over 139 labelled dev lines: primary 121/139, second 23/139 (material type alone: 96/139).

| policy | matched | correct | precision | CP-LB | coverage |
|---|---|---|---|---|---|
| shipped | 94 | 92 | 0.979 | 0.935 | 0.662 |
| rescue_any | 94 | 92 | 0.979 | 0.935 | 0.662 |
| rescue_both | 94 | 92 | 0.979 | 0.935 | 0.662 |
| veto_any | 26 | 26 | 1.000 | 0.891 | 0.187 |

| primary decision | second-model passes agreeing | primary correct | lines |
|---|---|---|---|
| matched | 0 | False | 2 |
| matched | 0 | True | 66 |
| matched | 1 | True | 18 |
| matched | 2 | True | 8 |
| needs_review | 0 | False | 16 |
| needs_review | 0 | True | 29 |

## FR

| role | model | mode | run | threshold | enrichment | attributed cost (USD) |
|---|---|---|---|---|---|---|
| primary | claude-haiku-4-5-20251001 | replay | `20261007T221357Z-7a732f90` | T8 | 057f062221ae | 0.4694 |
| second | gpt-4o-mini-2024-07-18 | live | `20261007T225805Z-b0fedde0` | T1 | none | 0.0664 |

Top-1 suggestion correct over 139 labelled dev lines: primary 127/139, second 36/139 (material type alone: 110/139).

| policy | matched | correct | precision | CP-LB | coverage |
|---|---|---|---|---|---|
| shipped | 103 | 102 | 0.990 | 0.955 | 0.734 |
| rescue_any | 107 | 104 | 0.972 | 0.929 | 0.748 |
| rescue_both | 103 | 102 | 0.990 | 0.955 | 0.734 |
| veto_any | 43 | 42 | 0.977 | 0.894 | 0.302 |

| primary decision | second-model passes agreeing | primary correct | lines |
|---|---|---|---|
| matched | 0 | True | 60 |
| matched | 1 | False | 1 |
| matched | 1 | True | 30 |
| matched | 2 | True | 12 |
| needs_review | 0 | False | 9 |
| needs_review | 0 | True | 23 |
| needs_review | 1 | False | 2 |
| needs_review | 1 | True | 2 |

## The pre-registered E-02(d) rule

DESIGN.md §7.2: a cross-model vote is adopted only if it adds at least 3 summed correct matches at equal precision.

| policy | correct matches added (EN + FR) | precision not lower | adopted |
|---|---|---|---|
| rescue_any | 2 | False | False |
| rescue_both | 0 | True | False |
