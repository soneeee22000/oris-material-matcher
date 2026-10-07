"""A68 arm C: the term map, the offline generator and the enrichment file it writes."""

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from oris_matcher.domain.library import Library, load_library
from oris_matcher.enrichment import (
    ENRICHMENT_OFF,
    GENERATOR,
    GENERATOR_VERSION,
    EnrichmentSources,
    TermMap,
    TermMapEntry,
    canonical_also,
    dump_enrichment,
    generate,
    is_enrichment_off,
    load_term_map,
    parse_enrichment,
    phrase_occurs,
    read_enrichment,
    require_enrichment_sha256,
    require_library,
    terms_missing_from,
)
from oris_matcher.settings import ConfigError

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "enrichment"
SMALL_LIBRARY = ROOT / "tests" / "fixtures" / "prompts" / "small_library.csv"
GLOBAL_LIBRARY = ROOT / "data" / "oris_materials_global.csv"
TERM_MAP = FIXTURES / "term_map_small.yaml"
SUPPLEMENT = FIXTURES / "supplement_small.yaml"
SCRIPT = ROOT / "scripts" / "enrich_library.py"
REVIEWER = "builder, 2026-10-07"
FORBIDDEN_DIRS = ("eval", "input")
GROUND_TRUTH = "boq_dataset_matched_GT"
AUDIT_PROBE = """
import json, os, runpy, sys
opened = []
def hook(event, args):
    if event == "open" and args and isinstance(args[0], (str, bytes)):
        opened.append(os.path.abspath(os.fsdecode(args[0])))
sys.addaudithook(hook)
sys.argv = sys.argv[1:]
try:
    runpy.run_path(sys.argv[0], run_name="__main__")
except SystemExit as stop:
    code = stop.code
else:
    code = 0
sys.stdout.write("\\n" + json.dumps({"code": code, "opened": opened}) + "\\n")
"""


def _small() -> Library:
    return load_library(SMALL_LIBRARY.read_bytes())


