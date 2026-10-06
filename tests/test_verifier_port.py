"""E-08 at the port: the wrapper validates verifier answers, a routing port, the fake verifier."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from oris_matcher.domain.boq import BoqLine, LineKind, SectionHeader, make_line_id
from oris_matcher.domain.decision import LLMFailureKind
from oris_matcher.domain.library import load_library
from oris_matcher.llm.base import LLMRequest
from oris_matcher.llm.fake_llm import FakeBehaviour, FakeLLM, default_answer
from oris_matcher.llm.recording import MemoryCallSink, ReasonForCall
from oris_matcher.llm.routing import RoutingLLM
from oris_matcher.llm.wrapper import (
    MAIN_ANSWERS,
    VERIFIER_ANSWERS,
    BudgetLedger,
    LLMWrapper,
    WrapperDeps,
)
from oris_matcher.prompts.v1.render import CANONICAL_V1, build_request
from oris_matcher.prompts.v1.verifier import (
    VerifierAnswer,
    build_verifier_request,
    is_verifier_request,
    payload_codes,
    sibling_rows,
)
from oris_matcher.settings import (
    NEVER_MATCH_FILE,
    load_models_config,
    load_never_match,
    load_pricing,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
ALLOWLIST = load_models_config(CONFIG / "models.toml").allowlist.patterns
PRICING = load_pricing(CONFIG / "pricing.toml")
NEVER_MATCH = load_never_match(CONFIG / NEVER_MATCH_FILE).patterns
LIBRARY = load_library(
    (ROOT / "data" / "oris_materials_global.csv").read_bytes(), NEVER_MATCH, catalogue="global"
)
HAIKU = "claude-haiku-4-5-20251001"
FIXED_NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
TOP1 = LIBRARY.rows[0].code
ROWS = sibling_rows(LIBRARY, TOP1)


def _line(position: int, short: str) -> BoqLine:
    item_no = f"02.{position:02d}"
    return BoqLine(
        position=position,
        line_id=make_line_id(position, item_no, short, ""),
        item_no=item_no,
        short=short,
        long="",
        unit="m3",
        qty="1",
        kind=LineKind.ITEM,
        section_path=(SectionHeader("02", "Works"),),
        extra=(),
        raw_row=(item_no, short, "", "m3", "1"),
    )


LINES = {f"L{n}": _line(n, f"Line {n}") for n in (3, 4, 5)}


def verifier_build(ids: tuple[str, ...]) -> LLMRequest:
    request = build_verifier_request([LINES[i] for i in ids], ROWS, model=HAIKU, max_tokens=4096)
    return replace(request, line_ids=ids)


def main_build(ids: tuple[str, ...]) -> LLMRequest:
    request = build_request(
        [LINES[i] for i in ids], LIBRARY, CANONICAL_V1, model=HAIKU, max_tokens=4096
    )
    return replace(request, line_ids=ids)


async def _no_sleep(seconds: float) -> None:
    del seconds


def _wrapper(adapter: Any, cap_usd: float = 100.0) -> LLMWrapper:
    deps = WrapperDeps(sink=MemoryCallSink(), sleep=_no_sleep, now=lambda: FIXED_NOW)
    return LLMWrapper(adapter, PRICING, BudgetLedger(cap_usd), None, deps)


def _verdict(line_id: str, code: str, evidence: str = "Line") -> dict[str, Any]:
    return {"id": line_id, "evidence": evidence, "code": code}


# FakeLLM answers a verifier request with verifier objects


def test_the_fake_answers_a_verifier_request_with_the_first_given_code() -> None:
    fake = FakeLLM(HAIKU, ALLOWLIST)
    request = verifier_build(("L3", "L4"))
    result = asyncio.run(fake.complete(request))
    lines = json.loads(result.raw_text)["lines"]
    first = payload_codes(request.user_payload)[0]
    assert lines == [_verdict("L3", first, ""), _verdict("L4", first, "")]
    for line in lines:
        VerifierAnswer.model_validate(line)


def test_the_fake_serves_canned_verifier_answers_apart_from_main_answers() -> None:
    other = ROWS[-1].code
    main = {"L3": {**default_answer("L3"), "top1": TOP1}}
    behaviour = FakeBehaviour(answers=main, verifier_answers={"L3": _verdict("L3", other)})
    fake = FakeLLM(HAIKU, ALLOWLIST, behaviour)
    verified = json.loads(asyncio.run(fake.complete(verifier_build(("L3",)))).raw_text)
    answered = json.loads(asyncio.run(fake.complete(main_build(("L3",)))).raw_text)
    assert verified["lines"] == [_verdict("L3", other)]
    assert answered["lines"][0]["top1"] == TOP1


# The routing port: picked by request kind


def test_the_routing_port_sends_each_kind_to_its_adapter() -> None:
    main, verifier = FakeLLM(HAIKU, ALLOWLIST), FakeLLM(HAIKU, ALLOWLIST)
    router = RoutingLLM(main=main, verifier=verifier, is_verifier=is_verifier_request)
    asyncio.run(router.complete(main_build(("L3",))))
    asyncio.run(router.complete(verifier_build(("L3",))))
    asyncio.run(router.complete(main_build(("L4",))))
    assert [request.line_ids for request in main.calls] == [("L3",), ("L4",)]
    assert [request.line_ids for request in verifier.calls] == [("L3",)]
    assert router.routes == (main, verifier)


# The wrapper validates verifier objects, line by line


def test_the_wrapper_returns_a_verdict_for_each_verifier_line() -> None:
    code = ROWS[1].code
    answers = {i: _verdict(i, code) for i in LINES}
    fake = FakeLLM(HAIKU, ALLOWLIST, FakeBehaviour(verifier_answers=answers))
    outcome = asyncio.run(_wrapper(fake).run_batch(tuple(LINES), verifier_build, VERIFIER_ANSWERS))
    for line_id, line in outcome.lines.items():
        assert line.verdict == VerifierAnswer.model_validate(answers[line_id])
        assert line.answer is None
        assert line.failure is None
        assert json.loads(line.raw_line_response) == answers[line_id]
    assert len(outcome.records) == 1


def test_an_invalid_verifier_object_fails_only_its_line_and_keeps_its_raw_text() -> None:
    code = ROWS[0].code
    answers = {
        "L3": _verdict("L3", code),
        "L4": {"id": "L4", "evidence": "x"},
        "L5": {**_verdict("L5", code), "extra": 1},
    }
    fake = FakeLLM(HAIKU, ALLOWLIST, FakeBehaviour(verifier_answers=answers))
    outcome = asyncio.run(_wrapper(fake).run_batch(tuple(LINES), verifier_build, VERIFIER_ANSWERS))
    assert outcome.lines["L3"].verdict is not None
    for line_id in ("L4", "L5"):
        line = outcome.lines[line_id]
        assert line.failure == LLMFailureKind.MALFORMED
        assert json.loads(line.raw_line_response) == answers[line_id]


def test_a_missing_verifier_line_is_re_asked() -> None:
    wrapper = _wrapper(DroppingFake(drop="L4"))
    outcome = asyncio.run(wrapper.run_batch(("L3", "L4"), verifier_build, VERIFIER_ANSWERS))
    assert outcome.lines["L4"].verdict is not None
    reasons = [record.reason_for_call for record in outcome.records]
    assert reasons == [ReasonForCall.FIRST, ReasonForCall.REASK_MISSING]


class DroppingFake(FakeLLM):
    """Leaves one id out of its first answer only."""

    def __init__(self, drop: str) -> None:
        super().__init__(HAIKU, ALLOWLIST)
        self.drop = drop

    async def complete(self, req: LLMRequest) -> Any:
        result = await super().complete(req)
        if len(self.calls) > 1:
            return result
        document = json.loads(result.raw_text)
        document["lines"] = [line for line in document["lines"] if line["id"] != self.drop]
        return replace(result, raw_text=json.dumps(document), parsed=document)


def test_main_answers_are_still_the_default_schema() -> None:
    fake = FakeLLM(HAIKU, ALLOWLIST)
    wrapper = _wrapper(fake)
    outcome = asyncio.run(wrapper.run_batch(("L3",), main_build))
    explicit = asyncio.run(
        _wrapper(FakeLLM(HAIKU, ALLOWLIST)).run_batch(("L3",), main_build, MAIN_ANSWERS)
    )
    assert outcome.lines["L3"].answer is not None
    assert outcome.lines["L3"].verdict is None
    assert explicit.lines["L3"].answer == outcome.lines["L3"].answer


def test_a_main_answer_to_a_verifier_request_is_malformed() -> None:
    class MainOnly(FakeLLM):
        async def complete(self, req: LLMRequest) -> Any:
            result = await super().complete(main_build(req.line_ids))
            self.calls[-1] = req
            return result

    outcome = asyncio.run(
        _wrapper(MainOnly(HAIKU, ALLOWLIST)).run_batch(("L3",), verifier_build, VERIFIER_ANSWERS)
    )
    assert outcome.lines["L3"].failure == LLMFailureKind.MALFORMED


@pytest.mark.parametrize("schema", [MAIN_ANSWERS, VERIFIER_ANSWERS])
def test_a_verdict_and_an_answer_never_share_an_outcome(schema: Any) -> None:
    build = verifier_build if schema is VERIFIER_ANSWERS else main_build
    outcome = asyncio.run(_wrapper(FakeLLM(HAIKU, ALLOWLIST)).run_batch(("L3",), build, schema))
    line = outcome.lines["L3"]
    assert (line.answer is None) != (line.verdict is None)
