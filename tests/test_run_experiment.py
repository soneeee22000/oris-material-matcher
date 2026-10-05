"""The dev experiment runner: no lockbox line ever reaches the port; the lockbox gate (§10.3)."""

import importlib.util
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from oris_matcher.doctor import LLMSpec, Runtime
from oris_matcher.domain.batching import transport_id
from oris_matcher.domain.boq import LineKind
from oris_matcher.io.boq_reader import read_boq
from oris_matcher.llm.base import LLMRequest
from oris_matcher.llm.fake_llm import FakeLLM
from oris_matcher.settings import load_models_config

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
SPLIT = ROOT / "eval" / "split_v1.json"
SLICE = ROOT / "eval" / "slice_v1.json"
INPUTS = {
    "en": ROOT / "input" / "boq_dataset_input_en.csv",
    "fr": ROOT / "input" / "boq_dataset_input_fr.csv",
}
LIBRARIES = {
    "en": ROOT / "data" / "oris_materials_global.csv",
    "fr": ROOT / "data" / "oris_materials_fr.csv",
}
ALLOWLIST = load_models_config(CONFIG / "models.toml").allowlist.patterns
FIXED_NOW = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
SMOKE_LIMIT = 5


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = _load("oris_eval_run_experiment", ROOT / "eval" / "run_experiment.py")
SPLIT_IDS = json.loads(SPLIT.read_text(encoding="utf-8"))
DEV_IDS = frozenset(SPLIT_IDS["item_ids_dev"])
LOCKBOX_IDS = frozenset(SPLIT_IDS["item_ids_lockbox"])
SLICE_IDS = frozenset(json.loads(SLICE.read_text(encoding="utf-8"))["item_ids"])


class RecordingFactory:
    """Hands out FakeLLMs and keeps every request they receive."""

    def __init__(self) -> None:
        self.fakes: list[FakeLLM] = []

    def __call__(self, spec: LLMSpec, model: str) -> FakeLLM:
        del spec
        fake = FakeLLM(model, ALLOWLIST)
        self.fakes.append(fake)
        return fake

    @property
    def requests(self) -> list[LLMRequest]:
        return [request for fake in self.fakes for request in fake.calls]


class Git:
    """A fake read-only git runner."""

    def __init__(self, tags: str = "") -> None:
        self.tags = tags
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, args: Sequence[str], root: Path) -> str:
        del root
        self.calls.append(tuple(args))
        if "rev-parse" in args:
            return "d" * 40
        return self.tags if "tag" in args else ""


async def no_sleep(seconds: float) -> None:
    del seconds


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "ORIS_PRIMARY_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ORIS_CONFIG_DIR", str(CONFIG))
    libraries = {"global": str(LIBRARIES["en"]), "fr": str(LIBRARIES["fr"])}
    monkeypatch.setenv("ORIS_LIBRARIES", json.dumps(libraries))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def make_runtime(
    tmp_path: Path, factory: RecordingFactory | None = None, git: Git | None = None
) -> Runtime:
    return Runtime(
        root=lambda: tmp_path,
        clock=lambda: FIXED_NOW,
        sleep=no_sleep,
        git=git or Git(),
        adapter_factory=factory or RecordingFactory(),
    )


def argv(tmp_path: Path, lang: str = "en", *extra: str) -> list[str]:
    return [
        "--lang",
        lang,
        "--input",
        str(INPUTS[lang]),
        "--library",
        str(LIBRARIES[lang]),
        "--split",
        str(SPLIT),
        "--llm",
        "fake",
        "--id",
        "E-test",
        "--hypothesis",
        "the runner keeps the lockbox out",
        "--change",
        "none (baseline)",
        "--runs-dir",
        str(tmp_path / "runs"),
        "--ledger",
        str(tmp_path / "experiments.jsonl"),
        *extra,
    ]


