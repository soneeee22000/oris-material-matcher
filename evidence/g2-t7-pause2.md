# G2 pause #2: owner review of the cause panel

Prepared 2026-10-06. Source: `eval/annotations/errors_dev_*`, `eval/errors_dev.csv`. Panel: labeller A ∥ labeller B, blind, then an adjudicator (A59.2). Agreement on the primary cause: raw .892, Cohen κ .844 pooled (EN .826, n 49; FR .862, n 44).

**What you review:** the 10 disagreements (section 1) and the 18 gt_suspect rulings (section 2). Then reply with your decision. Triggers (section 3) are computed only from the causes as merged here; if you change a cause, the triggers are recomputed.

## 1. Disagreements (10)

### 01.02.0010. (en): false_match, first wrong level `type`

- **Line:** Break out existing carriageway slabs, 240 mm, to recycler — Break out unreinforced JPCP slabs of existing northbound carriageway, load and haul to licensed recycling facility; weighbridge tickets required.
- **GT:** Construction and demolition material / 17 01 - Concrete, bricks, tiles and ceramics / 17 01 01 - Concrete
- **Proposed:** Excavations and Rock Cutting | For use as aggregates | Concrete arisings · rule D9 · reason `SIGNAL:T8` · v 2
- **Labeller A:** `gt_convention` + usage_confuser — Line says slabs hauled 'to recycler', which supports Concrete arisings for aggregates; GT 17 01 01 (suspect) follows the demolition-to-EWC convention.
- **Labeller B:** `header_context` + usage_confuser — Section 'Demolition, dismantling and waste' points to EWC 17 01 01 but both passes chose 'Concrete arisings for use as aggregates', with GT row only as top2.
- **Adjudicated:** `header_context` + usage_confuser — B wins. A's gt_convention needs a GT that rests on a project convention the text does not state. Here the waste header and the haul-to-recycler wording both support classifying broken-out concrete as C&D waste under EWC, and that is the general domain reading. Nothing in the line says the arisings are reused as aggregate on this project. The passes ignored the waste context (header_context) and slid to the confusable arisings-for-aggregate row (usage_confuser as cause_2).

### 01.02.0140. (fr): wrong_proposal_reviewed, first wrong level `usage`

- **Line:** Enlèvement de poteaux et traverses créosotés, passage agricole — Bois imprégné de créosote stocké en bennes couvertes et éliminé comme déchet dangereux.
- **GT:** Construction and demolition material / 17 02 - Wood, glass and plastic / 17 02 04 - Glass, plastic and wood containing or contaminated with hazardous substances
- **Proposed:** Construction and demolition material | 17 09 - Other construction and demolition wastes | 17 09 03 - Other construction and demolition wastes (including mixed wastes) containing hazardous substances · rule D4 · reason `NO_LIBRARY_EQUIVALENT` · v 1
- **Labeller A:** `lexical_gap` · element_right_usage_wrong — 'Bois créosoté' was mapped to 17 09 03 mixed hazardous waste (P1) or no_equivalent (P2) instead of 17 02 04 wood.
- **Labeller B:** `usage_confuser` + lexical_gap · element_right_usage_wrong — P1 identified creosoted timber waste but chose the catch-all 17 09 03 over 17 02 04 hazardous wood; P2 said no_equivalent.
- **Adjudicated:** `usage_confuser` + lexical_gap · element_right_usage_wrong — B wins. P1's element shows 'créosoté' was bridged correctly, so the term was not lost and lexical_gap cannot be primary. The error is choosing the generic hazardous C&D catch-all over the material-specific hazardous-wood code, which is a confusable sibling pair. A's evidence also misreads 17 02 04 as plain wood. lexical_gap is kept as cause_2 for P2's no_equivalent.

### 01.02.0150. (en): missed, first wrong level `decision`

- **Line:** Remove glazing units of toll booths — Frames separated, glass collected in dedicated container.
- **GT:** Construction and demolition material / 17 02 - Wood, glass and plastic / 17 02 02 - Glass
- **Proposed:** Construction and demolition material | 17 02 - Wood, glass and plastic | 17 02 02 - Glass · rule D4 · reason `NO_LIBRARY_EQUIVALENT` · v 1
- **Labeller A:** `header_context` — P2 called glass no_equivalent and missed that the demolition/waste section maps it to EWC 17 02 02, which P1 found.
- **Labeller B:** `llm_failure` — Missed row: P1 was correct (17 02 02 Glass) but P2 returned no_equivalent with no row, which broke agreement v.
- **Adjudicated:** `header_context` — A wins. Under A60.15, llm_failure is reserved for D1/D1b-type failures (timeout, malformed output, missing ID, truncation). P2's output was well-formed, with element, family and confidence present. It made a semantic no_equivalent call, so B's llm_failure does not fit the definition. The best fit for why v failed is that P2 read 'remove glazing units' as a product rather than as waste under the demolition/waste section.

