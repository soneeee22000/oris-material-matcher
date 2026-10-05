import dataclasses
import hashlib
import json
from typing import Any

import pytest
from pydantic import ValidationError

from oris_matcher.domain.attributes import Attributes, AttrResult
from oris_matcher.domain.boq import (
    LINE_ID_LENGTH,
    BoqFile,
    BoqLine,
    Column,
    LineKind,
    PathMode,
    SectionHeader,
    make_line_id,
)
from oris_matcher.domain.decision import (
    ENSEMBLE_DEGRADED_PREFIX,
    LLM_FAILURE_PREFIX,
    LOW_SIGNAL_PREFIX,
    NOT_A_MATERIAL_REASONS,
    SIGNAL_PREFIX,
    Decision,
    LLMFailureKind,
    ReasonCode,
    SignalName,
    ensemble_degraded,
    is_frozen_reason,
    llm_failure_reason,
    low_signal_reason,
    signal_reason,
)
from oris_matcher.domain.library import LibraryRow
from oris_matcher.llm.base import (
    ANTHROPIC_RATE_LIMIT_PREFIX,
    OPENAI_RATE_LIMIT_PREFIX,
    LLMPort,
    LLMRequest,
    LLMResult,
    LLMStatus,
    SystemBlock,
    Usage,
    canonical_json,
    select_rate_limit_headers,
)
from oris_matcher.prompts.v1.schema import (
    EVIDENCE_MAX_WORDS,
    BatchAnswer,
    LineAnswer,
    output_json_schema,
)

FROZEN_REASON_CODES = [
    "HEADER",
    "EMPTY_ROW",
    "HEADER_UNCONFIRMED",
    "G2_SERVICE",
    "NM_UNCONFIRMED",
    "NO_LIBRARY_EQUIVALENT",
    "INVALID_ROW_ID",
    "EVIDENCE_NOT_IN_LINE",
    "NEVER_MATCH_ROW",
    "ATTR_CONFLICT",
    "GENERIC_PARENT",
    "VERIFIER_DISAGREES",
    "LLM_UNAVAILABLE",
    "BUDGET_CAP",
    "INTERNAL_INVARIANT",
]
FROZEN_FAILURE_KINDS = [
    "timeout",
    "rate_limited",
    "overloaded",
    "api_error",
    "truncated",
    "malformed",
    "refusal",
    "missing_item",
    "duplicate_conflict",
    "partial_signal",
    "replay_miss",
]
PINNED_REQUEST_SHA256 = "5388b37c73a6b43393020dfe2845ae73d21603dab94e1a16b97df570fb6294a6"
PINNED_LINE_ID = "864a6a422cb90bcf"
PINNED_LINE_ID_NON_ASCII = "e9d9c38ea3403fb2"
PINNED_SCHEMA_SHA256 = "b14021c3c33b77b211d70433c3fa10888aa71eb1243817e5a463b9773b4949e6"
SCHEMA_FIELD_ORDER = [
    "id",
    "evidence",
    "element_or_application",
    "material_family",
    "kind",
    "nm_category",
    "top1",
    "top2",
    "confidence",
    "self_reported_candidate_gap",
]


def test_reason_codes_equal_frozen_list() -> None:
    assert [member.name for member in ReasonCode] == FROZEN_REASON_CODES
    assert [member.value for member in ReasonCode] == FROZEN_REASON_CODES


def test_reason_prefixes() -> None:
    assert SIGNAL_PREFIX == "SIGNAL:"
    assert LOW_SIGNAL_PREFIX == "LOW_SIGNAL:"
    assert ENSEMBLE_DEGRADED_PREFIX == "ENSEMBLE_DEGRADED:"
    assert LLM_FAILURE_PREFIX == "LLM_FAILURE:"


def test_llm_failure_kinds_and_reason() -> None:
    assert [member.value for member in LLMFailureKind] == FROZEN_FAILURE_KINDS
    assert llm_failure_reason(LLMFailureKind.REPLAY_MISS) == "LLM_FAILURE:replay_miss"
    assert llm_failure_reason(LLMFailureKind.PARTIAL_SIGNAL) == "LLM_FAILURE:partial_signal"


