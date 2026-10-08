# Four traced cases

Each case is the output of `uv run oris explain --run <run> --item <Item No.>` on a committed run. The command reads only the run folder: no model call, no ground truth. DESIGN.md §12 asks for these four cases to be rehearsed before the live session: a correct match, a plausible wrong match, an abstention, and an API failure that ends in review. The committed runs hold no provider error, so case 4 is the nearest real failure: a call refused by the budget reservation before it reached the API. A provider error takes the same path (D1) and is covered by a test. The reference rows quoted below come from `data/boq_dataset_matched_GT.csv`, which `explain` never reads.

## 1. A correct match (EN lockbox, `03.02.0040.`)

```
item 03.02.0040. (line 520890550505c334, position 92, transport L92) in run 20261008T023928Z-56f85fb8 [mode live, library global]
input: 'No-fines drainage base under carriageway in cutting' | unit m³ | qty 1450 | kind item
long: 'Open-textured no-fines concrete, void ratio ≥ 15 %, placed without vibration.'
section path: 3 Pavement > 03.02. Bound and recycled base layers
decision: matched, reason SIGNAL:T8, rule D9 (score s is at or above the frozen threshold)
policy T8 (exact, certified by dev_selection), profile b3, k 2; threshold T8: s >= (v 2, b no_evidence, confidence <70), compared lexicographically
signals: v 2, b no_evidence, confidence <70; attributes no_evidence; flags none
pass 1: kind material, top1 T12.U22.S00, top2 T12.U17.S00, confidence 68, evidence 'Open-textured no-fines concrete, void ratio ≥ 15 %, placed without vibration'
pass 2: kind material, top1 T12.U22.S00, top2 T12.U17.S00, confidence 68, evidence 'Open-textured no-fines concrete void ratio drainage base'
verifier: flagged, so asked (the line would match without it); its top1 T12.U22.S00 agrees with top1 T12.U22.S00, evidence 'Open-textured no-fines concrete, void ratio ≥ 15 %'
matched row: T12.U22.S00 Concrete / Porous concrete for the base layer / none
suggestion 1: T12.U22.S00 Concrete / Porous concrete for the base layer / none
suggestion 2: T12.U17.S00 Concrete / concrete mixture for base course / none
attributed cost $0.002212, latency 2216 ms, model claude-haiku-4-5-20251001
call 61269b6f68ff16df (main, first, attempt 1): HTTP 200; request sha256 8b3c9b24f5ff9c39aee57792a27bad71cfeb49f74cd22c6473d465666dbdd539, system prompt prompts/5fa37bb3a7606b0f06af1e1d76f24509ee88191f05e0c7af37acb4e237fa939a.txt, provider request id req_011Cfozjnez7xGD9zxHQgaUg
call 3c2bf31939b8242e (main, first, attempt 1): HTTP 200; request sha256 861277219aac618038fd1f7da5bb760b18e18560b22ff393cf6eaf8e62245e80, system prompt prompts/9808d9428f95eddb0f06c75d2775a8f6d71beb570a0ec9cc962450481b39ea9d.txt, provider request id req_011CfozpijELfy8VRZDpYbtC
call dd3c4d3965964dc7 (verifier, first, attempt 1): HTTP 200; request sha256 3ce083046f29e2ad50d27d996807435195c24b025b37ff0395b0fb4f1f48b6fb, system prompt prompts/d4612f363c84e75a4418dd74523f30678f8d4176a7e1e416a6d005950db21549.txt, provider request id req_011CfozuYCrwEBtai6xCHNsM
user message and raw response of each call: --full, --json or calls.jsonl
```

- **Reference:** Concrete / Porous concrete for the base layer. The match is exact.
- **Why it matched:** both renderings agreed on the same row, with evidence quoted from the line, so v = 2. The verifier, asked about the siblings of that usage, agreed.
- **The trade-off it shows:** confidence was 68, the lowest bucket. `T8` accepts it because the two renderings agree with evidence, and that is the operating point the dev selection chose.

## 2. A plausible wrong match (FR lockbox, `03.01.0020.`)