### 01.02.0150. (fr): missed, first wrong level `decision`

- **Line:** Dépose des vitrages des gares de péage — Menuiseries séparées, verre collecté en benne dédiée.
- **GT:** Construction and demolition material / 17 02 - Wood, glass and plastic / 17 02 02 - Glass
- **Proposed:** Construction and demolition material | 17 02 - Wood, glass and plastic | 17 02 02 - Glass · rule D4 · reason `NO_LIBRARY_EQUIVALENT` · v 1
- **Labeller A:** `header_context` — P2 gave no_equivalent for window glass, missing the demolition/waste section that maps it to EWC 17 02 02.
- **Labeller B:** `llm_failure` — Missed row: P1 was correct (17 02 02 Glass) but P2 returned no_equivalent with conf 20, an empty element and no row.
- **Adjudicated:** `header_context` — A wins. P2's empty element and low confidence make a weak output, but not one of the D1/D1b failure types (timeout, malformed output, missing ID, truncation) that A60.15 reserves for llm_failure. The line plus its déchets section mark the glass as C&D waste, and P2 treated it as having no equivalent, so header_context is the best fit for why v failed.

### 01.02.0160. (en): missed, first wrong level `decision`

- **Line:** Recover redundant pipes and inspection chambers — Redundant PVC-U drainage pipes and PE chambers exposed during excavation, cleaned and sent for recycling.
- **GT:** Construction and demolition material / 17 02 - Wood, glass and plastic / 17 02 03 - Plastic
- **Proposed:** Construction and demolition material | 17 02 - Wood, glass and plastic | 17 02 03 - Plastic · rule D4 · reason `NO_LIBRARY_EQUIVALENT` · v 1
- **Labeller A:** `header_context` — P2 called the PVC/PE pipes no_equivalent and ignored the demolition/waste section that points to EWC 17 02 03.
- **Labeller B:** `llm_failure` — Missed row: P1 was correct (17 02 03 Plastic) but P2 returned no_equivalent with no row for plainly plastic pipes.
- **Adjudicated:** `header_context` — A wins, for the same reason as the EN glass row. P2's output was not a timeout, malformed, missing an ID or truncated, so llm_failure is out under A60.15. The v failure comes from P2 treating recovered pipes as a product with no equivalent and not as plastic C&D waste, which the waste section header calls for.

### 01.02.0160. (fr): missed, first wrong level `decision`

- **Line:** Récupération de canalisations et regards hors service — Canalisations en PVC-U et regards en PE mis au jour lors des terrassements, nettoyés et envoyés au recyclage.
- **GT:** Construction and demolition material / 17 02 - Wood, glass and plastic / 17 02 03 - Plastic
- **Proposed:** Construction and demolition material | 17 02 - Wood, glass and plastic | 17 02 03 - Plastic · rule D4 · reason `NO_LIBRARY_EQUIVALENT` · v 2
- **Labeller A:** `header_context` — P2 said no_equivalent for the PVC/PE pipes despite the demolition/waste context pointing to EWC 17 02 03.
- **Labeller B:** `llm_failure` — Missed row: P2 labelled the item no_equivalent (conf 35) while its own top1 was the correct 17 02 03 Plastic.
- **Adjudicated:** `header_context` + llm_failure — A wins on the primary cause. P2's output was well-formed, so llm_failure in the A60.15 sense (D1/D1b) does not drive this miss. P2 did not commit to treating the recovered pipes as C&D waste even though the waste section and 'recyclage' wording call for it. B's point that P2's kind contradicts its own correct top1 is real but secondary, so llm_failure is kept as cause_2.

### 01.02.0170. (en): missed, first wrong level `decision`