def test_signal_reason_builders() -> None:
    assert signal_reason("T3") == "SIGNAL:T3"
    assert [member.value for member in SignalName] == ["v", "b", "confidence"]
    assert low_signal_reason([SignalName.VOTES]) == "LOW_SIGNAL:v"
    assert (
        low_signal_reason([SignalName.CONFIDENCE, SignalName.VOTES, SignalName.CONFIDENCE])
        == "LOW_SIGNAL:v+confidence"
    )
    assert low_signal_reason(reversed(list(SignalName))) == "LOW_SIGNAL:v+b+confidence"
    assert ensemble_degraded("SIGNAL:T3") == "ENSEMBLE_DEGRADED:SIGNAL:T3"
    assert ensemble_degraded("LOW_SIGNAL:b") == "ENSEMBLE_DEGRADED:LOW_SIGNAL:b"


@pytest.mark.parametrize("threshold_id", ["", "T 3", "T:3"])
def test_signal_reason_rejects_bad_threshold_id(threshold_id: str) -> None:
    with pytest.raises(ValueError, match="threshold"):
        signal_reason(threshold_id)


def test_low_signal_reason_rejects_empty() -> None:
    with pytest.raises(ValueError, match="signal"):
        low_signal_reason([])


@pytest.mark.parametrize(
    "reason", ["HEADER", "LLM_FAILURE:timeout", "ENSEMBLE_DEGRADED:SIGNAL:T3", "bogus"]
)
def test_ensemble_degraded_wraps_signal_forms_only(reason: str) -> None:
    with pytest.raises(ValueError, match="SIGNAL"):
        ensemble_degraded(reason)


def _every_frozen_form() -> list[str]:
    reasons = [member.value for member in ReasonCode]
    reasons += [llm_failure_reason(kind) for kind in LLMFailureKind]
    signal_forms = [signal_reason("T3"), low_signal_reason([SignalName.ATTRIBUTES])]
    signal_forms.append(low_signal_reason(list(SignalName)))
    reasons += signal_forms + [ensemble_degraded(form) for form in signal_forms]
    return reasons


@pytest.mark.parametrize("reason", _every_frozen_form())
def test_is_frozen_reason_accepts_every_member_and_form(reason: str) -> None:
    assert is_frozen_reason(reason)


@pytest.mark.parametrize(
    "reason",
    [
        "",
        "header",
        "TRUNCATED",
        "LLM_FAILURE:bogus",
        "LLM_FAILURE:",
        "LLM_FAILURE:Timeout",
        "SIGNAL:",
        "SIGNAL:T 3",
        "LOW_SIGNAL:",
        "LOW_SIGNAL:x",
        "LOW_SIGNAL:b+v",
        "LOW_SIGNAL:v+v",
        "LOW_SIGNAL:v,b",
        "ENSEMBLE_DEGRADED:",
        "ENSEMBLE_DEGRADED:HEADER",
        "ENSEMBLE_DEGRADED:LLM_FAILURE:timeout",
        "ENSEMBLE_DEGRADED:ENSEMBLE_DEGRADED:SIGNAL:T3",
        " HEADER",
    ],
)
def test_is_frozen_reason_rejects_other_strings(reason: str) -> None:
    assert not is_frozen_reason(reason)


def test_decisions_and_rq5_reasons() -> None:
    assert [member.value for member in Decision] == ["matched", "not_a_material", "needs_review"]
    assert frozenset({"HEADER", "EMPTY_ROW", "G2_SERVICE"}) == NOT_A_MATERIAL_REASONS
    assert all(isinstance(reason, ReasonCode) for reason in NOT_A_MATERIAL_REASONS)


def test_attr_result_values() -> None:
    assert {member.value for member in AttrResult} == {"conflict", "agree", "no_evidence"}


def test_line_kind_members() -> None:
    assert [member.name for member in LineKind] == [
        "HEADER",
        "EMPTY_ROW",
        "HEADER_UNCONFIRMED",
        "ITEM",
    ]


