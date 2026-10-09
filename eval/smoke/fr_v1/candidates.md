# FR smoke set v1: candidate lines and labels for owner review

Drafted by an agent on 2026-10-09 for A70 / DESIGN §6 / G3-T12. Every label below is a **proposal**; `labelled_by` is empty in `labels.csv` until the owner reviews it. Provenance: builder-labelled, unofficial.

## Summary

Item rows only (the 19 section-header label rows are counted separately below):

| subset | item lines | expected match | expected no_match |
| --- | --- | --- | --- |
| base_exact | 10 | 10 | 0 |
| base_fr_only | 8 | 8 | 0 |
| base_decoy | 7 | 0 | 7 |
| a10_extra | 4 | 2 | 2 |
| handwritten | 20 | 13 | 7 |
| **total** | **49** | **33** | **16** |

- Section headers: 19 rows in input.csv, each with one label row (subset handwritten, expected no_match, is_material false). labels.csv therefore has 68 rows = 49 items + 19 headers, one per input row, no repeated item_no. Per-subset totals in labels.csv including headers: handwritten 39, base_exact 10, base_fr_only 8, base_decoy 7, a10_extra 4.
- Base positives (base_exact + base_fr_only): 18. Pass bar (§6): at least 9 of 18 correct.
- `is_concrete=true` on 3 expected-match lines (02.01.0010.–0030.) for the A35 signal-b check, and on 1 concrete decoy (05.02.0050.).
- `decoy_close=true` on 8 lines: 3 base close decoys (01.02.0050., 03.01.0020., 04.01.0030.), the base wrong-usage decoy 03.01.0030. (type exists, usage does not fit; also lexically close), and 4 hand-written close decoys (05.02.0010.–0030., 05.02.0050.).
- `is_material=false` only on the 3 service lines 00.01.0010.–0030. and the 19 section headers (all subset handwritten).
- Input: 49 item rows, 13 section headers under 6 top-level headers, runs of at most 10 items; the project reader parses it and derives section paths; batches by section: 3, 6, 5, 2, 3, 3, 2, 3, 1, 3, 1, 10, 7.

## Hard rules followed

- Dev items only: `eval/split_v1.json` `item_ids_dev` was loaded and the FR input filtered in a script **before** any line text was printed. No lockbox text was printed, read or copied.
- `data/boq_dataset_matched_GT.csv`, `eval/annotations/**` and `eval/errors*` were not opened.
- Every `match` label is copied verbatim from one row of `data/oris_materials_fr.csv` (checked by script: 33/33 match triples found; all 35 no_match label rows (16 items + 19 headers) carry blank label columns).
- Every `base_exact` `source_item_no` is in `item_ids_dev` (checked by script).
- Lockbox leakage check (the G3-T12 six-gram test, run as hashes, printing only smoke item numbers): the first draft of 3 lines (01.01.0010.–0030., adapted from dev 03.03 items, whose section alternates dev/lockbox) shared a normalised 6-gram with some lockbox line. They were reworded; the final count is **0**.
- No model or paid API was called; nothing was committed.

## Deviations from the G3-T12 ticket text (owner to confirm)

- Item numbers follow the exercise style (`01.01.0010.`) as the task instruction asked, not `S-001…` as G3-T12 'Files' says; this lets section paths and `plan_batches` work as on the real input.
- `labels.csv` has the 13 columns of the task instruction and of `tests/test_smoke_a10.py` `LABEL_COLUMNS`; the ticket also lists a `date` column, which is not included.
- The coordinator's scorer update (commit 933cc34) requires one label row per input row, headers included; header rows are therefore in labels.csv (subset handwritten, expected no_match, is_material false, note says header). freeze.json is deliberately not written: the owner reviews first.
- The 3 service lines sit in subset `handwritten` (as `tests/test_smoke_a10.py` does); hand-written total is therefore 17 materials + 3 services = 20.

## Step 1 feasibility: dev items with an exact FR row

**10 dev items with a clean exact FR row were found, so no shortfall rule applies.** 3 more are plausible but weaker (not used). The FR library has only 70 rows, so most dev lines have no exact FR row.

