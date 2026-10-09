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
        ("- WMA", "en", {"enrobé tiède", "température abaissée", "reduced temperature"}),
        ("RAP", "en", {"agrégats d'enrobés", "ae", "ra"}),
        ("Concrete cable ducts", "en", {"caniveau à câbles"}),
        ("excavation beam", "en", {"lierne", "liernes"}),
        ("Other prefabricated concrete elements", "en", {"regard", "manhole"}),
        ("Brick (clay)", "en", {"pavé en terre cuite", "clay paver"}),
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


def _also(label: str) -> set[str]:
    """Return the also list the A68 §2 generator gives a node with this label.

    Every entry whose term occurs in the label as a whole phrase contributes, whatever its
    language, as the generator matches it.
    """
    found: set[str] = set()
    for entry in _entries(TERM_MAP):
        if _occurs(entry["term"], [normalize(label)]):
            found |= {normalize(value) for value in entry["equivalents"]}
    return found


WMA_MIXTURE_VOCABULARY = {
    "enrobé tiède",
    "tiède",
    "température abaissée",
    "basse température",
    "reduced temperature",
}
WMA_ADDITIVE_LABELS = (
    "For chemical process technologies (WMA)",
    "For foaming systems technologies (WMA)",
    "For organic additives technologies (WMA)",
    "Additif pour WMA (organique)",
)
BITUMINOUS_WASTE = "17 03 02 - Bituminous mixtures other than those mentioned in 17 03 01"
OTHER_PREFAB = "Other prefabricated concrete elements (i.e., curbs, edges, trenches)"


@pytest.mark.parametrize(
    ("label", "forbidden"),
    [
        ("Sheet piles", {"pieu", "pieux"}),
        ("Concrete for piers", {"pile", "piles"}),
        ("Concrete cable ducts", {"caniveau béton"}),
        ("Water resisting admixtures", {"eau de gâchage"}),
        (BITUMINOUS_WASTE, {"enrobés bitumineux"}),
        ("Reclaimed Asphalt", {"enrobés bitumineux", "mélanges bitumineux"}),
        ("for use in asphalt mixtures", {"enrobés bitumineux", "mélanges bitumineux"}),
        ("Additives for asphalt", {"enrobés bitumineux", "mélanges bitumineux"}),
        *((label, WMA_MIXTURE_VOCABULARY) for label in WMA_ADDITIVE_LABELS),
        ("For foaming systems technologies (WMA)", {"bitume moussé", "mousse de bitume"}),
        ("Asphalt Concrete (AC) - HMA 30% RAP", {"fraisats", "fraisats d'enrobés"}),
        ("Stone Mastic Asphalt (SMA) - WMA 10% RAP", {"fraisats", "fraisats d'enrobés"}),
        ("Cellular concrete mortar", {"béton cellulaire"}),
    ],
)
def test_terms_do_not_bleed_into_sibling_rows(label: str, forbidden: set[str]) -> None:
    assert not forbidden & _also(label)


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Concrete for piles", {"pieu", "pieux"}),
        ("Concrete for piers", {"pile de pont", "piles de pont"}),
        ("Sheet piles", {"palplanches"}),
        ("Concrete cable ducts", {"caniveau à câbles", "cable trough"}),
        (OTHER_PREFAB, {"bordurettes", "caniveaux", "regard"}),
        ("Precast Concrete sleeper for railway", {"traverse", "traverses"}),
        ("Emulsion d'enrobage 65%", {"coating emulsion"}),
        ("Water", {"eau"}),
        (BITUMINOUS_WASTE, {"mélanges bitumineux", "déchets d'enrobés"}),
        ("asphalt mixture for surface course", {"enrobés bitumineux", "mélanges bitumineux"}),
        ("Asphalt", {"enrobé", "enrobés"}),
        ("Asphalt Concrete (AC) - WMA", WMA_MIXTURE_VOCABULARY),
        ("Mastic Asphalt (MA) - WMA 10% RAP", WMA_MIXTURE_VOCABULARY),
        ("For foaming systems technologies (WMA)", {"moussage", "additif wma"}),
        ("Asphalt Concrete (AC) - HMA 30% RAP", {"ae", "agrégats d'enrobés", "ra"}),
        ("Reclaimed Asphalt Planings (RAP)", {"fraisats", "fraisats d'enrobés"}),
        ("Reclaimed Asphalt", {"fraisats d'enrobés"}),
        ("Cellular concrete mortar", {"mortier cellulaire", "béton mousse"}),
    ],
)
def test_rows_keep_their_specific_bridges(label: str, expected: set[str]) -> None:
    assert expected <= _also(label)


DEV_MOTIVATED_EQUIVALENTS = {
    "tiède",
    "enrobé tiède",
    "enrobés tièdes",
    "température abaissée",
    "basse température",
    "reduced temperature",
    "pavé en terre cuite",
    "pavés en terre cuite",
    "clay paver",
    "regard",
    "manhole",
    "caniveau à câbles",
    "lierne",
    "liernes",
}
DEV_MOTIVATED_SUPPLEMENT_TERMS = {
    "clay paver",
    "pavé en terre cuite",
    "manhole",
    "regard",
    "enrobé tiède",
    "température abaissée",
    "reduced temperature",
}


def test_dev_motivated_equivalents_sit_only_in_dev_error_entries() -> None:
    """A68 §6: the G4 dev_error exclusion must see every bridge the dev evidence motivated."""
    seen: set[str] = set()
    for entry in _entries(TERM_MAP):
        dev_motivated = DEV_MOTIVATED_EQUIVALENTS & {normalize(v) for v in entry["equivalents"]}
        seen |= dev_motivated
        if dev_motivated:
            assert entry["source"] == "dev_error", (entry["term"], dev_motivated)
    assert seen == DEV_MOTIVATED_EQUIVALENTS


def test_dev_motivated_supplement_entries_are_tagged_dev_error() -> None:
    tags = {normalize(entry["term"]): entry["source"] for entry in _entries(SUPPLEMENT)}
    tagged = {term: tags[normalize(term)] for term in DEV_MOTIVATED_SUPPLEMENT_TERMS}
    assert tagged == dict.fromkeys(DEV_MOTIVATED_SUPPLEMENT_TERMS, "dev_error")


def test_standard_supplement_meanings_carry_no_library_classification() -> None:
    """A standard entry states what its norm fixes, not where this library files the thing."""
    for entry in _entries(SUPPLEMENT):
        if entry["source"] == "standard":
            assert not re.search(r"rather than|classed with|so a ", entry["meaning"]), entry["term"]


def test_the_manhole_meaning_does_not_list_the_units_a_boq_line_names() -> None:
    manhole = next(e for e in _entries(SUPPLEMENT) if normalize(e["term"]) == "manhole")
    assert not re.search(r"\b(base|ring|rings|cone)\b", manhole["meaning"])


def test_en_13230_is_cited_only_for_concrete_sleepers() -> None:
    labels = _labels()
    for entry in _entries(TERM_MAP):
        if "13230" in entry["ref"]:
            hits = [label for label in labels if _occurs(entry["term"], [label])]
            assert hits, entry["term"]
            assert all("concrete" in label for label in hits), entry["term"]


@pytest.mark.parametrize(
    ("path", "term"),
    [
        (SUPPLEMENT, "GTLH"),
        (TERM_MAP, "Matériaux traités aux Liant hydraulique"),
    ],
)
def test_the_hbm_family_cites_the_whole_en_14227_series(path: Path, term: str) -> None:
    entry = next(e for e in _entries(path) if normalize(e["term"]) == normalize(term))
    assert "EN 14227 series" in entry["ref"]
