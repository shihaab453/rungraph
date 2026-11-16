"""CLI boundary tests for the experimental comparability route (Stage 2b).

Every manifest is a disposable temporary file. The tests exercise ``main`` and
the real ``python -m runevidence`` entry point: sanitized grammar handling,
exit codes, single-document output, loader/mapping failures and privacy. No
artifact path is ever opened or dereferenced.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from runevidence import _experimental_comparability_bridge as bridge
from runevidence import cli
from runevidence import run_comparability as command
from runevidence.manifest import dump_manifest
from runevidence.models import (
    Artifact,
    Manifest,
    Project,
    Relationship,
    Result,
    Run,
    RunConditions,
)

COMPARABLE = "COMPARABLE_RECORDED_CONDITIONS"
NOT_COMPARABLE = "NOT_COMPARABLE_RECORDED_CONDITIONS"
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"

HASH = "a" * 64
SECRET = "M9S2BCANARY7Q"
MISSING_SELECTOR = "M9S2BCANARY7Q-missing-result"

CANARIES = tuple(
    [
        SECRET,
        MISSING_SELECTOR,
        f"{SECRET}-project",
        f"{SECRET}-run",
        f"{SECRET}-result",
        f"{SECRET}-dataset",
        f"{SECRET}-checkpoint",
        f"{SECRET}-name",
        f"{SECRET}-value",
        f"{SECRET}-key",
        f"{SECRET}-path",
        f"{SECRET}-loaded-path",
        f"{SECRET}-locator",
        f"{SECRET}-note",
        f"{SECRET}-claim",
    ]
)


# ---------------------------------------------------------------- fixtures


def _manifest(tag: str, *, split: str = "test") -> Manifest:
    run_id = f"run-{tag}"
    dataset_id = f"ds-{tag}"
    checkpoint_id = f"ckpt-{tag}"
    return Manifest(
        project=Project(id=f"proj-{tag}", name=f"project {tag}"),
        runs=[
            Run(
                id=run_id,
                name=f"run {tag}",
                metadata={
                    "evaluation": {
                        "split": split,
                        "protocol": {"protocol_id": "protocol-1", "version": 1},
                    },
                    "source_revision": "rev-1",
                    "environment": {"python": "3.12", "tags": ["cpu"]},
                },
            )
        ],
        artifacts=[
            Artifact(
                id=dataset_id,
                path=f"data/raw-{tag}.csv",
                kind="dataset",
                sha256=HASH,
                metadata={"dataset_id": "imagenet"},
            ),
            Artifact(id="raw-shared", path="data/source.csv", sha256=HASH),
            Artifact(
                id=checkpoint_id,
                path=f"models/{tag}.bin",
                kind="checkpoint",
                sha256=HASH,
            ),
        ],
        results=[
            Result(
                id=f"res-{tag}",
                run_id=run_id,
                name="accuracy",
                value=0.9,
                metadata={
                    "metric_definition": {
                        "definition_id": "accuracy-v1",
                        "higher_is_better": True,
                    }
                },
            )
        ],
        relationships=[
            Relationship(source=dataset_id, target=run_id, type="input_to"),
            Relationship(source=checkpoint_id, target=run_id, type="input_to"),
            Relationship(source=dataset_id, target="raw-shared", type="derived_from"),
        ],
    )


def _canary_manifest(tag: str) -> Manifest:
    run_id = f"{SECRET}-run-{tag}"
    dataset_id = f"{SECRET}-dataset-{tag}"
    checkpoint_id = f"{SECRET}-checkpoint-{tag}"
    return Manifest(
        project=Project(
            id=f"{SECRET}-project-{tag}",
            name=f"{SECRET}-name",
            description=f"{SECRET}-value",
            tags=[f"{SECRET}-value"],
            metadata={f"{SECRET}-key": f"{SECRET}-value"},
        ),
        runs=[
            Run(
                id=run_id,
                name=f"{SECRET}-name",
                params={f"{SECRET}-key": f"{SECRET}-value"},
                metadata={
                    "evaluation": {
                        "split": "test",
                        "protocol": {
                            "protocol_id": f"{SECRET}-value",
                            "version": 1,
                        },
                    },
                    "source_revision": f"{SECRET}-value",
                    "environment": {f"{SECRET}-key": f"{SECRET}-value"},
                },
            )
        ],
        artifacts=[
            Artifact(
                id=dataset_id,
                path=f"{SECRET}-path/{tag}.csv",
                kind="dataset",
                sha256=HASH,
                metadata={
                    "dataset_id": f"{SECRET}-value",
                    f"{SECRET}-key": f"{SECRET}-value",
                },
            ),
            Artifact(id=f"{SECRET}-parent", path=f"{SECRET}-path/raw.csv", sha256=HASH),
            Artifact(
                id=checkpoint_id,
                path=f"{SECRET}-path/{tag}.bin",
                kind="checkpoint",
                sha256=HASH,
            ),
        ],
        results=[
            Result(
                id=f"{SECRET}-result-{tag}",
                run_id=run_id,
                name=f"{SECRET}-name",
                value=0.5,
                locator=f"/{SECRET}-locator",
                metadata={
                    "metric_definition": {
                        "definition_id": f"{SECRET}-value",
                    },
                    f"{SECRET}-key": f"{SECRET}-value",
                },
            )
        ],
        relationships=[
            Relationship(
                source=dataset_id, target=run_id, type="input_to",
                note=f"{SECRET}-note",
            ),
            Relationship(
                source=checkpoint_id, target=run_id, type="input_to",
                note=f"{SECRET}-note",
            ),
            Relationship(
                source=dataset_id, target=f"{SECRET}-parent",
                type="derived_from", note=f"{SECRET}-note",
            ),
        ],
    )


def _write(directory: Path, name: str, manifest: Manifest) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(dump_manifest(manifest), encoding="utf-8", newline="\n")
    return path


def _pair(
    directory: Path, *, target_split: str = "test"
) -> tuple[Path, Path]:
    baseline = _write(directory, "baseline.json", _manifest("b"))
    target = _write(directory, "target.json", _manifest("t", split=target_split))
    return baseline, target


def _manifest_0_3(tag: str, *, split: str) -> Manifest:
    manifest = _manifest(tag, split=split)
    run = manifest.runs[0].model_copy(
        update={"conditions": RunConditions(evaluation_split=split)}
    )
    return manifest.model_copy(update={"schema_version": "0.3", "runs": [run]})


def _argv(
    baseline: Path,
    target: Path,
    *,
    baseline_result: str = "res-b",
    target_result: str = "res-t",
    json_flag: bool = False,
    json_first: bool = False,
) -> list[str]:
    argv = ["comparability"]
    if json_flag and json_first:
        argv.append("--json")
    argv.extend(
        [
            str(baseline),
            str(target),
            "--baseline-result",
            baseline_result,
            "--target-result",
            target_result,
        ]
    )
    if json_flag and not json_first:
        argv.append("--json")
    return argv


def _run(capsys, argv: list[str]) -> tuple[int, str, str]:
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _single_json(text: str) -> dict:
    """Parse exactly one complete JSON document with no trailing content."""
    decoder = json.JSONDecoder()
    document, end = decoder.raw_decode(text)
    assert text[end:].strip() == ""
    return document


def _assert_error_document(
    document: dict,
    *,
    code: str,
    side: str | None,
    mapping_code: str | None = None,
) -> None:
    expected_error = {"code": code, "side": side}
    if mapping_code is not None:
        expected_error["mapping_code"] = mapping_code
    assert document == {
        "format": command.ERROR_FORMAT,
        "ok": False,
        "exit_code": 2,
        "error": expected_error,
    }


def _assert_no_canaries(*texts: str) -> None:
    blob = "\n".join(texts)
    for value in CANARIES:
        assert value not in blob, value


def _expected_json(document: dict) -> str:
    return json.dumps(
        document, indent=2, ensure_ascii=True, allow_nan=False
    ) + "\n"


# ------------------------------------------------------------------ success


def test_comparable_json_envelope_exit_zero(tmp_path, capsys):
    baseline, target = _pair(tmp_path)
    code, out, err = _run(
        capsys, _argv(baseline, target, json_flag=True)
    )
    assert code == 0
    assert err == ""
    document = _single_json(out)
    assert set(document) == {"format", "application_status", "core", "notice"}
    assert document["format"] == command.RESULT_FORMAT
    assert document["application_status"] == command.APPLICATION_STATUS
    assert document["notice"] == command.NOTICE
    core = document["core"]
    assert set(core) == command.CORE_KEYS
    assert core["decision"] == COMPARABLE
    assert core["input_rejected"] is False
    assert out == _expected_json(document)


def test_not_comparable_json_envelope_exit_one(tmp_path, capsys):
    baseline, target = _pair(tmp_path, target_split="train")
    code, out, err = _run(capsys, _argv(baseline, target, json_flag=True))
    assert code == 1
    assert err == ""
    document = _single_json(out)
    assert document["format"] == command.RESULT_FORMAT
    assert document["core"]["decision"] == NOT_COMPARABLE
    assert (
        "MANDATORY_DIMENSION_DIFFERENT:evaluation_split"
        in document["core"]["reason_codes"]
    )


def test_insufficient_json_envelope_exit_two(tmp_path, capsys):
    baseline = _write(
        tmp_path,
        "baseline.json",
        _manifest_without_split("b"),
    )
    target = _write(tmp_path, "target.json", _manifest("t"))
    code, out, err = _run(capsys, _argv(baseline, target, json_flag=True))
    assert code == 2
    assert err == ""
    document = _single_json(out)
    assert document["format"] == command.RESULT_FORMAT
    assert document["core"]["decision"] == INSUFFICIENT
    assert document["core"]["input_rejected"] is False


def test_core_rejected_json_envelope_exit_two(tmp_path, capsys):
    baseline, target = _pair(tmp_path)
    manifest = _manifest("t")
    manifest.runs[0].id = "run-b"
    manifest.results[0].run_id = "run-b"
    for relationship in manifest.relationships:
        if relationship.target == "run-t":
            relationship.target = "run-b"
    target.write_text(dump_manifest(manifest), encoding="utf-8", newline="\n")
    code, out, err = _run(capsys, _argv(baseline, target, json_flag=True))
    assert code == 2
    assert err == ""
    document = _single_json(out)
    assert document["format"] == command.RESULT_FORMAT
    core = document["core"]
    assert core["input_rejected"] is True
    assert "SAME_RUN_ID" in core["input_error_codes"]
    assert "MALFORMED_INPUT" in core["reason_codes"]


def _manifest_without_split(tag: str) -> Manifest:
    manifest = _manifest(tag)
    manifest.runs[0].metadata["evaluation"].pop("split")
    return manifest


def test_text_mode_matches_the_document_vocabulary(tmp_path, capsys):
    baseline, target = _pair(tmp_path)
    json_code, out, _ = _run(capsys, _argv(baseline, target, json_flag=True))
    text_code, text, err = _run(capsys, _argv(baseline, target))
    assert json_code == text_code == 0
    assert err == ""
    assert text.startswith(
        "Comparability of recorded conditions: " + COMPARABLE + "\n"
    )
    assert "Application status: " + command.APPLICATION_STATUS in text
    assert command.NOTICE in text
    assert command.DISCLAIMER in text
    assert not text.lstrip().startswith("{")
    # Same decision, same exit semantics in both output modes.
    assert _single_json(out)["core"]["decision"] == COMPARABLE


@pytest.mark.parametrize("outcome", ["different", "insufficient", "rejected"])
def test_text_mode_other_outcomes_use_fixed_vocabulary(
    outcome, tmp_path, capsys
):
    if outcome == "different":
        baseline, target = _pair(tmp_path, target_split="train")
        expected_code = 1
        expected_decision = NOT_COMPARABLE
    elif outcome == "insufficient":
        baseline = _write(
            tmp_path, "baseline.json", _manifest_without_split("b")
        )
        target = _write(tmp_path, "target.json", _manifest("t"))
        expected_code = 2
        expected_decision = INSUFFICIENT
    else:
        baseline, target = _pair(tmp_path)
        manifest = _manifest("t")
        manifest.runs[0].id = "run-b"
        manifest.results[0].run_id = "run-b"
        for relationship in manifest.relationships:
            if relationship.target == "run-t":
                relationship.target = "run-b"
        target.write_text(
            dump_manifest(manifest), encoding="utf-8", newline="\n"
        )
        expected_code = 2
        expected_decision = INSUFFICIENT

    code, text, err = _run(capsys, _argv(baseline, target))
    assert code == expected_code
    assert err == ""
    assert text.startswith(
        "Comparability of recorded conditions: " + expected_decision + "\n"
    )
    assert command.NOTICE in text and command.DISCLAIMER in text
    assert not text.lstrip().startswith("{")
    if outcome == "rejected":
        assert "Core input errors: SAME_RUN_ID" in text


def test_json_flag_before_and_after_operands_is_identical(tmp_path, capsys):
    baseline, target = _pair(tmp_path)
    first = _run(capsys, _argv(baseline, target, json_flag=True, json_first=True))
    second = _run(capsys, _argv(baseline, target, json_flag=True, json_first=False))
    assert first == second
    assert first[0] == 0 and first[2] == ""


def test_success_output_has_no_canaries(tmp_path, capsys):
    baseline = _write(
        tmp_path / "baseline", "manifest.json", _canary_manifest("b")
    )
    target = _write(
        tmp_path / "target", "manifest.json", _canary_manifest("t")
    )
    argv = _argv(
        baseline,
        target,
        baseline_result=f"{SECRET}-result-b",
        target_result=f"{SECRET}-result-t",
        json_flag=True,
    )
    code, out, err = _run(capsys, argv)
    assert code == 0
    assert err == ""
    _assert_no_canaries(out, err)
    document = _single_json(out)
    assert document["core"]["decision"] == COMPARABLE


# ---------------------------------------------------------- argument grammar


GRAMMAR_CASES = [
    ("no_args", [], False),
    ("one_operand", ["BASE"], False),
    ("missing_selectors", ["BASE", "TARGET"], False),
    ("missing_selectors_json", ["BASE", "TARGET", "--json"], True),
    ("missing_selector_value", ["BASE", "TARGET", "--baseline-result"], False),
    (
        "missing_target_selector",
        ["BASE", "TARGET", "--baseline-result", "res-b"],
        False,
    ),
    (
        "unknown_option_canary",
        ["BASE", "TARGET", "--baseline-result", "res-b",
         "--target-result", "res-t", "--bogus", SECRET, "--json"],
        True,
    ),
    (
        "extra_operand",
        ["BASE", "TARGET", "EXTRA", "--baseline-result", "res-b",
         "--target-result", "res-t"],
        False,
    ),
    (
        "option_like_selector",
        ["BASE", "TARGET", "--baseline-result", "--json",
         "--target-result", "res-t"],
        True,
    ),
    (
        "strict",
        ["BASE", "TARGET", "--baseline-result", "res-b",
         "--target-result", "res-t", "--strict"],
        False,
    ),
    (
        "strict_json",
        ["BASE", "TARGET", "--baseline-result", "res-b",
         "--target-result", "res-t", "--strict", "--json"],
        True,
    ),
    (
        "redact_manifest_path",
        ["BASE", "TARGET", "--baseline-result", "res-b",
         "--target-result", "res-t", "--redact-manifest-path"],
        False,
    ),
    (
        "summary_only",
        ["BASE", "TARGET", "--baseline-result", "res-b",
         "--target-result", "res-t", "--summary-only"],
        False,
    ),
]


@pytest.mark.parametrize(
    "name,suffix,as_json",
    GRAMMAR_CASES,
    ids=[case[0] for case in GRAMMAR_CASES],
)
def test_grammar_failures_are_fixed_and_do_no_work(
    name, suffix, as_json, tmp_path, capsys, monkeypatch
):
    baseline = tmp_path / "baseline.json"
    target = tmp_path / "target.json"
    # Grammar must fail before any manifest exists; the operands are never read.
    assert not baseline.exists() and not target.exists()

    calls = []

    def forbidden(*args, **kwargs):
        calls.append("work")
        raise AssertionError("grammar failure performed comparison work")

    monkeypatch.setattr(command, "compare_paths", forbidden)
    monkeypatch.setattr(command.manifest, "load_manifest", forbidden)

    argv = ["comparability"] + [
        str(baseline) if item == "BASE" else str(target) if item == "TARGET" else item
        for item in suffix
    ]
    code, out, err = _run(capsys, argv)

    assert calls == []
    assert code == 2
    assert err == ""
    _assert_no_canaries(out, err)
    assert str(baseline) not in out and str(target) not in out
    if as_json:
        document = _single_json(out)
        _assert_error_document(
            document, code=command.INVALID_ARGUMENTS, side=None
        )
        assert out == _expected_json(document)
    else:
        assert out == "ERROR INVALID_ARGUMENTS (exit 2)\n"


def test_grammar_error_does_not_echo_tokens(capsys):
    argv = [
        "comparability", "BASE", "TARGET",
        "--baseline-result", "res-b", "--target-result", "res-t",
        "--json", SECRET,
    ]
    code, out, err = _run(capsys, argv)
    assert code == 2
    assert err == ""
    _assert_no_canaries(out, err)
    assert "BASE" not in out and "TARGET" not in out
    _assert_error_document(
        _single_json(out), code=command.INVALID_ARGUMENTS, side=None
    )


def test_help_exits_zero_and_prints_usage(capsys):
    code, out, err = _run(capsys, ["comparability", "--help"])
    assert code == 0
    assert err == ""
    assert "usage: runevidence comparability" in out
    assert "--baseline-result" in out and "--target-result" in out
    assert command.ERROR_FORMAT not in out


def test_help_with_json_stays_help(capsys):
    # --help wins over the raw --json recognition and keeps its normal output.
    code, out, err = _run(capsys, ["comparability", "--json", "--help"])
    assert code == 0
    assert err == ""
    assert "usage: runevidence comparability" in out
    assert command.ERROR_FORMAT not in out


# --------------------------------------------------------- loader / mapping


@pytest.mark.parametrize("kind", ["missing_file", "directory", "malformed_json"])
def test_load_failures_are_fixed_and_leak_free(kind, tmp_path, capsys):
    path = tmp_path / (SECRET + "-path.json")
    if kind == "directory":
        path.mkdir()
    elif kind == "malformed_json":
        path.write_text(
            '{"project": {"id": "p", "name": "' + SECRET + '"',
            encoding="utf-8",
        )
    target = tmp_path / "target.json"
    code, out, err = _run(
        capsys,
        _argv(path, target, json_flag=True),
    )
    assert code == 2
    assert err == ""
    _assert_no_canaries(out, err)
    assert str(path) not in out and str(target) not in out
    document = _single_json(out)
    _assert_error_document(
        document, code=command.MANIFEST_LOAD_FAILED, side="baseline"
    )
    assert out == _expected_json(document)


def test_target_load_failure_text_shape(tmp_path, capsys):
    baseline, _ = _pair(tmp_path)
    missing = tmp_path / (SECRET + "-target.json")
    code, out, err = _run(capsys, _argv(baseline, missing))
    assert code == 2
    assert err == ""
    assert out == "ERROR MANIFEST_LOAD_FAILED (target) (exit 2)\n"
    _assert_no_canaries(out, err)


def test_0_3_pair_json_compares_and_is_not_an_unsupported_version(tmp_path, capsys):
    baseline = _write(
        tmp_path, "baseline-0.3.json", _manifest_0_3("b", split="test")
    )
    target = _write(
        tmp_path, "target-0.3.json", _manifest_0_3("t", split="validation")
    )
    code, out, err = _run(capsys, _argv(baseline, target, json_flag=True))
    assert code == 1
    assert err == ""
    _assert_no_canaries(out, err)
    assert NOT_COMPARABLE in out
    assert "UNSUPPORTED_VERSION" not in out
    assert str(baseline) not in out and str(target) not in out


def test_0_3_baseline_text_is_fixed_and_value_free(tmp_path, capsys):
    baseline = _write(
        tmp_path, "baseline-0.3.json", _manifest_0_3("b", split="test")
    )
    target = _write(
        tmp_path, "target-0.3.json", _manifest_0_3("t", split="validation")
    )
    code, out, err = _run(capsys, _argv(baseline, target))
    assert code == 1
    assert err == ""
    assert NOT_COMPARABLE in out
    _assert_no_canaries(out, err)


def test_0_3_target_compares(tmp_path, capsys):
    baseline, _ = _pair(tmp_path)
    target = _write(
        tmp_path, "target-0.3.json", _manifest_0_3("t", split="test")
    )
    code, out, err = _run(capsys, _argv(baseline, target, json_flag=True))
    assert code == 0
    assert err == ""
    assert COMPARABLE in out
    assert str(baseline) not in out and str(target) not in out


def test_directory_target_operand_is_a_load_failure(tmp_path, capsys):
    baseline, _ = _pair(tmp_path)
    directory = tmp_path / (SECRET + "-directory")
    directory.mkdir()
    code, out, err = _run(
        capsys, _argv(baseline, directory, json_flag=True)
    )
    assert code == 2
    assert err == ""
    _assert_no_canaries(out, err)
    _assert_error_document(
        _single_json(out), code=command.MANIFEST_LOAD_FAILED, side="target"
    )


def test_unknown_result_selector_is_fixed_mapping_failure(tmp_path, capsys):
    baseline, target = _pair(tmp_path)
    code, out, err = _run(
        capsys,
        _argv(
            baseline,
            target,
            target_result=MISSING_SELECTOR,
            json_flag=True,
        ),
    )
    assert code == 2
    assert err == ""
    _assert_no_canaries(out, err)
    document = _single_json(out)
    _assert_error_document(
        document,
        code=command.MAPPING_FAILED,
        side="target",
        mapping_code=bridge.RESULT_NOT_FOUND,
    )
    assert MISSING_SELECTOR not in out
    assert out == _expected_json(document)


def test_mapping_failure_text_shape(tmp_path, capsys):
    baseline, target = _pair(tmp_path)
    code, out, err = _run(
        capsys,
        _argv(baseline, target, baseline_result=MISSING_SELECTOR),
    )
    assert code == 2
    assert err == ""
    assert out == (
        "ERROR MAPPING_FAILED (baseline, RESULT_NOT_FOUND) (exit 2)\n"
    )
    _assert_no_canaries(out, err)


# ------------------------------------------------------------- real process


def test_real_entry_point_comparable(tmp_path, capsys):
    baseline, target = _pair(tmp_path)
    argv = [
        sys.executable,
        "-m",
        "runevidence",
        *_argv(baseline, target, json_flag=True),
    ]
    completed = subprocess.run(
        argv, capture_output=True, text=True, cwd=tmp_path
    )
    assert completed.returncode == 0
    assert completed.stderr == ""
    document = _single_json(completed.stdout)
    assert document["core"]["decision"] == COMPARABLE


def test_real_entry_point_grammar_failure_has_no_diagnostics(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "runevidence",
            "comparability",
            "--json",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert completed.returncode == 2
    assert completed.stderr == ""
    document = _single_json(completed.stdout)
    _assert_error_document(
        document, code=command.INVALID_ARGUMENTS, side=None
    )
    assert "usage:" not in completed.stdout


def test_real_entry_point_missing_manifest_is_leak_free(tmp_path):
    missing = tmp_path / (SECRET + "-missing.json")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "runevidence",
            "comparability",
            str(missing),
            str(tmp_path / (SECRET + "-target.json")),
            "--baseline-result",
            MISSING_SELECTOR,
            "--target-result",
            MISSING_SELECTOR,
            "--json",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert completed.returncode == 2
    assert completed.stderr == ""
    _assert_no_canaries(completed.stdout, completed.stderr)
    _assert_error_document(
        _single_json(completed.stdout),
        code=command.MANIFEST_LOAD_FAILED,
        side="baseline",
    )


# ------------------------------------------------------- output and I/O proof


def test_serialization_failure_prints_nothing(tmp_path, capsys, monkeypatch):
    baseline, target = _pair(tmp_path)
    original = json.dumps
    calls = []

    def guarded(value, *args, **kwargs):
        if isinstance(value, dict) and value.get("format") == command.RESULT_FORMAT:
            calls.append("serialize")
            raise RuntimeError("SERIALIZE-CANARY")
        return original(value, *args, **kwargs)

    monkeypatch.setattr(json, "dumps", guarded)
    with pytest.raises(RuntimeError):
        cli.main(_argv(baseline, target, json_flag=True))
    captured = capsys.readouterr()
    assert calls == ["serialize"]
    assert captured.out == ""
    assert captured.err == ""
    assert "SERIALIZE-CANARY" not in captured.out + captured.err


def test_cli_reads_only_the_two_manifests(tmp_path, capsys, monkeypatch):
    root = tmp_path / "experiment"
    baseline = _write(root, "baseline.json", _manifest("b"))
    target = _write(root, "target.json", _manifest("t"))
    tempting = root / "data" / "raw-b.csv"
    tempting.parent.mkdir(parents=True, exist_ok=True)
    tempting.write_text(SECRET, encoding="utf-8")

    allowed = {baseline.resolve(), target.resolve()}
    reads = []
    original = Path.read_bytes

    def guarded_read(self):
        resolved = self.resolve()
        assert resolved in allowed, f"unexpected read: {self}"
        reads.append(resolved)
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", guarded_read)
    try:
        code, out, err = _run(capsys, _argv(baseline, target, json_flag=True))
    finally:
        monkeypatch.undo()
    assert code == 0
    assert err == ""
    assert sorted(reads) == sorted(allowed)
    _assert_no_canaries(out, err)


def test_route_rejects_document_reads_beyond_loads(tmp_path, capsys, monkeypatch):
    """A second proof that no artifact or manifest is re-read after loading."""
    baseline, target = _pair(tmp_path)
    original = command.manifest.load_manifest
    loads = []

    def counted(path):
        loads.append(Path(path).resolve())
        return original(path)

    monkeypatch.setattr(command.manifest, "load_manifest", counted)
    code, out, err = _run(capsys, _argv(baseline, target, json_flag=True))
    assert code == 0
    assert err == ""
    assert loads == [baseline.resolve(), target.resolve()]
    _single_json(out)
