"""Material library contracts and the loader (DESIGN.md §9.2).

Rows keep the three library strings verbatim; ``code`` is the display code ``Txx.Uyy.Szz``
assigned on sorted normalised strings, with ``S00`` for the blank-subtype leaf. ``Library`` is
the loaded aggregate: its rows, its SHA-256 and the structure derived from the rows.
``load_library`` builds one from the bytes of any CSV holding the three library columns; the
structure is derived from the loaded rows only, never from known type names. The module does
no I/O: callers read the file.
"""

import csv
import dataclasses
import hashlib
import io
import logging
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol

from oris_matcher.domain.attributes import Attributes, apply_implicit_zero
from oris_matcher.domain.attributes import extract as extract_attributes
from oris_matcher.domain.normalize import normalize

CODE_RE = re.compile(r"T\d{2,}\.U\d{2,}\.S\d{2,}")
CODE_SEPARATOR = "."
BLANK_LEAF_SUFFIX = ".S00"
SUBTYPE_MARKER = ".S"
LIBRARY_SHA256_RE = re.compile(r"[0-9a-f]{64}")

ROW_ID_LENGTH = 12
ROW_ID_SEPARATOR = ""
ROW_ID_ENCODING = "utf-8"
LIBRARY_ENCODING = "utf-8-sig"
TYPE_FIELD = "material_type"
USAGE_FIELD = "material_usage"
SUBTYPE_FIELD = "material_subtype"
LIBRARY_FIELDS = (TYPE_FIELD, USAGE_FIELD, SUBTYPE_FIELD)
COMMA = ","
SEMICOLON = ";"
MIN_CODE_WIDTH = 2
BLANK_LEAF_INDEX = 0
FIRST_INDEX = 1
FIRST_DATA_LINE = 2
MIN_GROUP_SIZE = 2
WHOLE_LIBRARY_MAX_TOKENS = 30_000
CHARS_PER_TOKEN_ESTIMATE = 3
ROW_LAYOUT_CHARS = 24
CODE_REPEATS_PER_ROW = 3
LIBRARY_HARD_MAX_TOKENS = 150_000
RETRIEVAL_SWITCH = "the HybridRetriever candidate provider (install the [retrieval] extra)"

logger = logging.getLogger(__name__)

RawRow = tuple[str, str, str]
TokenCounts = int | Mapping[str, int]
IndexTriple = tuple[int, int, int]


class LibraryError(ValueError):
    """A library file cannot be loaded: bad encoding, columns, rows, or a row-id collision."""


class LibraryTooLargeError(LibraryError):
    """The rendered library exceeds the whole-library limit; it is never truncated (D-01)."""


