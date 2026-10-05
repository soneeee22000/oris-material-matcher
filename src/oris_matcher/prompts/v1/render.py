"""Prompt v1 renderer: system blocks, the coded library tree and the user payload (DESIGN.md §9.4).

Every rendering is canonical: rows in display-code order (or its one pre-registered reverse),
templates read with LF line endings, no timestamps, so each cached prefix is byte-stable.
Only the description, the unit and the section path of a line are rendered; never its quantity.
The B2 profile (§10.5) renders the raw library as CSV, with no glossary and no section path.
"""

import csv
import functools
import io
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from string import Template
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError, model_validator

from oris_matcher.domain.boq import BoqLine
from oris_matcher.domain.library import CODE_SEPARATOR, Library, LibraryRow
from oris_matcher.llm.base import LLMRequest, SystemBlock, canonical_json
from oris_matcher.prompts.v1.schema import output_json_schema

TEXT_ENCODING = "utf-8"
TEMPLATES_DIR = "templates"
GLOSSARY_FILE = "glossary.yaml"
VOCABULARY_TEMPLATE = "system_vocabulary.txt"
LIBRARY_TEMPLATE = "system_library.txt"
B2_LIBRARY_TEMPLATE = "system_library_b2.txt"
USER_TEMPLATE = "user.txt"
INDENT = "  "
PATH_SEPARATOR = " > "
HEADER_PART_SEPARATOR = " "
BLANK_LEAF_LABEL = "(no subtype)"
TRANSPORT_ID_PREFIX = "L"
DEFAULT_PROVIDER = "anthropic"
TEMPERATURE = 0.0
CSV_LINE_END = "\n"
B2_COLUMNS = ("code", "material_type", "material_usage", "material_subtype")
LINE_BREAKS = "\r\n\t\v\f" + "".join(map(chr, (0x85, 0x2028, 0x2029)))
LINE_BREAK_RE = re.compile(f"[{re.escape(LINE_BREAKS)}]")
EXCLUDED_GLOSSARY_SOURCES = frozenset({"lockbox_obs"})
REFERENCED_GLOSSARY_SOURCES = frozenset({"standard"})
JSON_MARKUP_CHARS = "<>&"
JSON_MARKUP_ESCAPES = str.maketrans({char: f"\\u{ord(char):04x}" for char in JSON_MARKUP_CHARS})

NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]
GlossarySource = Literal["standard", "library", "dev_obs", "dev_error", "lockbox_obs"]


class Profile(StrEnum):
    """What a prompt shows: the full v1 prompt, or the raw B2 baseline (§10.5)."""

    V1 = "v1"
    B2 = "b2"


class Rendering(StrEnum):
    """Row order of the library block: canonical, or the one fixed reverse alternative (A46)."""

    CANONICAL = "canonical"
    REVERSE = "reverse"


@dataclass(frozen=True)
class PromptVariant:
    """One pass's prompt: a profile rendered in one row order.

    Attributes:
        profile: Full v1 prompt or the B2 baseline.
        rendering: Row order of the library block.

    """

    profile: Profile
    rendering: Rendering

    @property
    def name(self) -> str:
        """The variant's stable name, e.g. ``v1:canonical``."""
        return f"{self.profile.value}:{self.rendering.value}"

    @property
    def templates(self) -> tuple[str, ...]:
        """The template files the variant renders, in block order."""
        library = LIBRARY_TEMPLATE if self.profile == Profile.V1 else B2_LIBRARY_TEMPLATE
        return VOCABULARY_TEMPLATE, library, USER_TEMPLATE

    @property
    def uses_glossary(self) -> bool:
        """Whether the glossary is rendered; B2 has none."""
        return self.profile == Profile.V1


CANONICAL_V1 = PromptVariant(Profile.V1, Rendering.CANONICAL)
REVERSE_V1 = PromptVariant(Profile.V1, Rendering.REVERSE)
B2 = PromptVariant(Profile.B2, Rendering.CANONICAL)
VARIANTS = (CANONICAL_V1, REVERSE_V1, B2)


