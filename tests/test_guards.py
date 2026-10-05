import ast
import csv
import hashlib
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from oris_matcher.domain.normalize import normalize

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SPLIT_PATH = ROOT / "eval" / "split_v1.json"
SLICE_PATH = ROOT / "eval" / "slice_v1.json"
GLOSSARY_PATH = SRC / "oris_matcher" / "prompts" / "v1" / "glossary.yaml"
LIBRARIES = (ROOT / "data" / "oris_materials_global.csv", ROOT / "data" / "oris_materials_fr.csv")
BOQ_INPUTS = (
    ROOT / "input" / "boq_dataset_input_en.csv",
    ROOT / "input" / "boq_dataset_input_fr.csv",
)
LIBRARY_COLUMNS = ("material_type", "material_usage", "material_subtype")

SPLIT_SHA256 = "c9a6d1f0aaafdd9f3b7499bf50f59f786f00270c3350e7d5cad7982b855039bd"
SLICE_SHA256 = "b41cf9512032cfbd35ccce660ca03e18bc3b3c251b899d1e9cc229327be51f2f"
SLICE_IDS_SHA256 = "248da575dde907de7df6e6e112b22f4930a3cafe3e5b2526cebc64aad6d565a7"
LEAKAGE_TOKENS = (
    "boq_dataset_matched_GT",
    "annotations/",
    "blank_line_classes",
    "split_v1",
    "item_ids_lockbox",
)
MIN_GUARDED_LENGTH = 7
OVERLAP_NGRAM = 6
WORD_RE = re.compile(r"\w+")
DOCSTRING_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _src_files() -> Iterator[Path]:
    for path in sorted(SRC.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            yield path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- (a) leakage guard -----------------------------------------------------------------


def test_src_has_files_to_guard() -> None:
    assert any(path.suffix == ".py" for path in _src_files())


@pytest.mark.parametrize("token", LEAKAGE_TOKENS)
def test_src_never_references_ground_truth_annotations_or_the_split(token: str) -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path in _src_files()
        if token in path.read_text(encoding="utf-8").replace("\\", "/")
    ]
    assert offenders == []


# --- (b) global-string guard -----------------------------------------------------------


def _library_strings() -> set[str]:
    strings: set[str] = set()
    for path in LIBRARIES:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for record in csv.DictReader(handle):
                strings |= {record[name] for name in LIBRARY_COLUMNS}
    return {value for value in strings if len(value) >= MIN_GUARDED_LENGTH}


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, DOCSTRING_OWNERS) or not node.body:
            continue
        first = node.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            ids.add(id(first.value))
    return ids


def _code_strings(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if path.suffix != ".py":
        return [text]
    tree = ast.parse(text)
    docstrings = _docstring_ids(tree)
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_library_strings_are_loaded() -> None:
    assert len(_library_strings()) > 100


def test_no_library_string_is_hard_coded_in_src() -> None:
    guarded = _library_strings()
    offenders = sorted(
        (str(path.relative_to(ROOT)), value)
        for path in _src_files()
        for text in _code_strings(path)
        for value in guarded
        if value in text
    )
    assert offenders == []


def test_global_string_guard_catches_a_literal(tmp_path: Path) -> None:
    value = next(iter(sorted(_library_strings())))
    module = tmp_path / "planted.py"
    module.write_text(f'"""Docstring {value}."""\nNAME = {value!r}\n', encoding="utf-8")
    assert any(value in text for text in _code_strings(module))
    docstring_only = tmp_path / "docstring_only.py"
    docstring_only.write_text(f'"""Docstring {value}."""\n', encoding="utf-8")
    assert not any(value in text for text in _code_strings(docstring_only))


# --- (c) split hash --------------------------------------------------------------------


def test_split_file_hash_is_frozen() -> None:
    assert _sha256(SPLIT_PATH) == SPLIT_SHA256


# --- (d) dev slice ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def slice_payload() -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(SLICE_PATH.read_text(encoding="utf-8"))
    return payload


def test_slice_file_hash_is_frozen() -> None:
    assert _sha256(SLICE_PATH) == SLICE_SHA256


def test_slice_ids_hash_is_frozen(slice_payload: dict[str, Any]) -> None:
    assert slice_payload["ids_sha256"] == SLICE_IDS_SHA256
    canonical = "\n".join(sorted(slice_payload["item_ids"])) + "\n"
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == SLICE_IDS_SHA256


def test_slice_ids_are_dev_only(slice_payload: dict[str, Any]) -> None:
    split = json.loads(SPLIT_PATH.read_text(encoding="utf-8"))
    ids = set(slice_payload["item_ids"])
    assert ids <= set(split["item_ids_dev"])
    assert not ids & set(split["item_ids_lockbox"])


def test_frozen_eval_files_are_byte_exact_in_git() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    for path in ("eval/split_v1.json", "eval/slice_v1.json"):
        assert f"{path} -text" in attributes


# --- (e) glossary 6-gram overlap -------------------------------------------------------


def _ngrams(text: str) -> set[tuple[str, ...]]:
    words = WORD_RE.findall(normalize(text))
    return {
        tuple(words[start : start + OVERLAP_NGRAM])
        for start in range(len(words) - OVERLAP_NGRAM + 1)
    }


def _boq_cell_ngrams() -> set[tuple[str, ...]]:
    grams: set[tuple[str, ...]] = set()
    for path in BOQ_INPUTS:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for record in csv.reader(handle):
                for value in record:
                    grams |= _ngrams(value)
                grams |= _ngrams(" ".join(record))
    return grams


def _glossary_texts() -> list[str]:
    payload = yaml.safe_load(GLOSSARY_PATH.read_text(encoding="utf-8"))
    texts: list[str] = []
    for entry in payload["entries"]:
        values = [str(value) for value in entry.values() if isinstance(value, str)]
        texts += [*values, " ".join(values)]
    return texts


def test_every_glossary_field_shares_no_six_gram_with_any_boq_cell() -> None:
    boq = _boq_cell_ngrams()
    assert boq
    overlaps = [text for text in _glossary_texts() if _ngrams(text) & boq]
    assert overlaps == []


def test_six_gram_check_catches_copied_item_text() -> None:
    boq = _boq_cell_ngrams()
    sample = next(gram for gram in sorted(boq))
    assert _ngrams("glossary says " + " ".join(sample)) & boq
