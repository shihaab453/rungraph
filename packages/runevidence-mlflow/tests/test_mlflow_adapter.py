"""The MLflow file-store adapter: discovery, manifest contents, metric
selection, hashing/ownership, metadata safety, and CLI integration."""

from __future__ import annotations

import ast
import errno
import json
import os
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from importlib.metadata import distribution, entry_points
from pathlib import Path

import pytest
from packaging.requirements import Requirement

pytest.importorskip("runevidence_mlflow", reason="install packages/runevidence-mlflow to run these tests")

from runevidence import __version__ as core_version  # noqa: E402
from runevidence.adapters import AdapterError, available_adapters  # noqa: E402
from runevidence.checks import run_checks  # noqa: E402
from runevidence.cli import main  # noqa: E402
from runevidence.hashing import sha256_file  # noqa: E402
from runevidence.manifest import dump_manifest, load_manifest, save_manifest  # noqa: E402
from runevidence.models import MANIFEST_FILENAME, Manifest  # noqa: E402
from runevidence_mlflow import MlflowAdapter, __version__  # noqa: E402
from runevidence_mlflow import adapter as adapter_module  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "examples" / "mlflow-file-store"
MLRUNS = EXAMPLE / "mlruns"
PACKAGE = Path(__file__).resolve().parents[1]
RUN_A = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
RUN_B = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
PARAMS_A = {
    "learning_rate": "0.01",
    "optimizer/name": "adam",
    "seed": "10",
    "use_batchnorm": "true",
}


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def extract(root: Path, project_id: str = "mlflow-example", name: str = "MLflow example") -> Manifest:
    adapter = MlflowAdapter()
    return adapter.extract(root, adapter.discover(root), project_id=project_id, project_name=name)


def assert_clean(root: Path, manifest: Manifest) -> None:
    save_manifest(manifest, root / MANIFEST_FILENAME)
    assert run_checks(load_manifest(root), include_untracked=True).findings == []


def experiment_meta(artifact_location: str = "file:///tmp/mlruns/0") -> str:
    return (
        f"artifact_location: {artifact_location}\n"
        "creation_time: 1728999900000\n"
        "experiment_id: '0'\n"
        "last_update_time: 1729000200000\n"
        "lifecycle_stage: active\n"
        "name: Default\n"
    )


def run_meta(
    run_id: str = "run1",
    *,
    run_uuid: str | None = None,
    status: str = "3",
    tags: str = "tags: []",
    experiment_id: str = "0",
    start_time: str = "1729000000000",
    extra: str = "",
) -> str:
    uuid_line = "" if run_uuid is None else f"run_uuid: {run_uuid}\n"
    return (
        f"artifact_uri: file:///tmp/mlruns/0/{run_id}/artifacts\n"
        "end_time: 1729000120000\n"
        "entry_point_name: ''\n"
        f"experiment_id: '{experiment_id}'\n"
        "lifecycle_stage: active\n"
        f"run_id: {run_id}\n"
        f"{uuid_line}"
        f"run_name: run-{run_id}\n"
        "source_name: file:///tmp/train.py\n"
        "source_type: 4\n"
        "source_version: ''\n"
        f"start_time: {start_time}\n"
        f"status: {status}\n"
        f"{tags}\n"
        "user_id: synthetic-user\n"
        f"{extra}"
    )


def make_store(
    root: Path,
    *,
    run_id: str = "run1",
    run_text: str | None = None,
    exp_text: str | None = None,
) -> Path:
    """A direct file-store root: <experiment>/meta.yaml + <experiment>/<run>/meta.yaml."""
    write(root / "0" / "meta.yaml", exp_text if exp_text is not None else experiment_meta())
    run_dir = root / "0" / run_id
    write(run_dir / "meta.yaml", run_text if run_text is not None else run_meta(run_id))
    return run_dir


def copy_example(tmp_path: Path) -> Path:
    dest = tmp_path / "mlflow-file-store"
    shutil.copytree(EXAMPLE, dest)
    return dest


# --- package and registration ----------------------------------------------


