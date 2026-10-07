"""A68.1, A68.2 and A68.5: the shipped enrichment files and their 6-gram overlap with BoQ text.

The files under ``data/enrichment/`` are written by the builder and reviewed before use. A68 is
in force once that folder exists or a ``config/policy.yaml`` entry names an enrichment: from
then on the term map, the supplement and ``global.yaml`` must be there, and a missing one fails
the suite rather than skipping its checks. Before that (a checkout without the content), the
checks skip, saying so. The overlap check runs over every ``data/enrichment/*.yaml`` and every
file a policy entry names, and every generated file must be what its current sources generate.
"""

import csv
import hashlib
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from oris_matcher.domain.library import Library, load_library
from oris_matcher.domain.normalize import normalize
from oris_matcher.enrichment import (
    EnrichmentSources,
    dump_enrichment,
    generate,
    load_term_map,
    parse_enrichment,
    terms_missing_from,
)
from oris_matcher.prompts.v1.render import load_glossary
from oris_matcher.settings import PolicyConfig, load_policy

ROOT = Path(__file__).resolve().parents[1]
ENRICHMENT_DIR = ROOT / "data" / "enrichment"
FIXTURES = ROOT / "tests" / "fixtures" / "enrichment"
TERM_MAP = ENRICHMENT_DIR / "term_map.yaml"
SUPPLEMENT = ENRICHMENT_DIR / "glossary_supplement.yaml"
SHIPPED_GENERATED = "global.yaml"
POLICY = ROOT / "config" / "policy.yaml"
LIBRARIES = (ROOT / "data" / "oris_materials_global.csv", ROOT / "data" / "oris_materials_fr.csv")
BOQ_INPUTS = (
    ROOT / "input" / "boq_dataset_input_en.csv",
    ROOT / "input" / "boq_dataset_input_fr.csv",
)
BOQ_TEXT_COLUMNS = ("Short Description", "Long Description")
OVERLAP_NGRAM = 6
WORD_RE = re.compile(r"\w+")
SOURCE_FILES = frozenset({TERM_MAP.name, SUPPLEMENT.name})
NOT_IN_FORCE = "data/enrichment/ is absent and no policy entry names an enrichment"


def policy_enrichments(config: PolicyConfig) -> dict[str, str]:
    """Return every enrichment path a policy entry names, with the SHA-256 it certifies."""
    return {
        entry.enrichment: str(entry.enrichment_sha256)
        for entries in config.policies.values()
        for entry in entries.values()
        if entry.enrichment is not None
    }


def _shipped_policy() -> dict[str, str]:
    return policy_enrichments(load_policy(POLICY))


def in_force(directory: Path, named: dict[str, str]) -> bool:
    """Tell whether A68 is in force: the folder exists or a policy entry names an enrichment."""
    return directory.is_dir() or bool(named)


def missing_shipped_files(directory: Path, named: dict[str, str]) -> list[str]:
    """Return the files A68.1-2 require once A68 is in force that the checkout lacks."""
    if not in_force(directory, named):
        return []
    required = (TERM_MAP.name, SUPPLEMENT.name, SHIPPED_GENERATED)
    return [name for name in required if not (directory / name).is_file()]


def _require(path: Path) -> Path:
    if path.is_file():
        return path
    where = path.relative_to(ROOT).as_posix()
    if in_force(ENRICHMENT_DIR, _shipped_policy()):
        pytest.fail(f"{where} is required once A68 is in force, and it is absent")
    pytest.skip(f"{where} is absent and A68 is not yet in force in this checkout")


def _libraries() -> list[Library]:
    return [load_library(path.read_bytes()) for path in LIBRARIES]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ngrams(text: str) -> set[tuple[str, ...]]:
    """Return the word 6-grams of a text after the §6 normaliser."""
    words = WORD_RE.findall(normalize(text))
    return {
        tuple(words[start : start + OVERLAP_NGRAM])
        for start in range(len(words) - OVERLAP_NGRAM + 1)
    }


def boq_ngrams() -> set[tuple[str, ...]]:
    """Return every 6-gram of every short and long description of both BoQ inputs."""
    grams: set[tuple[str, ...]] = set()
    for path in BOQ_INPUTS:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for record in csv.DictReader(handle):
                for column in BOQ_TEXT_COLUMNS:
                    grams |= ngrams(record[column] or "")
    return grams


