# G2-T3 B3 live smoke: owner pause #1

Run `20261006T001017Z-354cd333` (ledger id `B3-smoke-5`), EN, first 5 slice items, k = 2, decided at `T1` (fallback_strictest).

## 1. Per item

### 00.01.0030.: decision `needs_review`, reason `LOW_SIGNAL:v+b+confidence`, rule D10

- signals: `{"b": "no_evidence", "confidence_bucket": "<70", "v": 1}`; signal b (attribute_result): `no_evidence`

| pass | kind | top1 | top2 | conf | gap | evidence |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | material | T16.U02.S02 | T16.U02.S04 | 45 | narrow | break out compound hardstanding and reinstate land |
| 2 | material | T16.U02.S03 | T13.U01.S01 | 45 | narrow | break out compound hardstanding and reinstate land |

- suggested row id: `7d35e1ee82f7`; top1 label: T16.U02.S02

### 00.02.0010.: decision `needs_review`, reason `BUDGET_CAP`, rule D1

- signals: `null`; signal b (attribute_result): `None`

| pass | kind | top1 | top2 | conf | gap | evidence |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | non_material |  |  | 75 | clear | Temporary traffic management incl. crossovers, temporary barriers, signing, lighting |

- suggested row id: `None`; top1 label: (blank)

### 00.03.0040.: decision `needs_review`, reason `BUDGET_CAP`, rule D1

- signals: `null`; signal b (attribute_result): `None`

| pass | kind | top1 | top2 | conf | gap | evidence |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | non_material |  |  | 15 | tossup | CSL testing in pre-installed access tubes, 4 tubes per pile |

- suggested row id: `None`; top1 label: (blank)

### 01.01.0010.: decision `needs_review`, reason `BUDGET_CAP`, rule D1

- signals: `null`; signal b (attribute_result): `None`

| pass | kind | top1 | top2 | conf | gap | evidence |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | non_material |  |  | 0 | tossup | Clearance within works boundary, roots grubbed, arisings chipped and removed |

- suggested row id: `None`; top1 label: (blank)

### 01.02.0020.: decision `needs_review`, reason `BUDGET_CAP`, rule D1

- signals: `null`; signal b (attribute_result): `None`

| pass | kind | top1 | top2 | conf | gap | evidence |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | material | T13.U01.S02 | T13.U01.S05 | 85 | clear | Load-bearing fired-clay masonry removed under EWC 17 01 02 |

- suggested row id: `None`; top1 label: (blank)

## 2–3. Calls

| call | attempt | sha (8) | lines | cache_creation | cache_read | in | out | cost | ms | error |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| af1a88d3 | 1 | f230e548 | L4 | 0 | 10066 | 100 | 110 | 0.00166 | 2067 | None |
| 5088ef5e | 1 | f230e548 | L6 | 0 | 10066 | 118 | 99 | 0.00162 | 1792 | None |
| 1c680612 | 1 | f230e548 | L13 | 0 | 10066 | 108 | 97 | 0.00160 | 2443 | None |
| 4c5be07a | 1 | f230e548 | L17 | 0 | 10066 | 123 | 100 | 0.00163 | 1803 | None |
| 144fa867 | 1 | f230e548 | L21 | 0 | 10066 | 131 | 124 | 0.00176 | 2058 | None |
| aa18d1ef | 1 | be08167f | L4 | 10066 | 0 | 100 | 110 | 0.01323 | 2076 | None |

- distinct `system_blocks_sha256`: 2; calls per sha and calls with a cache write: `f230e548` 5 calls, 0 writes; `be08167f` 1 calls, 1 writes
- first attempts: 6; retries or splits: 0

## 4–5. Spend and manifest

- `mode`: `"live"`
- `passes_k`: `2`
- `prompt_version`: `["v1+d5e2bdb9", "v1+a58f410e"]`
- `spend_usd`: `0.0214955`
- `attributed_cost_usd`: `0.021495499999999997`
- `budget_cap_usd`: `0.1`
- `policy_resolution`: `"fallback_strictest"`
- `cache_hits`: `0`
- `code_dirty`: `false`
- `library_rendered_tokens`: `{"by_variant": {"v1+a58f410e": 9553, "v1+d5e2bdb9": 9553}, "cache_eligible": true, "estimated": false, "source": "evidence/doctor_2026-10-06.json", "tokens": 9553}`