class FieldPattern(Protocol):
    """A regular expression searched in one raw library field, e.g. a never-match pattern."""

    @property
    def field(self) -> str:
        """The library field searched: material_type, material_usage or material_subtype."""
        ...

    @property
    def compiled(self) -> re.Pattern[str]:
        """The compiled regular expression."""
        ...


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
        """Whether this is the blank-subtype leaf of its parent: subtype index 0, any width."""
        return int(self.code.rsplit(SUBTYPE_MARKER, 1)[1]) == BLANK_LEAF_INDEX


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
        rendered_tokens: Token count of the largest rendering of the library, measured or
            estimated.
        rendered_tokens_by_variant: Measured token count per rendering name, when the measure
            returned one count per rendering; empty otherwise.
        rendered_tokens_estimated: Whether ``rendered_tokens`` is the offline estimate rather
            than a measurement.
        by_code: Read-only map of code -> row, derived from ``rows``.

    """

    rows: tuple[LibraryRow, ...]
    sha256: str
    never_match_codes: frozenset[str]
    mixed_parents: Mapping[str, tuple[str, ...]] = field(hash=False)
    sole_children: frozenset[str]
    confusable_groups: tuple[tuple[str, ...], ...]
    rendered_tokens: int | None = None
    rendered_tokens_by_variant: Mapping[str, int] = field(default_factory=dict, hash=False)
    rendered_tokens_estimated: bool = False
    by_code: Mapping[str, LibraryRow] = field(init=False, hash=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        """Index the rows, freeze the mappings and check that the contents are consistent.

        Raises:
            ValueError: Duplicate codes or row ids, a malformed SHA-256, or an unknown code.

        """
        by_code = {row.code: row for row in self.rows}
        object.__setattr__(self, "by_code", MappingProxyType(by_code))
        object.__setattr__(self, "mixed_parents", MappingProxyType(dict(self.mixed_parents)))
        by_variant = MappingProxyType(dict(self.rendered_tokens_by_variant))
        object.__setattr__(self, "rendered_tokens_by_variant", by_variant)
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


def no_attributes(text: str) -> Attributes:
    """Extract nothing: an explicit opt-in for tests, returning empty ``Attributes``.

    Args:
        text: A row's normalised text, unused.

    Returns:
        Empty attributes.

    """
    del text
    return Attributes()


def make_row_id(material_type: str, material_usage: str, material_subtype: str) -> str:
    """Hash a row's three raw strings into its stable row id (D-07).

    Args:
        material_type: Raw material type.
        material_usage: Raw material usage.
        material_subtype: Raw material subtype.

    Returns:
        The first ``ROW_ID_LENGTH`` hex characters of a SHA-256 over the unit-separated strings.

    """
    joined = ROW_ID_SEPARATOR.join((material_type, material_usage, material_subtype))
    return hashlib.sha256(joined.encode(ROW_ID_ENCODING)).hexdigest()[:ROW_ID_LENGTH]


def load_library(
    source: bytes,
    never_match_patterns: Iterable[FieldPattern] = (),
    *,
    extract: Callable[[str], Attributes] = extract_attributes,
    catalogue: str | None = None,
    measure: Callable[[Library], TokenCounts] | None = None,
) -> Library:
    """Load a library CSV and derive its codes, attributes and structure (DESIGN.md §9.2).

    Args:
        source: The CSV file's bytes. UTF-8 (BOM allowed), ``,`` or ``;`` delimited, with
            ``material_type``, ``material_usage`` and ``material_subtype`` columns.
        never_match_patterns: Patterns whose matching rows must never be accepted (rule D6).
        extract: Row extractor run on each row's normalised text; the line extractor by default.
        catalogue: Catalogue id, e.g. ``"global"``, for the A2 implicit-zero sibling post-pass;
            the post-pass is skipped when None and is a no-op on unchecked catalogues.
        measure: Measure of the rendered library in tokens, one count or one per rendering
            name; it is only called here, so the caller decides whether it is a live
            ``count_tokens`` call. When None, the tiers run on a conservative offline estimate.

    Returns:
        The library, rows in display-code order.

    Raises:
        LibraryError: The file cannot be decoded or parsed, has no rows, or two rows share an id.
        LibraryTooLargeError: The rendering exceeds ``LIBRARY_HARD_MAX_TOKENS``.

    """
    raw_rows = _read_raw_rows(_decode(source))
    _check_row_ids(raw_rows)
    codes = _assign_codes(raw_rows)
    rows = tuple(sorted((_make_row(raw, codes[raw], extract) for raw in raw_rows), key=_code_key))
    rows = _with_implicit_zero(rows, catalogue)
    library = Library(
        rows=rows,
        sha256=hashlib.sha256(source).hexdigest(),
        never_match_codes=_never_match_codes(rows, tuple(never_match_patterns)),
        mixed_parents=_mixed_parents(rows),
        sole_children=_sole_children(rows),
        confusable_groups=_confusable_groups(rows),
    )
    return _measured(library, measure)


def _decode(data: bytes) -> str:
    """Decode library bytes as UTF-8, tolerating a BOM."""
    try:
        return data.decode(LIBRARY_ENCODING)
    except UnicodeDecodeError as error:
        raise LibraryError(f"library file is not valid UTF-8: {error}") from error


def _read_raw_rows(text: str) -> list[RawRow]:
    """Parse the CSV text into distinct raw (type, usage, subtype) triples, in file order."""
    first_line = text.split("\n", 1)[0]
    if not first_line.strip():
        raise LibraryError("library file has no header row")
    delimiter = SEMICOLON if first_line.count(SEMICOLON) > first_line.count(COMMA) else COMMA
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    header = next(reader)
    indexes = _column_indexes(header)
    rows = [
        _raw_row(_fitted(record, len(header), reader.line_num), indexes, reader.line_num)
        for record in reader
        if any(cell.strip() for cell in record)
    ]
    if not rows:
        raise LibraryError("library file has no rows")
    return _deduplicated(rows)


def _fitted(record: list[str], width: int, line_num: int) -> list[str]:
    """Drop blank trailing cells past the header; reject any other field-count mismatch."""
    if len(record) > width and not any(cell.strip() for cell in record[width:]):
        return record[:width]
    if len(record) != width:
        raise LibraryError(f"library line {line_num}: {len(record)} fields, header has {width}")
    return record


def _deduplicated(rows: Sequence[RawRow]) -> list[RawRow]:
    """Keep the first of each exact duplicate row, logging the duplicates."""
    duplicates = sorted(raw for raw, count in Counter(rows).items() if count > 1)
    if duplicates:
        logger.warning("library has duplicate rows, each kept once: %s", duplicates)
    return list(dict.fromkeys(rows))


def _column_indexes(header: Sequence[str]) -> tuple[int, int, int]:
    """Find the three library columns by name, ignoring case and surrounding spaces."""
    names = [name.strip().casefold() for name in header]
    repeated = [name for name in LIBRARY_FIELDS if names.count(name) > 1]
    if repeated:
        raise LibraryError(f"library header names {repeated} more than once: {list(header)}")
    missing = [name for name in LIBRARY_FIELDS if name not in names]
    if missing:
        raise LibraryError(f"library header lacks column(s) {missing}: {list(header)}")
    return names.index(TYPE_FIELD), names.index(USAGE_FIELD), names.index(SUBTYPE_FIELD)


def _raw_row(record: Sequence[str], indexes: tuple[int, int, int], line_num: int) -> RawRow:
    """Pick one record's raw triple, rejecting a blank type or usage."""
    raw: RawRow = (record[indexes[0]], record[indexes[1]], record[indexes[2]])
    for name, value in zip(LIBRARY_FIELDS[:2], raw[:2], strict=True):
        if not normalize(value):
            raise LibraryError(f"library line {line_num}: {name} is blank")
    return raw


