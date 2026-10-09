"""Closed-world check of the model's codes against the loaded library (DESIGN.md §9.4, §11.3).

Codes are compared case-sensitively and exactly, never snapped or repaired. Only a code is
taken from the model: a matched triple is always the library's own row, so labels the model
writes (``material_family``, ``element_or_application``) are never read here.
"""

from dataclasses import dataclass
from enum import StrEnum

from oris_matcher.domain.library import CODE_RE, CODE_SEPARATOR, Library, LibraryRow
from oris_matcher.prompts.v1.schema import LineAnswer


class CodeStatus(StrEnum):
    """Result of checking one model-supplied code against the library."""

    VALID = "valid"
    EMPTY = "empty"
    MALFORMED = "malformed"
    UNKNOWN_PARENT = "unknown_parent"
    UNKNOWN_CODE = "unknown_code"


@dataclass(frozen=True)
class AnswerCheck:
    """The closed-world status of an answer's two codes.

    Attributes:
        top1: Status of ``top1``; only ``VALID`` can lead to a match.
        top2: Status of ``top2``; ``EMPTY`` is allowed.

    """

    top1: CodeStatus
    top2: CodeStatus

    @property
    def is_closed_world(self) -> bool:
        """Whether top1 is a library code and top2 is empty or a library code."""
        top2_ok = self.top2 in {CodeStatus.VALID, CodeStatus.EMPTY}
        return self.top1 == CodeStatus.VALID and top2_ok


def _parent_of(code: str) -> str:
    """Return the ``Txx.Uyy`` prefix of a well-formed code."""
    return code.rsplit(CODE_SEPARATOR, 1)[0]


def check_code(code: str, library: Library) -> CodeStatus:
    """Check one code from the model, case-sensitively, against the loaded library.

    Args:
        code: The code exactly as the model wrote it.
        library: The loaded library.

    Returns:
        ``VALID`` for a code of a library row; otherwise why it is not one: empty, not of the
        form ``Txx.Uyy.Szz`` (case and surrounding spaces count), a ``Txx.Uyy`` prefix that no
        row has, or an unknown leaf under a known prefix.

    """
    if not code:
        return CodeStatus.EMPTY
    if CODE_RE.fullmatch(code) is None:
        return CodeStatus.MALFORMED
    if code in library.by_code:
        return CodeStatus.VALID
    parents = {row.parent_code for row in library.rows}
    if _parent_of(code) not in parents:
        return CodeStatus.UNKNOWN_PARENT
    return CodeStatus.UNKNOWN_CODE


def is_valid_code(code: str, library: Library) -> bool:
    """Tell whether a code names a row of the loaded library.

    Args:
        code: The code exactly as the model wrote it.
        library: The loaded library.

    Returns:
        True only for an exact, case-sensitive library code.

    """
    return check_code(code, library) == CodeStatus.VALID


def resolve_row(code: str, library: Library) -> LibraryRow:
    """Return the library's own row for a valid code, with its strings verbatim.

    Args:
        code: A code that ``is_valid_code`` accepts.
        library: The loaded library.

    Returns:
        The row object held by the library.

    Raises:
        KeyError: The code is not a library code.

    """
    if not is_valid_code(code, library):
        raise KeyError(f"code {code!r} is not in the loaded library")
    return library.by_code[code]


def check_answer(answer: LineAnswer, library: Library) -> AnswerCheck:
    """Check both codes of one answer against the library.

    Args:
        answer: A strictly validated line answer.
        library: The loaded library.

    Returns:
        The status of ``top1`` and ``top2``.

    """
    return AnswerCheck(check_code(answer.top1, library), check_code(answer.top2, library))
