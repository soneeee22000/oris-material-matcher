"""Deterministic text and unit normalisation for lines and library rows (DESIGN.md §6, §9.5).

Normalised text is used for matching only (extractors, the D5a evidence check, library codes),
never for output. The same function runs on both sides, so the two always agree.
"""

import re
import unicodedata
from typing import Final

UNICODE_FORM: Final = "NFKC"
DIAMETER_SIGN = "ø"
DIAMETER_SIGNS = str.maketrans(dict.fromkeys("Ø∅⌀", DIAMETER_SIGN))
DECIMAL_COMMA_RE = re.compile(r"(?<=\d),(?=\d)")
WHITESPACE_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Normalise text for matching.

    Applies NFKC, maps Ø, ∅ and ⌀ to ø, casefolds, turns a decimal comma between digits into a
    point, collapses whitespace runs to one space and trims the ends. The result is idempotent.

    Args:
        text: Raw text, e.g. a line's short description.

    Returns:
        The normalised text.

    """
    folded = unicodedata.normalize(UNICODE_FORM, text).translate(DIAMETER_SIGNS).casefold()
    pointed = DECIMAL_COMMA_RE.sub(".", folded)
    return WHITESPACE_RE.sub(" ", pointed).strip()
