# Output files

- `improved_output_en.csv` and `improved_output_fr.csv` are the system's results. They are the B3 outputs of the single post-freeze lockbox session at the `eval-freeze` tag (Thu 8 Oct 2026):
  - EN: `runs/submission/20261008T023928Z-56f85fb8`;
  - FR: `runs/submission/20261008T024244Z-de394c39`.
- Each has the 319 input rows in order, then the decision, the matched `type / usage / subtype` when `matched`, the reason code, and the audit columns.
- They are checked by `eval/check_requirements.py`: STRICT and RQ1–RQ11 are green, and `oris replay --check` is byte-identical. The claim and its analysis are in `docs/evaluation.md`.
- `boq_dataset_output_sample.csv` is the sample provided with the exercise.
- The B0 placeholders written at v0.1 (rules only) were replaced by these files at gate G4.