def test_entry_point_package_version_and_dependencies():
    assert available_adapters()["mlflow"] is MlflowAdapter
    assert __version__ == "0.1.1"
    dist = distribution("runevidence-mlflow")
    assert dist.version == "0.1.1"
    core_dependency = next(
        Requirement(raw) for raw in dist.requires or []
        if Requirement(raw).name == "runevidence"
    )
    assert core_version in core_dependency.specifier
    assert "0.1.0" not in core_dependency.specifier
    assert "0.2.0" not in core_dependency.specifier

    base = [
        req.split(";")[0].replace(" ", "").lower()
        for req in (dist.requires or [])
        if "extra" not in req.lower()
    ]
    assert "runevidence>=0.1.1,<0.2.0" in base or "runevidence<0.2.0,>=0.1.1" in base
    assert "pyyaml>=6" in base
    assert not any(req.startswith("mlflow") for req in base)

    eps = [ep for ep in entry_points(group="runevidence.adapters") if ep.name == "mlflow"]
    assert len(eps) == 1 and eps[0].value == "runevidence_mlflow:MlflowAdapter"


def test_package_does_not_import_mlflow():
    for path in (PACKAGE / "src" / "runevidence_mlflow").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            assert not any(name == "mlflow" or name.startswith("mlflow.") for name in names), path


# --- roots and example fixture ---------------------------------------------


def test_direct_store_and_project_root_are_both_supported():
    adapter = MlflowAdapter()
    direct_discovery = adapter.discover(MLRUNS)
    project_discovery = adapter.discover(EXAMPLE)

    assert direct_discovery.details["store"] == ""
    assert project_discovery.details["store"] == "mlruns"

    direct = extract(MLRUNS)
    project = extract(EXAMPLE)
    assert {r.name for r in direct.runs} == {"baseline", "tuned"}
    assert {r.name for r in project.runs} == {"baseline", "tuned"}
    assert all(not a.path.startswith("mlruns/") for a in direct.artifacts)

    expected = sorted(p.relative_to(EXAMPLE).as_posix() for p in EXAMPLE.rglob("*") if p.is_file())
    assert [a.path for a in project.artifacts] == expected
    readme = next(a for a in project.artifacts if a.path == "README.md")
    assert readme.run_id is None
    assert len(project.artifacts) == len(direct.artifacts) + 1


def test_experiment_info_params_start_time_and_whitelisted_metadata():
    manifest = extract(EXAMPLE)
    runs = {r.name: r for r in manifest.runs}
    baseline = runs["baseline"]

    assert baseline.params == PARAMS_A
    assert all(isinstance(v, str) for v in baseline.params.values())
    assert baseline.started_at == datetime(2024, 10, 15, 13, 46, 40, tzinfo=timezone.utc)
    assert baseline.started_at.tzinfo == timezone.utc
    assert baseline.metadata["mlflow"] == {
        "experiment_id": "0",
        "experiment_name": "Default",
        "run_id": RUN_A,
        "run_name": "baseline",
        "lifecycle_stage": "active",
        "status": "FINISHED",
    }
    assert runs["tuned"].params == {
        "learning_rate": "0.05",
        "optimizer/name": "sgd",
        "seed": "20",
    }
    assert runs["tuned"].started_at == datetime(2024, 10, 15, 13, 47, 30, tzinfo=timezone.utc)
    assert manifest.project.metadata["mlflow"]["experiments"] == [
        {"experiment_id": "0", "name": "Default", "lifecycle_stage": "active"}
    ]
    assert manifest.evidence == [] and manifest.relationships == []

    # The fixture uses the genuine integer enum MLflow writes, plus both id fields.
    run_meta_text = (MLRUNS / "0" / RUN_A / "meta.yaml").read_text(encoding="utf-8")
    assert "status: 3\n" in run_meta_text
    assert f"run_uuid: {RUN_A}\n" in run_meta_text
    assert "tags: []\n" in run_meta_text


