"""Attribute contracts, extractors and the line-side row comparison (DESIGN.md §9.3).

Extractors (one per family, registered in ``EXTRACTORS``, run on normalised text) fill
``Attributes``; the same extractors run on line text and library rows. ``compare`` judges a
library row against a line from the line side; only hard families take part, and the soft
grading family never conflicts. A hard family that the text states but that has no single
comparable value (several values, or a non-integer number) is left unset, so it never vetoes, and
is recorded in ``ambiguous_families`` so that gate G2 still sees a hard attribute.
``apply_implicit_zero`` is the library-level sibling post-pass for recycled content (amendment A2).
"""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Final, NamedTuple

from oris_matcher.domain.normalize import normalize


class AttrResult(StrEnum):
    """Result of comparing a library row's attributes with a line's, judged from the line side."""

    CONFLICT = "conflict"
    AGREE = "agree"
    NO_EVIDENCE = "no_evidence"


class Process(StrEnum):
    """Asphalt production process."""

    WMA = "wma"
    HMA = "hma"


@dataclass(frozen=True)
class Attributes:
    """Values extracted from one normalised text; an absent family is None or empty.

    Attributes:
        strength_class: Concrete strength class, e.g. ``(30, 37)`` for C30/37.
        exposure_classes: Exposure classes, e.g. ``{"xc4", "xa1"}``.
        cem_type: Cement type at the level stated, e.g. ``("ii",)`` or ``("ii", "a")``.
        recycled_pct: Recycled content in percent; 0 is a stated value.
        process: WMA or HMA.
        ewc_code: European Waste Catalogue code, digits only, e.g. ``"170504"``.
        diameter_mm: Nominal diameter.
        areal_mass_g_m2: Areal mass in g/m².
        grading: Grading d/D values, e.g. ``{"0/20"}``; soft, never part of ``compare``.
        ambiguous_families: Hard fields the text states without one comparable value; their
            value stays unset and never takes part in ``compare``.

    """

    strength_class: tuple[int, int] | None = None
    exposure_classes: frozenset[str] = frozenset()
    cem_type: tuple[str, ...] = ()
    recycled_pct: int | None = None
    process: Process | None = None
    ewc_code: str | None = None
    diameter_mm: int | None = None
    areal_mass_g_m2: int | None = None
    grading: frozenset[str] = frozenset()
    ambiguous_families: frozenset[str] = frozenset()


def _equal(line_value: Any, row_value: Any) -> bool:
    """Tell whether two stated values are equal."""
    return bool(line_value == row_value)


def _line_subset(line_value: frozenset[str], row_value: frozenset[str]) -> bool:
    """Tell whether every value the line states is in the row's set."""
    return line_value <= row_value


def _same_at_common_level(line_value: tuple[str, ...], row_value: tuple[str, ...]) -> bool:
    """Tell whether two levelled values agree at every level both sides state."""
    common = min(len(line_value), len(row_value))
    return line_value[:common] == row_value[:common]


def _either_prefix(line_value: str, row_value: str) -> bool:
    """Tell whether one code is a prefix of the other."""
    return line_value.startswith(row_value) or row_value.startswith(line_value)


class HardFamily(NamedTuple):
    """One hard attribute family: the ``Attributes`` field and its compatibility test."""

    field: str
    compatible: Callable[[Any, Any], bool]


HARD_FAMILIES: tuple[HardFamily, ...] = (
    HardFamily("strength_class", _equal),
    HardFamily("exposure_classes", _line_subset),
    HardFamily("cem_type", _same_at_common_level),
    HardFamily("recycled_pct", _equal),
    HardFamily("process", _equal),
    HardFamily("ewc_code", _either_prefix),
    HardFamily("diameter_mm", _equal),
    HardFamily("areal_mass_g_m2", _equal),
)


def _is_stated(value: object) -> bool:
    """Tell whether a family value is present: not None and not an empty collection."""
    if isinstance(value, tuple | frozenset):
        return bool(value)
    return value is not None


def has_hard_attribute(attributes: Attributes) -> bool:
    """Tell whether any hard family is stated (gate G2, decision rule D2).

    Args:
        attributes: Values extracted from a line.

    Returns:
        True when at least one hard family is present, including one stated ambiguously.

    """
    if attributes.ambiguous_families:
        return True
    return any(_is_stated(getattr(attributes, family.field)) for family in HARD_FAMILIES)


