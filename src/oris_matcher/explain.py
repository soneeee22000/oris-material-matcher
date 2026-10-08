"""``oris explain``: why one line of a recorded run got its decision (DESIGN.md §12, A45).

Everything comes from the run folder (``manifest.json``, ``audit.jsonl``, ``calls.jsonl``,
``prompts/``) and from the input and library files its manifest names, which the caller
resolves and hash-checks as a replay does. Nothing here calls a model, reads a ground truth
or writes a file.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from oris_matcher.domain.boq import BoqFile, BoqLine
from oris_matcher.domain.decision import Rule, candidate_thresholds, section_path_text
from oris_matcher.domain.library import Library
from oris_matcher.io.audit import AUDIT_FILE, CALLS_FILE, PROMPT_SUFFIX, PROMPTS_DIR
from oris_matcher.llm.recording import CallRecord, read_calls_jsonl
from oris_matcher.service import BUCKET_LABELS

TEXT_ENCODING = "utf-8"
FALLBACK_PREFIX = "fallback_"
MAIN_KIND = "main"
VERIFIER_KIND = "verifier"
NO_VALUE = "none"
COST_DIGITS = 6
FIRST_PASS = 1
RULE_MEANINGS: Mapping[str, str] = MappingProxyType(
    {
        Rule.D0: "a structural header with empty Unit and Qty",
        Rule.D0A: "a fully empty row",
        Rule.D0B: "empty Unit and Qty, but not a confirmed header",
        Rule.D1: "no valid answer within the line's budget, a duplicate conflict or a replay miss",
        Rule.D1B: "a pass or voter the frozen threshold needs is missing after retries",
        Rule.D2: "non_material with a service unit, no hard attribute and no supply marker",
        Rule.D3: "non_material in any other case",
        Rule.D4: "the model found no library equivalent",
        Rule.D5: "top1 is unknown, malformed or inconsistent with its prefix",
        Rule.D5A: "a word of the evidence is not a word of the line",
        Rule.D6: "top1 is a configured never-match row",
        Rule.D7: "top1 conflicts with a hard attribute of the line",
        Rule.D8: "top1 is the blank leaf of a mixed parent and a sibling agrees",
        Rule.D8A: "the E-08 verifier disagrees with top1",
        Rule.D9: "score s is at or above the frozen threshold",
        Rule.D10: "anything else, including a vote tie: s is below the threshold",
    }
)


class ExplainError(ValueError):
    """The item is not in the run, or names more than one of its lines."""


@dataclass(frozen=True)
class RunRecords:
    """What ``explain`` reads of one recorded run.

    Attributes:
        run_dir: The run folder.
        manifest: Its ``manifest.json``.
        boq: The input the manifest names, already hash-checked.
        library: The library the manifest names, already hash-checked.

    """

    run_dir: Path
    manifest: Mapping[str, Any]
    boq: BoqFile
    library: Library


def read_audit(run_dir: Path) -> list[dict[str, Any]]:
    """Read every record of a run's ``audit.jsonl``.

    Args:
        run_dir: The run folder.

    Returns:
        The records in output order.

    """
    text = (run_dir / AUDIT_FILE).read_text(encoding=TEXT_ENCODING)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def find_record(records: Sequence[Mapping[str, Any]], item: str, run_id: str) -> Mapping[str, Any]:
    """Find the one audit record of an item number, or of a line id.

    Args:
        records: The run's audit records.
        item: An ``Item No.`` as written in the input, or a line id.
        run_id: The run id, for the error message.

    Returns:
        The record.

    Raises:
        ExplainError: No line, or more than one, has that item number.

    """
    found = [record for record in records if record["item_no"] == item]
    found = found or [record for record in records if record["line_id"] == item]
    if not found:
        raise ExplainError(f"item {item!r} is not in run {run_id} (no such Item No. or line id)")
    if len(found) > 1:
        ids = ", ".join(record["line_id"] for record in found)
        raise ExplainError(f"item {item!r} names {len(found)} lines of run {run_id}: {ids}")
    return found[0]


def _input_fields(line: BoqLine | None) -> dict[str, Any] | None:
    """Return the line's raw input cells and its section path, or None when absent."""
    if line is None:
        return None
    return {
        "short": line.short,
        "long": line.long,
        "unit": line.unit,
        "qty": line.qty,
        "kind": line.kind.value,
        "section_path": section_path_text(line),
    }