def _line(**overrides: Any) -> BoqLine:
    values: dict[str, Any] = {
        "position": 3,
        "line_id": make_line_id(3, "01.02.0010.", "Concrete C30/37", ""),
        "item_no": "01.02.0010.",
        "short": "Concrete C30/37",
        "long": "",
        "unit": "m³",
        "qty": "12,5",
        "kind": LineKind.ITEM,
        "section_path": (SectionHeader(item_no="01", text="Earthworks "),),
        "extra": (("Notes", "x"),),
        "raw_row": ("01.02.0010.", "Concrete C30/37", "", "m³", "12,5", "x"),
    }
    values.update(overrides)
    return BoqLine(**values)


def test_boq_line_is_frozen_and_hashable() -> None:
    line = _line()
    with pytest.raises(dataclasses.FrozenInstanceError):
        line.short = "changed"  # type: ignore[misc]
    assert hash(line) == hash(_line())
    assert line.extra_map == {"Notes": "x"}


def test_boq_file_column_lookup() -> None:
    boq = BoqFile(
        lines=(_line(),),
        header=("Item No.", "Short Description", "Long Description", "Unit", "BoQ Qty", "Notes"),
        encoding="utf-8",
        delimiter=",",
        path_mode=PathMode.DERIVED,
        column_map=((Column.ITEM_NO, "Item No."), (Column.UNIT, "Unit")),
    )
    assert boq.column(Column.UNIT) == "Unit"
    assert boq.column_dict == {"item_no": "Item No.", "unit": "Unit"}
    assert boq.path_mode == "derived"
    with pytest.raises(KeyError):
        boq.column(Column.QTY)


def test_make_line_id() -> None:
    first = make_line_id(0, "0", "Preliminaries", "")
    assert len(first) == LINE_ID_LENGTH == 16
    assert int(first, 16) >= 0
    assert first == make_line_id(0, "0", "Preliminaries", "")
    assert first != make_line_id(1, "0", "Preliminaries", "")
    assert first != make_line_id(0, "00", "Preliminaries", "")
    assert first != make_line_id(0, "0", "Preliminaries ", "")
    assert first != make_line_id(0, "0", "Preliminaries", " ")
    assert make_line_id(0, "a", "b", "") != make_line_id(0, "", "ab", "")


def test_make_line_id_golden_values() -> None:
    assert make_line_id(0, "0", "Preliminaries", "") == PINNED_LINE_ID
    assert make_line_id(7, "02.01.", "Béton C30/37 m³", "Fourniture é") == (
        PINNED_LINE_ID_NON_ASCII
    )