def _check_row_ids(rows: Sequence[RawRow]) -> None:
    """Raise LibraryError when two distinct rows share a row id."""
    seen: dict[str, RawRow] = {}
    for raw in rows:
        row_id = make_row_id(*raw)
        other = seen.setdefault(row_id, raw)
        if other != raw:
            raise LibraryError(f"row id collision on {row_id!r}: {other!r} and {raw!r}")


def _sort_key(raw: str) -> tuple[str, str]:
    """Order raw strings by their normalised form, then by the raw string as a tie-break."""
    return normalize(raw), raw


def _ranks(values: Iterable[str]) -> dict[str, int]:
    """Rank distinct values from ``FIRST_INDEX`` in sorted normalised order."""
    ordered = sorted(set(values), key=_sort_key)
    return {value: rank for rank, value in enumerate(ordered, start=FIRST_INDEX)}


def _subtype_ranks(subtypes: Iterable[str], parent: tuple[str, str]) -> dict[str, int]:
    """Rank one parent's subtypes, giving the single blank subtype ``BLANK_LEAF_INDEX``."""
    distinct = set(subtypes)
    blanks = sorted(subtype for subtype in distinct if not normalize(subtype))
    if len(blanks) > 1:
        raise LibraryError(f"library parent {parent!r} has more than one blank subtype {blanks}")
    ranks = _ranks(subtype for subtype in distinct if normalize(subtype))
    ranks.update(dict.fromkeys(blanks, BLANK_LEAF_INDEX))
    return ranks