def _threshold_minimum(threshold_id: Any, passes: Any) -> dict[str, Any] | None:
    """Return the minimum score of a pre-registered threshold id, or None for any other id."""
    if not isinstance(passes, int) or passes < 1:
        return None
    for threshold in candidate_thresholds(passes):
        if threshold.threshold_id == threshold_id:
            minimum = threshold.minimum
            return {
                "v": minimum.votes,
                "b": minimum.attributes.value,
                "confidence_bucket": BUCKET_LABELS[minimum.confidence],
            }
    return None


def policy_fields(manifest: Mapping[str, Any], model: str) -> dict[str, Any]:
    """Return the policy and threshold the line was decided at.

    Args:
        manifest: The run manifest.
        model: The line's ``model`` column; the fallback model's lines use its policy.

    Returns:
        The policy id, threshold id and its minimum score, the resolution, what certified
        it, the profile, k and the decision profile.

    """
    fallback = model == manifest.get("fallback_model") and manifest.get("fallback_policy_id")
    prefix = FALLBACK_PREFIX if fallback else ""
    threshold_id = manifest.get(f"{prefix}threshold_id")
    return {
        "policy_id": manifest.get(f"{prefix}policy_id"),
        "threshold_id": threshold_id,
        "threshold_minimum": _threshold_minimum(threshold_id, manifest.get("passes_k")),
        "policy_resolution": manifest.get(f"{prefix}policy_resolution"),
        "certified_by": None if fallback else manifest.get("policy_certified_by"),
        "profile": manifest.get("profile"),
        "passes_k": manifest.get("passes_k"),
        "decision_profile": manifest.get("decision_profile"),
    }


def pass_answers(raw: Sequence[str]) -> list[dict[str, Any]]:
    """Parse the line's verbatim answer object from each pass.

    Args:
        raw: The audit's ``raw_line_response``, one JSON text per pass.

    Returns:
        One object per pass, numbered from 1; text that is not a JSON object is kept raw.

    """
    answers: list[dict[str, Any]] = []
    for number, text in enumerate(raw, start=FIRST_PASS):
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        body = parsed if isinstance(parsed, dict) else {"raw": text}
        answers.append({**body, "pass": number})
    return answers


