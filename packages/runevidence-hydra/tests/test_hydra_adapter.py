"""The Hydra adapter: discovery, what lands in the manifest, and the edges
(no runs, unreadable or hostile YAML, a run folder used as the root)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("runevidence_hydra", reason="install packages/runevidence-hydra to run these tests")

from runevidence.adapters import AdapterError, available_adapters  # noqa: E402
from runevidence.checks import run_checks  # noqa: E402
from runevidence.cli import main  # noqa: E402
from runevidence.manifest import dump_manifest, load_manifest, save_manifest  # noqa: E402
from runevidence.models import MANIFEST_FILENAME, Manifest  # noqa: E402
from runevidence_hydra import HydraAdapter  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "examples" / "hydra-sweep"
DOGFOOD = REPO / "examples" / "dogfood-mlp-sweep"
SWEEP = "multirun/2026-10-25/19-16-16"
SINGLE = "outputs/2026-10-25/19-16-14"


def extract(root: Path, name: str = "Hydra sweep example") -> Manifest:
    adapter = HydraAdapter()
    return adapter.extract(root, adapter.discover(root), project_id="hydra-sweep-example", project_name=name)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def assert_clean(root: Path, manifest: Manifest) -> None:
    save_manifest(manifest, root / MANIFEST_FILENAME)
    assert run_checks(load_manifest(root), include_untracked=True).findings == []


@pytest.fixture
def example(tmp_path: Path) -> Path:
    dest = tmp_path / "hydra-sweep"
    shutil.copytree(EXAMPLE, dest)
    (dest / MANIFEST_FILENAME).unlink()
    return dest


# --- happy path on real Hydra output ---------------------------------------


def test_is_registered_through_the_entry_point():
    assert available_adapters()["hydra"] is HydraAdapter


def test_discover_finds_single_runs_and_sweep_jobs_at_any_depth(example):
    details = HydraAdapter().discover(example).details

    assert details["runs"] == [f"{SWEEP}/0", f"{SWEEP}/1", SINGLE]
    assert details["sweeps"] == [SWEEP]


def test_runs_get_config_as_params_and_hydra_settings_as_metadata(example):
    runs = {r.name: r for r in extract(example).runs}

    job1 = runs[f"{SWEEP}/1"]
    assert job1.id == "run-multirun-2026-10-25-19-16-16-1"
    assert job1.params["optimizer"] == {"name": "momentum", "lr": 0.05, "momentum": 0.9}
    assert job1.params["epochs"] == 40
    assert job1.started_at.isoformat() == "2026-10-25T19:16:16"
    assert job1.metadata["hydra"] == {
        "overrides": ["optimizer=momentum"],
        "job_name": "train",
        "job_num": 1,
        "override_dirname": "optimizer=momentum",
        "mode": "MULTIRUN",
        "version": "1.3.8",
        "choices": {"optimizer": "momentum"},  # hydra/* groups left out
        "sweep_dir": SWEEP,
    }

    single = runs[SINGLE].metadata["hydra"]
    assert single["mode"] == "RUN"
    assert single["overrides"] == []
    assert "job_num" not in single  # Hydra writes `num: ???` outside a sweep
    assert "sweep_dir" not in single


def test_files_belong_to_their_run_and_get_kinds(example):
    arts = {a.path: a for a in extract(example).artifacts}

    assert arts[f"{SWEEP}/0/metrics.json"].run_id == "run-multirun-2026-10-25-19-16-16-0"
    assert arts[f"{SWEEP}/0/metrics.json"].kind is None
    assert arts[f"{SWEEP}/0/.hydra/config.yaml"].kind == "config"
    assert arts[f"{SINGLE}/train.log"].kind == "log"
    # Sweep-level and project files belong to no run.
    assert arts[f"{SWEEP}/multirun.yaml"].run_id is None
    assert arts[f"{SWEEP}/multirun.yaml"].kind == "config"
    assert arts["data/points.csv"].run_id is None
    assert arts["train.py"].run_id is None


def test_no_absolute_paths_from_hydra_yaml_reach_the_manifest(example):
    text = dump_manifest(extract(example))

    assert "C:\\\\work" not in text and "C:/work" not in text
    assert "output_dir" not in text and "cwd" not in text


def test_manifest_validates_clean_including_untracked_check(example):
    assert_clean(example, extract(example))


def test_committed_example_manifest_is_exactly_what_the_adapter_writes(example):
    """Guards both the example (hashes still match) and the adapter (output
    is deterministic and unchanged)."""
    committed = (EXAMPLE / MANIFEST_FILENAME).read_text(encoding="utf-8")

    assert dump_manifest(extract(example)) == committed
    assert run_checks(load_manifest(EXAMPLE)).findings == []


def test_cli_init_with_hydra_adapter_then_strict_validate(example, capsys):
    assert main(["init", str(example), "--adapter", "hydra"]) == 0
    assert "3 runs" in capsys.readouterr().out
    assert main(["validate", str(example), "--strict"]) == 0


def test_closes_gap_g8_on_the_hydra_style_dogfood(tmp_path):
    """Dogfood 2 imitated Hydra with JSON files; `generic` found 0 runs there."""
    root = tmp_path / "dogfood"
    shutil.copytree(DOGFOOD, root)
    (root / MANIFEST_FILENAME).unlink()

    manifest = extract(root, "dogfood")

    assert [r.params["learning_rate_init"] for r in manifest.runs] == [0.0003, 0.001, 0.01]
    assert manifest.runs[2].metadata["hydra"]["overrides"] == ["learning_rate_init=0.01"]
    assert_clean(root, manifest)


# --- failures and edges ------------------------------------------------------


def test_folder_without_hydra_runs_is_refused(tmp_path):
    write(tmp_path / "runs" / "a" / "metrics.json", "{}\n")

    with pytest.raises(AdapterError, match="no Hydra run folders"):
        extract(tmp_path)


def test_cli_init_exits_two_and_writes_nothing_without_hydra_runs(tmp_path, capsys):
    write(tmp_path / "data.csv", "x\n1\n")

    assert main(["init", str(tmp_path), "--adapter", "hydra"]) == 2
    out = capsys.readouterr().out
    assert "no Hydra run folders" in out and "--adapter generic" in out
    assert not (tmp_path / MANIFEST_FILENAME).exists()


def test_unparsable_and_unsafe_yaml_is_hashed_but_not_read(tmp_path):
    run = tmp_path / "outputs" / "2026-01-02" / "03-04-05"
    write(run / ".hydra" / "config.yaml", "lr: [0.1\n")  # broken YAML
    # safe_load must refuse this tag rather than build the object.
    write(run / ".hydra" / "overrides.yaml", "- !!python/object/apply:os.system ['echo pwned']\n")
    write(run / ".hydra" / "hydra.yaml", "just a string\n")  # valid YAML, wrong shape

    manifest = extract(tmp_path)

    (only,) = manifest.runs
    assert only.params == {}
    assert only.metadata["hydra"]["unreadable"] == [
        "outputs/2026-01-02/03-04-05/.hydra/config.yaml",
        "outputs/2026-01-02/03-04-05/.hydra/overrides.yaml",
        "outputs/2026-01-02/03-04-05/.hydra/hydra.yaml",
    ]
    assert only.started_at.isoformat() == "2026-01-02T03:04:05"
    assert len(manifest.artifacts) == 3
    assert_clean(tmp_path, manifest)


def test_yaml_only_values_become_plain_json(tmp_path):
    write(
        tmp_path / "exp" / ".hydra" / "config.yaml",
        "when: 2026-10-25\nbad: .nan\n1: int key\ntags: !!set {a: null}\n",
    )

    manifest = extract(tmp_path)

    assert manifest.runs[0].params == {"when": "2026-10-25", "bad": "nan", "1": "int key", "tags": ["a"]}
    assert "NaN" not in dump_manifest(manifest)  # stays standard JSON (gap G12)
    assert_clean(tmp_path, manifest)


def test_root_that_is_itself_a_run_folder(tmp_path):
    """`runevidence init --adapter hydra outputs/<date>/<time>` on one run."""
    root = tmp_path / "19-16-14"
    write(root / ".hydra" / "config.yaml", "lr: 0.1\n")
    write(root / ".hydra" / "overrides.yaml", "[]\n")
    write(root / "metrics.json", '{"loss": 1.0}\n')

    manifest = extract(root)

    (only,) = manifest.runs
    assert (only.id, only.name, only.params) == ("run-19-16-14", "19-16-14", {"lr": 0.1})
    assert {a.run_id for a in manifest.artifacts} == {"run-19-16-14"}
    assert "sweep_dir" not in only.metadata["hydra"]
    assert_clean(root, manifest)


def test_custom_output_dirs_and_nested_runs(tmp_path):
    """No reliance on outputs/ or multirun/; a file belongs to the innermost run."""
    write(tmp_path / "exp" / "a" / ".hydra" / "config.yaml", "x: 1\n")
    write(tmp_path / "exp" / "a" / "inner" / ".hydra" / "config.yaml", "x: 2\n")
    write(tmp_path / "exp" / "a" / "inner" / "out.txt", "hi\n")

    manifest = extract(tmp_path)

    runs = {r.name: r for r in manifest.runs}
    assert set(runs) == {"exp/a", "exp/a/inner"}
    assert runs["exp/a"].started_at is None  # not Hydra's default <date>/<time> naming
    out = next(a for a in manifest.artifacts if a.path == "exp/a/inner/out.txt")
    assert out.run_id == runs["exp/a/inner"].id
    assert_clean(tmp_path, manifest)


def test_extract_does_not_write_into_the_folder(example):
    before = sorted(p.relative_to(example).as_posix() for p in example.rglob("*"))
    extract(example)
    assert sorted(p.relative_to(example).as_posix() for p in example.rglob("*")) == before


def test_params_round_trip_through_the_manifest_file(example):
    manifest = extract(example)
    save_manifest(manifest, example / MANIFEST_FILENAME)

    reloaded = json.loads((example / MANIFEST_FILENAME).read_text(encoding="utf-8"))

    assert reloaded["runs"][0]["params"] == manifest.runs[0].params
