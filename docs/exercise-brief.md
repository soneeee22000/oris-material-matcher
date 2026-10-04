# Technical Exercise — Senior AI ML Engineer

**Deadline:** One week from receipt
**Format:** Take-home + 60-minute live follow-up session

---

Hi, and thanks for making it to this stage.

This exercise is designed to give us a realistic picture of how you work: how you explore data, how you make trade-offs, and how you communicate your decisions. A well-documented partial submission beats a rushed complete one.

---

## Who We Are

ORIS builds a platform that helps construction teams make better material decisions: environmental impact, availability, sourcing. A big part of that is structured material data: what materials a project uses, what they're for, and how they map to our reference library.

---

## The Problem

Our clients send us Bill of Quantities (BoQ) files which are spreadsheets/PDF files that list every material in a construction project. A single file can have hundreds of lines like these:

```
Béton armé dosage 350 kg/m3 pour fondations
Bituminous membrane SBS 4mm self-adhesive
Gravier lavé 0/31,5 classe II
Prestressed concrete beams type IPN 300
Membrane d'étanchéité EPDM 1,14 mm
```

Each line needs to be mapped to our material library which is a hierarchical classification of material types, usages, and subtypes that exists in the ORIS platform:

```json
{
  "material_type": "Concrete",
  "material_usage": "Concrete for piers",
  "material_subtype": "C30/37"
}
```

A match is only useful if all three levels are right, each combination carries its own environmental data. Not every line maps to something: headers, labour, services and some specialist products have no equivalent in the library and should stay blank.

Once mapped, the lines auto-fill a project on our platform. Without this, someone manually enters them, line by line.

The library is the catalogue of materials a project can draw from — each entry carries its own environmental data (CO₂ impact, availability, sourcing). Most BoQs can be matched against the **global** library, but some projects require a **regional** library: CO₂ emissions for the same material differ between regions (energy mix, production methods, transport distances), so regional libraries carry locally accurate values.

## Your Task

**Build a material matching service and show, with evidence, that it clears the bar below.**

### What the service does

The provided library files contain raw category names. You are encouraged to write an offline script to enrich them with a `semantic_description` column (e.g., using off-line LLM generation, domain synonyms, French/English translation maps, or hierarchical path merging) to give your matching service better context during inference.

For every line of a BoQ file, it returns exactly one decision:

| Decision | Meaning | What happens on the platform |
|---|---|---|
| `matched` | A valid ORIS type / usage / subtype | Auto-filled into the client's project |
| `not_a_material` | The line carries no material (header, labour, service, …) | Skipped |
| `needs_review` | The line has a material, but the service isn't confident enough | Sent to a human |

### What success looks like

- **Precision of `matched` lines** — the share with all three levels correct. This is what the client sees. We need **at least 90%** before we switch auto-fill back on.
- **Coverage** — the share of material lines that end up correctly `matched`. Maximise it without breaking the precision target.
- Marking a line `not_a_material` when it does carry a material is an error: the material silently disappears from the project.
- Both languages count. The English and French files describe the same project.

### Engineering requirements

- Runs from the command line on any BoQ file with the same columns, e.g. `--input input/boq_dataset_input_fr.csv --library data/oris_materials_global.csv --output improved_output_fr.csv`.
- Exposes the same logic through a small HTTP API (a batch of lines in, decisions out).
- Never emits a type / usage / subtype combination that doesn't exist in the selected library file.
- Handles the ways an LLM call fails (timeouts, rate limits, malformed responses) without losing lines or silently mislabelling them.
- Records, for every line, how the decision was made (model, prompt version, raw response, cost, latency) so it can be audited later.

How you get there is up to you in regards to prompt, library presentation, decision logic, architecture. There is no single expected answer. If you use labelled data at inference time (examples, retrieval, fine-tuning), please explain where it came from and how you kept your evaluation honest.

---

## What's in This Repo

```
data/
  oris_materials_global.csv               The global ORIS library (type → usage → subtype)
  oris_materials_fr.csv                   The French ORIS library
  boq_dataset_matched_GT.csv              Correct labels per Item No. — applies to both input files
input/
  boq_dataset_input_en.csv                One project's BoQ, ~320 lines, English
  boq_dataset_input_fr.csv                The same BoQ in French (same item codes)
output/
  boq_dataset_output_sample.csv           Example rows showing the expected output format
```

**Which library to use:** your service must work with both libraries, selectable via an option (e.g. `--library data/oris_materials_fr.csv`). For this exercise, evaluate **both** input files against the **global** library — that is the library your results will be scored against. At the live session we will run your service on a new, unseen BoQ using the **French** library, so make sure nothing in your pipeline is hard-wired to the global one.

## Constraints

There are some hard constraints we would like to keep:

- **Model size:** Use a small model. GPT-4o-mini, Gemini Flash, Claude Haiku, or any local model ≤ 8B parameters. You can mix models if you want — see the cost budget.
- **Cost:** ≤ $2 per 100 lines at current API pricing. Local models are $0.
- **Latency:** ≤ 2 seconds per line, averaged. Batching is fine.

## Deliverables

Please submit a git repository (GitHub/GitLab) or a zip file containing:

1. **A design document:** What's your approach? What will you try first? What will you cut if you run out of time?

2. **The service:** code, CLI, API and tests, plus its output on both input files: the input columns, a `decision` column and the three label columns.

3. **An evaluation:** precision and coverage of your output computed against `data/boq_dataset_matched_GT.csv`, via a script that can score any output file against any reference CSV with the three label columns — we will reuse it on our own labels for the unseen dataset. Report accuracy at each hierarchy level, share of lines per decision, cost and latency per line, for both languages.

4. **A README:** explaining how to run your solution, what you'd do differently with more time, and what the known weaknesses of your approach are.

### Optional: a small UI

If you have time left over, add a minimal web UI on top of the service: upload a BoQ file (CSV or Excel), run the matching, and display the resulting table when it's done with decision and labels per line, and `needs_review` rows easy to spot.

## How We'll Evaluate

- **How well you understood the data** Where do the hard lines concentrate — by material category, by hierarchy level? Are French and English descriptions handled differently? Did anything surprise you?
- **Did you define a baseline?** What's the simplest reasonable approach, and how much better is yours — on what metric, and how confident are you in that number?
- **Are your trade-offs explicit?** Precision vs. coverage vs. review load vs. cost vs. latency — what did you optimize for, and why?
- **Is it engineered?** Would we be comfortable putting it behind our platform? Clear boundaries between parsing, matching, validation and decision logic; meaningful tests; failures handled; reproducible runs.
- **Can you defend your choices?** In a 60-minute live session, we'll ask you to walk us through your solution, extend it, or modify it.

## AI Tools Policy

You are welcome to use AI coding assistants — Copilot, Claude, Cursor, whatever you use day to day. We use them too, and this role is partly about helping the organization adopt them well.

Good luck. We're looking forward to seeing what you build!