class GlossaryEntry(BaseModel):
    """One glossary entry with its provenance tag (§10.8).

    Attributes:
        term: The domain term as it appears in BoQ text.
        meaning: Its general meaning, in English.
        lang: Language of the term.
        source: Provenance tag.
        ref: Public source of the entry, required for a standard entry; not rendered.
        false_friend: The word, as it can appear in a library, that this entry tells apart
            from the term; empty when the entry is not a false friend. Not rendered.

    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    term: NonEmptyStr
    meaning: NonEmptyStr
    lang: Literal["en", "fr"]
    source: GlossarySource
    ref: str = ""
    false_friend: str = ""

    @model_validator(mode="after")
    def _standard_entries_cite_a_source(self) -> Self:
        """Reject a standard entry with no public reference (§10.8 provenance)."""
        if self.source in REFERENCED_GLOSSARY_SOURCES and not self.ref.strip():
            raise ValueError(f"glossary entry {self.term!r} is {self.source} but has no ref")
        return self


class Glossary(BaseModel):
    """The glossary file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entries: tuple[GlossaryEntry, ...]


def _resource_text(*parts: str) -> str:
    """Read a packaged text file with LF line endings, whatever the checkout did."""
    resource = resources.files(__package__).joinpath(*parts)
    return resource.read_text(encoding=TEXT_ENCODING).replace("\r\n", "\n")


@functools.cache
def read_template(name: str) -> str:
    """Return one template's text.

    Args:
        name: Template file name, e.g. ``VOCABULARY_TEMPLATE``.

    Returns:
        The template text, LF line endings.

    """
    return _resource_text(TEMPLATES_DIR, name)


@functools.cache
def glossary_text() -> str:
    """Return the packaged glossary file's text, LF line endings."""
    return _resource_text(GLOSSARY_FILE)


def load_glossary(text: str | None = None) -> Glossary:
    """Parse and validate a glossary.

    Args:
        text: YAML text; the packaged ``glossary.yaml`` when None.

    Returns:
        The validated glossary.

    Raises:
        ValueError: The YAML is invalid or an entry is malformed or untagged.

    """
    try:
        return Glossary.model_validate(yaml.safe_load(glossary_text() if text is None else text))
    except (yaml.YAMLError, ValidationError) as error:
        raise ValueError(f"invalid glossary: {error}") from error


def render_glossary(glossary: Glossary) -> str:
    """Render the glossary as sorted bullet lines, leaving out lockbox observations.

    Args:
        glossary: The glossary.

    Returns:
        One ``- term [lang]: meaning`` line per entry, sorted by term.

    """
    entries = (e for e in glossary.entries if e.source not in EXCLUDED_GLOSSARY_SOURCES)
    ordered = sorted(entries, key=lambda entry: (entry.term.casefold(), entry.term))
    return "\n".join(f"- {entry.term} [{entry.lang}]: {entry.meaning}" for entry in ordered)


def ordered_rows(library: Library, rendering: Rendering) -> tuple[LibraryRow, ...]:
    """Return the library rows in a rendering's order; codes never change.

    Args:
        library: The loaded library, rows in display-code order.
        rendering: Canonical or reverse.

    Returns:
        The rows in sorted code order, or in reverse sorted code order.

    """
    rows = tuple(sorted(library.rows, key=lambda row: row.code))
    return rows[::-1] if rendering == Rendering.REVERSE else rows


def _display(text: str) -> str:
    """Keep a library string on one rendered line."""
    return LINE_BREAK_RE.sub(" ", text)


def render_coded_tree(library: Library, rendering: Rendering) -> str:
    """Render the library as a coded tree of types, usages and rows.

    Args:
        library: The loaded library.
        rendering: Row order.

    Returns:
        One line per type (``Txx``), usage (``Txx.Uyy``) and row (``Txx.Uyy.Szz``), indented.

    """
    lines: list[str] = []
    last_type = last_parent = ""
    for row in ordered_rows(library, rendering):
        type_code = row.code.split(CODE_SEPARATOR, 1)[0]
        if type_code != last_type:
            lines.append(f"{type_code} {_display(row.material_type)}")
            last_type = type_code
        if row.parent_code != last_parent:
            lines.append(f"{INDENT}{row.parent_code} {_display(row.material_usage)}")
            last_parent = row.parent_code
        label = BLANK_LEAF_LABEL if row.is_blank_leaf else _display(row.material_subtype)
        lines.append(f"{INDENT * 2}{row.code} {label}")
    return "\n".join(lines)


def render_raw_library(library: Library, rendering: Rendering) -> str:
    """Render the library as raw CSV rows with their codes, for the B2 baseline.

    Args:
        library: The loaded library.
        rendering: Row order.

    Returns:
        CSV text with a header, LF line endings, no trailing newline.

    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator=CSV_LINE_END)
    writer.writerow(B2_COLUMNS)
    for row in ordered_rows(library, rendering):
        writer.writerow((row.code, row.material_type, row.material_usage, row.material_subtype))
    return buffer.getvalue().removesuffix(CSV_LINE_END)


def _fill(name: str, **values: str) -> str:
    """Substitute values into a template, trimming the file's final newline."""
    return Template(read_template(name)).substitute(values).removesuffix("\n")