| dev item | short (dev) | proposed FR row | used? |
| --- | --- | --- | --- |
| 03.03.0090. | BBSG 0/14 couche de liaison, matériaux neufs | BBSG (enrobé chaud) | yes → 01.01.0010. |
| 03.03.0170. | BBSG 0/10 roulement, matériaux neufs | BBSG (enrobé chaud) | yes → 01.01.0020. |
| 03.03.0210. | SMA 0/6 tiède, 20 % AE | SMA (enrobé tiède) 20% AE | yes → 01.01.0030. |
| 08.04.0060. | Cire Fischer-Tropsch / amide | Additif pour WMA (organique) | yes → 01.02.0010. |
| 08.04.0130. | Filler calcaire 0/0,063, enrobés | Fillers calcaire : Dry ground calcium carbonate (GCC-Dry) Fine | yes → 01.02.0020. |
| 08.02.0010. | Gravillons 4/16 et 16/32 roulés naturels | Granulats naturels › Gravillons | yes → 01.02.0030. |
| 08.04.0090. | Gravillons 6/10 basalte | Granulats naturels › Gravillons | yes → 01.02.0040. |
| 04.01.0090. | Béton maigre C16/20 sous semelles | Béton X0 C16/20 150kg CEM I | yes → 02.01.0010. (X0 added) |
| 04.03.0010. | Hourdis C35/45 XC4/XD1 | Fascicule 65 … C35/45 350kg CEM I | yes → 02.01.0020. |
| 04.01.0060. | Semelles sur pieux C30/37 XC4/XA1 | Fascicule 65 … C30/37 330kg CEM I | yes → 02.01.0030. |
| 08.02.0030. | Sable 0/4 naturel lavé | Granulats naturels › Granulats naturels (generic) | no: generic row, weaker |
| 01.04.0010. | Remblai tout-venant de carrière | Granulats naturels › Granulats naturels (generic) | no: generic row, weaker |
| 03.02.0190. | Fines calcaires 0/0,125 pour assise traitée | Fillers calcaire … GCC-Dry Fine | no: grading and use differ |

