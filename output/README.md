# Output files

- `improved_output_en.csv` and `improved_output_fr.csv` are **B0 placeholders (rules only, no model call)**, written at v0.1 on 2026-10-05 by `oris --input input/boq_dataset_input_<lang>.csv --library data/oris_materials_global.csv --output output/improved_output_<lang>.csv --profile b0`. The 37 headers are `not_a_material`; the other 282 rows are `needs_review`. They show the output format and the safety floor (F_NM = 0). They are not the system's results.
- They are replaced at gate G4 by the single post-freeze lockbox session (B3 on both full files, `DESIGN.md` §10.3), committed with its evidence under `runs/submission/`.
- `boq_dataset_output_sample.csv` is the sample provided with the exercise.