def _family_verdicts(line: Attributes, row: Attributes) -> list[bool]:
    """Return the compatibility of each hard family that both sides state."""
    verdicts: list[bool] = []
    for family in HARD_FAMILIES:
        line_value = getattr(line, family.field)
        row_value = getattr(row, family.field)
        if _is_stated(line_value) and _is_stated(row_value):
            verdicts.append(family.compatible(line_value, row_value))
    return verdicts


def compare(line: Attributes, row: Attributes) -> AttrResult:
    """Compare a library row with a line, judged from the line side (signal (b), §10.6).

    A row is never penalised for lacking a family the line has, nor required to state every
    family (the absence rule).

    Args:
        line: Values extracted from the line.
        row: Values extracted from the library row.

    Returns:
        ``CONFLICT`` when a hard family stated on both sides is incompatible; ``AGREE`` when
        none conflicts and at least one is stated on both sides; ``NO_EVIDENCE`` otherwise.

    """
    verdicts = _family_verdicts(line, row)
    if not all(verdicts):
        return AttrResult.CONFLICT
    return AttrResult.AGREE if verdicts else AttrResult.NO_EVIDENCE


STRENGTH_RE: Final = re.compile(r"\bc(\d{1,3})/(\d{1,3})\b")
EXPOSURE_RE: Final = re.compile(r"\bx(0|c[1-4]|d[1-3]|s[1-3]|f[1-4]|a[1-3]|m[1-3])\b")
CEM_RE: Final = re.compile(r"\bcem\s*(iii|ii|iv|v|i)\b(?:\s*/\s*([abc])\b)?")
NUMBER: Final = r"(\d+(?:\.\d+)?)"
LEADING_NUMBER: Final = r"(?<![\d.])" + NUMBER
RECYCLED_AFTER_RE: Final = re.compile(
    LEADING_NUMBER + r"\s*%\s*(?:de\s+|d.)?(?:(?:rap|ra|ae)\b|reclaimed|agr[ée]gats d.enrob)"
)
RECYCLED_BEFORE_RE: Final = re.compile(r"\b(?:rap|ra|ae)\s*" + NUMBER + r"\s*%")
RECYCLED_ZERO_RE: Final = re.compile(r"\bno ra\b|sans ae|virgin|mat[ée]riaux neufs")
WMA_RE: Final = re.compile(r"\bwma\b|warm|ti[èe]de|temp[ée]rature abaiss[ée]e|reduced temperature")
HMA_RE: Final = re.compile(r"\bhma\b|\bhot\b|\bchaud\b")
EWC_RE: Final = re.compile(r"\b17\s?\d\d(?:\s?\d\d)?\b")
EWC_EXCLUSION_RE: Final = re.compile(r"other than|mentioned in|autres? que|vis[ée]e?s? (?:à|au)")
DIAMETER_RES: Final = (re.compile(r"ø\s*" + NUMBER), re.compile(r"\bdn\s*" + NUMBER))
AREAL_MASS_RE: Final = re.compile(LEADING_NUMBER + r"\s*g/m[²2]")
GRADING_RE: Final = re.compile(r"\b\d+(?:\.\d+)?/\d+(?:\.\d+)?\b")
NON_DIGIT_RE: Final = re.compile(r"\D")
EXPOSURE_PREFIX: Final = "x"
STATED_ZERO_PCT: Final = 0
IMPLICIT_ZERO_CHECKED_LIBRARIES: Final = frozenset({"global"})
"""Catalogues on which the A2 implicit-zero rule was checked against dev data."""
RECYCLED_FIELD: Final = "recycled_pct"
STATED_PATTERNS: Final[Mapping[str, tuple[re.Pattern[str], ...]]] = {
    "strength_class": (STRENGTH_RE,),
    "cem_type": (CEM_RE,),
    RECYCLED_FIELD: (RECYCLED_AFTER_RE, RECYCLED_BEFORE_RE, RECYCLED_ZERO_RE),
    "process": (WMA_RE, HMA_RE),
    "ewc_code": (EWC_RE,),
    "diameter_mm": DIAMETER_RES,
    "areal_mass_g_m2": (AREAL_MASS_RE,),
}
"""Per single-valued hard field, the patterns whose match means the text states that family."""


def _single[T](values: Iterable[T]) -> T | None:
    """Return the one distinct value stated, or None when there are none or several."""
    distinct = set(values)
    return distinct.pop() if len(distinct) == 1 else None


