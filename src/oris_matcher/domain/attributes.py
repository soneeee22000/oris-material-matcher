"""Attribute contracts and the line-side row comparison (DESIGN.md §9.3).

Extractors (one per family, run on normalised text) fill ``Attributes``. ``compare`` judges a
library row against a line from the line side; only hard families take part, and the soft
grading family never conflicts.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, NamedTuple


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
        True when at least one hard family is present.

    """
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