def sent_transport_ids(requests: Sequence[LLMRequest]) -> set[str]:
    sent: set[str] = set()
    for request in requests:
        body = request.user_payload.split("<boq_lines>", 1)[1].rsplit("</boq_lines>", 1)[0]
        sent |= {line["id"] for line in json.loads(body)["lines"]}
        sent |= set(request.line_ids)
    return sent


def transport_ids_of(lang: str, item_ids: frozenset[str]) -> set[str]:
    boq = read_boq(INPUTS[lang])
    return {
        transport_id(line)
        for line in boq.lines
        if line.kind == LineKind.ITEM and line.item_no in item_ids
    }


def ledger_rows(tmp_path: Path) -> list[dict[str, Any]]:
    text = (tmp_path / "experiments.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


CASES = [
    pytest.param("en", (), DEV_IDS, id="en-full-dev"),
    pytest.param("fr", ("--slice", str(SLICE)), SLICE_IDS, id="fr-slice"),
    pytest.param("en", ("--limit", str(SMOKE_LIMIT)), DEV_IDS, id="en-limit"),
    pytest.param("en", ("--slice", str(SLICE), "--limit", "3"), SLICE_IDS, id="en-slice-limit"),
    pytest.param("en", ("--profile", "b2", "--limit", "4"), DEV_IDS, id="en-b2-limit"),
]


@pytest.mark.parametrize(("lang", "extra", "allowed"), CASES)
def test_no_lockbox_line_ever_reaches_the_port(
    isolated: Path, lang: str, extra: tuple[str, ...], allowed: frozenset[str]
) -> None:
    factory = RecordingFactory()
    code = runner.main(argv(isolated, lang, *extra), make_runtime(isolated, factory))

    assert code == 0
    assert factory.requests
    sent = sent_transport_ids(factory.requests)
    assert not sent & transport_ids_of(lang, LOCKBOX_IDS)
    assert sent <= transport_ids_of(lang, allowed)
    payloads = "".join(request.user_payload for request in factory.requests)
    assert not [item for item in LOCKBOX_IDS if item in payloads]


def test_limit_takes_the_first_selected_items_in_file_order(isolated: Path) -> None:
    factory = RecordingFactory()
    extra = ("--limit", str(SMOKE_LIMIT), "--profile", "b2")
    assert runner.main(argv(isolated, "en", *extra), make_runtime(isolated, factory)) == 0

    boq = read_boq(INPUTS["en"])
    dev_items = [
        line for line in boq.lines if line.kind == LineKind.ITEM and line.item_no in DEV_IDS
    ]
    expected = {transport_id(line) for line in dev_items[:SMOKE_LIMIT]}
    assert sent_transport_ids(factory.requests) == expected
    row = ledger_rows(isolated)[0]
    assert row["n_lines"] == SMOKE_LIMIT
    assert row["limit"] == SMOKE_LIMIT
    assert row["metrics"]["strict"] is False


def test_ledger_row_has_the_registry_fields_and_appends(isolated: Path) -> None:
    runtime = make_runtime(isolated)
    assert runner.main(argv(isolated, "en"), runtime) == 0
    first = (isolated / "experiments.jsonl").read_text(encoding="utf-8")
    assert runner.main(argv(isolated, "en", "--limit", "2"), runtime) == 0

    rows = ledger_rows(isolated)
    assert len(rows) == 2
    assert (isolated / "experiments.jsonl").read_text(encoding="utf-8").startswith(first)
    row = rows[0]
    for key in ("id", "hypothesis", "change", "run_id", "lang", "profile", "prompt_version"):
        assert row[key]
    assert row["n_lines"] == len(transport_ids_of("en", DEV_IDS))
    assert row["side"] == "dev"
    assert row["kept"] == "baseline"
    for key in ("before", "after", "d", "sign_test_p", "baseline_id"):
        assert row[key] is None
    assert row["metrics"]["strict"] is True
    assert "precision" in row["metrics"]
    assert row["spend_usd"] >= 0
    folder = isolated / "runs" / row["run_id"]
    assert (folder / "output.csv").is_file()
    assert (folder / "score.json").is_file()
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["split_sha256"] == runner.file_sha256(SPLIT)
    assert manifest["select_count"] is not None


def test_lockbox_side_is_refused_without_a_session(isolated: Path) -> None:
    factory = RecordingFactory()
    code = runner.main(argv(isolated, "en", "--side", "lockbox"), make_runtime(isolated, factory))
    assert code == runner.EXIT_REFUSED
    assert factory.fakes == []
    assert not (isolated / "experiments.jsonl").exists()
    assert not (isolated / "runs").exists()


@pytest.mark.parametrize("tags", ["", "eval-freeze\n"])
def test_side_lockbox_is_refused_even_with_a_session_and_the_tag(isolated: Path, tags: str) -> None:
    factory = RecordingFactory()
    args = argv(isolated, "en", "--side", "lockbox", "--lockbox-session")
    code = runner.main(args, make_runtime(isolated, factory, Git(tags)))
    assert code == runner.EXIT_REFUSED
    assert factory.fakes == []
    assert not (isolated / "runs").exists()


def test_side_all_is_refused_without_a_session(isolated: Path) -> None:
    factory = RecordingFactory()
    args = argv(isolated, "en", "--side", "all")
    code = runner.main(args, make_runtime(isolated, factory, Git("eval-freeze\n")))
    assert code == runner.EXIT_REFUSED
    assert factory.fakes == []


@pytest.mark.parametrize("tags", ["", "prereg-v1\n", "eval-freeze-old\n"])
def test_lockbox_session_is_refused_unless_eval_freeze_is_at_head(
    isolated: Path, tags: str
) -> None:
    factory, git = RecordingFactory(), Git(tags)
    args = argv(isolated, "en", "--side", "all", "--lockbox-session")
    code = runner.main(args, make_runtime(isolated, factory, git))
    assert code == runner.EXIT_REFUSED
    assert ("git", "tag", "--points-at", "HEAD") in git.calls
    assert factory.fakes == []


def test_lockbox_gate_opens_only_with_the_tag(isolated: Path) -> None:
    parser = runner.build_parser()
    args = parser.parse_args(argv(isolated, "en", "--side", "all", "--lockbox-session"))
    runner.check_lockbox_gate(args, make_runtime(isolated, git=Git("eval-freeze\n")))
    with pytest.raises(runner.RefusedError):
        runner.check_lockbox_gate(args, make_runtime(isolated, git=Git("other\n")))
    lockbox = parser.parse_args(argv(isolated, "en", "--side", "lockbox", "--lockbox-session"))
    with pytest.raises(runner.RefusedError):
        runner.check_lockbox_gate(lockbox, make_runtime(isolated, git=Git("eval-freeze\n")))


@pytest.mark.parametrize("extra", [("--limit", "3"), ("--slice", str(SLICE))])
def test_lockbox_session_refuses_a_narrowed_run(isolated: Path, extra: tuple[str, ...]) -> None:
    factory = RecordingFactory()
    args = argv(isolated, "en", "--side", "all", "--lockbox-session", *extra)
    code = runner.main(args, make_runtime(isolated, factory, Git("eval-freeze\n")))
    assert code == runner.EXIT_REFUSED
    assert factory.fakes == []


def test_lockbox_session_runs_the_full_file_once_without_cache(isolated: Path) -> None:
    factory, log = RecordingFactory(), isolated / "lockbox_log.md"
    args = argv(isolated, "en", "--side", "all", "--lockbox-session", "--lockbox-log", str(log))
    runtime = make_runtime(isolated, factory, Git("eval-freeze\n"))

    assert runner.main(args, runtime) == 0
    sent = sent_transport_ids(factory.requests)
    assert sent == transport_ids_of("en", DEV_IDS | LOCKBOX_IDS)
    assert log.read_text(encoding="utf-8").count("| en |") == 1
    assert not (isolated / "experiments.jsonl").exists()

    assert runner.main(args, runtime) == runner.EXIT_REFUSED
    assert log.read_text(encoding="utf-8").count("| en |") == 1


def test_slice_with_ids_outside_the_side_is_refused(isolated: Path) -> None:
    payload = json.loads(SLICE.read_text(encoding="utf-8"))
    payload["item_ids"] = [*payload["item_ids"], sorted(LOCKBOX_IDS)[0]]
    tampered = isolated / "slice.json"
    tampered.write_text(json.dumps(payload), encoding="utf-8")
    factory = RecordingFactory()

    code = runner.main(
        argv(isolated, "en", "--slice", str(tampered)), make_runtime(isolated, factory)
    )

    assert code == runner.EXIT_ERROR
    assert factory.fakes == []


def test_slice_from_another_split_is_refused(isolated: Path) -> None:
    payload = json.loads(SLICE.read_text(encoding="utf-8"))
    payload["split_sha256"] = "0" * 64
    other = isolated / "slice.json"
    other.write_text(json.dumps(payload), encoding="utf-8")
    code = runner.main(argv(isolated, "en", "--slice", str(other)), make_runtime(isolated))
    assert code == runner.EXIT_ERROR


def test_selection_refuses_an_item_of_the_other_side() -> None:
    boq = read_boq(INPUTS["en"])
    leaking = runner.Selection(DEV_IDS | {sorted(LOCKBOX_IDS)[0]}, LOCKBOX_IDS, None)
    with pytest.raises(runner.RefusedError):
        leaking.line_ids(boq)


def test_budget_is_a_hard_stop(isolated: Path) -> None:
    factory = RecordingFactory()
    extra = ("--budget-usd", "0.000001", "--limit", "3")
    assert runner.main(argv(isolated, "en", *extra), make_runtime(isolated, factory)) == 0
    assert factory.requests == []
    row = ledger_rows(isolated)[0]
    assert row["spend_usd"] == 0
    output = (isolated / "runs" / row["run_id"] / "output.csv").read_text(encoding="utf-8")
    assert output.count("BUDGET_CAP") == 3


def test_bad_limit_is_an_error(isolated: Path) -> None:
    assert runner.main(argv(isolated, "en", "--limit", "0"), make_runtime(isolated)) == 2


def test_baseline_id_compares_with_the_baseline_rows_run(isolated: Path) -> None:
    runtime = make_runtime(isolated)
    limit = ("--limit", str(SMOKE_LIMIT))
    assert runner.main(argv(isolated, "en", *limit), runtime) == 0
    change = [*argv(isolated, "en", *limit, "--baseline-id", "E-test"), "--id", "E-next"]
    assert runner.main(change, runtime) == 0

    base, row = ledger_rows(isolated)
    assert row["baseline_id"] == "E-test"
    assert row["before"] == {"run_id": base["run_id"], "correct": row["after"]["correct"]}
    assert row["after"]["run_id"] == row["run_id"]
    assert (row["d"], row["sign_test_p"], row["kept"]) == (0, 1.0, False)
    checks = row["comparison"]["keep_checks"]
    assert set(checks) == {"gain", "language_loss", "f_nm_zero", "dev_bar", "cost", "latency"}
    assert (checks["gain"], checks["language_loss"], checks["f_nm_zero"]) == (False, True, True)
    assert checks["dev_bar"] is False


def test_baseline_id_needs_a_row_with_the_same_item_set(isolated: Path) -> None:
    runtime = make_runtime(isolated)
    assert runner.main(argv(isolated, "en", "--limit", "2"), runtime) == 0
    other_items = [*argv(isolated, "en", "--limit", "3", "--baseline-id", "E-test")]
    assert runner.main(other_items, runtime) == runner.EXIT_ERROR
    other_lang = [*argv(isolated, "fr", "--limit", "2", "--baseline-id", "E-test")]
    assert runner.main(other_lang, runtime) == runner.EXIT_ERROR


def test_sign_test_p_is_the_exact_binomial_tail() -> None:
    assert runner.sign_test_p(0, 0) == 1.0
    assert runner.sign_test_p(3, 3) == 0.125
    assert runner.sign_test_p(9, 10) == 11 / 1024


def _compared(
    lang: str,
    deltas: dict[str, int],
    before: int,
    f_nm: int = 0,
    **overrides: Any,
) -> dict[str, Any]:
    after = before + sum(deltas.values())
    metrics = {
        "false_not_a_material": f_nm,
        "precision": {"value": 0.96, "matched": 45},
        "strict": True,
        "mean_cost_usd": 0.005,
    }
    metrics.update(overrides.pop("metrics", {}))
    return {
        "lang": lang,
        "metrics": metrics,
        "comparison": {"item_deltas": deltas, "before_correct": before, "after_correct": after},
        **overrides,
    }


def test_keep_rule_pools_both_languages() -> None:
    en = _compared("en", {f"e{n}": 1 for n in range(5)}, 50)
    fr = _compared("fr", {"f1": 1, "f2": 1, "f3": -1}, 50)
    pooled = runner.keep_verdict([en, fr])
    assert (pooled["d"], pooled["gain"], pooled["kept"]) == (8, 6, True)
    assert pooled["sign_test_p"] == runner.sign_test_p(7, 8)
    assert runner.keep_verdict([en])["kept"] is False

    losing = _compared("fr", {"f1": -1, "f2": -1}, 50)
    big = _compared("en", {f"e{n}": 1 for n in range(9)}, 50)
    assert runner.keep_verdict([big, losing])["kept"] is True
    worse = _compared("fr", {"f1": -1, "f2": -1, "f3": -1}, 50)
    assert runner.keep_verdict([big, worse])["keep_checks"]["language_loss"] is False
    leaking = _compared("en", {f"e{n}": 1 for n in range(9)}, 50, f_nm=1)
    assert runner.keep_verdict([leaking])["keep_checks"]["f_nm_zero"] is False


def test_keep_rule_also_gates_on_dev_bar_cost_and_latency() -> None:
    gain = {f"e{n}": 1 for n in range(9)}
    assert runner.keep_verdict([_compared("en", gain, 50)])["kept"] is True
    below_bar = _compared("en", gain, 50, metrics={"precision": {"value": 0.93, "matched": 45}})
    assert runner.keep_verdict([below_bar])["keep_checks"]["dev_bar"] is False
    assert runner.keep_verdict([below_bar])["kept"] is False
    too_few = _compared("en", gain, 50, metrics={"precision": {"value": 0.99, "matched": 39}})
    assert runner.keep_verdict([too_few])["kept"] is False
    pricey = _compared("en", gain, 50, metrics={"mean_cost_usd": 0.021})
    assert runner.keep_verdict([pricey])["keep_checks"]["cost"] is False
    slow = _compared("en", gain, 50, latency_s_per_routed=2.4)
    assert runner.keep_verdict([slow])["keep_checks"]["latency"] is False
    unmeasured = runner.keep_verdict([_compared("en", gain, 50)])
    assert unmeasured["keep_checks"]["latency"] is True
    assert unmeasured["keep_notes"] == {"latency_measured": False}


def test_score_json_and_ledger_are_byte_portable(isolated: Path) -> None:
    assert runner.main(argv(isolated, "en", "--limit", "2"), make_runtime(isolated)) == 0
    row = ledger_rows(isolated)[0]
    raw = (isolated / "runs" / row["run_id"] / "score.json").read_bytes()
    assert b"\r" not in raw
    payload = json.loads(raw)
    expected = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    assert raw.decode("utf-8") == expected
    assert payload["reference"]["path"] == {"external": True, "path": "boq_dataset_matched_GT.csv"}
    assert payload["outputs"][0]["path"] == f"runs/{row['run_id']}/output.csv"
    assert row["output"] == f"runs/{row['run_id']}/output.csv"
    for text in (raw.decode("utf-8"), (isolated / "experiments.jsonl").read_text("utf-8")):
        assert str(ROOT) not in text
        assert str(isolated) not in text
        assert str(isolated.as_posix()) not in text