def render_system_blocks(
    library: Library, variant: PromptVariant
) -> tuple[SystemBlock, SystemBlock]:
    """Render the two system blocks; a cache breakpoint follows the library block.

    Args:
        library: The loaded library.
        variant: Profile and rendering of the pass.

    Returns:
        The decision-vocabulary block, then the glossary and library block.

    """
    vocabulary = SystemBlock(text=_fill(VOCABULARY_TEMPLATE), cache=False)
    if variant.profile == Profile.B2:
        tree = render_raw_library(library, variant.rendering)
        return vocabulary, SystemBlock(text=_fill(B2_LIBRARY_TEMPLATE, library=tree), cache=True)
    text = _fill(
        LIBRARY_TEMPLATE,
        glossary=render_glossary(load_glossary()),
        library=render_coded_tree(library, variant.rendering),
    )
    return vocabulary, SystemBlock(text=text, cache=True)


def section_path_text(line: BoqLine) -> str:
    """Render a line's section path, outermost header first.

    Args:
        line: The BoQ line.

    Returns:
        Each header's raw item number and text, joined by ``PATH_SEPARATOR``; empty when the
        line has no section context.

    """
    headers = (
        HEADER_PART_SEPARATOR.join(part for part in (header.item_no, header.text) if part)
        for header in line.section_path
    )
    return PATH_SEPARATOR.join(headers)


def transport_id(line: BoqLine) -> str:
    """Return the line's id inside a batch, ``L<position>``."""
    return f"{TRANSPORT_ID_PREFIX}{line.position}"


def line_record(line: BoqLine, profile: Profile) -> dict[str, str]:
    """Build the JSON object sent for one line; it never holds the quantity.

    Args:
        line: The BoQ line.
        profile: V1 sends the section path; B2 does not.

    Returns:
        ``{id, path, short, long, unit}``, without ``path`` for B2.

    """
    record = {"id": transport_id(line), "short": line.short, "long": line.long, "unit": line.unit}
    if profile == Profile.V1:
        record["path"] = section_path_text(line)
    return record


def _check_transport_ids(lines: Iterable[BoqLine]) -> None:
    """Raise ValueError when two lines in one batch share a transport id."""
    seen: set[str] = set()
    for line in lines:
        line_id = transport_id(line)
        if line_id in seen:
            raise ValueError(f"duplicate transport id {line_id} in one batch")
        seen.add(line_id)


def render_user_payload(lines: Sequence[BoqLine], profile: Profile) -> str:
    """Render the user message: the batch as one JSON object inside ``<boq_lines>``.

    Line text stays inside its JSON string, so it cannot forge a record boundary, and ``<``,
    ``>`` and ``&`` are written as JSON unicode escapes, so it cannot close the wrapper either;
    the JSON still parses to the same object.

    Args:
        lines: The batch's lines, in batch order.
        profile: V1 or B2.

    Returns:
        The user message.

    Raises:
        ValueError: Two lines share a transport id.

    """
    _check_transport_ids(lines)
    batch = {"lines": [line_record(line, profile) for line in lines]}
    body = json.dumps(batch, ensure_ascii=False, sort_keys=True).translate(JSON_MARKUP_ESCAPES)
    return _fill(USER_TEMPLATE, lines=body)


def schema_json() -> str:
    """Return the output schema as canonical JSON text."""
    return canonical_json(output_json_schema())


def build_request(
    lines: Sequence[BoqLine],
    library: Library,
    variant: PromptVariant,
    *,
    model: str,
    max_tokens: int,
) -> LLMRequest:
    """Build one model call for a batch at temperature 0.

    Args:
        lines: The batch's lines, in batch order.
        library: The loaded library.
        variant: Profile and rendering of the pass.
        model: Requested model snapshot id.
        max_tokens: Output token cap.

    Returns:
        The request, for ``DEFAULT_PROVIDER``; a fallback call replaces ``provider`` with
        ``dataclasses.replace``, which leaves the request hash unchanged.

    Raises:
        ValueError: Two lines share a transport id.

    """
    return LLMRequest(
        provider=DEFAULT_PROVIDER,
        model=model,
        system_blocks=render_system_blocks(library, variant),
        schema_json=schema_json(),
        user_payload=render_user_payload(lines, variant.profile),
        max_tokens=max_tokens,
        temperature=TEMPERATURE,
        line_ids=tuple(line.line_id for line in lines),
    )
