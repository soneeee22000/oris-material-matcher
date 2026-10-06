"""A68.1 and A68.5: the shipped enrichment sources, and their 6-gram overlap with the BoQ inputs.

The files under ``data/enrichment/`` are written by the builder and reviewed before use. Each
test here runs over the files that exist and skips, saying so, when a file is absent from the
checkout; the overlap check runs over every ``data/enrichment/*.yaml`` there is.
"""

import csv
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from oris_matcher.domain.library import Library, load_library
from oris_matcher.domain.normalize import normalize
from oris_matcher.enrichment import load_term_map, parse_enrichment, terms_missing_from
from oris_matcher.prompts.v1.render import load_glossary

ROOT = Path(__file__).resolve().parents[1]
ENRICHMENT_DIR = ROOT / "data" / "enrichment"
TERM_MAP = ENRICHMENT_DIR / "term_map.yaml"
SUPPLEMENT = ENRICHMENT_DIR / "glossary_supplement.yaml"
LIBRARIES = (ROOT / "data" / "oris_materials_global.csv", ROOT / "data" / "oris_materials_fr.csv")
BOQ_INPUTS = (
    ROOT / "input" / "boq_dataset_input_en.csv",
    ROOT / "input" / "boq_dataset_input_fr.csv",
)
BOQ_TEXT_COLUMNS = ("Short Description", "Long Description")
OVERLAP_NGRAM = 6
WORD_RE = re.compile(r"\w+")
SOURCE_FILES = frozenset({TERM_MAP.name, SUPPLEMENT.name})


def _require(path: Path) -> Path:
    if not path.is_file():
        pytest.skip(f"{path.relative_to(ROOT).as_posix()} is absent from this checkout")
    return path


def _libraries() -> list[Library]:
    return [load_library(path.read_bytes()) for path in LIBRARIES]


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
    return sorted(ENRICHMENT_DIR.glob("*.yaml")) if ENRICHMENT_DIR.is_dir() else []


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


@pytest.mark.parametrize(
    "path",
    [path for path in _enrichment_files() if path.name not in SOURCE_FILES],
    ids=lambda path: path.name,
)
def test_a_generated_file_belongs_to_a_shipped_library(path: Path) -> None:
    loaded = parse_enrichment(path.read_bytes(), path)
    shas = {library.sha256 for library in _libraries()}
    assert loaded.content.library_sha256 in shas


# Leakage (A68.5, §10.8)


def test_no_enrichment_file_shares_a_six_gram_with_the_boq_inputs() -> None:
    files = _enrichment_files()
    if not files:
        pytest.skip("data/enrichment/ holds no file in this checkout")
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
