"""The A68 §1 enrichment sources: data/enrichment/term_map.yaml and glossary_supplement.yaml.

These tests check the reviewed content against the A68 §1 contract on their own, by parsing the
YAML directly, so they do not depend on the generator or the loaders.
"""

import csv
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from oris_matcher.domain.normalize import normalize

ROOT = Path(__file__).resolve().parents[1]
ENRICHMENT = ROOT / "data" / "enrichment"
TERM_MAP = ENRICHMENT / "term_map.yaml"
SUPPLEMENT = ENRICHMENT / "glossary_supplement.yaml"
DEFAULT_GLOSSARY = ROOT / "src" / "oris_matcher" / "prompts" / "v1" / "glossary.yaml"
LIBRARIES = (ROOT / "data" / "oris_materials_global.csv", ROOT / "data" / "oris_materials_fr.csv")
LABEL_COLUMNS = ("material_type", "material_usage", "material_subtype")

TERM_MAP_KEYS = ("term", "lang", "equivalents", "source", "ref")
TERM_MAP_SOURCES = {"library", "standard", "dev_error"}
GLOSSARY_REQUIRED = ("term", "meaning", "lang", "source")
GLOSSARY_KEYS = (*GLOSSARY_REQUIRED, "ref", "false_friend")
GLOSSARY_SOURCES = {"standard", "library", "dev_obs", "dev_error"}
LANGS = {"en", "fr"}
MAX_PHRASE_WORDS = 5


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _entries(path: Path) -> list[dict[str, Any]]:
    data = _load(path)
    assert isinstance(data, dict)
    assert list(data) == ["entries"]
    entries = data["entries"]
    assert isinstance(entries, list)
    assert entries
    return entries


def _labels() -> list[str]:
    labels: list[str] = []
    for library in LIBRARIES:
        with library.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                labels.extend(normalize(row[column]) for column in LABEL_COLUMNS if row[column])
    return labels


def _occurs(term: str, labels: list[str]) -> bool:
    pattern = re.compile(rf"(?<!\w){re.escape(normalize(term))}(?!\w)")
    return any(pattern.search(label) for label in labels)


def _sort_key(entry: dict[str, Any]) -> tuple[str, str]:
    return entry["lang"], normalize(entry["term"])


def _equivalents(term: str, lang: str) -> list[str]:
    for entry in _entries(TERM_MAP):
        if entry["lang"] == lang and normalize(entry["term"]) == normalize(term):
            return [normalize(value) for value in entry["equivalents"]]
    raise AssertionError(f"no term-map entry for {term!r} ({lang})")


@pytest.mark.parametrize("path", [TERM_MAP, SUPPLEMENT, ENRICHMENT / "README.md"])
def test_the_files_are_utf8_with_lf_line_ends(path: Path) -> None:
    raw = path.read_bytes()
    raw.decode("utf-8")
    assert b"\r" not in raw
    assert raw.endswith(b"\n")


@pytest.mark.parametrize("path", [TERM_MAP, SUPPLEMENT])
def test_the_files_open_with_a_provenance_header(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("#")
    header = text.split("entries:", 1)[0]
    for tag in ("library", "standard", "dev_error"):
        assert tag in header


def test_term_map_entries_follow_the_fixed_key_order_and_vocabulary() -> None:
    for entry in _entries(TERM_MAP):
        assert tuple(entry) == TERM_MAP_KEYS, entry
        assert entry["lang"] in LANGS
        assert entry["source"] in TERM_MAP_SOURCES
        assert isinstance(entry["term"], str)
        assert entry["term"].strip() == entry["term"]
        assert entry["term"]
        assert isinstance(entry["ref"], str)
        if entry["source"] == "standard":
            assert entry["ref"].strip(), entry["term"]


def test_term_map_equivalents_are_distinct_non_empty_phrases() -> None:
    for entry in _entries(TERM_MAP):
        equivalents = entry["equivalents"]
        assert isinstance(equivalents, list)
        assert equivalents, entry["term"]
        assert all(isinstance(value, str) and value.strip() == value for value in equivalents)
        assert all(equivalents)
        normalized = [normalize(value) for value in equivalents]
        assert len(set(normalized)) == len(normalized), entry["term"]
        assert normalize(entry["term"]) not in normalized, entry["term"]


def test_term_map_is_sorted_by_language_then_term_without_duplicates() -> None:
    keys = [_sort_key(entry) for entry in _entries(TERM_MAP)]
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)


