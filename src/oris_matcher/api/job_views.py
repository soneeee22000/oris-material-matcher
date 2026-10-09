"""JSON bodies of the jobs API (docs/ui-spec.md §3): status, result rows, summary, libraries.

Every value comes from the run's ``RunResult``, its manifest and the service's audit record,
the same sources as the output CSV and ``audit.jsonl``; nothing here decides or scores. The
audit's evidence, element, confidence and candidate gap are read from the line's first pass
answer (``raw_line_response``), verbatim as the model gave them; the raw answers of every pass
are returned too. Confidence is the model's own uncalibrated self-report.
"""

import json
import math
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Final

from oris_matcher.domain.batching import transport_id
from oris_matcher.domain.decision import LLM_FAILURE_PREFIX, Decision, ReasonCode
from oris_matcher.domain.library import Library, LibraryRow
from oris_matcher.service import LineResult, RunResult, audit_record

SHA_PREFIX_LENGTH: Final = 12
PERCENT: Final = 100.0
P95: Final = 95
SECONDS_DIGITS: Final = 3
FAILURE_REASONS: Final = frozenset({ReasonCode.LLM_UNAVAILABLE.value, ReasonCode.BUDGET_CAP.value})
DECISIONS: Final = tuple(decision.value for decision in Decision)
EVIDENCE_KEY: Final = "evidence"
ELEMENT_KEY: Final = "element_or_application"
CONFIDENCE_KEY: Final = "confidence"
GAP_KEY: Final = "self_reported_candidate_gap"


def library_record(library_id: str, library: Library) -> dict[str, Any]:
    """Return a library's identity: id, row count and the first 12 hex of its SHA-256.

    Args:
        library_id: The configured id.
        library: The loaded library.

    Returns:
        ``{name, rows, sha256_12}``.

    """
    return {
        "name": library_id,
        "rows": len(library.rows),
        "sha256_12": library.sha256[:SHA_PREFIX_LENGTH],
    }


def is_failure(reason: str) -> bool:
    """Tell whether a line's reason is a model failure rather than a decision.

    Args:
        reason: The line's reason code.

    Returns:
        True for ``LLM_FAILURE:<kind>``, ``LLM_UNAVAILABLE`` and ``BUDGET_CAP``.

    """
    return reason.startswith(LLM_FAILURE_PREFIX) or reason in FAILURE_REASONS


def nearest_rank(values: Sequence[int], percent: int) -> int | None:
    """Return a nearest-rank percentile, as the CLI summary computes it.

    Args:
        values: The values.
        percent: 0 to 100.

    Returns:
        The percentile, or None for no values.

    """
    if not values:
        return None
    ordered = sorted(values)
    rank = max(math.ceil(percent / PERCENT * len(ordered)), 1)
    return ordered[rank - 1]


def summary_record(result: RunResult) -> dict[str, Any]:
    """Return a finished run's summary bar.

    Latency is attributed per line and taken over the lines that reached the model, as in the
    CLI summary; the gate figure is the run's wall clock per line.

    Args:
        result: The run.

    Returns:
        Decision counts, failures, cost, wall clock per line, mean and p95 attributed latency,
        served models and the policy that decided the run.

    """
    lines, manifest = result.lines, result.manifest
    counts = Counter(item.decision.decision.value for item in lines)
    latencies = [item.latency_ms for item in lines if item.call_ids]
    wall_clock = float(manifest["wall_clock_s"])
    return {
        **{decision: counts.get(decision, 0) for decision in DECISIONS},
        "errors": sum(is_failure(item.decision.reason) for item in lines),
        "cost_usd": result.attributed_cost_usd,
        "wall_clock_s": wall_clock,
        "wall_clock_s_per_line": round(wall_clock / len(lines), SECONDS_DIGITS) if lines else 0.0,
        "mean_attributed_latency_ms": sum(latencies) / len(latencies) if latencies else None,
        "p95_attributed_latency_ms": nearest_rank(latencies, P95),
        "served_models": list(manifest["served_models"]),
        "policy_resolution": result.policy.resolution.value,
        "policy_id": result.policy.policy_id,
        "run_id": result.run_id,
        "mode": manifest["mode"],
        "prompt_version": list(result.prompt_versions),
        "library": library_record(result.library_id, result.library),
    }


