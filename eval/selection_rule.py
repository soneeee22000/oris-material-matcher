"""The §10.6 threshold-selection rule, generic over the threshold key (DESIGN.md §10.6, A60.3).

A table is a list of (threshold key, {language: LanguageStats}) rows ordered **strictest
first**. A threshold qualifies when every language has at least 40 matched lines and
precision >= .95; qualifiers need not be contiguous. With qualifiers, the strictest one whose
summed correct matches are within 3 of the loosest qualifier's ships, compared once against
that loosest one. Without, the threshold with the highest minimum-over-languages one-sided 95%
Clopper-Pearson lower bound ships (0 when nothing is matched; ties go to the stricter, because
``max`` keeps the first of equal rows), labelled "below the dev bar". Sensitivity rows (one
language alone; another precision bar) are computed by the same rule and are never shipped.

B1 (``baseline_tfidf.py``, keyed by cosine score) and ``oris select`` (``select_threshold.py``,
keyed by ``T1``-``T8``) both use these functions.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from scipy.stats import beta  # type: ignore[import-untyped]

DEV_BAR_PRECISION = 0.95
DEV_BAR_MIN_MATCHED = 40
TIE_WINDOW = 3
CP_ALPHA = 0.05
STATUS_MEETS_BAR = "meets the dev bar"
STATUS_BELOW_BAR = "below the dev bar"


@dataclass(frozen=True)
class LanguageStats:
    """Dev figures of one language at one threshold.

    Attributes:
        matched: Lines matched at the threshold.
        correct: Matched lines whose triple equals the reference.
        precision: correct / matched, or None when nothing is matched.
        cp_lower_95: One-sided 95% Clopper-Pearson lower bound on precision (0 if none).

    """

    matched: int
    correct: int
    precision: float | None
    cp_lower_95: float


@dataclass(frozen=True)
class Selection[K]:
    """The selected threshold.

    Attributes:
        threshold: The selected threshold key.
        status: ``meets the dev bar`` or ``below the dev bar``.
        loosest_qualifying: Loosest threshold meeting the bar, or None.
        per_language: Dev figures at the selected threshold.
        candidates: Number of candidate thresholds considered.

    """

    threshold: K
    status: str
    loosest_qualifying: K | None
    per_language: dict[str, LanguageStats]
    candidates: int


@dataclass(frozen=True)
class RuleOutcome[K]:
    """The shipped selection and the sensitivity rows that are never shipped.

    Attributes:
        selected: The §10.6 selection at the .95 bar over every language.
        per_language: The same rule on each language alone.
        target: The whole rule with .95 replaced by ``target_value``, or None.
        target_value: The ``--target`` bar, or None.

    """

    selected: Selection[K]
    per_language: dict[str, Selection[K]]
    target: Selection[K] | None
    target_value: float | None


type Row[K] = tuple[K, dict[str, LanguageStats]]


def cp_lower(correct: int, total: int) -> float:
    """One-sided 95% exact lower bound, Beta^-1(.05; k, n-k+1); 0 when k = 0."""
    if correct == 0 or total == 0:
        return 0.0
    return float(beta.ppf(CP_ALPHA, correct, total - correct + 1))


def language_stats(matched: int, correct: int) -> LanguageStats:
    """Build one language's figures from its matched and correct counts."""
    precision = correct / matched if matched else None
    return LanguageStats(matched, correct, precision, cp_lower(correct, matched))


def meets_bar(stats: LanguageStats, precision_bar: float = DEV_BAR_PRECISION) -> bool:
    """Dev bar: precision >= the bar (.95 when shipped) with at least 40 matched lines."""
    if stats.precision is None:
        return False
    return stats.matched >= DEV_BAR_MIN_MATCHED and stats.precision >= precision_bar


def qualifies(stats: dict[str, LanguageStats], precision_bar: float = DEV_BAR_PRECISION) -> bool:
    """Whether every language of one threshold meets the bar."""
    return all(meets_bar(item, precision_bar) for item in stats.values())


def summed_correct(stats: dict[str, LanguageStats]) -> int:
    """Correct matches summed over languages (the selection objective)."""
    return sum(item.correct for item in stats.values())


def pick_qualifying[K](qualifying: list[Row[K]], total: int) -> Selection[K]:
    """Strictest qualifying threshold within 3 summed correct matches of the loosest one."""
    loosest_threshold, loosest_stats = qualifying[-1]
    floor = summed_correct(loosest_stats) - TIE_WINDOW
    threshold, stats = next(row for row in qualifying if summed_correct(row[1]) >= floor)
    return Selection(threshold, STATUS_MEETS_BAR, loosest_threshold, stats, total)


def pick_fallback[K](table: list[Row[K]]) -> Selection[K]:
    """Highest minimum-over-languages CP lower bound; ties go to the stricter threshold."""
    threshold, stats = max(table, key=lambda row: min(item.cp_lower_95 for item in row[1].values()))
    return Selection(threshold, STATUS_BELOW_BAR, None, stats, len(table))


def apply_rule[K](
    table: Sequence[Row[K]], precision_bar: float = DEV_BAR_PRECISION
) -> Selection[K]:
    """Apply the §10.6 rule to a strictest-first table.

    Args:
        table: (threshold key, per-language figures) rows, strictest first.
        precision_bar: The precision bar; .95 for the shipped selection.

    Returns:
        The selection.

    Raises:
        ValueError: The table is empty.

    """
    rows = list(table)
    if not rows:
        raise ValueError("no candidate thresholds to select from")
    qualifying = [row for row in rows if qualifies(row[1], precision_bar)]
    if qualifying:
        return pick_qualifying(qualifying, len(rows))
    return pick_fallback(rows)


def language_view[K](table: Sequence[Row[K]], lang: str) -> list[Row[K]]:
    """Return the table restricted to one language."""
    return [(key, {lang: stats[lang]}) for key, stats in table]


def run_rule[K](table: Sequence[Row[K]], target: float | None = None) -> RuleOutcome[K]:
    """Select the shipped threshold and compute the sensitivity rows.

    Args:
        table: (threshold key, per-language figures) rows, strictest first.
        target: A precision bar for the ``--target`` sensitivity row, or None.

    Returns:
        The shipped selection (always at .95), the per-language optima and the target row.

    """
    languages = sorted(table[0][1]) if table else []
    return RuleOutcome(
        selected=apply_rule(table),
        per_language={lang: apply_rule(language_view(table, lang)) for lang in languages},
        target=None if target is None else apply_rule(table, target),
        target_value=target,
    )
