"""Prompt v1 with an arm C enrichment loaded (DESIGN.md A68.3).

The enriched library block is the v1 block with two additions: each type, usage and row line
of the coded tree whose node has a non-empty ``also`` list ends with `` [also: a; b; c]``, in
both renderings, and the glossary holds the supplement's entries next to the default ones.
Every other byte is the plain rendering's, and without an enrichment, or for the B2 profile,
the plain rendering is returned unchanged. This module is kept apart from ``render.py`` so the
plain renderer's source, and with it every pinned prompt version, never moves.
"""

import dataclasses
from collections.abc import Mapping, Sequence
from string import Template

from oris_matcher.domain.boq import BoqLine
from oris_matcher.domain.library import CODE_SEPARATOR, Library
from oris_matcher.enrichment import Enrichment
from oris_matcher.llm.base import LLMRequest, SystemBlock
from oris_matcher.prompts.v1.render import (
    LIBRARY_TEMPLATE,
    VOCABULARY_TEMPLATE,
    Glossary,
    Profile,
    PromptVariant,
    Rendering,
    build_request,
    load_glossary,
    read_template,
    render_coded_tree,
    render_glossary,
    render_system_blocks,
)

ALSO_OPEN = " [also: "
ALSO_SEPARATOR = "; "
ALSO_CLOSE = "]"
TREE_LINE_SEPARATOR = "\n"
LABEL_SEPARATOR = " "
TEMPLATE_END = "\n"
NO_ALSO: tuple[str, ...] = ()


def renders_enrichment(variant: PromptVariant, enrichment: Enrichment | None) -> bool:
    """Tell whether a pass renders an enrichment: a loaded one, on the v1 profile only.

    Args:
        variant: Profile and rendering of the pass.
        enrichment: The run's enrichment, or None.

    Returns:
        True for a v1 pass with an enrichment; B2 never renders one.

    """
    return enrichment is not None and variant.profile == Profile.V1


def also_suffix(also: Sequence[str]) -> str:
    """Return the suffix a tree line gets for its ``also`` list.

    Args:
        also: The node's equivalents.

    Returns:
        `` [also: a; b; c]``, or nothing for an empty list.

    """
    if not also:
        return ""
    return f"{ALSO_OPEN}{ALSO_SEPARATOR.join(also)}{ALSO_CLOSE}"


def code_also(library: Library, enrichment: Enrichment) -> dict[str, tuple[str, ...]]:
    """Map each type code, usage code and row code of a library to its node's ``also`` list.

    Args:
        library: The loaded library.
        enrichment: The enrichment of that library.

    Returns:
        ``Txx``, ``Txx.Uyy`` and ``Txx.Uyy.Szz`` -> the equivalents of the node with that
        label; empty for a node the enrichment does not list.

    """
    types = {node.type: node.also for node in enrichment.types}
    usages = {(node.type, node.usage): node.also for node in enrichment.usages}
    rows = {(node.type, node.usage, node.subtype): node.also for node in enrichment.subtypes}
    mapped: dict[str, tuple[str, ...]] = {}
    for row in library.rows:
        parent = (row.material_type, row.material_usage)
        mapped[row.code.split(CODE_SEPARATOR, 1)[0]] = types.get(row.material_type, NO_ALSO)
        mapped[row.parent_code] = usages.get(parent, NO_ALSO)
        mapped[row.code] = rows.get((*parent, row.material_subtype), NO_ALSO)
    return mapped


def _line_code(line: str) -> str:
    """Return the code a coded-tree line starts with, after its indent."""
    return line.lstrip(LABEL_SEPARATOR).split(LABEL_SEPARATOR, 1)[0]


def _with_suffixes(tree: str, also: Mapping[str, tuple[str, ...]]) -> str:
    """Append each tree line's ``also`` suffix, looked up by the line's code."""
    lines = tree.split(TREE_LINE_SEPARATOR)
    suffixed = (line + also_suffix(also.get(_line_code(line), NO_ALSO)) for line in lines)
    return TREE_LINE_SEPARATOR.join(suffixed)


def render_enriched_tree(library: Library, rendering: Rendering, enrichment: Enrichment) -> str:
    """Render the coded tree with each node's ``also`` list (A68.3).

    Args:
        library: The loaded library.
        rendering: Row order.
        enrichment: The enrichment of that library.

    Returns:
        The plain coded tree, each line whose node has equivalents ending in ``[also: ...]``.

    """
    return _with_suffixes(render_coded_tree(library, rendering), code_also(library, enrichment))


def enriched_glossary(enrichment: Enrichment) -> Glossary:
    """Return the default glossary with the supplement's entries added.

    Args:
        enrichment: The run's enrichment.

    Returns:
        The glossary rendered in an enriched prompt.

    """
    return Glossary(entries=(*load_glossary().entries, *enrichment.glossary))


def _fill(name: str, **values: str) -> str:
    """Substitute values into a template, trimming the file's final newline."""
    return Template(read_template(name)).substitute(values).removesuffix(TEMPLATE_END)


def render_enriched_system_blocks(
    library: Library, variant: PromptVariant, enrichment: Enrichment | None
) -> tuple[SystemBlock, SystemBlock]:
    """Render a pass's two system blocks, enriched when the pass renders an enrichment.

    Args:
        library: The loaded library.
        variant: Profile and rendering of the pass.
        enrichment: The run's enrichment, or None.

    Returns:
        The plain blocks when the pass renders none; else the vocabulary block and the
        library block with the supplemented glossary and the enriched tree.

    """
    if enrichment is None or not renders_enrichment(variant, enrichment):
        return render_system_blocks(library, variant)
    text = _fill(
        LIBRARY_TEMPLATE,
        glossary=render_glossary(enriched_glossary(enrichment)),
        library=render_enriched_tree(library, variant.rendering, enrichment),
    )
    vocabulary = SystemBlock(text=_fill(VOCABULARY_TEMPLATE), cache=False)
    return vocabulary, SystemBlock(text=text, cache=True)


def build_enriched_request(  # noqa: PLR0913
    lines: Sequence[BoqLine],
    library: Library,
    variant: PromptVariant,
    enrichment: Enrichment | None,
    *,
    model: str,
    max_tokens: int,
) -> LLMRequest:
    """Build one model call, its system blocks enriched when the pass renders an enrichment.

    Args:
        lines: The batch's lines, in batch order.
        library: The loaded library.
        variant: Profile and rendering of the pass.
        enrichment: The run's enrichment, or None.
        model: Requested model snapshot id.
        max_tokens: Output token cap.

    Returns:
        ``build_request``'s request, with the enriched system blocks when they apply.

    Raises:
        ValueError: Two lines share a transport id.

    """
    request = build_request(lines, library, variant, model=model, max_tokens=max_tokens)
    if not renders_enrichment(variant, enrichment):
        return request
    blocks = render_enriched_system_blocks(library, variant, enrichment)
    return dataclasses.replace(request, system_blocks=blocks)
