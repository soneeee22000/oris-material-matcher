"""`oris select` over E-08 runs: a threshold is measured only where every verifier call replays.

Re-deciding a verifier-on run at another threshold changes which lines are flagged, so the
verifier groups and their request hashes change and the recorded run cannot answer them. Such a
threshold is unmeasured at $0; its ceiling is the verifier-off replay, since a veto only removes
matches. The selection refuses unless no unmeasured threshold could be selected (A67).
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from oris_matcher.doctor import read_manifest
from oris_matcher.domain.library import load_library
from test_select_threshold import (
    CLASSES,
    GLOBAL_LIBRARY,
    ITEMS,
    REFERENCE,
    _environment,
    _run,
    first_dev_items,
    make_runtime,
    selector,
    write_split,
)

HAIKU = "claude-haiku-4-5-20251001"
E08_ID = "E-fake"
E08_STRICT_ID = "E-fake-T6"
STRICT = "T6"
LANGUAGES = ("en", "fr")
rule = selector.rule


@dataclass(frozen=True)
class VerifierWorld:
    """Fake verifier-on B3 dev runs: one pair recorded at T8, one pair forced to T6.

    In this fixture T6 flags a subset of T8's lines that regroups them, so the T8 runs cannot
    answer its verifier requests.
    """

    root: Path
    split: Path
    ledger: Path
    t8: dict[str, Path]
    strict: dict[str, Path]
    off_en: Path


def policy_file(root: Path, threshold_id: str) -> Path:
    """Write an override policy forcing one threshold for Haiku on the global library."""
    sha = load_library(GLOBAL_LIBRARY.read_bytes()).sha256
    entry = {"policy_id": threshold_id, "certified_by": "dev_selection"}
    path = root / f"policy_{threshold_id}.yaml"
    path.write_text(json.dumps({"policies": {HAIKU: {sha: entry}}}), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Iterator[VerifierWorld]:
    root = tmp_path_factory.mktemp("select_e08")
    split = write_split(root / "split.json", first_dev_items(ITEMS))
    forced = ("--policy", str(policy_file(root, STRICT)))
    with pytest.MonkeyPatch.context() as patch:
        _environment(patch, root)
        patch.setenv("ORIS_ENRICHMENT", "none")
        t8 = {
            lang: _run(root, split, lang, "--id", E08_ID, "--verifier-adopted")
            for lang in LANGUAGES
        }
        strict = {
            lang: _run(root, split, lang, "--id", E08_STRICT_ID, "--verifier-adopted", *forced)
            for lang in LANGUAGES
        }
        off_en = _run(root, split, "en", "--id", "B3-off")
    yield VerifierWorld(root, split, root / "experiments.jsonl", t8, strict, off_en)


@pytest.fixture
def env(world: VerifierWorld, monkeypatch: pytest.MonkeyPatch) -> VerifierWorld:
    _environment(monkeypatch, world.root)
    monkeypatch.setenv("ORIS_ENRICHMENT", "none")
    return world


def _inputs(world: VerifierWorld) -> object:
    return selector.ScoreInputs(REFERENCE, world.split, CLASSES)


def _stats(matched: int, correct: int) -> object:
    return rule.language_stats(matched, correct)


# The runner's --policy override


def test_the_runner_decides_a_run_at_a_forced_policy(env: VerifierWorld) -> None:
    manifest = read_manifest(env.strict["en"])
    assert manifest["policy_resolution"] == "override"
    assert manifest["policy_id"] == STRICT
    assert manifest["decision_profile"]["verifier_adopted"] is True
    rows = [json.loads(line) for line in env.ledger.read_text(encoding="utf-8").splitlines()]
    forced = [row for row in rows if row["id"] == E08_STRICT_ID]
    assert {row["policy_resolution"] for row in forced} == {"override"}


# Which thresholds a run measures


def test_a_verifier_replay_miss_is_counted_not_hidden(env: VerifierWorld, tmp_path: Path) -> None:
    runtime = make_runtime(env.root)
    exact = selector.redecide(env.t8["en"], "T8", runtime, tmp_path / "t8")
    stricter = selector.redecide(env.t8["en"], STRICT, runtime, tmp_path / "strict")
    assert selector.unanswered_verifier_lines(exact.result) == 0
    assert selector.unanswered_verifier_lines(stricter.result) > 0


def test_each_threshold_is_measured_by_the_first_run_that_answers_it(
    env: VerifierWorld, tmp_path: Path
) -> None:
    runtime = make_runtime(env.root)
    runs = [env.t8["en"], env.strict["en"]]
    at_t8 = selector.measure(runs, "T8", runtime, tmp_path / "t8")
    at_strict = selector.measure(runs, STRICT, runtime, tmp_path / "strict")
    alone = selector.measure(runs[:1], STRICT, runtime, tmp_path / "alone")
    assert at_t8 is not None
    assert at_t8.source_run_id == env.t8["en"].name
    assert at_strict is not None
    assert at_strict.source_run_id == env.strict["en"].name
    assert selector.unanswered_verifier_lines(at_strict.result) == 0
    assert alone is None


def test_an_unmeasured_threshold_carries_a_verifier_off_ceiling(
    env: VerifierWorld, tmp_path: Path
) -> None:
    runtime = make_runtime(env.root)
    inputs = _inputs(env)
    results = {
        lang: selector.evaluate_run(lang, [env.t8[lang]], inputs, runtime, tmp_path / lang)
        for lang in LANGUAGES
    }
    entries = {entry["id"]: entry for entry in selector.thresholds_block(results)}
    assert entries["T8"]["measured"] is True
    assert entries["T8"]["source_runs"] == {lang: env.t8[lang].name for lang in LANGUAGES}
    assert entries[STRICT]["measured"] is False
    assert "per_language" not in entries[STRICT]
    assert entries[STRICT]["ceiling_summed_correct"] == sum(
        entries[STRICT]["per_language_ceiling"][lang]["correct"] for lang in LANGUAGES
    )
    measured = [key for key, _ in selector.rule_table(results)]
    assert "T8" in measured
    assert STRICT not in measured
    assert all(entries[key]["measured"] for key in measured)


def test_the_ceiling_bounds_the_measured_figure(env: VerifierWorld, tmp_path: Path) -> None:
    runtime = make_runtime(env.root)
    inputs = _inputs(env)
    for lang in LANGUAGES:
        measured = selector.evaluate_run(lang, [env.t8[lang]], inputs, runtime, tmp_path / lang)
        space = selector.Workspace(lang, inputs, runtime, tmp_path / f"ceiling_{lang}")
        ceiling = selector.ceiling_figures(env.t8[lang], "T8", space)
        assert ceiling["correct"] >= measured.figures["T8"]["correct"]
        assert ceiling["matched"] >= measured.figures["T8"]["matched"]


# The selection refuses unless no unmeasured threshold could be selected


def test_select_refuses_when_an_unmeasured_threshold_could_win(
    env: VerifierWorld, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = [
        *("--run-en", str(env.t8["en"]), "--run-en", str(env.strict["en"])),
        *("--run-fr", str(env.t8["fr"]), "--run-fr", str(env.strict["fr"])),
        *("--split", str(env.split), "--reference", str(REFERENCE), "--classes", str(CLASSES)),
        *("--ledger", str(env.ledger)),
        *("--output-json", str(tmp_path / "s.json"), "--output-md", str(tmp_path / "s.md")),
    ]
    assert selector.main(argv, make_runtime(env.root)) == selector.EXIT_REFUSED
    assert "unmeasured" in capsys.readouterr().err
    assert not (tmp_path / "s.json").exists()


def _qualifying(correct_en: int, correct_fr: int) -> dict[str, object]:
    return {"en": _stats(correct_en, correct_en), "fr": _stats(correct_fr, correct_fr)}


def _selected(table: list[tuple[str, dict[str, object]]]) -> object:
    return rule.apply_rule(table)


ORDER = [f"T{index}" for index in range(1, 9)]


def test_unmeasured_thresholds_below_the_window_cannot_change_the_selection() -> None:
    table = [("T7", _qualifying(60, 60)), ("T8", _qualifying(82, 81))]
    selector.check_unmeasured(_selected(table), table, ORDER, {"T1": 20, "T6": 159})


def test_an_unmeasured_threshold_inside_the_window_is_refused() -> None:
    table = [("T8", _qualifying(82, 81))]
    with pytest.raises(selector.RefusedError, match="T7"):
        selector.check_unmeasured(_selected(table), table, ORDER, {"T7": 160})


def test_an_unmeasured_threshold_looser_than_the_loosest_qualifier_is_refused() -> None:
    table = [("T7", _qualifying(80, 80))]
    with pytest.raises(selector.RefusedError, match="T8"):
        selector.check_unmeasured(_selected(table), table, ORDER, {"T8": 0})


def test_any_unmeasured_threshold_is_refused_below_the_dev_bar() -> None:
    below = {"en": _stats(50, 40), "fr": _stats(50, 40)}
    table = [("T8", below)]
    with pytest.raises(selector.RefusedError, match="T1"):
        selector.check_unmeasured(_selected(table), table, ORDER, {"T1": 0})


def test_nothing_unmeasured_is_never_refused() -> None:
    below = {"en": _stats(50, 40), "fr": _stats(50, 40)}
    table = [("T8", below)]
    selector.check_unmeasured(_selected(table), table, ORDER, {})


def test_a_further_run_with_another_decision_profile_is_refused(
    env: VerifierWorld, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A verifier-off run must never measure a threshold of a verifier-on selection."""
    argv = [
        *("--run-en", str(env.t8["en"]), "--run-en", str(env.off_en)),
        *("--run-fr", str(env.t8["fr"])),
        *("--split", str(env.split), "--reference", str(REFERENCE), "--classes", str(CLASSES)),
        *("--ledger", str(env.ledger)),
        *("--output-json", str(tmp_path / "s.json"), "--output-md", str(tmp_path / "s.md")),
    ]
    assert selector.main(argv, make_runtime(env.root)) == selector.EXIT_REFUSED
    assert "decision_profile" in capsys.readouterr().err


def test_no_threshold_measured_in_both_languages_is_refused() -> None:
    with pytest.raises(selector.RefusedError, match="T8"):
        selector.check_measured_table([], {"T8": 0})


@pytest.mark.parametrize(
    ("failure", "unanswered"),
    [
        ("replay_miss", True),
        ("LLM_UNAVAILABLE", True),
        ("BUDGET_CAP", True),
        ("invalid_code", False),
        ("malformed", False),
        (None, False),
    ],
)
def test_a_declined_verifier_request_is_unanswered_too(
    failure: str | None, unanswered: bool
) -> None:
    """G2-T11 review: a request the recorded run declined never reached the model (A67.2)."""
    from types import SimpleNamespace  # noqa: PLC0415

    result = SimpleNamespace(audit=({"verifier_failure": failure},))
    assert selector.unanswered_verifier_lines(result) == int(unanswered)