- **Line:** AC pipes and corrugated roof sheets, licensed removal — Asbestos cement removed under enclosure by licensed contractor, wetted, double-bagged, approved landfill, air monitoring.
- **GT:** Construction and demolition material / 17 06 - Insulation materials and asbestos-containing construction materials / 17 06 05 - Construction materials containing asbestos
- **Proposed:** Construction and demolition material | 17 06 - Insulation materials and asbestos-containing construction materials | 17 06 05 - Construction materials containing asbestos · rule D10 · reason `LOW_SIGNAL:v` · v 1
- **Labeller A:** `subtype_parse` — P2 picked 17 06 01 (insulation with asbestos) instead of 17 06 05 for asbestos cement, and the ewc_code attribute was not extracted.
- **Labeller B:** `usage_confuser` — Missed row: P2 chose sibling 17 06 01 (insulation containing asbestos) instead of 17 06 05 (construction materials containing asbestos) for asbestos-cement pipes and sheets.
- **Adjudicated:** `usage_confuser` — B wins. subtype_parse covers an attribute present in the text that was mis-extracted or missed. This line states no EWC code, so the null ewc_code is not an extraction miss. The error is picking the wrong sibling by application (insulation vs pipes and sheets), which is a confusable-pair error.

### 02.01.0030. (en): wrong_proposal_reviewed, first wrong level `type`

- **Line:** Manholes DN 1000, precast rings, depth ≤ 3 m — Precast base unit, rings, cone, frame and cover D400, step irons; joints sealed with elastomeric rings.
- **GT:** Prefab / Other prefabricated concrete elements (i.e., curbs, edges, trenches) / 
- **Proposed:** (none) · rule D4 · reason `NO_LIBRARY_EQUIVALENT` · v 2
- **Labeller A:** `lexical_gap` + gt_convention — Both passes returned no_equivalent for precast manhole rings, which were not bridged to the 'Other prefabricated concrete elements' row.
- **Labeller B:** `gt_convention` + llm_failure — There is no manhole row, so GT maps it to the catch-all 'Other prefabricated concrete elements'; both passes said no_equivalent with conf 0 and an empty element.
- **Adjudicated:** `lexical_gap` + gt_convention — A wins on the primary cause. Both passes knew the item was precast concrete but did not bridge 'manhole/rings' to the 'Other prefabricated' catch-all, which is a lexical bridging failure. B's llm_failure does not apply: both outputs were consistent and well-formed, and a conf-0 no_equivalent is a semantic decision, not a timeout, malformed output, missing ID or truncation. gt_convention stays as cause_2 because the catch-all's examples (curbs, edges, trenches) do not name manholes, so the GT partly relies on reading 'Other' broadly.

### 02.01.0030. (fr): wrong_proposal_reviewed, first wrong level `type`

- **Line:** Regards DN 1000 en éléments préfabriqués, profondeur ≤ 3 m — Fond préfabriqué, rehausses, cône, cadre et tampon D400, échelons; joints élastomères.
- **GT:** Prefab / Other prefabricated concrete elements (i.e., curbs, edges, trenches) / 
- **Proposed:** (none) · rule D4 · reason `NO_LIBRARY_EQUIVALENT` · v 2
- **Labeller A:** `lexical_gap` + gt_convention — Both passes returned no_equivalent for 'regards préfabriqués', which were not bridged to the 'Other prefabricated concrete elements' row.
- **Labeller B:** `gt_convention` + llm_failure — There is no manhole row and GT uses the 'Other prefabricated' catch-all; both passes said no_equivalent with conf 0 and an empty element.
- **Adjudicated:** `lexical_gap` + gt_convention — A wins, for the same reason as the EN row. 'regards préfabriqués' was understood as precast concrete but not bridged to the catch-all row. The outputs were consistent and well-formed, so llm_failure does not apply. gt_convention is kept as cause_2 because the catch-all's examples do not name manholes.

### 07.01.0060. (en): wrong_proposal_reviewed, first wrong level `usage`