def _single_integer(numbers: Iterable[str]) -> int | None:
    """Return the one distinct number stated as an int; None when absent, several or decimal."""
    number = _single(numbers)
    return int(number) if number is not None and number.isdigit() else None


def extract_strength_class(text: str) -> tuple[int, int] | None:
    """Extract the concrete strength class, e.g. ``c30/37`` -> ``(30, 37)``.

    Args:
        text: Normalised text.

    Returns:
        The strength class, or None when absent or when several different classes are stated.

    """
    return _single((int(m[1]), int(m[2])) for m in STRENGTH_RE.finditer(text))


def extract_exposure_classes(text: str) -> frozenset[str]:
    """Extract every exposure class stated, e.g. ``xc4/xa1`` -> ``{"xc4", "xa1"}``.

    Args:
        text: Normalised text.

    Returns:
        The set of exposure classes, empty when none is stated.

    """
    return frozenset(EXPOSURE_PREFIX + m[1] for m in EXPOSURE_RE.finditer(text))


def _common_prefix(first: tuple[str, ...], second: tuple[str, ...]) -> tuple[str, ...]:
    """Return the leading levels on which two levelled values agree."""
    common: list[str] = []
    for left, right in zip(first, second, strict=False):
        if left != right:
            break
        common.append(left)
    return tuple(common)


def extract_cem_type(text: str) -> tuple[str, ...]:
    """Extract the cement type at the level stated, e.g. ``cem ii/a`` -> ``("ii", "a")``.

    Args:
        text: Normalised text.

    Returns:
        The levels shared by every cement type stated, empty when none is stated or the
        stated types differ at the first level.

    """
    stated = [tuple(part for part in m.groups() if part) for m in CEM_RE.finditer(text)]
    if not stated:
        return ()
    common = stated[0]
    for other in stated[1:]:
        common = _common_prefix(common, other)
    return common


def extract_recycled_pct(text: str) -> int | None:
    """Extract the recycled content in percent; explicit "virgin"-type wording gives 0.

    Args:
        text: Normalised text.

    Returns:
        The percentage, or None when absent, when several different values are stated or when
        the stated value is not an integer.

    """
    values = [m[1] for m in RECYCLED_AFTER_RE.finditer(text)]
    values += [m[1] for m in RECYCLED_BEFORE_RE.finditer(text)]
    if RECYCLED_ZERO_RE.search(text):
        values.append(str(STATED_ZERO_PCT))
    return _single_integer(str(int(value)) if value.isdigit() else value for value in values)


def extract_process(text: str) -> Process | None:
    """Extract the asphalt production process with the A2 lexicon.

    Args:
        text: Normalised text.

    Returns:
        WMA or HMA, or None when neither or both are stated.

    """
    stated = {
        process
        for process, pattern in ((Process.WMA, WMA_RE), (Process.HMA, HMA_RE))
        if pattern.search(text)
    }
    return _single(stated)


def extract_ewc_code(text: str) -> str | None:
    """Extract a European Waste Catalogue code as digits, e.g. ``17 05 04`` -> ``"170504"``.

    Codes after an exclusion phrase are ignored, so a row such as ``17 03 02 … other than
    those mentioned in 17 03 01`` keeps its own code; of the rest, the most specific is kept.

    Args:
        text: Normalised text.

    Returns:
        The code's digits, or None when absent or when several different most specific codes
        are stated.

    """
    exclusion = EWC_EXCLUSION_RE.search(text)
    cutoff = exclusion.start() if exclusion else len(text)
    codes = [NON_DIGIT_RE.sub("", m[0]) for m in EWC_RE.finditer(text) if m.start() < cutoff]
    if not codes:
        return None
    longest = max(len(code) for code in codes)
    return _single(code for code in codes if len(code) == longest)


def extract_diameter_mm(text: str) -> int | None:
    """Extract the nominal diameter from ``ø160`` or ``dn 160``.

    Args:
        text: Normalised text.

    Returns:
        The diameter, or None when absent, when several different diameters are stated or when
        the stated value is not an integer.

    """
    return _single_integer(m[1] for pattern in DIAMETER_RES for m in pattern.finditer(text))


def extract_areal_mass_g_m2(text: str) -> int | None:
    """Extract the areal mass from ``250 g/m2``.

    Args:
        text: Normalised text.

    Returns:
        The areal mass, or None when absent, when several different values are stated or when
        the stated value is not an integer.

    """
    return _single_integer(m[1] for m in AREAL_MASS_RE.finditer(text))


