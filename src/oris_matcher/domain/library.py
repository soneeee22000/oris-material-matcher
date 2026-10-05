"""Material library contracts (DESIGN.md §9.2).

Rows keep the three library strings verbatim; ``code`` is the display code ``Txx.Uyy.Szz``
assigned on sorted normalised strings, with ``S00`` for the blank-subtype leaf. ``Library`` is
the loaded aggregate: its rows, its SHA-256 and the structure derived from the rows.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from oris_matcher.domain.attributes import Attributes

CODE_RE = re.compile(r"T\d{2,}\.U\d{2,}\.S\d{2,}")
CODE_SEPARATOR = "."
BLANK_LEAF_SUFFIX = ".S00"
LIBRARY_SHA256_RE = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class LibraryRow:
    """One library row.

    Attributes:
        row_id: Short SHA-256 of the three raw strings.
        code: Display code ``Txx.Uyy.Szz``.
        material_type: Raw material type, verbatim.
        material_usage: Raw material usage, verbatim.
        material_subtype: Raw material subtype, verbatim (may be empty).
        normalized_text: ``normalize`` of the row's strings, used for matching only.
        attributes: Values extracted from ``normalized_text`` by the line extractors.

    """

    row_id: str
    code: str
    material_type: str
    material_usage: str
    material_subtype: str
    normalized_text: str
    attributes: Attributes

    def __post_init__(self) -> None:
        """Reject a code that is not of the form ``Txx.Uyy.Szz``.

        Raises:
            ValueError: The code is malformed.

        """
        if CODE_RE.fullmatch(self.code) is None:
            raise ValueError(f"library row code {self.code!r} is not of the form Txx.Uyy.Szz")

    @property
    def parent_code(self) -> str:
        """The type+usage prefix ``Txx.Uyy`` shared with sibling rows."""
        return self.code.rsplit(CODE_SEPARATOR, 1)[0]

    @property
    def is_blank_leaf(self) -> bool:
        """Whether this is the blank-subtype leaf ``S00`` of its parent."""
        return self.code.endswith(BLANK_LEAF_SUFFIX)


@dataclass(frozen=True)
class Library:
    """A loaded library with its derived structure; every code it names is one of its rows.

    Attributes:
        rows: Every row, in display-code order.
        sha256: SHA-256 hex digest of the library file.
        never_match_codes: Codes of rows matched by ``config/never_match.yaml`` (rule D6).
        mixed_parents: Blank-leaf code of each mixed parent -> its specific siblings' codes.
        sole_children: Codes of rows that are the only row under their parent.
        confusable_groups: Groups of codes sharing a subtype within one type.
        by_code: Read-only map of code -> row, derived from ``rows``.

    """

    rows: tuple[LibraryRow, ...]
    sha256: str
    never_match_codes: frozenset[str]
    mixed_parents: Mapping[str, tuple[str, ...]] = field(hash=False)
    sole_children: frozenset[str]
    confusable_groups: tuple[tuple[str, ...], ...]
    by_code: Mapping[str, LibraryRow] = field(init=False, hash=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        """Index the rows, freeze the mappings and check that the contents are consistent.

        Raises:
            ValueError: Duplicate codes or row ids, a malformed SHA-256, or an unknown code.

        """
        by_code = {row.code: row for row in self.rows}
        object.__setattr__(self, "by_code", MappingProxyType(by_code))
        object.__setattr__(self, "mixed_parents", MappingProxyType(dict(self.mixed_parents)))
        _check_unique(self.rows)
        if LIBRARY_SHA256_RE.fullmatch(self.sha256) is None:
            raise ValueError(f"library sha256 {self.sha256!r} is not a lower-case hex digest")
        _check_known(by_code, self._referenced_codes())

    def _referenced_codes(self) -> Iterable[str]:
        """Yield every code named by the derived structure."""
        yield from self.never_match_codes
        yield from self.sole_children
        for parent, siblings in self.mixed_parents.items():
            yield parent
            yield from siblings
        for group in self.confusable_groups:
            yield from group

    def is_never_match(self, code: str) -> bool:
        """Tell whether a code is a configured never-match row.

        Args:
            code: A display code.

        Returns:
            True when the row must never be accepted as a match.

        """
        return code in self.never_match_codes


def _check_unique(rows: tuple[LibraryRow, ...]) -> None:
    """Raise ValueError when two rows share a code or a row id."""
    if len({row.code for row in rows}) != len(rows):
        raise ValueError("library has duplicate display codes")
    if len({row.row_id for row in rows}) != len(rows):
        raise ValueError("library has duplicate row ids")


def _check_known(by_code: Mapping[str, LibraryRow], codes: Iterable[str]) -> None:
    """Raise ValueError when a code is not one of the library's rows."""
    unknown = sorted(set(codes) - by_code.keys())
    if unknown:
        raise ValueError(f"library structure names unknown codes: {unknown}")