def _term_map_texts(payload: dict[str, Any]) -> Iterator[str]:
    for entry in payload.get("entries") or ():
        yield str(entry["term"])
        yield from (str(value) for value in entry.get("equivalents") or ())


def _glossary_texts(entries: Any) -> Iterator[str]:
    for entry in entries or ():
        yield str(entry["term"])
        yield str(entry["meaning"])


def _generated_texts(payload: dict[str, Any]) -> Iterator[str]:
    for name in ("types", "usages", "subtypes"):
        for node in payload.get(name) or ():
            also = [str(value) for value in node.get("also") or ()]
            yield from also
            yield "; ".join(also)
    yield from _glossary_texts(payload.get("glossary"))


def checked_texts(path: Path) -> list[str]:
    """Return the texts of one enrichment file that the overlap check covers (A68.5)."""
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if path.name == TERM_MAP.name:
        return list(_term_map_texts(payload))
    if path.name == SUPPLEMENT.name:
        return list(_glossary_texts(payload.get("entries")))
    return list(_generated_texts(payload))


def _enrichment_files() -> list[Path]:
    found = set(ENRICHMENT_DIR.glob("*.yaml")) if ENRICHMENT_DIR.is_dir() else set()
    found |= {ROOT / path for path in _shipped_policy() if (ROOT / path).is_file()}
    return sorted(found)


def _generated_files() -> list[Path]:
    return [path for path in _enrichment_files() if path.name not in SOURCE_FILES]


def stale_reasons(
    path: Path, term_map: Path, supplement: Path, libraries: list[Library]
) -> list[str]:
    """Return why a generated file is not what its current sources generate; empty if it is."""
    content = parse_enrichment(path.read_bytes(), path).content
    reasons = []
    if content.term_map_sha256 != _sha(term_map):
        reasons.append("term_map_sha256")
    if content.glossary_supplement_sha256 != _sha(supplement):
        reasons.append("glossary_supplement_sha256")
    library = next((item for item in libraries if item.sha256 == content.library_sha256), None)
    if library is None:
        return [*reasons, "library_sha256"]
    sources = EnrichmentSources(
        term_map=term_map.read_bytes(),
        supplement=supplement.read_bytes(),
        reviewed_by=content.reviewed_by,
    )
    if dump_enrichment(generate(library, sources)).encode("utf-8") != path.read_bytes():
        reasons.append("bytes")
    return reasons


def policy_problems(named: dict[str, str], root: Path) -> list[str]:
    """Return each policy-named enrichment that is missing or not the bytes its entry certifies."""
    problems = []
    for relative, sha in sorted(named.items()):
        path = root / relative
        if not path.is_file():
            problems.append(f"{relative}: missing")
        elif _sha(path) != sha:
            problems.append(f"{relative}: sha256")
    return problems


# A68 in force


def test_the_shipped_files_are_present_once_a68_is_in_force() -> None:
    named = _shipped_policy()
    if not in_force(ENRICHMENT_DIR, named):
        pytest.skip(NOT_IN_FORCE)
    assert missing_shipped_files(ENRICHMENT_DIR, named) == []


def test_the_in_force_check_names_every_missing_file(tmp_path: Path) -> None:
    absent = tmp_path / "absent"
    assert missing_shipped_files(absent, {}) == []
    named = {"data/enrichment/global.yaml": "a" * 64}
    expected = [TERM_MAP.name, SUPPLEMENT.name, SHIPPED_GENERATED]
    assert missing_shipped_files(absent, named) == expected
    tmp_path.joinpath(TERM_MAP.name).write_text("entries: []\n", encoding="utf-8")
    assert missing_shipped_files(tmp_path, {}) == [SUPPLEMENT.name, SHIPPED_GENERATED]


def test_every_policy_named_enrichment_is_shipped_with_its_certified_bytes() -> None:
    assert policy_problems(_shipped_policy(), ROOT) == []


def test_the_policy_check_catches_a_missing_or_changed_file(tmp_path: Path) -> None:
    present = tmp_path / "global.yaml"
    present.write_bytes(b"x\n")
    named = {"global.yaml": "0" * 64, "gone.yaml": _sha(present)}
    assert policy_problems(named, tmp_path) == ["global.yaml: sha256", "gone.yaml: missing"]
    assert policy_problems({"global.yaml": _sha(present)}, tmp_path) == []


# The sources (A68.1)


