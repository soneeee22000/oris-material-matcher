"""The pre-registered v1 output schema (DESIGN.md §9.4).

Field order is the model's only "think first" lever, so it is part of the contract. Ranges and
lengths are not in the JSON schema (structured outputs do not enforce them); after-validators on
``LineAnswer`` check them during strict validation (§11.3) without changing the generated schema.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CONFIDENCE_MIN = 0
CONFIDENCE_MAX = 100
EVIDENCE_MAX_WORDS = 12
SCHEMA_TITLE_KEY = "title"

Kind = Literal["material", "non_material", "no_equivalent"]
NmCategory = Literal["labour", "service", "temporary_works", "fee", "hire", "other", ""]
CandidateGap = Literal["decisive", "clear", "narrow", "tossup"]


class LineAnswer(BaseModel):
    """The model's answer for one line, fields in the pre-registered order."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    id: str
    evidence: str
    element_or_application: str
    material_family: str
    kind: Kind
    nm_category: NmCategory
    top1: str
    top2: str
    confidence: int
    self_reported_candidate_gap: CandidateGap

    @field_validator("kind", "nm_category", "self_reported_candidate_gap", mode="before")
    @classmethod
    def _casefold_enum(cls, value: object) -> object:
        """Compare enum values case-insensitively (DESIGN.md §11.3)."""
        return value.casefold() if isinstance(value, str) else value

    @field_validator("confidence", mode="after")
    @classmethod
    def _confidence_in_range(cls, confidence: int) -> int:
        """Reject a confidence outside ``CONFIDENCE_MIN..CONFIDENCE_MAX``."""
        if not CONFIDENCE_MIN <= confidence <= CONFIDENCE_MAX:
            raise ValueError(f"confidence {confidence} outside {CONFIDENCE_MIN}..{CONFIDENCE_MAX}")
        return confidence

    @field_validator("evidence", mode="after")
    @classmethod
    def _evidence_short_enough(cls, evidence: str) -> str:
        """Reject evidence longer than ``EVIDENCE_MAX_WORDS`` words."""
        word_count = len(evidence.split())
        if word_count > EVIDENCE_MAX_WORDS:
            raise ValueError(f"evidence has {word_count} words, more than {EVIDENCE_MAX_WORDS}")
        return evidence


class BatchAnswer(BaseModel):
    """The model's answer for one batch."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    lines: tuple[LineAnswer, ...] = Field(strict=False)


def _close_objects(node: Any) -> Any:
    """Return a copy of a schema node with titles dropped and every object closed.

    A closed object forbids extras and requires all its fields.
    """
    if isinstance(node, list):
        return [_close_objects(item) for item in node]
    if not isinstance(node, dict):
        return node
    closed = {
        key: _close_objects(value)
        for key, value in node.items()
        if not (key == SCHEMA_TITLE_KEY and isinstance(value, str))
    }
    if closed.get("type") == "object" and "properties" in closed:
        closed["additionalProperties"] = False
        closed["required"] = list(closed["properties"])
    return closed


def output_json_schema() -> dict[str, Any]:
    """Build the JSON schema sent through native structured outputs.

    Every object has ``additionalProperties: false`` and lists all its fields as required.
    Generated ``title`` strings are dropped, so only intended text reaches the model.

    Returns:
        The batch answer schema.

    """
    closed: dict[str, Any] = _close_objects(BatchAnswer.model_json_schema())
    return closed