def _index_triples(rows: Sequence[RawRow]) -> dict[RawRow, IndexTriple]:
    """Give each row its (type, usage, subtype) indexes on sorted normalised strings."""
    usages: defaultdict[str, set[str]] = defaultdict(set)
    subtypes: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    for material_type, material_usage, material_subtype in rows:
        usages[material_type].add(material_usage)
        subtypes[material_type, material_usage].add(material_subtype)
    type_ranks = _ranks(usages)
    usage_ranks = {name: _ranks(values) for name, values in usages.items()}
    subtype_ranks = {parent: _subtype_ranks(values, parent) for parent, values in subtypes.items()}
    return {
        raw: (
            type_ranks[raw[0]],
            usage_ranks[raw[0]][raw[1]],
            subtype_ranks[raw[0], raw[1]][raw[2]],
        )
        for raw in rows
    }


def _level_width(triples: Iterable[IndexTriple], level: int) -> int:
    """Return the zero-padded width that fits every index at one level."""
    largest = max((triple[level] for triple in triples), default=BLANK_LEAF_INDEX)
    return max(MIN_CODE_WIDTH, len(str(largest)))


def _assign_codes(rows: Sequence[RawRow]) -> dict[RawRow, str]:
    """Format each row's indexes as ``Txx.Uyy.Szz``, one zero-padded width per level."""
    triples = _index_triples(rows)
    type_w, usage_w, subtype_w = (
        _level_width(triples.values(), level) for level in range(len(LIBRARY_FIELDS))
    )
    return {
        raw: f"T{type_i:0{type_w}d}.U{usage_i:0{usage_w}d}.S{subtype_i:0{subtype_w}d}"
        for raw, (type_i, usage_i, subtype_i) in triples.items()
    }


def _code_key(row: LibraryRow) -> str:
    """Sort rows by display code; codes share one width per level, so text order is code order."""
    return row.code


def _make_row(raw: RawRow, code: str, extract: Callable[[str], Attributes]) -> LibraryRow:
    """Build one library row, extracting its attributes from its normalised text."""
    normalized_text = normalize(" ".join(raw))
    return LibraryRow(
        row_id=make_row_id(*raw),
        code=code,
        material_type=raw[0],
        material_usage=raw[1],
        material_subtype=raw[2],
        normalized_text=normalized_text,
        attributes=extract(normalized_text),
    )


def _by_parent(rows: Iterable[LibraryRow]) -> dict[str, list[LibraryRow]]:
    """Group rows by parent code ``Txx.Uyy``, keeping row order."""
    parents: defaultdict[str, list[LibraryRow]] = defaultdict(list)
    for row in rows:
        parents[row.parent_code].append(row)
    return dict(parents)


def _with_implicit_zero(
    rows: tuple[LibraryRow, ...], catalogue: str | None
) -> tuple[LibraryRow, ...]:
    """Apply the A2 implicit-zero sibling post-pass to each parent's rows (amendment A2)."""
    if catalogue is None:
        return rows
    parents = _by_parent(rows)
    groups = {parent: [row.attributes for row in children] for parent, children in parents.items()}
    updated = apply_implicit_zero(groups, library=catalogue)
    attributes = {
        row.code: row_attributes
        for parent, children in parents.items()
        for row, row_attributes in zip(children, updated[parent], strict=True)
    }
    return tuple(dataclasses.replace(row, attributes=attributes[row.code]) for row in rows)