def extract_grading(text: str) -> frozenset[str]:
    """Extract d/D gradings such as ``0/31.5``, excluding strength classes (soft family).

    Args:
        text: Normalised text.

    Returns:
        The set of gradings, empty when none is stated.

    """
    strength_spans = [m.span() for m in STRENGTH_RE.finditer(text)]
    return frozenset(
        m[0]
        for m in GRADING_RE.finditer(text)
        if not any(start < m.end() and m.start() < end for start, end in strength_spans)
    )


class Extractor(NamedTuple):
    """One registered extractor: the ``Attributes`` field it fills and its pure function."""

    field: str
    extract: Callable[[str], Any]


EXTRACTORS: tuple[Extractor, ...] = (
    Extractor("strength_class", extract_strength_class),
    Extractor("exposure_classes", extract_exposure_classes),
    Extractor("cem_type", extract_cem_type),
    Extractor("recycled_pct", extract_recycled_pct),
    Extractor("process", extract_process),
    Extractor("ewc_code", extract_ewc_code),
    Extractor("diameter_mm", extract_diameter_mm),
    Extractor("areal_mass_g_m2", extract_areal_mass_g_m2),
    Extractor("grading", extract_grading),
)


def _ambiguous_families(text: str, values: Mapping[str, Any]) -> frozenset[str]:
    """Name the hard fields the text states but that were left without a single value."""
    return frozenset(
        field
        for field, patterns in STATED_PATTERNS.items()
        if not _is_stated(values[field]) and any(pattern.search(text) for pattern in patterns)
    )


def extract(text: str) -> Attributes:
    """Run every registered extractor on one text (a line or a library row).

    Args:
        text: Normalised text; ``normalize`` is idempotent and is applied again here, so raw
            text gives the same result.

    Returns:
        The extracted attributes; the implicit-zero sibling rule is not applied here.

    """
    normalised = normalize(text)
    values = {extractor.field: extractor.extract(normalised) for extractor in EXTRACTORS}
    return Attributes(**values, ambiguous_families=_ambiguous_families(normalised, values))


def _states_positive_pct(attributes: Attributes) -> bool:
    """Tell whether a row states a positive recycled percentage."""
    return attributes.recycled_pct is not None and attributes.recycled_pct > STATED_ZERO_PCT


def _states_no_recycled_content(attributes: Attributes) -> bool:
    """Tell whether a row says nothing about recycled content, not even ambiguously."""
    return attributes.recycled_pct is None and RECYCLED_FIELD not in attributes.ambiguous_families


def implicit_zero_siblings(siblings: Sequence[Attributes]) -> tuple[Attributes, ...]:
    """Apply the A2 implicit-zero rule to the rows of one type+usage pair.

    A row that states no recycled percentage counts as 0 only when a sibling states a positive
    percentage; a sibling that states only an explicit 0 ("virgin") does not trigger it, and a
    row that states contradictory or non-integer percentages is left unset.

    Args:
        siblings: Extracted attributes of every row under one parent, in any order.

    Returns:
        The attributes in the same order, with the implicit zero filled in where it applies.

    """
    if not any(_states_positive_pct(row) for row in siblings):
        return tuple(siblings)
    return tuple(
        replace(row, recycled_pct=STATED_ZERO_PCT) if _states_no_recycled_content(row) else row
        for row in siblings
    )


def implicit_zero_applies(library: str) -> bool:
    """Tell whether the A2 implicit-zero rule was checked on, and so applies to, a catalogue.

    Args:
        library: Catalogue id, e.g. ``"global"`` or ``"fr"``.

    Returns:
        True only for catalogues in ``IMPLICIT_ZERO_CHECKED_LIBRARIES``.

    """
    return library in IMPLICIT_ZERO_CHECKED_LIBRARIES


def apply_implicit_zero[K](
    groups: Mapping[K, Sequence[Attributes]], *, library: str
) -> dict[K, tuple[Attributes, ...]]:
    """Apply the A2 implicit-zero rule to every type+usage group of a library.

    Args:
        groups: Parent key (e.g. ``Txx.Uyy``) -> the extracted attributes of its rows.
        library: Catalogue id; on a catalogue where the rule was not checked, the rows are
            returned unchanged.

    Returns:
        The same keys, in the same order, each with its rows' post-pass attributes.

    """
    if not implicit_zero_applies(library):
        return {parent: tuple(rows) for parent, rows in groups.items()}
    return {parent: implicit_zero_siblings(rows) for parent, rows in groups.items()}