@pytest.mark.parametrize("status", [0, 9])
def test_older_run_uuid_and_unknown_integer_status_are_supported(tmp_path, status):
    root = tmp_path / "store"
    make_store(
        root,
        run_id="run1",
        run_text=(
            "artifact_uri: file:///tmp/mlruns/0/run1/artifacts\n"
            "experiment_id: '0'\n"
            "lifecycle_stage: active\n"
            "run_uuid: legacy-run-uuid\n"
            "run_name: legacy\n"
            "start_time: 1729000000000\n"
            f"status: {status}\n"
            "tags: []\n"
        ),
    )

    (only,) = extract(root).runs

    assert only.metadata["mlflow"]["run_id"] == "legacy-run-uuid"
    assert only.metadata["mlflow"]["status"] == status  # unknown enum preserved


def test_metric_summaries_use_max_step_then_timestamp_and_legacy_rows():
    manifest = extract(EXAMPLE)
    runs = {r.name: r for r in manifest.runs}
    artifacts = {a.id: a for a in manifest.artifacts}

    def result(run_name: str, name: str):
        (found,) = [r for r in manifest.results if r.run_id == runs[run_name].id and r.name == name]
        return found

    loss = result("baseline", "train/loss")
    assert loss.value == 0.45  # step 2, even though it is not the last physical line
    assert loss.metadata == {"step": 2, "timestamp_ms": 1729000020000}
    assert loss.locator is None
    assert artifacts[loss.artifact_id].path == f"mlruns/0/{RUN_A}/metrics/train/loss"

    accuracy = result("baseline", "val/accuracy")
    assert accuracy.value == 0.86  # legacy rows are step 0; latest timestamp wins
    assert accuracy.metadata == {"step": 0, "timestamp_ms": 1729000050000}

    assert result("tuned", "train/loss").value == 0.65
    assert result("tuned", "eval/f1").value == 0.79
    assert len(manifest.results) == 4
    assert all(r.locator is None and r.artifact_id for r in manifest.results)


def test_metric_ties_are_resolved_deterministically(tmp_path):
    root = tmp_path / "store"
    run_dir = make_store(root)
    write(
        run_dir / "metrics" / "value-tie",
        "100 0.2 5\n100 0.8 5\n100 0.5 5\n",
    )
    write(run_dir / "metrics" / "step-timestamp-tie", "100 0.9 5\n200 0.1 5\n")
    write(run_dir / "metrics" / "legacy-tie", "100 0.3\n200 0.7\n")

    manifest = extract(root)
    values = {r.name: r.value for r in manifest.results}

    assert values == {
        "value-tie": 0.8,
        "step-timestamp-tie": 0.1,
        "legacy-tie": 0.7,
    }


# --- hashing, ownership and ids --------------------------------------------


def test_every_file_is_hashed_once_and_owned_by_the_right_run():
    manifest = extract(EXAMPLE)
    expected = sorted(p.relative_to(EXAMPLE).as_posix() for p in EXAMPLE.rglob("*") if p.is_file())

    assert [a.path for a in manifest.artifacts] == expected
    for art in manifest.artifacts:
        path = EXAMPLE / art.path
        assert art.sha256 == sha256_file(path)
        assert art.size_bytes == path.stat().st_size

    by_path = {a.path: a for a in manifest.artifacts}
    assert by_path[f"mlruns/0/{RUN_A}/artifacts/model.txt"].run_id is not None
    assert by_path[f"mlruns/0/{RUN_A}/artifacts/model.txt"].run_id == next(
        r.id for r in manifest.runs if r.name == "baseline"
    )
    assert by_path[f"mlruns/0/{RUN_B}/artifacts/predictions.csv"].run_id == next(
        r.id for r in manifest.runs if r.name == "tuned"
    )
    assert by_path["mlruns/0/meta.yaml"].run_id is None
    assert by_path["README.md"].run_id is None


def test_nested_files_named_like_the_generated_manifest_are_hashed(tmp_path):
    dest = copy_example(tmp_path)
    nested = dest / "mlruns" / "0" / RUN_A / "artifacts"
    write(nested / MANIFEST_FILENAME, '{"nested": true}\n')
    write(nested / "runevidence.json.bak", '{"nested": "backup"}\n')

    manifest = extract(dest)
    by_path = {a.path: a for a in manifest.artifacts}
    baseline_id = next(r.id for r in manifest.runs if r.name == "baseline")

    assert by_path[f"mlruns/0/{RUN_A}/artifacts/{MANIFEST_FILENAME}"].run_id == baseline_id
    assert by_path[f"mlruns/0/{RUN_A}/artifacts/{MANIFEST_FILENAME}.bak"].run_id == baseline_id

    save_manifest(manifest, dest / MANIFEST_FILENAME)
    assert MANIFEST_FILENAME not in {a.path for a in manifest.artifacts}
    assert run_checks(load_manifest(dest), include_untracked=True).findings == []