def _mixed_parents(rows: Sequence[LibraryRow]) -> dict[str, tuple[str, ...]]:
    """Map the blank leaf of each parent that also has specific rows to those rows' codes."""
    mixed: dict[str, tuple[str, ...]] = {}
    for children in _by_parent(rows).values():
        blanks = [row.code for row in children if row.is_blank_leaf]
        specific = tuple(row.code for row in children if not row.is_blank_leaf)
        if blanks and specific:
            mixed[blanks[0]] = specific
    return mixed


def _sole_children(rows: Sequence[LibraryRow]) -> frozenset[str]:
    """Return the codes of rows that are alone under their parent."""
    children = _by_parent(rows).values()
    return frozenset(group[0].code for group in children if len(group) == 1)


def _confusable_groups(rows: Sequence[LibraryRow]) -> tuple[tuple[str, ...], ...]:
    """Group codes of rows sharing a non-blank normalised subtype within one type."""
    groups: defaultdict[tuple[str, str], list[str]] = defaultdict(list)
    for row in rows:
        subtype = normalize(row.material_subtype)
        if subtype:
            groups[row.code.split(CODE_SEPARATOR, 1)[0], subtype].append(row.code)
    confusable = (tuple(sorted(codes)) for codes in groups.values() if len(codes) >= MIN_GROUP_SIZE)
    return tuple(sorted(confusable))


def _never_match_codes(
    rows: Sequence[LibraryRow], patterns: Sequence[FieldPattern]
) -> frozenset[str]:
    """Return the codes of rows where any pattern is found in its raw field."""
    return frozenset(
        row.code
        for row in rows
        if any(pattern.compiled.search(getattr(row, pattern.field)) for pattern in patterns)
    )


def estimate_tokens(rows: Iterable[LibraryRow]) -> int:
    """Estimate, offline and conservatively, the tokens of the largest library rendering.

    Every row is charged its code several times, its three strings in full, and a fixed layout
    allowance, then the characters are divided by ``CHARS_PER_TOKEN_ESTIMATE``, rounding up.

    Args:
        rows: The library rows.

    Returns:
        The estimated token count.

    """
    chars = sum(
        CODE_REPEATS_PER_ROW * len(row.code)
        + len(row.material_type)
        + len(row.material_usage)
        + len(row.material_subtype)
        + ROW_LAYOUT_CHARS
        for row in rows
    )
    return -(-chars // CHARS_PER_TOKEN_ESTIMATE)


def _token_counts(measured: TokenCounts) -> tuple[int, dict[str, int]]:
    """Return the largest count and the per-rendering counts of one measurement."""
    if isinstance(measured, int):
        return measured, {}
    if not measured:
        raise LibraryError("library measure returned no token counts")
    return max(measured.values()), dict(measured)


def _apply_tiers(tokens: int, estimated: bool) -> None:
    """Apply the D-01 size tiers: warn over the whole-library tier, refuse over the hard one."""
    kind = "estimated" if estimated else "measured"
    if tokens > LIBRARY_HARD_MAX_TOKENS:
        raise LibraryTooLargeError(
            f"rendered library is {tokens} tokens ({kind}), over {LIBRARY_HARD_MAX_TOKENS}; "
            f"switch to {RETRIEVAL_SWITCH}"
        )
    if tokens > WHOLE_LIBRARY_MAX_TOKENS:
        logger.warning(
            "rendered library is %d tokens (%s), over %d: whole library kept; check the cache hits",
            tokens,
            kind,
            WHOLE_LIBRARY_MAX_TOKENS,
        )


def _measured(library: Library, measure: Callable[[Library], TokenCounts] | None) -> Library:
    """Record the rendered size, measured or estimated, and apply the D-01 tiers."""
    estimated = measure is None
    counts = estimate_tokens(library.rows) if measure is None else measure(library)
    tokens, by_variant = _token_counts(counts)
    _apply_tiers(tokens, estimated)
    return dataclasses.replace(
        library,
        rendered_tokens=tokens,
        rendered_tokens_by_variant=by_variant,
        rendered_tokens_estimated=estimated,
    )