- **Line:** Cable troughs with lids, along track, precast — Precast trough units with lockable lids, bedded on sand.
- **GT:** Prefab / Concrete cable ducts / 
- **Proposed:** Prefab | Other prefabricated concrete elements (i.e., curbs, edges, trenches) |  · rule D4 · reason `NO_LIBRARY_EQUIVALENT` · v 2
- **Labeller A:** `usage_confuser` + lexical_gap · element_right_usage_wrong — The cable trough was identified, but the passes chose 'Other prefabricated (curbs, edges, trenches)' over 'Concrete cable ducts'.
- **Labeller B:** `lexical_gap` + usage_confuser · element_right_usage_wrong — 'Cable troughs' were not bridged to 'Concrete cable ducts' (only P1 top2); P1 chose the 'Other prefabricated' catch-all.
- **Adjudicated:** `lexical_gap` + usage_confuser · element_right_usage_wrong — B wins. The failure is that the term 'trough' was not bridged to the library row 'Concrete cable ducts', and it attached instead to 'trenches' in the catch-all's example list. trough vs duct is not one of the listed known confusable pairs, so usage_confuser fits as cause_2, not as the primary cause. Both labellers agree on element_right_usage_wrong.

## 2. gt_suspect rulings (18 Item Nos, 20 language rows; 0 upheld)

A candidate: both passes valid, both material, the same library top1, and that triple differs from the GT. The adjudicator rules once per Item No.; "true" only if the line text supports the model over the GT as a general domain reading. Headline metrics stay "as labelled" either way.