def test_ignored_directories_never_supply_experiments_or_runs(tmp_path):
    root = tmp_path / "project"
    make_store(root / "mlruns")
    write(root / ".venv" / "9" / "meta.yaml", experiment_meta())
    write(root / ".venv" / "9" / "run1" / "meta.yaml", run_meta("run1"))
    write(root / ".venv" / "9" / "run1" / "artifacts" / "hidden.txt", "hidden\n")
    write(root / "mlruns" / ".git" / "0" / "meta.yaml", experiment_meta())
    write(root / "mlruns" / ".git" / "0" / "run1" / "meta.yaml", run_meta("run1"))
    # An experiment tag file can legitimately be named meta.yaml; it is not a run.
    write(root / "mlruns" / "0" / "tags" / "meta.yaml", "experiment-tag-value")

    manifest = extract(root)
    paths = {a.path for a in manifest.artifacts}

    assert [r.name for r in manifest.runs] == ["run-run1"]
    assert "mlruns/0/tags/meta.yaml" in paths
    assert next(a for a in manifest.artifacts if a.path == "mlruns/0/tags/meta.yaml").run_id is None
    assert not any(path.startswith(".venv/") or "/.git/" in path for path in paths)


def test_ids_are_unique_across_the_whole_namespace_even_when_slugs_collide(tmp_path):
    root = tmp_path / "collide"
    run_dir = make_store(root, run_id="run1")
    write(run_dir / "artifacts" / "a" / "b.txt", "first\n")
    write(run_dir / "artifacts" / "a-b.txt", "second\n")
    write(run_dir / "metrics" / "a" / "b", "100 0.5 0\n")
    write(run_dir / "metrics" / "a-b", "100 0.7 0\n")

    first = extract(root, project_id="run-0-run1")
    second = extract(root, project_id="run-0-run1")
    ids = (
        [first.project.id]
        + [r.id for r in first.runs]
        + [a.id for a in first.artifacts]
        + [r.id for r in first.results]
    )

    assert len(ids) == len(set(ids))
    assert first.runs[0].id != first.project.id
    assert {r.name for r in first.results} == {"a/b", "a-b"}
    assert dump_manifest(first) == dump_manifest(second)


# --- validation and CLI -----------------------------------------------------


def test_strict_checks_and_cli_init_validate_are_clean_on_a_temp_copy(tmp_path, capsys):
    dest = copy_example(tmp_path)

    assert main(["init", str(dest), "--adapter", "mlflow", "--name", "MLflow copy"]) == 0
    out = capsys.readouterr().out
    assert "2 runs" in out and "adapter: mlflow" in out
    assert main(["validate", str(dest), "--strict"]) == 0
    assert "OK:" in capsys.readouterr().out

    manifest = extract(dest, name="MLflow copy")
    assert_clean(dest, manifest)