```
item 03.01.0020. (line 3be715cf5fe899e9, position 78, transport L78) in run 20261008T024244Z-de394c39 [mode live, library global]
input: 'Couche de fondation en GNT recyclée 0/45, 300 mm, bretelles' | unit m³ | qty 3400 | kind item
long: 'Granulats de béton recyclé 0/45, bretelles B1–B4.'
section path: 3 Chaussées > 03.01. Couches non liées
decision: matched, reason SIGNAL:T8, rule D9 (score s is at or above the frozen threshold)
policy T8 (exact, certified by dev_selection), profile b3, k 2; threshold T8: s >= (v 2, b no_evidence, confidence <70), compared lexicographically
signals: v 2, b no_evidence, confidence 80-89; attributes no_evidence; flags none
pass 1: kind material, top1 T03.U11.S02, top2 T03.U15.S02, confidence 80, evidence 'Granulats de béton recyclé 0/45, bretelles B1–B4'
pass 2: kind material, top1 T03.U11.S02, top2 T03.U15.S02, confidence 80, evidence 'Granulats de béton recyclé 0/45, bretelles B1–B4'
verifier: flagged, so asked (the line would match without it); its top1 T03.U11.S02 agrees with top1 T03.U11.S02, evidence 'Granulats béton recyclé, couche fondation'
matched row: T03.U11.S02 Aggregates / unbound aggregates for base layer / Recycled Aggregates
suggestion 1: T03.U11.S02 Aggregates / unbound aggregates for base layer / Recycled Aggregates
suggestion 2: T03.U15.S02 Aggregates / unbound aggregates for subbase / Recycled Aggregates
attributed cost $0.002218, latency 2253 ms, model claude-haiku-4-5-20251001
call 26ac5b333f82f677 (main, first, attempt 1): HTTP 200; request sha256 dd3d499ee842a0675916dc3d8421b663289fa13a13fba86a1e023d1a63099a29, system prompt prompts/5fa37bb3a7606b0f06af1e1d76f24509ee88191f05e0c7af37acb4e237fa939a.txt, provider request id req_011CfozyzGdBeJEFT3rudAbE
call ba727f8a15eb0348 (main, first, attempt 1): HTTP 200; request sha256 3c44390ce0ac1d9bc1de57ddd8ff515ba732d304f609736f00ea40a2450feeb4, system prompt prompts/9808d9428f95eddb0f06c75d2775a8f6d71beb570a0ec9cc962450481b39ea9d.txt, provider request id req_011Cfp15DP42idBWogf2FxMQ
call bad60571807c856e (verifier, first, attempt 1): HTTP 200; request sha256 94bfd73676eed817f93b96b385199fd6a154f63ba8e10adaee0b8aa3786ae161, system prompt prompts/d4612f363c84e75a4418dd74523f30678f8d4176a7e1e416a6d005950db21549.txt, provider request id req_011Cfp19tXNpNuVXzdZVzVZw
user message and raw response of each call: --full, --json or calls.jsonl
```

- **Reference:** Aggregates / unbound aggregates for subbase / Recycled Aggregates. The match is wrong.
- **Why it went wrong:** both passes quoted the long description, recycled concrete aggregate 0/45, and picked the base-layer row, with the sub-base row as their second choice. *Couche de fondation* in the short description means the sub-base, and the glossary says so, but it did not decide the answer.
- **Why the verifier did not stop it:** the verifier sees only the line and the sibling usages, and it agreed.
- **What it shows:** a usage confusion inside the right material type. It is the error class that remains (`docs/evaluation.md`).

## 3. An abstention (EN lockbox, `01.03.0010.`)

