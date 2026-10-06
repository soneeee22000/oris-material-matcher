"""E-08 sibling verifier prompt: system block, user payload, schema and code check (§7.2, A65.2).

The verifier is a separate prompt variant with its own templates, schema and
``verifier_prompt_version``, so the main passes' requests, cache keys and prompt versions do not
change. It sees each flagged line (short, long, unit), its section path, and only the rows of
the agreed material type: every usage and subtype of that type, with their library codes. It
answers ``{id, evidence, code}`` per line, where ``code`` is one of those codes or ``NONE``;
the code is checked here, case-sensitively, and never through a schema enum (A44).

A request carries the lines of one material type, so the candidate rows are rendered once per
request and every line of it is checked against the same codes.
"""

import json
from collections.abc import Collection, Sequence
from string import Template
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from oris_matcher.domain.boq import BoqLine
from oris_matcher.domain.decision import VERIFIER_NONE
from oris_matcher.domain.library import Library, LibraryRow
from oris_matcher.llm.base import LLMRequest, SystemBlock, canonical_json
from oris_matcher.prompts.v1.render import (
    DEFAULT_PROVIDER,
    JSON_MARKUP_ESCAPES,
    TEMPERATURE,
    Profile,
    line_record,
    read_template,
)
from oris_matcher.prompts.v1.schema import closed_schema

VERIFIER_SYSTEM_TEMPLATE = "verifier_system.txt"
VERIFIER_USER_TEMPLATE = "verifier_user.txt"
VERIFIER_TEMPLATES = (VERIFIER_SYSTEM_TEMPLATE, VERIFIER_USER_TEMPLATE)
VERIFIER_VARIANT_NAME = "v1:verifier"
PAYLOAD_OPEN = "<verify_lines>"
PAYLOAD_CLOSE = "</verify_lines>"
TYPE_KEY = "material_type"
CANDIDATES_KEY = "candidates"
LINES_KEY = "lines"
CODE_KEY = "code"


class VerifierAnswer(BaseModel):
    """The verifier's answer for one line; evidence comes first, as the only "think first" lever."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    id: str
    evidence: str
    code: str


class VerifierBatchAnswer(BaseModel):
    """The verifier's answer for one request."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    lines: tuple[VerifierAnswer, ...] = Field(strict=False)


def verifier_output_schema() -> dict[str, Any]:
    """Return the closed JSON schema sent through native structured outputs.

    Returns:
        The schema of ``VerifierBatchAnswer``.

    """
    return closed_schema(VerifierBatchAnswer)


def verifier_schema_json() -> str:
    """Return the verifier schema as canonical JSON text."""
    return canonical_json(verifier_output_schema())


def is_verifier_request(req: LLMRequest) -> bool:
    """Tell a verifier request from a main-pass request, by its output schema.

    Args:
        req: Any request.

    Returns:
        True when the request asks for the verifier schema.

    """
    return req.schema() == verifier_output_schema()


def verifier_template(name: str) -> str:
    """Return one verifier template's text, LF line endings.

    Args:
        name: ``VERIFIER_SYSTEM_TEMPLATE`` or ``VERIFIER_USER_TEMPLATE``.

    Returns:
        The template text.

    """
    return read_template(name)


def sibling_rows(library: Library, code: str) -> tuple[LibraryRow, ...]:
    """Return every row of the material type of a code, in display-code order.

    Args:
        library: The loaded library.
        code: A library code, the agreed top1.

    Returns:
        All usages and subtypes of the code's material type, the code's own row included.

    Raises:
        KeyError: The code is not a library row.

    """
    material_type = library.by_code[code].material_type
    return tuple(row for row in library.rows if row.material_type == material_type)


def _candidate(row: LibraryRow) -> dict[str, str]:
    """Return one candidate row as the verifier sees it: code, usage and subtype, verbatim."""
    return {CODE_KEY: row.code, "usage": row.material_usage, "subtype": row.material_subtype}


def render_verifier_payload(lines: Sequence[BoqLine], rows: Sequence[LibraryRow]) -> str:
    """Render the user message: the type, its candidate rows and the lines, in one JSON object.

    Like the main payload (§9.4), line text stays inside JSON strings and ``<``, ``>`` and ``&``
    are written as JSON escapes, so no line can forge a boundary or close the wrapper.

    Args:
        lines: The flagged lines of one material type, as rendered in the main prompt.
        rows: That type's rows, from ``sibling_rows``.

    Returns:
        The user message.

    """
    document = {
        TYPE_KEY: rows[0].material_type if rows else "",
        CANDIDATES_KEY: [_candidate(row) for row in rows],
        LINES_KEY: [line_record(line, Profile.V1) for line in lines],
    }
    body = json.dumps(document, ensure_ascii=False, sort_keys=True).translate(JSON_MARKUP_ESCAPES)
    return Template(verifier_template(VERIFIER_USER_TEMPLATE)).substitute(payload=body).rstrip("\n")


def render_verifier_system() -> tuple[SystemBlock]:
    """Return the verifier's one system block; it holds no library, so it has no breakpoint."""
    text = verifier_template(VERIFIER_SYSTEM_TEMPLATE).rstrip("\n")
    return (SystemBlock(text=text, cache=False),)


def build_verifier_request(
    lines: Sequence[BoqLine], rows: Sequence[LibraryRow], *, model: str, max_tokens: int
) -> LLMRequest:
    """Build one verifier call at temperature 0 for lines of one material type.

    Args:
        lines: The flagged lines, as rendered in the main prompt.
        rows: The agreed type's rows, from ``sibling_rows``.
        model: The run's primary model.
        max_tokens: Output token cap.

    Returns:
        The request; ``line_ids`` are the lines' ids, which the caller replaces with transport
        ids exactly as for a main pass.

    """
    return LLMRequest(
        provider=DEFAULT_PROVIDER,
        model=model,
        system_blocks=render_verifier_system(),
        schema_json=verifier_schema_json(),
        user_payload=render_verifier_payload(lines, rows),
        max_tokens=max_tokens,
        temperature=TEMPERATURE,
        line_ids=tuple(line.line_id for line in lines),
    )


def payload_codes(user_payload: str) -> tuple[str, ...]:
    """Return the candidate codes a verifier user message lists, in order.

    Args:
        user_payload: A message from ``render_verifier_payload``.

    Returns:
        The codes.

    """
    body = user_payload.split(PAYLOAD_OPEN, 1)[1].rsplit(PAYLOAD_CLOSE, 1)[0]
    candidates = json.loads(body)[CANDIDATES_KEY]
    return tuple(str(candidate[CODE_KEY]) for candidate in candidates)


def validated_code(answer: str, codes: Collection[str]) -> str | None:
    """Check a verifier's code against the codes it was given, case-sensitively.

    Args:
        answer: The ``code`` field of a schema-valid answer.
        codes: The candidate codes of the line's request.

    Returns:
        The answer when it is one of ``codes`` or exactly ``VERIFIER_NONE``; None otherwise,
        which the decision table treats as a missing verifier (D1b).

    """
    if answer == VERIFIER_NONE or answer in codes:
        return answer
    return None