def test_source_mutation_is_reported_as_a_hash_mismatch(tmp_path, capsys):
    dest = copy_example(tmp_path)
    assert main(["init", str(dest), "--adapter", "mlflow"]) == 0
    capsys.readouterr()

    model = dest / "mlruns" / "0" / RUN_A / "artifacts" / "model.txt"
    model.write_text(model.read_text(encoding="utf-8") + "edited\n", encoding="utf-8", newline="\n")

    assert main(["validate", str(dest), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert any(f["code"] == "hash-mismatch" for f in data["findings"])


def test_no_runs_is_actionable_and_cli_writes_nothing(tmp_path, capsys):
    root = tmp_path / "empty"
    write(root / "mlruns" / "1" / "loose" / "artifacts" / "file.txt", "no metadata here\n")
    write(
        root / "mlruns" / "1" / "loose" / "artifacts" / "decoy" / "meta.yaml",
        run_meta("decoy"),
    )

    with pytest.raises(AdapterError, match="No MLflow runs found") as excinfo:
        extract(root)
    assert "--adapter generic" in str(excinfo.value)

    assert main(["init", str(root), "--adapter", "mlflow"]) == 2
    out = capsys.readouterr().out
    assert "No MLflow runs found" in out and "--adapter generic" in out
    assert not (root / MANIFEST_FILENAME).exists()


@pytest.mark.parametrize(
    ("target", "text", "match"),
    [
        ("0/meta.yaml", "name: [unterminated\n", "not valid YAML"),
        ("0/run1/meta.yaml", "- not\n- a mapping\n", "must be a YAML mapping"),
        ("0/run1/meta.yaml", "experiment_id: '0'\nstart_time: 1\n", "run_id"),
        (
            "0/run1/meta.yaml",
            run_meta("run1", experiment_id="1"),
            "experiment_id",
        ),
        ("0/run1/meta.yaml", run_meta("run1", start_time="not-a-number"), "start_time"),
    ],
)
def test_broken_or_wrong_shape_metadata_is_an_adapter_error(tmp_path, target, text, match):
    root = tmp_path / "store"
    make_store(root)
    write(root / target, text)

    with pytest.raises(AdapterError, match=match):
        extract(root)


def test_unsafe_yaml_is_refused_without_execution(tmp_path):
    marker = tmp_path / "executed.txt"
    root = tmp_path / "store"
    make_store(
        root,
        run_text=(
            "experiment_id: '0'\n"
            f"run_id: !!python/object/apply:pathlib.Path.touch ['{marker.as_posix()}']\n"
            "start_time: 1729000000000\n"
        ),
    )

    with pytest.raises(AdapterError, match="safe loading"):
        extract(root)
    assert not marker.exists()


def test_broken_metadata_cli_exits_two_without_traceback(tmp_path, capsys):
    root = tmp_path / "store"
    make_store(root)
    write(root / "0" / "meta.yaml", "name: [unterminated\n")

    assert main(["init", str(root), "--adapter", "mlflow"]) == 2
    out = capsys.readouterr().out
    assert "ERROR: adapter 'mlflow' cannot read" in out
    assert "Traceback" not in out
    assert not (root / MANIFEST_FILENAME).exists()


# --- bad metric histories ---------------------------------------------------


def test_bad_metric_logs_are_omitted_and_diagnosed_without_partial_summaries(tmp_path):
    root = tmp_path / "store"
    run_dir = make_store(root)
    metrics = run_dir / "metrics"
    write(metrics / "group" / "score", "100 0.5 0\n200 0.7 1\n")
    write(metrics / "bad" / "empty", "")
    write(metrics / "bad" / "blank", "100 0.5 0\n\n")
    write(metrics / "bad" / "nan", "100 nan 0\n")
    write(metrics / "bad" / "inf", "100 inf 0\n")
    write(metrics / "bad" / "shape", "100 0.5 0 extra\n")
    write(metrics / "bad" / "partial", "100 0.1 0\n200 broken 1\n")
    (metrics / "bad" / "encoding").parent.mkdir(parents=True, exist_ok=True)
    (metrics / "bad" / "encoding").write_bytes(b"\xff\xfe\n")

    manifest = extract(root)
    (run,) = manifest.runs

    assert [(r.name, r.value) for r in manifest.results] == [("group/score", 0.7)]
    expected_bad = sorted(
        f"0/run1/metrics/bad/{name}"
        for name in ("blank", "empty", "encoding", "inf", "nan", "partial", "shape")
    )
    assert run.metadata["mlflow"]["unreadable"] == expected_bad
    artifacts = {a.path: a for a in manifest.artifacts}
    for path in expected_bad:
        assert artifacts[path].sha256 == sha256_file(root / path)
    assert_clean(root, manifest)


# --- metadata whitelist, external URIs and symlinks ------------------------


def test_external_artifact_uris_are_not_followed_copied_or_leaked(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret bytes\n", encoding="utf-8")
    root = tmp_path / "store"
    run_dir = make_store(
        root,
        run_text=(
            f"artifact_uri: {outside.as_uri()}\n"
            "experiment_id: '0'\n"
            "lifecycle_stage: active\n"
            "run_id: run1\n"
            "source_name: /private/source/train.py\n"
            "start_time: 1729000000000\n"
            "status: FAILED\n"
            "tags:\n"
            "- key: mlflow.runName\n"
            "  value: tagged-name\n"
            "- key: team\n"
            "  value: secret-team\n"
            "run_uuid: run1\n"
            "user_id: secret-person\n"
        ),
        exp_text=experiment_meta(outside.as_uri()),
    )
    write(run_dir / "metrics" / "score", "100 0.5 0\n")

    manifest = extract(root)
    (run,) = manifest.runs
    text = dump_manifest(manifest)

    assert run.name == "tagged-name"
    assert run.metadata["mlflow"]["status"] == "FAILED"
    assert "secret.txt" not in {a.path for a in manifest.artifacts}
    assert (outside / "secret.txt").read_text(encoding="utf-8") == "secret bytes\n"
    assert manifest.results[0].locator is None
    for forbidden in (
        outside.as_uri(),
        "secret.txt",
        "secret-team",
        "secret-person",
        "artifact_uri",
        "artifact_location",
        "source_name",
        "/private/source",
    ):
        assert forbidden not in text


def test_symlinks_outside_the_root_are_not_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret bytes\n", encoding="utf-8")
    write(outside / "meta.yaml", experiment_meta())
    write(outside / "escaped-run" / "meta.yaml", run_meta("escaped-run"))

    root = tmp_path / "project"
    run_dir = make_store(root / "mlruns")
    (run_dir / "artifacts").mkdir(parents=True)
    try:
        os.symlink(outside / "secret.txt", run_dir / "artifacts" / "leak.txt")
        os.symlink(outside, run_dir / "external-dir", target_is_directory=True)
        os.symlink(outside, root / "mlruns" / "0" / "linked-experiment", target_is_directory=True)
    except NotImplementedError:
        pytest.skip("this platform has no symlink support")
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314 or exc.errno in (
            errno.EPERM,
            errno.EACCES,
            errno.ENOSYS,
        ):
            pytest.skip("this platform cannot create symlinks without extra privileges")
        raise

    manifest = extract(root)
    paths = {a.path for a in manifest.artifacts}

    assert [r.name for r in manifest.runs] == ["run-run1"]
    assert len(manifest.runs) == 1
    assert "mlruns/0/run1/artifacts/leak.txt" not in paths
    assert "mlruns/0/run1/external-dir/secret.txt" not in paths
    assert not any("linked-experiment" in path for path in paths)
    assert "secret" not in dump_manifest(manifest)


def make_dir_link(link: Path, target: Path) -> bool:
    """A directory link: a junction on Windows (no privilege needed), a
    symbolic link elsewhere. Returns False when the platform refuses."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        return False
    return True


def discover_within(root: Path, seconds: float = 60.0):
    """Run discovery in a daemon thread so a traversal loop fails the test
    instead of hanging the whole run."""
    out: dict = {}

    def go() -> None:
        try:
            out["discovery"] = MlflowAdapter().discover(root)
        except BaseException as exc:  # reported in the main thread
            out["error"] = exc

    worker = threading.Thread(target=go, daemon=True)
    worker.start()
    worker.join(seconds)
    assert not worker.is_alive(), f"discovery did not finish within {seconds} seconds"
    if "error" in out:
        raise out["error"]
    return out["discovery"]


def looped_store(tmp_path: Path) -> tuple[Path, Path]:
    """A store whose run folder contains a directory link back to the run
    itself and one to the experiment above it: two in-root loops."""
    root = tmp_path / "project"
    run_dir = make_store(root / "mlruns")
    write(run_dir / "artifacts" / "model.txt", "weights\n")
    write(run_dir / "metrics" / "loss", "1729000000000 0.5 1\n")
    if not (
        make_dir_link(run_dir / "artifacts" / "loop", run_dir)
        and make_dir_link(run_dir / "up", root / "mlruns" / "0")
    ):
        pytest.skip("this platform cannot create directory links")
    return root, run_dir


LOOPED_FILES = [
    "mlruns/0/meta.yaml",
    "mlruns/0/run1/artifacts/model.txt",
    "mlruns/0/run1/meta.yaml",
    "mlruns/0/run1/metrics/loss",
]


def test_in_root_directory_link_loops_are_not_followed(tmp_path):
    # F-01: os.walk(followlinks=False) still enters a Windows junction, so a
    # junction back to an ancestor repeated every file under ever longer
    # paths. Links of either kind are now skipped, and discovery ends.
    root, _ = looped_store(tmp_path)

    discovery = discover_within(root)

    assert discovery.files == LOOPED_FILES
    manifest = extract(root)
    assert [a.path for a in manifest.artifacts] == LOOPED_FILES
    assert [r.name for r in manifest.runs] == ["run-run1"]
    assert [(r.name, r.value) for r in manifest.results] == [("loss", 0.5)]
    assert_clean(root, manifest)


def test_each_physical_directory_is_entered_once_even_if_a_link_is_missed(tmp_path, monkeypatch):
    # Second guard: pretend link detection failed and let os.walk follow
    # links. The directory identity check alone must stop the loops.
    root, _ = looped_store(tmp_path)
    real_walk = os.walk
    monkeypatch.setattr(adapter_module, "_is_link", lambda path: False)
    monkeypatch.setattr(
        adapter_module.os,
        "walk",
        lambda top, followlinks=False, **kw: real_walk(top, followlinks=True, **kw),
    )

    files = discover_within(root).files

    assert len(files) == len(set(files))
    assert sorted(f for f in files if "/loop/" not in f and "/up/" not in f) == LOOPED_FILES
    assert len(files) == len(LOOPED_FILES)


def test_a_root_reached_through_a_directory_link_is_read_normally(tmp_path):
    real = copy_example(tmp_path)
    alias = tmp_path / "alias"
    if not make_dir_link(alias, real):
        pytest.skip("this platform cannot create directory links")

    via_alias = discover_within(alias)
    direct = discover_within(real)

    assert via_alias.files == direct.files
    assert len(via_alias.files) > 0
    assert {r.name for r in extract(alias).runs} == {"baseline", "tuned"}


def test_trash_files_are_hashed_but_never_become_runs(tmp_path):
    dest = copy_example(tmp_path)
    trash_run = dest / "mlruns" / ".trash" / "0" / "deleted-run"
    write(trash_run / "meta.yaml", run_meta("deleted-run"))
    write(
        dest / "mlruns" / "0" / RUN_A / "artifacts" / "decoy" / "meta.yaml",
        run_meta("decoy"),
    )

    manifest = extract(dest)
    paths = {a.path: a for a in manifest.artifacts}

    assert {r.name for r in manifest.runs} == {"baseline", "tuned"}
    assert "mlruns/.trash/0/deleted-run/meta.yaml" in paths
    assert paths["mlruns/.trash/0/deleted-run/meta.yaml"].run_id is None
    assert "mlruns/0/a1b2c3d4e5f60718293a4b5c6d7e8f90/artifacts/decoy/meta.yaml" in paths
    assert len(manifest.runs) == 2


# --- read-only and portability ---------------------------------------------


def test_extract_is_read_only_and_the_manifest_survives_moving_the_folder(tmp_path, capsys):
    source = copy_example(tmp_path)
    before = {
        p.relative_to(source).as_posix(): p.read_bytes()
        for p in source.rglob("*")
        if p.is_file()
    }

    extract(source)
    after = {
        p.relative_to(source).as_posix(): p.read_bytes()
        for p in source.rglob("*")
        if p.is_file()
    }
    assert after == before

    moved = tmp_path / "elsewhere" / "moved-example"
    moved.parent.mkdir()
    shutil.copytree(source, moved)
    assert main(["init", str(moved), "--adapter", "mlflow"]) == 0
    assert main(["validate", str(moved), "--strict"]) == 0
    capsys.readouterr()

    text = (moved / MANIFEST_FILENAME).read_text(encoding="utf-8")
    assert str(tmp_path) not in text
    manifest = load_manifest(moved).manifest
    assert all("\\" not in a.path and ":" not in a.path for a in manifest.artifacts)

    final = tmp_path / "final-location"
    shutil.move(str(moved), str(final))
    assert main(["validate", str(final), "--strict"]) == 0