def verifier_fields(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the E-08 verifier fields, or None when the run recorded none for the line.

    Args:
        record: The line's audit record.

    Returns:
        flagged, the verifier's top1, its failure, its raw answer and its call ids.

    """
    if "verifier_flagged" not in record:
        return None
    return {
        "flagged": record["verifier_flagged"],
        "top1": record.get("verifier_top1"),
        "failure": record.get("verifier_failure"),
        "raw": record.get("verifier_raw"),
        "call_ids": list(record.get("verifier_call_ids") or []),
    }


def row_fields(library: Library, code: str) -> dict[str, Any] | None:
    """Return a library row by its display code.

    Args:
        library: The run's library.
        code: A display code, possibly empty or unknown.

    Returns:
        The row's code, id, type, usage and subtype; ``in_library`` false for an unknown
        code; None for an empty code.

    """
    if not code:
        return None
    row = library.by_code.get(code)
    if row is None:
        return {"code": code, "in_library": False}
    return {
        "code": row.code,
        "row_id": row.row_id,
        "material_type": row.material_type,
        "material_usage": row.material_usage,
        "material_subtype": row.material_subtype,
        "in_library": True,
    }


def call_fields(
    call_id: str, calls: Mapping[str, CallRecord], verifier_ids: frozenset[str], run_dir: Path
) -> dict[str, Any]:
    """Return what the run recorded of one call.

    Args:
        call_id: The call id.
        calls: The run's call records by id.
        verifier_ids: The line's verifier call ids.
        run_dir: The run folder, to check the stored system prompt.

    Returns:
        Kind, request SHA-256, the stored system prompt path, provider ids, the served model,
        status, cost and latency; ``recorded`` false when ``calls.jsonl`` lacks the id.

    """
    kind = VERIFIER_KIND if call_id in verifier_ids else MAIN_KIND
    call = calls.get(call_id)
    if call is None:
        return {"call_id": call_id, "kind": kind, "recorded": False}
    prompt = f"{PROMPTS_DIR}/{call.system_blocks_sha256}{PROMPT_SUFFIX}"
    return {
        "call_id": call_id,
        "kind": kind,
        "recorded": True,
        "reason_for_call": call.reason_for_call.value,
        "attempt_no": call.attempt_no,
        "request_sha256": call.request_sha256,
        "system_prompt_file": prompt if (run_dir / prompt).is_file() else None,
        "provider_request_id": call.provider_request_id,
        "response_id": call.response_id,
        "response_model": call.response_model,
        "http_status": call.http_status,
        "error_class": call.error_class,
        "cache_hit": call.cache_hit,
        "source_call_id": call.source_call_id,
        "cost_usd": call.cost_usd,
        "latency_ms": call.latency_ms,
        "user_message": call.user_message,
    }


def _calls(record: Mapping[str, Any], run_dir: Path) -> list[dict[str, Any]]:
    """Return the line's calls, main passes and verifier, in the audit's order."""
    ids = list(record.get("call_ids") or [])
    ids += [item for item in record.get("verifier_call_ids") or [] if item not in ids]
    if not ids:
        return []
    calls = {call.call_id: call for call in read_calls_jsonl(run_dir / CALLS_FILE)}
    verifier_ids = frozenset(record.get("verifier_call_ids") or [])
    return [call_fields(call_id, calls, verifier_ids, run_dir) for call_id in ids]


def _line(boq: BoqFile, line_id: str) -> BoqLine | None:
    """Return the input line with a line id, or None."""
    return next((line for line in boq.lines if line.line_id == line_id), None)


def explain_item(records: RunRecords, item: str) -> dict[str, Any]:
    """Explain one line of a recorded run.

    Args:
        records: The run folder and the files its manifest names.
        item: An ``Item No.`` or a line id.

    Returns:
        The explanation, a JSON-ready object.

    Raises:
        ExplainError: The item is unknown or ambiguous.

    """
    manifest = records.manifest
    record = find_record(read_audit(records.run_dir), item, str(manifest.get("run_id")))
    explanation = _identity(manifest, record)
    explanation.update(_decision(record, records))
    explanation["input"] = _input_fields(_line(records.boq, record["line_id"]))
    explanation["calls"] = _calls(record, records.run_dir)
    return explanation


def _identity(manifest: Mapping[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
    """Return the run and line identity fields."""
    return {
        "run_id": manifest.get("run_id"),
        "mode": manifest.get("mode"),
        "library_id": manifest.get("library_id"),
        "item_no": record["item_no"],
        "line_id": record["line_id"],
        "position": record.get("position"),
        "transport_id": record.get("transport_id"),
    }


def _decision(record: Mapping[str, Any], records: RunRecords) -> dict[str, Any]:
    """Return the decision, the rule and policy behind it, the passes, rows, cost and latency."""
    rule = str(record.get("rule") or "")
    top1, top2 = str(record.get("top1") or ""), str(record.get("top2") or "")
    matched = record["decision"] == "matched"
    return {
        "decision": record["decision"],
        "reason": record["reason"],
        "rule": rule,
        "rule_meaning": RULE_MEANINGS.get(rule),
        "flags": list(record.get("flags") or []),
        "context": record.get("context"),
        "model": record.get("model"),
        "model_called": bool(record.get("call_ids")),
        "policy": policy_fields(records.manifest, str(record.get("model"))),
        "signals": record.get("signals"),
        "attribute_result": record.get("attribute_result"),
        "passes": pass_answers(record.get("raw_line_response") or []),
        "verifier": verifier_fields(record),
        "matched_row": row_fields(records.library, top1) if matched else None,
        "suggestions": [row_fields(records.library, code) for code in (top1, top2) if code],
        "suggested_row_id": record.get("suggested_row_id"),
        "cost_usd": record.get("cost_usd"),
        "latency_ms": record.get("latency_ms"),
    }


def render_json(explanation: Mapping[str, Any]) -> str:
    """Render an explanation as one JSON object with sorted keys.

    Args:
        explanation: From ``explain_item``.

    Returns:
        The JSON text.

    """
    return json.dumps(explanation, sort_keys=True, ensure_ascii=False)


def _or_none(value: Any) -> str:
    """Render a missing value as ``none``."""
    return NO_VALUE if value is None or value == "" else str(value)


def _header_lines(explanation: Mapping[str, Any]) -> list[str]:
    """Render the line identity and its input."""
    lines = [
        f"item {explanation['item_no']} (line {explanation['line_id']}, position "
        f"{explanation['position']}, transport {_or_none(explanation['transport_id'])}) in run "
        f"{explanation['run_id']} [mode {explanation['mode']}, library {explanation['library_id']}]"
    ]
    fields = explanation["input"]
    if fields is None:
        return [*lines, "input: the line is not in the recorded input"]
    return [
        *lines,
        f"input: {fields['short']!r} | unit {_or_none(fields['unit'])} | "
        f"qty {_or_none(fields['qty'])} | kind {fields['kind']}",
        f"long: {fields['long']!r}",
        f"section path: {_or_none(fields['section_path'])}",
    ]


def _signals_text(signals: Mapping[str, Any] | None) -> str:
    """Render score s as ``v 2, b agree, confidence >=90``."""
    if not signals:
        return NO_VALUE
    return (
        f"v {signals.get('v')}, b {signals.get('b')}, confidence {signals.get('confidence_bucket')}"
    )


def _decision_lines(explanation: Mapping[str, Any]) -> list[str]:
    """Render the decision, the rule that fired and the policy it was decided at."""
    policy = explanation["policy"]
    minimum = policy["threshold_minimum"]
    bar = (
        f"v >= {minimum['v']}, b >= {minimum['b']}, confidence {minimum['confidence_bucket']}"
        if minimum
        else "not a score threshold"
    )
    return [
        f"decision: {explanation['decision']}, reason {explanation['reason']}, rule "
        f"{explanation['rule']} ({_or_none(explanation['rule_meaning'])})",
        f"policy {_or_none(policy['policy_id'])} ({_or_none(policy['policy_resolution'])}, "
        f"certified by {_or_none(policy['certified_by'])}), profile {policy['profile']}, "
        f"k {policy['passes_k']}; threshold {_or_none(policy['threshold_id'])}: {bar}",
        f"signals: {_signals_text(explanation['signals'])}; attributes "
        f"{_or_none(explanation['attribute_result'])}; flags {explanation['flags'] or NO_VALUE}",
    ]


def _pass_lines(explanation: Mapping[str, Any]) -> list[str]:
    """Render each pass's answer for the line, then the verifier's."""
    if not explanation["model_called"]:
        return [f"no model call was made: the line was decided by rule {explanation['rule']}"]
    lines = [
        f"pass {answer['pass']}: kind {_or_none(answer.get('kind'))}, top1 "
        f"{_or_none(answer.get('top1'))}, top2 {_or_none(answer.get('top2'))}, confidence "
        f"{_or_none(answer.get('confidence'))}, evidence {answer.get('evidence', '')!r}"
        if "raw" not in answer
        else f"pass {answer['pass']}: unparsed {answer['raw']!r}"
        for answer in explanation["passes"]
    ]
    verifier = explanation["verifier"]
    if verifier is not None:
        lines.append(
            f"verifier: flagged {verifier['flagged']}, top1 {_or_none(verifier['top1'])}, "
            f"failure {_or_none(verifier['failure'])}"
        )
    return lines


def _row_text(row: Mapping[str, Any] | None) -> str:
    """Render a library row as code, type / usage / subtype."""
    if row is None:
        return NO_VALUE
    if not row["in_library"]:
        return f"{row['code']} (not a library code)"
    return (
        f"{row['code']} {row['material_type']} / {row['material_usage']} / "
        f"{_or_none(row['material_subtype'])}"
    )


def _row_lines(explanation: Mapping[str, Any]) -> list[str]:
    """Render the matched row, the suggestions, the cost and the latency."""
    lines = [f"matched row: {_row_text(explanation['matched_row'])}"]
    lines += [
        f"suggestion {rank}: {_row_text(row)}"
        for rank, row in enumerate(explanation["suggestions"], start=FIRST_PASS)
    ]
    cost = float(explanation["cost_usd"] or 0.0)
    lines.append(
        f"attributed cost ${cost:.{COST_DIGITS}f}, latency {explanation['latency_ms']} ms, "
        f"model {explanation['model']}"
    )
    return lines


def _call_lines(explanation: Mapping[str, Any]) -> list[str]:
    """Render each call's ids and where its prompt is stored."""
    lines = []
    for call in explanation["calls"]:
        if not call["recorded"]:
            lines.append(f"call {call['call_id']} ({call['kind']}): not in {CALLS_FILE}")
            continue
        lines.append(
            f"call {call['call_id']} ({call['kind']}, {call['reason_for_call']}): request "
            f"sha256 {call['request_sha256']}, system prompt "
            f"{_or_none(call['system_prompt_file'])}, provider request id "
            f"{_or_none(call['provider_request_id'])}"
        )
    return lines


def render_text(explanation: Mapping[str, Any]) -> str:
    """Render an explanation for the terminal.

    Args:
        explanation: From ``explain_item``.

    Returns:
        The text, one fact per line.

    """
    sections = (_header_lines, _decision_lines, _pass_lines, _row_lines, _call_lines)
    return "\n".join(line for section in sections for line in section(explanation))