def _sources(reviewed_by: str = REVIEWER) -> EnrichmentSources:
    return EnrichmentSources(
        term_map=TERM_MAP.read_bytes(), supplement=SUPPLEMENT.read_bytes(), reviewed_by=reviewed_by
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# The term map


def test_a_standard_term_needs_a_reference() -> None:
    with pytest.raises(ValueError, match="ref"):
        TermMapEntry(term="GNT", lang="fr", equivalents=("unbound mixture",), source="standard")
    entry = TermMapEntry(term="a", lang="en", equivalents=("b",), source="library")
    assert entry.ref == ""


@pytest.mark.parametrize(
    "entry",
    [
        "{term: a, lang: de, equivalents: [b], source: library}",
        "{term: a, lang: en, equivalents: [], source: library}",
        "{term: a, lang: en, equivalents: [b], source: dev_obs}",
        "{term: a, lang: en, equivalents: [b], source: library, note: x}",
        "{term: '', lang: en, equivalents: [b], source: library}",
        '{term: a, lang: en, equivalents: ["b\\nc"], source: library}',
    ],
)
def test_the_term_map_schema_is_closed(entry: str) -> None:
    with pytest.raises(ValueError, match="term map"):
        load_term_map(f"entries:\n  - {entry}\n".encode())


def test_the_fixture_term_map_loads() -> None:
    term_map = load_term_map(TERM_MAP.read_bytes())
    assert isinstance(term_map, TermMap)
    assert {entry.source for entry in term_map.entries} == {"library", "standard", "dev_error"}


@pytest.mark.parametrize(
    ("term", "label", "expected"),
    [
        ("footings", "for footings", True),
        ("foot", "for footings", False),
        ("CONCRETE", "Concrete", True),
        ("c30/37", "C30/37", True),
        ("unbound   mixtures", "Unbound mixtures", True),
        ("rebar", "Rebars", False),
        ("béton", "Béton armé", True),
        ("", "anything", False),
    ],
)
def test_terms_match_a_label_as_a_whole_phrase(term: str, label: str, expected: bool) -> None:
    assert phrase_occurs(term, label) is expected


def test_terms_missing_from_every_library_are_reported() -> None:
    term_map = load_term_map(TERM_MAP.read_bytes())
    assert terms_missing_from(term_map, [_small()]) == ["foot"]
    extra = TermMap(
        entries=(
            *term_map.entries,
            TermMapEntry(term="footing pad", lang="en", equivalents=("x",), source="library"),
        )
    )
    assert terms_missing_from(extra, [_small()]) == ["foot", "footing pad"]


def test_also_lists_are_sorted_and_deduplicated_after_the_normaliser() -> None:
    assert canonical_also(["béton", "BPE", "bpe ", "Armatures", "armatures"]) == (
        "Armatures",
        "BPE",
        "béton",
    )
    assert canonical_also([]) == ()


# The generator


def test_the_generator_writes_every_node_with_its_also_list() -> None:
    enrichment = generate(_small(), _sources())
    assert enrichment.library_sha256 == _sha(SMALL_LIBRARY)
    assert enrichment.generator == GENERATOR
    assert enrichment.generator_version == GENERATOR_VERSION
    assert enrichment.term_map_sha256 == _sha(TERM_MAP)
    assert enrichment.glossary_supplement_sha256 == _sha(SUPPLEMENT)
    assert enrichment.reviewed_by == REVIEWER
    types = {node.type: node.also for node in enrichment.types}
    assert types == {"Aggregates": (), "Concrete": ("BPE", "béton"), "Steel": ()}
    usages = {(node.type, node.usage): node.also for node in enrichment.usages}
    assert usages[("Concrete", "for footings")] == ("semelles",)
    assert usages[("Aggregates", "Unbound mixtures")] == ("GNT", "grave non traitée")
    assert usages[("Steel", "Rebar")] == ("aciers HA", "Armatures")
    assert usages[("Concrete", "for walls")] == ()
    subtypes = {(node.type, node.usage, node.subtype): node.also for node in enrichment.subtypes}
    assert len(subtypes) == len(_small().rows)
    assert subtypes[("Concrete", "for footings", "C30/37")] == ("classe C30/37",)
    assert subtypes[("Concrete", "for footings", "")] == ()
    assert [entry.term for entry in enrichment.glossary] == [
        "HA (haute adhérence)",
        "semelle",
        "ZZZ hidden",
    ]


def test_the_output_is_byte_identical_for_identical_inputs() -> None:
    first = dump_enrichment(generate(_small(), _sources()))
    second = dump_enrichment(generate(_small(), _sources()))
    assert first == second
    assert "\r" not in first
    assert first.endswith("\n")
    parsed = yaml.safe_load(first)
    assert list(parsed) == sorted(parsed)
    assert dump_enrichment(generate(_small(), _sources("someone else"))) != first


def test_a_shuffled_library_gives_the_same_nodes() -> None:
    header, *rows = SMALL_LIBRARY.read_text(encoding="utf-8").splitlines()
    shuffled = load_library(("\n".join([header, *reversed(rows)]) + "\n").encode())
    left = generate(_small(), _sources()).model_dump()
    right = generate(shuffled, _sources()).model_dump()
    del left["library_sha256"], right["library_sha256"]
    assert left == right


def test_the_dump_round_trips_through_the_loader(tmp_path: Path) -> None:
    text = dump_enrichment(generate(_small(), _sources()))
    path = tmp_path / "small.yaml"
    path.write_bytes(text.encode("utf-8"))
    loaded = read_enrichment(path)
    assert loaded.sha256 == _sha(path)
    assert loaded.path == path
    assert loaded.content == generate(_small(), _sources())
    assert parse_enrichment(path.read_bytes(), path) == loaded


def test_an_unreadable_or_invalid_enrichment_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        read_enrichment(tmp_path / "absent.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("library_sha256: nope\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid enrichment"):
        read_enrichment(bad)


def test_an_unsorted_also_list_is_refused(tmp_path: Path) -> None:
    payload = yaml.safe_load(dump_enrichment(generate(_small(), _sources())))
    payload["types"][1]["also"] = ["béton", "BPE"]
    path = tmp_path / "edited.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ConfigError, match="sorted"):
        read_enrichment(path)


def test_the_library_and_the_bound_hash_are_checked(tmp_path: Path) -> None:
    path = tmp_path / "small.yaml"
    path.write_bytes(dump_enrichment(generate(_small(), _sources())).encode("utf-8"))
    loaded = read_enrichment(path)
    require_library(loaded, _small())
    with pytest.raises(ConfigError, match="library"):
        require_library(loaded, load_library(GLOBAL_LIBRARY.read_bytes()))
    require_enrichment_sha256(loaded, None)
    require_enrichment_sha256(loaded, loaded.sha256)
    with pytest.raises(ConfigError, match="sha256"):
        require_enrichment_sha256(loaded, "0" * 64)


def test_none_turns_enrichment_off() -> None:
    assert is_enrichment_off(Path(ENRICHMENT_OFF))
    assert not is_enrichment_off(Path("data/enrichment/global.yaml"))


# The script


def _script_args(output: Path, *extra: str) -> list[str]:
    return [
        "--library",
        str(SMALL_LIBRARY),
        "--output",
        str(output),
        "--term-map",
        str(TERM_MAP),
        "--supplement",
        str(SUPPLEMENT),
        *extra,
    ]


def _run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        cwd=ROOT,
    )


def test_the_script_writes_the_same_bytes_twice(tmp_path: Path) -> None:
    first, second = tmp_path / "a.yaml", tmp_path / "b.yaml"
    for output in (first, second):
        completed = _run_script(*_script_args(output, "--reviewed-by", REVIEWER))
        assert completed.returncode == 0, completed.stderr
    assert first.read_bytes() == second.read_bytes()
    expected = dump_enrichment(generate(_small(), _sources())).encode("utf-8")
    assert first.read_bytes() == expected


def test_the_script_refuses_a_bad_term_map(tmp_path: Path) -> None:
    bad = tmp_path / "term_map.yaml"
    bad.write_text("entries:\n  - {term: a}\n", encoding="utf-8")
    args = _script_args(tmp_path / "out.yaml")
    args[args.index(str(TERM_MAP))] = str(bad)
    completed = _run_script(*args)
    assert completed.returncode == 2
    assert "term map" in completed.stderr
    assert not (tmp_path / "out.yaml").exists()


# Isolation: the generator never reads BoQ text, labels or annotations (A68.5)


def _probe_report(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    report: dict[str, Any] = json.loads(completed.stdout.strip().splitlines()[-1])
    return report


def _forbidden(path: str) -> bool:
    given = Path(path)
    resolved = (given if given.is_absolute() else ROOT / given).resolve()
    under = any(resolved.is_relative_to(ROOT / name) for name in FORBIDDEN_DIRS)
    return under or GROUND_TRUTH in path


def test_a_run_of_the_generator_opens_nothing_under_eval_input_or_the_ground_truth(
    tmp_path: Path,
) -> None:
    output = tmp_path / "out.yaml"
    completed = subprocess.run(
        [sys.executable, "-c", AUDIT_PROBE, str(SCRIPT), *_script_args(output)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        cwd=ROOT,
    )
    report = _probe_report(completed)
    assert report["code"] in (0, None), completed.stderr
    assert output.is_file()
    opened = report["opened"]
    assert any(Path(path).resolve() == SMALL_LIBRARY for path in opened)
    assert [path for path in opened if _forbidden(path)] == []


@pytest.mark.parametrize(
    "target",
    [str(ROOT / "input" / "boq_dataset_input_en.csv"), "input/boq_dataset_input_en.csv"],
    ids=["absolute", "relative"],
)
def test_the_audit_probe_sees_a_forbidden_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    planted = tmp_path / "planted.py"
    planted.write_text(f"open({target!r}, encoding='utf-8').close()\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    completed = subprocess.run(
        [sys.executable, "-c", AUDIT_PROBE, str(planted)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        cwd=ROOT,
    )
    opened = _probe_report(completed)["opened"]
    assert all(Path(path).is_absolute() for path in opened)
    assert [path for path in opened if _forbidden(path)]


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("path", [SCRIPT, ROOT / "src" / "oris_matcher" / "enrichment.py"])
def test_the_generator_source_names_no_eval_input_or_ground_truth(path: Path) -> None:
    text = path.read_text(encoding="utf-8").replace("\\", "/")
    for token in ("eval/", "input/", GROUND_TRUTH, "annotations", "split_v1", "boq_dataset"):
        assert token not in text, token
    imported = _imported_modules(path)
    assert not any(name == "eval" or name.startswith("eval.") for name in imported)
    assert all(not name.startswith("oris_matcher.io") for name in imported)