| Item No. | GT | Model (both passes) | Ruling | Reason |
| --- | --- | --- | --- | --- |
| 01.02.0010. | Construction and demolition material / 17 01 - Concrete, bricks, tiles and ceramics / 17 01 01 - Concrete | Excavations and Rock Cutting | For use as aggregates | Concrete arisings | false | The line is 'Break out ... slabs ... haul to licensed recycling facility; weighbridge tickets' under 'Demolition, dismantling and waste'. That describes demolition concrete leaving site as waste, which is classified under EWC 17 01 01 whatever its destination. The text never says the material is reused as aggregate, and a pavement slab is not an 'Excavations and Rock Cutting' item. GT is the general reading. |
| 03.04.0010. | Concrete / Concrete mixture for wearing course / C12/15 | Concrete | General Ready mixed concrete | C12/15 | false | 'Hardstanding top slab ... broom finish, joints sawn' under '03.04. Rigid pavement' describes the exposed top layer of a rigid pavement. That is a wearing course, so the text supports the GT 'Concrete mixture for wearing course' over 'General Ready mixed concrete'. |
| 03.04.0020. | Concrete / Concrete mixture for wearing course / C20/25 | Concrete | General Ready mixed concrete | C20/25 | false | 'Maintenance depot yard slab ... Surface slab, sawn joints, dowels' under Rigid pavement is a surface/wearing layer. The text supports wearing course over generic ready-mix. |
| 03.04.0030. | Concrete / Concrete mixture for wearing course / C25/30 | Concrete | General Ready mixed concrete | C25/30 | false | 'Surface slabs to bus lanes, exposed-aggregate texture' is explicitly the trafficked surface layer, which supports wearing course over generic ready-mix. |
| 03.04.0040. | Concrete / Concrete mixture for wearing course / C30/37 | Concrete | General Ready mixed concrete | C30/37 | false | 'JPCP top layer ... Top layer of jointed plain pavement' explicitly names the top pavement layer, which is the wearing course. GT is supported. |
| 03.04.0050. | Concrete / Concrete mixture for wearing course / C35/45 | Concrete | General Ready mixed concrete | C35/45 | false | 'Toll plaza slabs ... Surface slabs, air-entrained, tined texture; skid resistance' describes a wearing surface. The text supports wearing course over generic ready-mix. |
| 03.04.0060. | Concrete / Concrete mixture for wearing course / C40/50 | Concrete | General Ready mixed concrete | C40/50 | false | 'Container handling area ... Top layer, abrasion class to EN 13892-3, power-floated' is a wearing top layer, so GT wearing course is supported. |
| 03.05.0040. | Building Components / Brick (clay) /  | Building Components | Tile (clay) |  | false | 'Fired clay pavers 200 x 100 x 52 ... herringbone' / 'Pavés en terre cuite 200 x 100 x 52' are brick-format clay pavers, 52 mm thick and laid like brick. That fits 'Brick (clay)' better than 'Tile (clay)', since clay tiles are thin units. GT is supported. |
| 04.01.0100. | Concrete / Concrete for excavation beam / C30/37 | Concrete | Concrete for footings | C30/37 | false | 'Poutre de couronnement des palplanches du batardeau ... consoles de butons' is a capping beam on the temporary sheet-pile support of an excavation, under 'soutènements provisoires'. The text supports 'Concrete for excavation beam' over generic ready-mix. |
| 04.01.0110. | Concrete / Concrete for excavation beam / C25/30 | Concrete | Concrete for excavation beam | C30/37 | false | 'Liernes de la paroi en pieux sécants, puits d'attaque' are walings of a temporary excavation retaining wall, which is exactly an excavation beam. GT is supported. |
| 04.01.0120. | Concrete / Concrete for excavation beam / C35/45 | Concrete | Concrete for excavation beam | C35/45 | false | 'Butons et liernes, fouille profonde ... Poutres d'étaiement provisoires pour fouille de 14 m' explicitly names temporary excavation bracing beams. GT 'Concrete for excavation beam' is directly supported. |
| 04.03.0050. | Concrete / Concrete for beam in prestressed concrete / C25/30 | Concrete | Concrete for bridge deck | C25/30 | false | 'Entretoises du tablier précontraint ... bétonnage autour des zones d'ancrage' names beam elements (cross-beams) in a prestressed deck, with prestress anchor zones inside them. 'Concrete for bridge deck' is also defensible from the 'Tabliers' section, but the text does not favour it over 'beam in prestressed concrete'. That is ambiguity, not label noise. |
| 04.03.0070. | Concrete / Concrete for beam in prestressed concrete / C35/45 | Concrete | Concrete for bridge deck | C35/45 | false | 'Âmes et hourdis inférieur du caisson, encorbellements successifs ... gaines de précontrainte' describes a prestressed box girder built by balanced cantilever. The deck reading is plausible, but the text stresses a prestressed girder, so it does not support the model's triple over the GT. |
| 04.03.0080. | Concrete / Concrete for beam in steel reinforced concrete / C25/30 | Concrete | Concrete for bridge deck | C25/30 | false | 'Longrines de rive et poutres de liaison en béton armé' explicitly says these are reinforced-concrete beams. That supports GT 'Concrete for beam in steel reinforced concrete' over the generic deck row. |
| 04.03.0090. | Concrete / Concrete for beam in steel reinforced concrete / C30/37 | Concrete | Concrete for bridge deck | C30/37 | false | 'Poutres retombantes ... béton armé ... sans précontrainte' explicitly describes non-prestressed reinforced-concrete beams. GT is directly supported. |
| 04.03.0100. | Concrete / Concrete for beam in steel reinforced concrete / C35/45 | Concrete | Concrete for bridge deck | C35/45 | false | 'Composite deck beams ... heavily reinforced, reinforcement density > 250 kg/m3' (FR: 'Poutres ... fortement armées') names reinforced beams explicitly. GT 'beam in steel reinforced concrete' is supported over 'Concrete for bridge deck'. The ruling covers both languages. |
| 05.01.0010. | Excavations and Rock Cutting / For use as aggregates / High quality rock from tunnel blasting | Excavations and Rock Cutting | For use as aggregates | High quality rock from rock cutting | false | 'Drill and blast ... Full-face excavation' in the Kalkberg tunnel section is tunnel blasting, not open rock cutting. GT 'High quality rock from tunnel blasting' matches the text, and the model's 'from rock cutting' does not. |
| 05.01.0070. | Steel / Steel for tunnel lining /  | Steel | Structural steel (generic construction steel) |  | false | 'Lattice girders ... Steel lattice arches incl. foot plates' under '05.01. Excavation and primary support' of a tunnel is tunnel lining support steel. GT 'Steel for tunnel lining' is the specific, text-supported row over generic structural steel. |

## 3. Triggers (≥ 5 rows or ≥ 20% of one language's error rows; primary cause; D1 = A)

| Cause | EN | FR | Pre-mapped response (§10.7) |
| --- | --- | --- | --- |
| `usage_confuser` | 23 (47%) **triggered** | 22 (50%) **triggered** | E-08 sibling verifier |
| `lexical_gap` | 11 (22%) **triggered** | 13 (30%) **triggered** | E-01 enrichment |
| `subtype_parse` | 7 (14%) **triggered** | 1 (2%) | extractor fix, test first |
| `header_context` | 6 (12%) **triggered** | 4 (9%) | path rendering (amendment first, A60.13) |
| `not_in_library` | 0 (0%) | 0 (0%) | no-equivalent guard |
| `llm_failure` | 1 (2%) | 3 (7%) | engineering |
| `gt_convention` | 1 (2%) | 1 (2%) | documented only |