Near misses (no exact FR row, not used as base_exact): 03.03.0190. BBSG tiède 10 % AE (row is chaud 10 % AE); 03.03.0230. BBDr tiède 10 % AE (row is chaud); 03.03.0070. EME 2 50 % AE; 03.03.0050. GB 3 tiède 50 % AE; 03.03.0150. SMA tiède sans AE; 08.04.0010. bitume pur 50/70; 08.04.0020. PmB 25/55-55 (no polymer %); 03.02.0010. grave-ciment (no dosage); 03.02.0200. retardateur pour grave-ciment (row is for bétons); 03.02.0140. laitier pour grave-laitier (row is for bétons); 01.04.0080. géotextile (no grammage); 07.01.0050. multitubulaire PEHD (Tube PEHD vs Fourreaux TPC); 04.01.0010. pieux C35/45 XC4/XA2 (no row covers both); 03.03.0250. asphalte coulé de trottoir (row is étanchéité d'OA); 08.04.0040. additif tensioactif WMA (row is organique).

## Base: exact FR row (dev items only) (10)

| item_no | short | long | unit | proposed label | flags | reason | source dev item |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 01.01.0010. | Couche de liaison en BBSG 0/14, épaisseur 6 cm | Béton bitumineux semi-grenu à chaud, liant PmB 25/55-55, aucun recyclé. | t | Mélanges Bitumineux › Enrobés bitumineux › BBSG (enrobé chaud) |  | BBSG hot, no RAP stated: the plain BBSG (enrobé chaud) row. | 03.03.0090. |
| 01.01.0020. | Roulement BBSG 0/10 ép. 4 cm, bretelles | Béton bitumineux semi-grenu à chaud, granulats neufs uniquement, raccords à la main compris. | t | Mélanges Bitumineux › Enrobés bitumineux › BBSG (enrobé chaud) |  | BBSG hot, 0 % AE: the plain BBSG (enrobé chaud) row. | 03.03.0170. |
| 01.01.0030. | Roulement en SMA 0/6 tiède, ép. 3 cm | Stone mastic asphalt tiède, taux d'AE 20 %, mis en œuvre au finisseur. | t | Mélanges Bitumineux › Enrobés bitumineux › SMA (enrobé tiède) 20% AE |  | SMA warm with 20 % AE: exact FR row. | 03.03.0210. |
| 01.02.0010. | Cire Fischer-Tropsch, réducteur de viscosité | Cire de synthèse ajoutée au liant pour enrobés fabriqués à température abaissée. | t | Additifs › Additifs pour mélange bitumineux › Additif pour WMA (organique) |  | Synthetic wax is an organic WMA additive. | 08.04.0060. |
| 01.02.0020. | Filler calcaire 0/0,063 pour enrobés | Calcaire broyé à sec en filler d'apport, livraison en silo. | t | Additifs › Filler › Fillers calcaire : Dry ground calcium carbonate (GCC-Dry) Fine |  | Dry ground limestone filler = GCC-Dry Fine row; 'à sec' added to the dev text. | 08.04.0130. |
| 01.02.0030. | Gravillons 4/16 et 16/32, roulés naturels, lavés | Granulats alluvionnaires roulés selon EN 12620 pour bétons de structure. | t | Granulats › Granulats naturels › Gravillons |  | Natural gravel chippings: Granulats naturels / Gravillons. | 08.02.0010. |
| 01.02.0040. | Gravillons 6/10 basalte, CPA ≥ 0,55 | Gravillons de basalte de carrière selon EN 13043 pour couches de roulement. | t | Granulats › Granulats naturels › Gravillons |  | Natural crushed basalt chippings: Granulats naturels / Gravillons. | 08.04.0090. |
| 02.01.0010. | Béton de propreté C16/20 X0, 50 mm, sous semelles | Béton maigre de réglage sous semelles isolées, taloché, protégé de la pluie 24 h. | m³ | Bétons › Béton NF EN 206 CN › Béton X0 C16/20 150kg CEM I | concrete | C16/20 lean concrete; X0 added to the dev text; only C16/20 row is X0. | 04.01.0090. |
| 02.01.0020. | Hourdis du pont principal, C35/45 XC4/XD1, E/C ≤ 0,45 | Béton pompé et réglé à la règle vibrante, surface prête pour l'étanchéité. | m³ | Bétons › Béton Fascicule 65 › Béton XS3-XC1-XC2-XC3-XC4-XS1-XS2-XD1-XD2-XF1-XA1 C35/45 350kg CEM I | concrete | Bridge deck C35/45 with XC4 and XD1: only the Fascicule 65 C35/45 row covers both. | 04.03.0010. |
| 02.01.0030. | Semelles sur pieux P1–P8, C30/37 XC4/XA1 | Semelles coulées sur béton de propreté après recépage des pieux, cure 7 jours. | m³ | Bétons › Béton Fascicule 65 › Béton XC3-XC4-XS1-XS2-XD1-XD2-XF1-XA1 C30/37 330kg CEM I | concrete | C30/37 with XC4 and XA1: only the Fascicule 65 C30/37 row covers both. | 04.01.0060. |

## Base: FR-only leaves (8)

| item_no | short | long | unit | proposed label | flags | reason |
| --- | --- | --- | --- | --- | --- | --- |
| 01.01.0040. | EME 2 0/14, 100 mm, 40 % AE, couche de base | Enrobé à module élevé classe 2, fabriqué à chaud, 40 % d'agrégats d'enrobés. | t | Mélanges Bitumineux › Enrobés bitumineux › EME2 (enrobé chaud) 40% AE |  | EME2 hot 40 % AE: FR-only leaf (global has 30/50 % RAP only). |
| 01.01.0050. | EME 1 0/10, 80 mm, 20 % AE, élargissement de chaussée | Enrobé à module élevé classe 1, fabriqué à chaud, 20 % d'agrégats d'enrobés. | t | Mélanges Bitumineux › Enrobés bitumineux › EME1 (enrobé chaud) 20% AE |  | EME1 hot 20 % AE: FR-only leaf. |
| 01.01.0060. | GB 1 0/14 tiède, 40 % AE, couche de fondation | Grave-bitume classe 1 fabriquée à 130 °C maximum, 40 % d'agrégats d'enrobés. | t | Mélanges Bitumineux › Enrobés bitumineux › GB1 (enrobé tiède) 40% AE |  | GB1 warm 40 % AE: FR-only leaf. |
| 02.02.0010. | Appareils d'appui à pot, piles P2 à P4, 6 000 kN | Appareils d'appui à pot fixes et mobiles unidirectionnels, y compris platines et scellement. | U | Appareils d'appui › Appareil d'appui à pot › (blank) |  | Pot bearing: FR-only leaf (blank subtype in the FR library). |
| 03.01.0010. | Conduite fonte ductile DN 300, eau potable | Tuyaux fonte ductile à emboîtement, joint automatique, posés en tranchée sur lit de sable. | m | Canalisations › Canalisations en fonte ductile › Tuyau fonte ductile ∅ 300 |  | Ductile iron DN 300: FR-only leaf. |
| 03.02.0010. | Cordeau détonant 3,6 g/m, tirs en déblai rocheux | Cordeau détonant de faible grammage pour l'amorçage des volées, fourni en bobines. | m | Travaux à l'explosif › Cordeaux › Cordeau 3.6 g |  | Detonating cord 3.6 g: FR-only leaf. |
| 04.01.0010. | Défenses en pneus usagés, quai de service | Pneus de récupération suspendus par chaînes galvanisées au couronnement du quai. | U | Travaux Maritimes › Équipement maritime › Défense pneu usagé |  | Used-tyre fender: FR-only leaf. |
| 04.01.0020. | Bollards acier 15 t, quai de service | Bollards en acier moulé de capacité 15 t, scellés dans la poutre de couronnement. | U | Travaux Maritimes › Équipement maritime › Bollard Acier 15 T |  | Steel bollard 15 t: FR-only leaf. |

## Base: decoys (no FR row) (7)

| item_no | short | long | unit | proposed label | flags | reason |
| --- | --- | --- | --- | --- | --- | --- |
| 01.02.0050. | Bitume pur 50/70, livraison en centrale | Liant routier pour enrobés à chaud, transport en citerne calorifugée. | t | no match | close decoy | Close decoy: only Bitume pur 15/25 exists; 50/70 has no FR row. |
| 02.03.0010. | Palplanches acier AZ 18-700, L = 12 m, laissées en place | Rideau de palplanches S355GP mis en fiche au vibrofonceur. | t | no match |  | Far decoy: no sheet-pile row in the FR library. |
| 02.03.0020. | Granulats de verre cellulaire pour remblai allégé | Verre expansé recyclé 10/50 en remblai léger derrière les culées. | m³ | no match |  | Far decoy: no foam-glass row. |
| 03.01.0020. | Conduite fonte ductile DN 150, branchements | Tuyaux fonte ductile à emboîtement pour antennes de distribution, joint automatique. | m | no match | close decoy | Close decoy: ductile rows exist for ∅ 200 and ∅ 300 only. |
| 03.01.0030. | Câble aluminium haute tension, pose aérienne sur pylônes | Conducteur en alliage d'aluminium tendu entre pylônes, y compris pinces d'ancrage. | m | no match | close decoy | Wrong-usage decoy: the HT aluminium cable row is for underground laying only. |
| 04.01.0030. | Défenses cylindriques en caoutchouc neuf, Ø 600 | Défenses extrudées en élastomère neuf, suspendues par chaînes au couronnement du quai. | m | no match | close decoy | Close decoy: only the used-tyre fender row exists; new rubber fenders have none. |
| 04.02.0010. | Pavés de terre cuite posés sur mortier, place de la gare | Pavés en terre cuite 220 × 110 × 50, joints au mortier. | m² | no match |  | Far decoy: no clay-paver row. |

## A10 extras (4)

| item_no | short | long | unit | proposed label | flags | reason |
| --- | --- | --- | --- | --- | --- | --- |
| 01.03.0010. | Grave-ciment dosée à 4 % de ciment, couche de fondation | Grave traitée au ciment fabriquée en centrale, dosage 4 % CEM II/B, 220 mm. | t | no match |  | A10 subtype conflict: the only row is Grave Ciment 6 %; text states 4 %. |
| 01.03.0020. | Géotextile non tissé de séparation sous remblai | Nappe géotextile marquée CE, recouvrements 500 mm, grammage selon avis du maître d'œuvre. | m² | no match |  | A10 missing spec: no grammage, so 150 / 250 / 800 g/m² cannot be chosen. |
| 02.02.0020. | Garde-corps métalliques normalisés type S8, fourniture et pose | Fourniture, pose et scellement chimique sur longrines, y compris main-d'œuvre et réglage. | m | Équipements métalliques › Garde-corps › Garde-corps métalliques normalisés |  | A10 mixed supply + labour; the material has an exact row. |
| 02.02.0030. | Bois de coffrage d'origine locale France, fourniture | Fourniture du bois de coffrage pour l'ensemble des élévations, au forfait. | Ft | Bois › Coffrage › Coffrage bois - local France |  | A10 material priced Ft; the material has an exact row. |

## Hand-written lines (incl. 3 services) (20)

| item_no | short | long | unit | proposed label | flags | reason |
| --- | --- | --- | --- | --- | --- | --- |
| 00.01.0010. | Installation et repli de chantier, base vie | Bungalows, clôtures, raccordements provisoires et remise en état en fin de travaux. | Ft | no match | service | Service (installation de chantier), not a material. |
| 00.01.0020. | Études d'exécution et plans de récolement | Notes de calcul, plans d'exécution et dossier de récolement au format DAO convenu. | Ft | no match | service | Service (études), not a material. |
| 00.01.0030. | Signalisation temporaire de chantier | Location, pose, déplacements et entretien des panneaux de chantier pendant toute la durée des travaux. | mois | no match | service | Service (location et entretien), not a material. |
| 05.01.0010. | enrobe BBSG 0/10 ep.6cm chaud, 0%AE | couche de roulement parking PL, matériaux neufs | t | Mélanges Bitumineux › Enrobés bitumineux › BBSG (enrobé chaud) |  | Mangled BBSG hot, no AE: plain BBSG (enrobé chaud). |
| 05.01.0020. | Bitume pol. 5 % (SBS) - fourn. centrale | liant modifie pr BBSG, livraison citerne | t | Liants bitumineux › Bitumes modifiés › Bitume polymère 5% |  | Abbreviated polymer bitumen 5 %. |
| 05.01.0030. | Emuls. cationique 65% enrobage | pour enrobés a froid, stockage en cuve | t | Liants bitumineux › Emulsions › Emulsion d'enrobage 65% |  | Abbreviated coating emulsion 65 %. |
| 05.01.0040. | Arma. HA B500B façonnées pr BA | aciers pour béton armé coupés et façonnés, y.c. ligatures | t | Armatures › Armatures béton armé › Armatures de béton armé |  | Abbreviated reinforcing steel for reinforced concrete. |
| 05.01.0050. | Gtx NT 800g/m2 sous enrochements | anti-poinçonnement, rouleaux 5,3 m | m² | Géotextiles et membranes › Géotextiles › Géotextile 800 g/m² |  | Abbreviated geotextile 800 g/m². |
| 05.01.0060. | PSE remblai léger - ens. | blocs polystyrène expansé derrière culée C1, fourniture | Ft | Plastiques › Polystyrène › Polystyrène expansé |  | Abbreviated EPS, priced Ft. |
| 05.01.0070. | membrane EPDM bassin ep 1,1 mm | feuille d'étanchéité EPDM soudée, bassin de rétention | m² | Feuilles d'étanchéité › Feuille EPDM › (blank) |  | EPDM sheet (blank subtype in the FR library). |
| 05.01.0080. | Candélabres composite h 8m (fourn.) | mâts composite pour éclairage du parking, fourniture seule | LS | Équipements électriques › Éclairage extérieur › Mat de candélabre composite - 6 à 12 m |  | Composite lighting mast 8 m, priced LS. |
| 05.01.0090. | Fourreaux TPC rouge D90 | gaine annelée TPC pour réseau éclairage, aiguille incluse | m | Canalisations › Tuyaux › Fourreaux TPC |  | Abbreviated TPC ducts. |
| 05.01.0100. | Adjuvant accélérateur de prise, forfait hiver | accélérateur de prise pour bétons coulés par temps froid | Ft | Adjuvants › Adjuvants pour bétons › Accélérateurs de prise |  | Set accelerator for concrete, priced Ft. |
| 05.02.0010. | traverses beton bi-bloc VS | traverses béton bi-blocs pour voie de service, posées sur ballast | U | no match | close decoy | Close decoy: only Traverse métallique exists. |
| 05.02.0020. | Gtx tissé 400 g/m2 renfort | géotextile tissé de renforcement sous remblai | m² | no match | close decoy | Close decoy: geotextile rows are 150 / 250 / 800 g/m² only. |
| 05.02.0030. | bollard fonte 30T | bollards en fonte, capacité 30 t, quai fluvial | U | no match | close decoy | Close decoy: only Bollard Acier 15 T exists; cast iron 30 t conflicts. |
| 05.02.0040. | CMC boue forage pieux, sacs 25kg | carboxyméthylcellulose pour boue bentonitique des pieux forés | t | Adjuvants › Adjuvants pour boue de forage › Carboxyméthylcellulose |  | Abbreviated CMC for drilling mud. |
| 05.02.0050. | Beton C25/30 XC2 voiles | béton pour voiles des culées, coulé en place, vibré | m³ | no match | concrete, close decoy | Close concrete decoy: no C25/30 row in the FR library. |
| 05.02.0060. | Asphalte d'étanchéité OA, ép. 2 cm - forfait | asphalte coulé d'étanchéité sur hourdis du PS-3 | Ft | Mélanges Bitumineux › Asphalte d'étanchéité pour ouvrage d'art › (blank) |  | Bridge waterproofing asphalt, priced Ft (blank subtype). |
| 05.02.0070. | Pigment rouge (oxyde fer) enrobé piste cyclable | colorant pour enrobé de la piste cyclable | t | Additifs › Additifs pour mélange bitumineux › Pigment rouge (oxyde de fer) |  | Red iron-oxide pigment for asphalt. |

## Labels the drafter is unsure about

| item(s) | why |
| --- | --- |
| 02.01.0020., 02.01.0030. | The standard (Fascicule 65 vs NF EN 206 CN) is not stated; the row was chosen because only the Fascicule 65 row lists both exposure classes. An owner may prefer no_match. |
| 02.01.0010. | X0 was added to the dev text; the 150 kg CEM I content is not stated (a lean concrete at C16/20 is consistent). |
| 01.02.0020. | "Dry ground calcium carbonate (GCC-Dry) Fine" is read as dry-ground limestone filler; 'à sec' was added to the dev text to make this explicit. |
| 01.02.0030. | Rounded alluvial gravel for structural concrete mapped to the generic Granulats naturels › Gravillons row; the FR library has no concrete-aggregate usage. |
| 03.01.0030. | Wrong-usage decoy: an aerial aluminium HT conductor vs the 'Câbles pose souterraine' row. A reviewer could argue the cable row should match regardless of laying method. |
| 04.01.0030. | New rubber fenders vs the used-tyre fender row: labelled no_match because the material differs, but it is a close call by design. |
| 01.03.0010. | A10 subtype conflict: 4 % stated vs the only row Grave Ciment 6 %. If the owner reads 6 % as a representative leaf, this would become a match. |
| 02.02.0030. | Formwork timber priced Ft mapped to Coffrage bois - local France; the line says 'origine locale France' explicitly, so it is probably fine. |
| 05.01.0040. | Armatures for BA → Armatures béton armé; the 'Armatures › Pieux' row also exists but does not fit a non-pile line. |