def test_every_term_occurs_as_a_whole_phrase_in_a_library_label() -> None:
    labels = _labels()
    missing = [entry["term"] for entry in _entries(TERM_MAP) if not _occurs(entry["term"], labels)]
    assert missing == []


def test_terms_and_equivalents_stay_below_six_words() -> None:
    """A phrase of at most five words cannot share a word 6-gram with a BoQ line (A68 §5)."""
    for entry in _entries(TERM_MAP):
        for phrase in (entry["term"], *entry["equivalents"]):
            assert len(normalize(phrase).split()) <= MAX_PHRASE_WORDS, phrase


def test_both_libraries_get_entries_in_their_own_language() -> None:
    langs = {entry["lang"] for entry in _entries(TERM_MAP)}
    assert langs == LANGS


@pytest.mark.parametrize(
    ("term", "lang", "expected"),
    [
        ("Quicklime", "en", {"chaux vive", "cl 90-q"}),
        ("Hydrated lime", "en", {"chaux éteinte", "chaux hydratée", "cl 90-s"}),
        ("hydraulically bound mixtures", "en", {"hbm", "cbgm", "grave-ciment"}),
        ("WMA", "en", {"enrobé tiède", "température abaissée", "reduced temperature"}),
        ("RAP", "en", {"agrégats d'enrobés", "ae", "ra"}),
        ("Concrete cable ducts", "en", {"caniveau à câbles"}),
        ("excavation beam", "en", {"lierne", "liernes"}),
        ("Other prefabricated concrete elements", "en", {"regard", "manhole"}),
        ("Brick", "en", {"pavé en terre cuite", "clay paver"}),
        ("enrobé tiède", "fr", {"wma", "warm mix asphalt"}),
        ("AE", "fr", {"rap", "reclaimed asphalt"}),
        ("Grave ciment", "fr", {"cbgm"}),
    ],
)
def test_the_dev_lexical_gaps_are_bridged(term: str, lang: str, expected: set[str]) -> None:
    assert expected <= set(_equivalents(term, lang))


def test_lime_classes_never_cross_between_quicklime_and_hydrated_lime() -> None:
    quick, hydrated = set(_equivalents("Quicklime", "en")), set(_equivalents("Hydrated lime", "en"))
    assert not quick & hydrated
    assert not any(value.endswith("-s") for value in quick)
    assert not any(value.endswith("-q") for value in hydrated)


def test_supplement_entries_match_the_glossary_schema() -> None:
    for entry in _entries(SUPPLEMENT):
        assert set(GLOSSARY_REQUIRED) <= set(entry), entry
        assert set(entry) <= set(GLOSSARY_KEYS), entry
        assert entry["lang"] in LANGS
        assert entry["source"] in GLOSSARY_SOURCES
        assert all(isinstance(entry[key], str) and entry[key].strip() for key in GLOSSARY_REQUIRED)
        if entry["source"] == "standard":
            assert entry.get("ref", "").strip(), entry["term"]


def test_supplement_meanings_are_one_sentence() -> None:
    for entry in _entries(SUPPLEMENT):
        meaning = entry["meaning"]
        assert "\n" not in meaning
        assert not re.search(r"[.;!?]\s", meaning), meaning
        assert not meaning.endswith(".")


def test_supplement_terms_are_short_sorted_and_new() -> None:
    entries = _entries(SUPPLEMENT)
    keys = [_sort_key(entry) for entry in entries]
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)
    default = {normalize(entry["term"]) for entry in _entries(DEFAULT_GLOSSARY)}
    for entry in entries:
        assert normalize(entry["term"]) not in default, entry["term"]
        assert len(normalize(entry["term"]).split()) <= MAX_PHRASE_WORDS, entry["term"]


@pytest.mark.parametrize(
    "needle", ["cl 90-q", "cl 90-s", "cbgm", "grave-ciment", "enrobé tiède", "regard", "lierne"]
)
def test_the_supplement_explains_the_dev_gap_terms(needle: str) -> None:
    text = " ".join(
        normalize(f"{entry['term']} {entry['meaning']}") for entry in _entries(SUPPLEMENT)
    )
    assert needle in text
