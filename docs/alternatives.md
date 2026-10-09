# Alternatives: what was measured, what was not, and why

The brief allows GPT-4o-mini, Gemini Flash, Claude Haiku or a local model of 8B parameters or fewer, mixed if wanted, for up to $2 per 100 lines. The shipped system uses one of them, Claude Haiku 4.5, for $0.22 per 100 lines. This page lists each alternative, the evidence that exists for it, and what is missing.

Rows marked _post-freeze_ were added on 9 Oct 2026, after the lockbox was scored. They read dev items only, from runs already recorded, and change no claim.

| Alternative                                           | Status                                            | Result                                                                           |
| ----------------------------------------------------- | ------------------------------------------------- | -------------------------------------------------------------------------------- |
| Rules only (B0)                                       | measured on dev                                   | no matches ([development results](development-results.md))                       |
| TF-IDF (B1)                                           | measured on dev                                   | EN precision .413 (43/104), FR .429 (12/28)                                      |
| GPT-4o-mini as the only model                         | measured on dev (`FB-dev`, [G2 O26](gates/G2.md)) | top-1 right on EN 23/139 and FR 36/139 labelled lines; Haiku 121/139 and 127/139 |
| GPT-4o-mini as a second voter (E-02(d))               | _post-freeze_, recorded votes, dev                | fails the pre-registered rule ([report](../eval/cross_model_dev.md))             |
| Gemini Flash                                          | not run                                           | none                                                                             |
| Multilingual embeddings, as a baseline or a retriever | not run                                           | none                                                                             |
| A trained or fine-tuned classifier                    | not run, by choice                                | none                                                                             |
| A local open-weight model of 8B or fewer (A25)        | pre-registered, not run                           | none                                                                             |

## The model choice was argued first and measured second

D-10 in [DESIGN.md](../DESIGN.md) picked Haiku before any model call, on structured outputs and price. GPT-4o-mini was measured later, as the candidate fallback. It ran at k = 2 on all dev items, on the prompt built with Haiku, without the verifier or the enrichment.

**What it got wrong.** Its errors are model errors, not harness errors: the JSON is valid and the row codes resolve.

- It finds the right material type on 96 of 139 EN lines. It finds the full triple on only 23.
- Most misses pick the wrong subtype inside the right usage. Examples: iron and steel for aluminium sign faces, and non-hazardous cable for oil-filled 110 kV cable.

**What this does not settle.** A prompt tuned for GPT-4o-mini might narrow the gap, and that was not tried.

## Mixing models, and the unused budget

The run spends about a ninth of the budget, and French sends about 30% of lines to review. The obvious question is whether a second model could buy coverage with the rest. The pre-registration already asked it as E-02(d) ("Is a cross-model vote worth it", DESIGN.md §7.2), with this adoption rule: it must add at least 3 summed correct matches at equal precision. The arm never ran before the freeze.

[`eval/cross_model_dev.py`](../eval/cross_model_dev.py) answers it after the freeze, for $0. It reads the recorded GPT-4o-mini votes against the shipped Haiku decisions on the same dev items. Coverage is over 139 labelled dev lines per language.

| Policy                                                      | EN matched (correct) | EN coverage | FR matched (correct) | FR coverage |
| ----------------------------------------------------------- | -------------------- | ----------- | -------------------- | ----------- |
| Shipped, Haiku alone                                        | 94 (92)              | .662        | 103 (102)            | .734        |
| Rescue a review line when either GPT pass names Haiku's row | 94 (92)              | .662        | 107 (104)            | .748        |
| Rescue only when both GPT passes agree                      | 94 (92)              | .662        | 103 (102)            | .734        |
| Keep a Haiku match only when GPT agrees                     | 26 (26)              | .187        | 43 (42)              | .302        |

GPT-4o-mini agrees with Haiku mostly where Haiku is already right. On the lines Haiku sends to review, it names the same row on 0 of 45 in EN and 4 of 36 in FR, and 2 of those 4 are wrong.

**The rule is not met:** two correct matches are added, and French precision falls from .990 to .972. As a veto, the second model would remove most of Haiku's correct matches.

So with this second model, the spare budget cannot buy coverage. The untested route is a stronger second model, such as Gemini Flash, judged by the same rule.

## Approaches without an LLM

- **Rules and TF-IDF.** These were measured as B0 and B1. No TF-IDF operating point is usable: at precision .90 or better it covers 4% of EN lines, and FR never reaches .90.
- **Multilingual embeddings (this is a gap).** A model such as multilingual-e5 or BGE-M3, used for top-1 classification with a threshold, was not measured.
  - French lines matched against an English-labelled library is a cross-language problem. TF-IDF cannot solve it and embeddings are built for it, so B1 understates the simplest reasonable approach in French.
  - The reasons recorded at the time cover retrieval, not a baseline. D-01 shows the whole library to the model, because lexical recall@20 was only .869 EN and .690 FR. The designed `HybridRetriever` (BGE-M3 plus BM25) switches on above 30k rendered tokens, and this library renders to about 13.7k. Its torch dependency, about 2 GB, was kept out of the core install.
  - An embedding baseline should still have been measured.
- **A trained or fine-tuned classifier.** This was not pursued, by choice. The global library has 342 rows and dev has 139 labelled lines per language, fewer than one example per class. Training on lockbox lines would leak the held-out set.

## A local open-weight model (A25)

- **Planned.** A one-off benchmark of a local model of 8B or fewer, on the 40-line dev slice, was pre-registered before any model call (A25).
- **Kept, not run.** A proposal to cut it (A37) was not adopted, and it stayed in the deferred list before the freeze. It did not run.
- **What exists.** The LLM port admits local model ids, but there is no local adapter.
- **Why it matters.** For a client who cannot send BoQ text to an external API, this is the only route, and its accuracy cost is unknown.

## Next, in order

1. An embedding baseline on dev: multilingual top-1 with a threshold chosen by the §10.6 rule. It costs nothing in API calls and gives French a fair floor.
2. Gemini Flash as a second voter, judged by the E-02(d) rule. It costs a few cents on dev.
3. A local adapter for an open-weight model of 8B or fewer, with output constrained to valid row codes, benchmarked on the 40-line slice (A25).
4. Anything adopted from these needs a new held-out set, because the lockbox has already been scored once.