def test_the_term_map_validates_and_every_term_occurs_in_a_library() -> None:
    term_map = load_term_map(_require(TERM_MAP).read_bytes())
    assert term_map.entries
    assert terms_missing_from(term_map, _libraries()) == []


def test_the_term_map_check_catches_a_term_in_no_library() -> None:
    text = (
        "entries:\n"
        "  - {term: Quicklime, lang: en, equivalents: [chaux vive], source: library}\n"
        "  - {term: Armatures, lang: fr, equivalents: [rebar], source: library}\n"
        "  - {term: qzx unheard of, lang: en, equivalents: [x], source: library}\n"
    )
    missing = terms_missing_from(load_term_map(text.encode()), _libraries())
    assert missing == ["qzx unheard of"]


def test_the_supplement_has_the_glossary_schema() -> None:
    text = _require(SUPPLEMENT).read_text(encoding="utf-8")
    glossary = load_glossary(text)
    assert glossary.entries
    assert all(entry.source != "lockbox_obs" for entry in glossary.entries)


# The generated files (A68.2)


@pytest.mark.parametrize("path", _generated_files(), ids=lambda path: path.name)
def test_a_generated_file_belongs_to_a_shipped_library(path: Path) -> None:
    loaded = parse_enrichment(path.read_bytes(), path)
    shas = {library.sha256 for library in _libraries()}
    assert loaded.content.library_sha256 in shas


@pytest.mark.parametrize("path", _generated_files(), ids=lambda path: path.name)
def test_a_generated_file_is_what_its_current_sources_generate(path: Path) -> None:
    term_map, supplement = _require(TERM_MAP), _require(SUPPLEMENT)
    assert stale_reasons(path, term_map, supplement, _libraries()) == []


def test_the_staleness_check_catches_an_edited_source(tmp_path: Path) -> None:
    term_map, supplement = tmp_path / TERM_MAP.name, tmp_path / SUPPLEMENT.name
    term_map.write_bytes((FIXTURES / "term_map_global.yaml").read_bytes())
    supplement.write_bytes((FIXTURES / "supplement_small.yaml").read_bytes())
    libraries = _libraries()
    sources = EnrichmentSources(term_map.read_bytes(), supplement.read_bytes(), "tests")
    generated = tmp_path / SHIPPED_GENERATED
    generated.write_bytes(dump_enrichment(generate(libraries[0], sources)).encode("utf-8"))
    assert stale_reasons(generated, term_map, supplement, libraries) == []
    term_map.write_bytes(term_map.read_bytes() + b"# edited\n")
    assert stale_reasons(generated, term_map, supplement, libraries) == ["term_map_sha256", "bytes"]
    edited = generated.read_bytes().replace(_sha(supplement).encode(), b"f" * 64)
    generated.write_bytes(edited)
    reasons = stale_reasons(generated, term_map, supplement, libraries)
    assert reasons == ["term_map_sha256", "glossary_supplement_sha256", "bytes"]


# Leakage (A68.5, §10.8)


def test_no_enrichment_file_shares_a_six_gram_with_the_boq_inputs() -> None:
    if not in_force(ENRICHMENT_DIR, _shipped_policy()):
        pytest.skip(NOT_IN_FORCE)
    files = _enrichment_files()
    required = {TERM_MAP.name, SUPPLEMENT.name, SHIPPED_GENERATED}
    assert required <= {path.name for path in files}
    boq = boq_ngrams()
    assert boq
    overlaps = [
        (path.name, text) for path in files for text in checked_texts(path) if ngrams(text) & boq
    ]
    assert overlaps == []


def test_the_overlap_check_catches_copied_item_text(tmp_path: Path) -> None:
    boq = boq_ngrams()
    sample = " ".join(sorted(boq)[0])
    term_map = tmp_path / TERM_MAP.name
    term_map.write_text(
        yaml.safe_dump(
            {
                "entries": [
                    {"term": "x", "lang": "en", "equivalents": [sample], "source": "library"}
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    supplement = tmp_path / SUPPLEMENT.name
    supplement.write_text(
        yaml.safe_dump(
            {"entries": [{"term": "y", "meaning": f"see {sample}", "lang": "en"}]},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    generated = tmp_path / "global.yaml"
    generated.write_text(
        yaml.safe_dump({"types": [{"type": "T", "also": ["a", sample]}]}, allow_unicode=True),
        encoding="utf-8",
    )
    for path in (term_map, supplement, generated):
        assert any(ngrams(text) & boq for text in checked_texts(path)), path.name