def test_library_row_frozen() -> None:
    row = LibraryRow(
        row_id="abc",
        code="T01.U01.S00",
        material_type="x",
        material_usage="y",
        material_subtype="",
        normalized_text="x y",
        attributes=Attributes(),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        row.code = "T02"  # type: ignore[misc]


def _request(**overrides: Any) -> LLMRequest:
    values: dict[str, Any] = {
        "provider": "anthropic",
        "model": "claude-haiku-4-5-20251001",
        "system_blocks": (
            SystemBlock(text="vocabulary", cache=False),
            SystemBlock(text="library tree é", cache=True),
        ),
        "schema_json": canonical_json({"type": "object", "properties": {}}),
        "user_payload": '<boq_lines>{"lines": []}</boq_lines>',
        "max_tokens": 2048,
        "temperature": 0.0,
        "line_ids": ("L1", "L2"),
    }
    values.update(overrides)
    return LLMRequest(**values)


def test_request_sha256_is_stable() -> None:
    digest = _request().sha256()
    assert digest == _request().sha256()
    assert len(digest) == 64
    assert digest == PINNED_REQUEST_SHA256


@pytest.mark.parametrize(
    "override",
    [
        {"model": "claude-haiku-4-5-20260101"},
        {"system_blocks": (SystemBlock(text="vocabulary", cache=False),)},
        {
            "system_blocks": (
                SystemBlock(text="vocabulary", cache=False),
                SystemBlock(text="library tree é", cache=False),
            )
        },
        {
            "system_blocks": (
                SystemBlock(text="vocabulary", cache=False),
                SystemBlock(text="library tree e", cache=True),
            )
        },
        {"schema_json": canonical_json({"type": "object"})},
        {"user_payload": '<boq_lines>{"lines": [1]}</boq_lines>'},
        {"max_tokens": 2049},
        {"temperature": 0.5},
    ],
)
def test_request_sha256_sensitive_to_each_field(override: dict[str, Any]) -> None:
    assert _request(**override).sha256() != _request().sha256()


@pytest.mark.parametrize(
    "override",
    [
        {"provider": "openai"},
        {"line_ids": ("X",)},
        {"line_ids": ()},
        {"temperature": 0},
    ],
)
def test_request_sha256_ignores_excluded_fields(override: dict[str, Any]) -> None:
    assert _request(**override).sha256() == _request().sha256() == PINNED_REQUEST_SHA256


@pytest.mark.parametrize("schema_json", ["", "{", "[]", '"object"', "null", "1"])
def test_request_rejects_schema_json_that_is_not_an_object(schema_json: str) -> None:
    with pytest.raises(ValueError, match="schema_json"):
        _request(schema_json=schema_json)


def test_request_sha256_ignores_key_order_in_schema() -> None:
    reordered = '{"properties":{},"type":"object"}'
    spaced = '{"type": "object", "properties": {}}'
    assert _request(schema_json=spaced).sha256() == _request(schema_json=reordered).sha256()


def test_request_is_frozen_and_schema_roundtrips() -> None:
    request = _request()
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.model = "x"  # type: ignore[misc]
    assert request.schema() == {"type": "object", "properties": {}}


def test_llm_result_defaults_and_status() -> None:
    result = LLMResult(status=LLMStatus.TIMEOUT, raw_text="")
    assert result.parsed is None
    assert result.usage == Usage(input_tokens=0, output_tokens=0, cache_read=0, cache_write=0)
    assert result.finish_reasons == ()
    assert result.rate_limit_headers == ()
    assert {member.value for member in LLMStatus} == {
        "ok",
        "timeout",
        "rate_limited",
        "overloaded",
        "api_error",
        "truncated",
        "refusal",
        "malformed",
    }


def test_llm_result_with_parsed_is_hashable_and_isolated() -> None:
    source: dict[str, Any] = {"a": 1, "nested": {"b": 2}}
    result = LLMResult(status=LLMStatus.OK, raw_text='{"a":1}', parsed=source)
    assert isinstance(hash(result), int)
    assert hash(result) == hash(LLMResult(status=LLMStatus.OK, raw_text='{"a":1}', parsed={}))
    source["a"] = 99
    source["nested"]["b"] = 99
    assert result.parsed is not None
    assert result.parsed["a"] == 1
    assert result.parsed["nested"] == {"b": 2}
    with pytest.raises(TypeError):
        result.parsed["a"] = 2  # type: ignore[index]


def test_llm_result_carries_rate_limit_headers() -> None:
    headers = (("anthropic-ratelimit-requests-remaining", "49"),)
    result = LLMResult(status=LLMStatus.OK, raw_text="", rate_limit_headers=headers)
    assert result.rate_limit_headers == headers
    assert isinstance(hash(result), int)


def test_select_rate_limit_headers() -> None:
    headers = {
        "Anthropic-RateLimit-Requests-Remaining": "49",
        "anthropic-ratelimit-tokens-limit": "50000",
        "x-ratelimit-remaining-requests": "9",
        "request-id": "req_1",
    }
    assert ANTHROPIC_RATE_LIMIT_PREFIX == "anthropic-ratelimit-"
    assert OPENAI_RATE_LIMIT_PREFIX == "x-ratelimit-"
    assert select_rate_limit_headers(headers, ANTHROPIC_RATE_LIMIT_PREFIX) == (
        ("anthropic-ratelimit-requests-remaining", "49"),
        ("anthropic-ratelimit-tokens-limit", "50000"),
    )
    assert select_rate_limit_headers(headers, OPENAI_RATE_LIMIT_PREFIX) == (
        ("x-ratelimit-remaining-requests", "9"),
    )
    assert select_rate_limit_headers({}, ANTHROPIC_RATE_LIMIT_PREFIX) == ()


class _EchoLLM:
    async def complete(self, req: LLMRequest) -> LLMResult:
        return LLMResult(status=LLMStatus.OK, raw_text=req.user_payload)


async def test_llm_port_protocol() -> None:
    port: LLMPort = _EchoLLM()
    result = await port.complete(_request())
    assert result.status == LLMStatus.OK


def _answer(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": "L118",
        "evidence": "concrete C30/37 for footings",
        "element_or_application": "footings",
        "material_family": "concrete",
        "kind": "material",
        "nm_category": "",
        "top1": "T05.U03.S02",
        "top2": "",
        "confidence": 85,
        "self_reported_candidate_gap": "clear",
    }
    values.update(overrides)
    return values


def test_schema_field_order() -> None:
    assert list(LineAnswer.model_fields) == SCHEMA_FIELD_ORDER
    schema = output_json_schema()
    line_schema = schema["$defs"]["LineAnswer"]
    assert list(line_schema["properties"]) == SCHEMA_FIELD_ORDER
    assert line_schema["required"] == SCHEMA_FIELD_ORDER


def test_output_schema_is_pinned() -> None:
    digest = hashlib.sha256(canonical_json(output_json_schema()).encode("utf-8")).hexdigest()
    assert digest == PINNED_SCHEMA_SHA256


def test_output_schema_is_closed_and_has_no_range_constraints() -> None:
    schema = output_json_schema()
    text = json.dumps(schema)
    for keyword in ("minimum", "maximum", "maxLength", "minLength", "default", "title"):
        assert f'"{keyword}"' not in text
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["lines"]
    assert schema["$defs"]["LineAnswer"]["additionalProperties"] is False


def test_line_answer_parses_and_forbids_extra() -> None:
    batch = BatchAnswer.model_validate({"lines": [_answer()]})
    assert batch.lines[0].top1 == "T05.U03.S02"
    assert isinstance(batch.lines, tuple)
    assert isinstance(hash(batch), int)
    from_json = BatchAnswer.model_validate_json(json.dumps({"lines": [_answer()]}))
    assert from_json == batch
    with pytest.raises(ValidationError):
        LineAnswer.model_validate(_answer(rationale="extra"))
    with pytest.raises(ValidationError):
        LineAnswer.model_validate(_answer(kind="maybe"))


def test_line_answer_enums_case_insensitive() -> None:
    answer = LineAnswer.model_validate(
        _answer(kind="Material", nm_category="", self_reported_candidate_gap="CLEAR")
    )
    assert answer.kind == "material"
    assert answer.self_reported_candidate_gap == "clear"


@pytest.mark.parametrize("confidence", [0, 1, 99, 100])
def test_line_answer_accepts_confidence_in_range(confidence: int) -> None:
    assert LineAnswer.model_validate(_answer(confidence=confidence)).confidence == confidence


@pytest.mark.parametrize("confidence", [-1, 101, 150])
def test_line_answer_rejects_confidence_out_of_range(confidence: int) -> None:
    with pytest.raises(ValidationError, match="confidence"):
        LineAnswer.model_validate(_answer(confidence=confidence))
    with pytest.raises(ValidationError, match="confidence"):
        LineAnswer.model_validate_json(json.dumps(_answer(confidence=confidence)))


def test_line_answer_evidence_word_limit() -> None:
    assert EVIDENCE_MAX_WORDS == 25
    at_limit = " ".join(["word"] * 25)
    assert LineAnswer.model_validate(_answer(evidence=at_limit)).evidence == at_limit
    assert LineAnswer.model_validate(_answer(evidence="")).evidence == ""
    with pytest.raises(ValidationError, match="evidence"):
        LineAnswer.model_validate(_answer(evidence=at_limit + " more"))
    batch = json.dumps({"lines": [_answer(evidence=at_limit + " more")]})
    with pytest.raises(ValidationError, match="evidence"):
        BatchAnswer.model_validate_json(batch)
