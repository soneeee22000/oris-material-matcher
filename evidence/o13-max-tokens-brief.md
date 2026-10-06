# O13 decision brief: `max_tokens` = 4096 vs DESIGN §9.4 "p99 × 1.3"

Prepared 2026-10-06 during G2, read-only (no model call). It was spot-checked against the G2 smoke measurements: the prefix estimate is 21,831 chars ÷ 4 ≈ 5.4k tokens against 10,066 billed cache-write tokens, and the largest output is 1,213 tokens. Decided as A63.

## Measured output tokens (505 fresh Haiku calls, G1 + G2; 0 truncations, 0 re-asks)

| lines per batch | n | max | p99 |
| --- | --- | --- | --- |
| 1 | 343 | 138 | 136.0 |
| 2 | 42 | 243 | 241.0 |
| 5 | 18 | 607 | 606.1 |
| 8 | 12 | 1019 | 1011.1 |
| 10 | 42 | 1213 | 1211.4 |

- **Per line:** p99 135, max 138. FR runs slightly longer than EN.
- **Fit:** output ≈ 11.4 + 104 × lines.
- **The two renderings** cannot be told apart, so one rule covers both.
- **Candidate rule:** `ceil(135 × lines × 1.3) + 64` is 240 for 1 line and 1,819 for 10. A single constant of 1,600 also clears every recorded call (1.32× over the maximum, 1,213).

## What a change touches

- **`max_tokens` is in the request hash** (`llm/base.py` `LLMRequest.sha256`) and in the response-cache key.
- **What stops replaying:** every recorded run, which means the golden fixture, the B2 and B3 replays behind `eval/selection_v1.json`, A62/O15 and `runs/submission/*`.
- **Not affected:** `prompt_version` and the prompt files, as long as the sizing stays in `service.py`.
- **Rebuilding live:** about $0.58 (B3 dev EN + FR) to $0.90 (with B2 and the golden fixture), plus a smoke. The selection is then re-derived on new, non-deterministic votes, about two days before `eval-freeze`.

## Hidden dependency

The reservation estimates input as chars ÷ 4. It counts the cached prefix as about 5.3k tokens, but about 10.1k are billed. The 4,096-token output slack (about $0.0205) is what keeps every reservation a true worst case today. Lowering the output reservation alone would reserve less than a cold call costs ($0.0079 against $0.0132 for one line). Options (b) and (c) must therefore also fix the prefix count, using the doctor's measured `count_tokens`. That fix touches the ledger only, not any hash.

## Options

| # | Option | Cost | Breaks | Risk |
| --- | --- | --- | --- | --- |
| a ⭐ | Keep 4096; amend §9.4 to match the code and record why | $0, doc only | nothing | small runs wait; output rate limit not binding (1M/min) |
| b | Size `max_tokens` from measurement before the freeze | ~$0.58–0.90 + re-deriving the selection | every replay, the golden fixture, the cache | truncation unlikely but possible on the harder lockbox (margin ≥ 1.44×); selection moves |
| c | Keep request at 4096; reserve from a measured bound (+ prefix fix) | $0 API; code + §11.3/§9.4 amendment | no hashes; one formula test changes, a spec-driven change | the cap is no longer a hard bound (≤ ~$0.011–0.019 per call in flight) |

**Owner decision (A63):** (a) keep 4096 and amend §9.4; and fix the prefix count in the reservation (ledger only, $0).