def _labels(row: LibraryRow | None) -> dict[str, str] | None:
    """Return a library row's verbatim labels and id, or None."""
    if row is None:
        return None
    return {
        "material_type": row.material_type,
        "material_usage": row.material_usage,
        "material_subtype": row.material_subtype,
        "library_row_id": row.row_id,
    }


def _first_answer(raw_responses: Sequence[str]) -> dict[str, Any]:
    """Return the first pass's answer object, or {} when no pass gave a readable one."""
    for raw in raw_responses:
        try:
            answer = json.loads(raw)
        except ValueError:
            continue
        if isinstance(answer, dict):
            return answer
    return {}


def _verifier_view(record: dict[str, Any]) -> dict[str, Any] | None:
    """Return the E-08 verifier's fields of an audit record, or None when it did not run."""
    if "verifier_flagged" not in record:
        return None
    return {
        "flagged": record["verifier_flagged"],
        "top1": record["verifier_top1"],
        "failure": record["verifier_failure"],
        "call_ids": record["verifier_call_ids"],
    }


def audit_view(item: LineResult) -> dict[str, Any]:
    """Return the audit drawer of one line, from its ``audit.jsonl`` record.

    Args:
        item: One line's result.

    Returns:
        The gate (decision-table rule) that fired, the first pass's evidence, element,
        confidence and candidate gap, top-1 and top-2 codes, every pass's raw answer, the
        signals, attribute result, flags, verifier fields and the failure reason, if any.

    """
    record = audit_record(item)
    first = _first_answer(item.raw_line_responses)
    reason = str(item.decision.reason)
    return {
        "gate": record["rule"],
        "evidence": first.get(EVIDENCE_KEY),
        "element_or_application": first.get(ELEMENT_KEY),
        "top1": record["top1"] or None,
        "top2": record["top2"] or None,
        "confidence": first.get(CONFIDENCE_KEY),
        "candidate_gap": first.get(GAP_KEY),
        "raw_line_response": record["raw_line_response"],
        "signals": record["signals"],
        "attribute_result": record["attribute_result"],
        "context": record["context"],
        "flags": record["flags"],
        "verifier": _verifier_view(record),
        "error": reason if is_failure(reason) else None,
    }


def _line_cells(item: LineResult) -> dict[str, Any]:
    """Return a line's identity, raw cells and place in the section hierarchy."""
    line = item.line
    return {
        "line_id": line.line_id,
        "transport_id": transport_id(line),
        "position": line.position,
        "item_no": line.item_no,
        "short": line.short,
        "long": line.long,
        "unit": line.unit,
        "qty": line.qty,
        "kind": line.kind.value,
        "level": len(line.section_path),
        "section_path": [header.text for header in line.section_path],
    }


def row_record(item: LineResult) -> dict[str, Any]:
    """Return one result row: the line, its decision as the output CSV states it, its audit.

    Args:
        item: One line's result.

    Returns:
        The row; labels are verbatim library strings on matched lines, else null.

    """
    decision = item.decision
    labels = _labels(decision.row if decision.decision == Decision.MATCHED else None) or {}
    return {
        **_line_cells(item),
        "decision": decision.decision.value,
        "material_type": labels.get("material_type"),
        "material_usage": labels.get("material_usage"),
        "material_subtype": labels.get("material_subtype"),
        "reason": str(decision.reason),
        "model": item.model,
        "prompt_version": item.prompt_version,
        "latency_ms": item.latency_ms,
        "cost_usd": item.cost_usd,
        "library_row_id": item.suggested.row_id if item.suggested else None,
        "suggestions": [_labels(item.suggested), _labels(item.suggested2)],
        "call_ids": list(item.call_ids),
        "audit": audit_view(item),
    }


def result_record(result: RunResult) -> dict[str, Any]:
    """Return ``GET /v1/jobs/{id}/result``: the summary and one row per input line, in order.

    Args:
        result: The finished run.

    Returns:
        ``{summary, rows}``.

    """
    return {
        "summary": summary_record(result),
        "rows": [row_record(item) for item in result.lines],
    }


def iso(moment: datetime | None) -> str | None:
    """Return a moment in ISO 8601, or None.

    Args:
        moment: A timezone-aware moment, or None.

    Returns:
        The text, or None.

    """
    return None if moment is None else moment.isoformat()
