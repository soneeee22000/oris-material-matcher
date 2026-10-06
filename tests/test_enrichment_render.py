"""A68.3: an enrichment adds ``[also: ...]`` to the coded tree and its own prompt version."""

import dataclasses
from pathlib import Path

import pytest

from oris_matcher.domain.boq import BoqLine, LineKind, SectionHeader, make_line_id
from oris_matcher.domain.library import Library, load_library
from oris_matcher.enrichment import EnrichmentSources, LoadedEnrichment, dump_enrichment, generate
from oris_matcher.enrichment import parse_enrichment as parse
from oris_matcher.prompts.v1 import enriched
from oris_matcher.prompts.v1.enriched import (
    build_enriched_request,
    render_enriched_system_blocks,
    render_enriched_tree,
)
from oris_matcher.prompts.v1.render import (
    B2,
    CANONICAL_V1,
    REVERSE_V1,
    VARIANTS,
    PromptVariant,
    Rendering,
    build_request,
    render_system_blocks,
)
from oris_matcher.prompts.v1.version import prompt_version, version_components

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "enrichment"
SMALL_LIBRARY = ROOT / "tests" / "fixtures" / "prompts" / "small_library.csv"
MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 2048


def _small() -> Library:
    return load_library(SMALL_LIBRARY.read_bytes())


def _enrichment(reviewed_by: str = "builder") -> LoadedEnrichment:
    sources = EnrichmentSources(
        term_map=(FIXTURES / "term_map_small.yaml").read_bytes(),
        supplement=(FIXTURES / "supplement_small.yaml").read_bytes(),
        reviewed_by=reviewed_by,
    )
    text = dump_enrichment(generate(_small(), sources))
    return parse(text.encode("utf-8"), Path("small.yaml"))


def _expected(rendering: Rendering) -> str:
    golden = FIXTURES / f"small_tree_{rendering.value}_enriched.txt"
    return golden.read_text(encoding="utf-8").replace("\r\n", "\n").removesuffix("\n")


def _line() -> BoqLine:
    return BoqLine(
        position=1,
        line_id=make_line_id(1, "01.1", "fill", ""),
        item_no="01.1",
        short="fill",
        long="",
        unit="m3",
        qty="1",
        kind=LineKind.ITEM,
        section_path=(SectionHeader("01", "Earthworks"),),
        extra=(),
        raw_row=(),
    )


@pytest.mark.parametrize("rendering", list(Rendering))
def test_the_tree_appends_also_lists_in_both_renderings(rendering: Rendering) -> None:
    tree = render_enriched_tree(_small(), rendering, _enrichment().content)
    assert tree == _expected(rendering)


def test_an_enrichment_with_no_also_lists_renders_the_plain_tree() -> None:
    content = _enrichment().content
    bare = content.model_copy(
        update={
            "types": tuple(node.model_copy(update={"also": ()}) for node in content.types),
            "usages": tuple(node.model_copy(update={"also": ()}) for node in content.usages),
            "subtypes": tuple(node.model_copy(update={"also": ()}) for node in content.subtypes),
        }
    )
    plain = render_system_blocks(_small(), CANONICAL_V1)[1].text
    library_part = plain.split("<library>\n", 1)[1]
    assert library_part in render_enriched_system_blocks(_small(), CANONICAL_V1, bare)[1].text


@pytest.mark.parametrize("variant", (CANONICAL_V1, REVERSE_V1), ids=lambda v: v.name)
def test_the_glossary_block_adds_the_supplement(variant: PromptVariant) -> None:
    library = _small()
    vocabulary, block = render_enriched_system_blocks(library, variant, _enrichment().content)
    base_vocabulary, base_block = render_system_blocks(library, variant)
    assert vocabulary == base_vocabulary
    assert block.cache is True
    assert "- HA (haute adhérence) [fr]: high-bond reinforcing steel bars" in block.text
    assert "- semelle [fr]: footing of a foundation" in block.text
    assert "ZZZ hidden" not in block.text
    for line in (
        base_block.text.split("<glossary>\n", 1)[1].split("\n</glossary>", 1)[0].split("\n")
    ):
        assert line in block.text
    assert _expected(variant.rendering) in block.text


def test_b2_and_no_enrichment_render_exactly_as_before() -> None:
    library, content = _small(), _enrichment().content
    assert render_enriched_system_blocks(library, B2, content) == render_system_blocks(library, B2)
    for variant in VARIANTS:
        assert render_enriched_system_blocks(library, variant, None) == render_system_blocks(
            library, variant
        )


def test_the_enriched_request_differs_only_in_its_system_blocks() -> None:
    library, content = _small(), _enrichment().content
    base = build_request([_line()], library, CANONICAL_V1, model=MODEL, max_tokens=MAX_TOKENS)
    rich = build_enriched_request(
        [_line()], library, CANONICAL_V1, content, model=MODEL, max_tokens=MAX_TOKENS
    )
    assert rich.system_blocks == render_enriched_system_blocks(library, CANONICAL_V1, content)
    assert dataclasses.replace(rich, system_blocks=base.system_blocks) == base
    assert rich.sha256() != base.sha256()
    plain = build_enriched_request(
        [_line()], library, CANONICAL_V1, None, model=MODEL, max_tokens=MAX_TOKENS
    )
    assert plain == base
    b2 = build_enriched_request([_line()], library, B2, content, model=MODEL, max_tokens=MAX_TOKENS)
    assert b2 == build_request([_line()], library, B2, model=MODEL, max_tokens=MAX_TOKENS)


# The prompt version (§9.3)


def test_without_enrichment_every_version_is_unchanged() -> None:
    for variant in VARIANTS:
        assert prompt_version(variant, None) == prompt_version(variant)
        assert version_components(variant, None) == version_components(variant)


def test_the_enrichment_hash_gives_each_v1_pass_its_own_version() -> None:
    first, second = _enrichment("a"), _enrichment("b")
    assert first.sha256 != second.sha256
    for variant in (CANONICAL_V1, REVERSE_V1):
        base = prompt_version(variant)
        rich = prompt_version(variant, first.sha256)
        assert rich != base
        assert prompt_version(variant, second.sha256) not in {base, rich}
        assert first.sha256 in version_components(variant, first.sha256)
    assert prompt_version(CANONICAL_V1, first.sha256) != prompt_version(REVERSE_V1, first.sha256)
    assert prompt_version(B2, first.sha256) == prompt_version(B2)


def test_the_enriched_renderer_source_enters_the_version(monkeypatch: pytest.MonkeyPatch) -> None:
    sha = _enrichment().sha256
    before = prompt_version(CANONICAL_V1, sha)
    plain = prompt_version(CANONICAL_V1)
    original = enriched.render_enriched_tree
    monkeypatch.setattr(
        "oris_matcher.prompts.v1.version.enriched_renderer_source",
        lambda: "changed",
    )
    assert prompt_version(CANONICAL_V1, sha) != before
    assert prompt_version(CANONICAL_V1) == plain
    assert enriched.render_enriched_tree is original