```
item 01.03.0010. (line 38a40613ce8928c8, position 39, transport L39) in run 20261008T023928Z-56f85fb8 [mode live, library global]
input: 'Bulk dig in cutting, soil classes 3–5, surplus off site' | unit m³ | qty 42000 | kind item
long: "Excavation in cutting CH 2+300–3+100, load and haul surplus to Contractor's deposit ≤ 15 km."
section path: 1 Site clearance, demolition and earthworks > 01.03. Excavation
decision: needs_review, reason LOW_SIGNAL:v, rule D10 (anything else, including a vote tie: s is below the threshold)
policy T8 (exact, certified by dev_selection), profile b3, k 2; threshold T8: s >= (v 2, b no_evidence, confidence <70), compared lexicographically
signals: v 1, b no_evidence, confidence 80-89; attributes no_evidence; flags none
pass 1: kind material, top1 T16.U02.S01, top2 T16.U01.S03, confidence 85, evidence "Excavation in cutting, load and haul surplus to Contractor's deposit"
pass 2: kind material, top1 T16.U01.S03, top2 T13.U05.S02, confidence 75, evidence 'Excavation in cutting CH 2+300–3+100, load and haul surplus'
verifier: not asked (the line would not match without it)
matched row: none
suggestion 1: T16.U02.S01 Excavations and Rock Cutting / For use as aggregates / Average quality rock from rock cutting
suggestion 2: T16.U01.S03 Excavations and Rock Cutting / For landfill / Soil Excavation
attributed cost $0.001720, latency 1995 ms, model claude-haiku-4-5-20251001
call 0a6cae8e97f529c2 (main, first, attempt 1): HTTP 200; request sha256 b26b4496c96daea37e3b5e667e95740bf96c6a7a22fe6f11e869b3ece9f49ad7, system prompt prompts/5fa37bb3a7606b0f06af1e1d76f24509ee88191f05e0c7af37acb4e237fa939a.txt, provider request id req_011CfoziquRLdKvsnW3iTok2
call d46a404524d84b3f (main, first, attempt 1): HTTP 200; request sha256 6623b5070e494788acd84704afe2f4586f88600a6b0ffe0a6968091cc162c405, system prompt prompts/9808d9428f95eddb0f06c75d2775a8f6d71beb570a0ec9cc962450481b39ea9d.txt, provider request id req_011Cfozon297Zm5XGDcHU64v
user message and raw response of each call: --full, --json or calls.jsonl
```

- **Reference:** Excavations and Rock Cutting / For landfill / Soil Excavation.
- **Why it abstained:** the two renderings disagreed. Pass 1 chose rock for aggregates; pass 2 chose soil for landfill, the right row. With v = 1, the line goes to `needs_review` as `LOW_SIGNAL:v`.
- **What the reviewer gets:** the right row is suggestion 2, so the engineer confirms it instead of searching the library. This is the abstention the design wants: no wrong match is written.

## 4. A failed call that ends in review (G2 smoke, `01.01.0010.`)

```
item 01.01.0010. (line e42c6958477c94c8, position 17, transport L17) in run 20261006T001017Z-354cd333 [mode live, library global]
input: 'Clear vegetation, grub roots, trees ≤ 300 mm girth' | unit m² | qty 18500 | kind item
long: 'Clearance within works boundary CH 0+000–4+800, roots grubbed, arisings chipped and removed.'
section path: 1 Site clearance, demolition and earthworks > 01.01. Site clearance
decision: needs_review, reason BUDGET_CAP, rule D1 (no valid answer within the line's budget, a duplicate conflict or a replay miss)
policy T1 (fallback_strictest, certified by none), profile b3, k 2; threshold T1: s >= (v 2, b agree, confidence >=90), compared lexicographically
signals: none; attributes none; flags none
pass 1: kind non_material, top1 none, top2 none, confidence 0, evidence 'Clearance within works boundary, roots grubbed, arisings chipped and removed'
matched row: none
attributed cost $0.001630, latency 1803 ms, model claude-haiku-4-5-20251001
call 4c5be07a38839bcf (main, first, attempt 1): HTTP 200; request sha256 174158bde54b812228522ef1af0c9f11eee0eb4cab3dfed3a534c7fe312abf98, system prompt prompts/f230e54898968545f1e279c2ed3aeebace6c79d7f53abc9dc8f58e3f27cf8505.txt, provider request id req_011Cfk1iYUmFDYGBrEgyk4BL
user message and raw response of each call: --full, --json or calls.jsonl
```

- **What happened:** this is the G2-T3 smoke run (`docs/gates/G2.md` O12), with a $0.10 cap. Pass 1's call returned (HTTP 200). Pass 2's request was declined by the budget reservation before it reached the API, and the run manifest lists it under `declined_attempts`, reason `BUDGET_CAP`.
- **Outcome:** with one pass missing, rule D1 fails the line closed. It goes to `needs_review` with reason `BUDGET_CAP`, never to a guessed match.
- **The fix it led to:** the reservation logic now waits for in-flight calls instead of refusing (A61). The re-run was a GO.
- **Reference:** blank. The line is vegetation clearance, a service, so the case is about the failure path only.
- **Another failure:** a provider error (for example HTTP 500 after its retries) ends the same way, as `LLM_FAILURE:api_error` under D1. `oris explain` then prints each attempt's status. A test covers this (`tests/test_explain_demo.py`).
