"""Schema 0.5 migration gate: additive 0.1-0.4 -> 0.5, version-only bumps,
downgrade refusal, and the frozen 0.5 command texts.

Migration is manifest-only and transactional. 0.1/0.2 sources pass the existing
locator pre-check before any write; 0.3/0.4 sources are version-only bumps. A
0.5 source never migrates down. The checks here also pin the CLI-layer 0.5
refusals, which precede adapter discovery and any write.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
from pathlib import Path

import pytest

from runevidence import cli
from runevidence import edit as edit_module
from runevidence import migrate as migrate_module
from runevidence.manifest import dump_raw

MANIFEST = "runevidence.json"
TB_LOCATOR = {
    "kind": "tensorboard",
    "tag": "loss/val",
    "step": 40,
    "occurrence": 0,
}
JSONL_LOCATOR = {"kind": "jsonl", "line": 2, "pointer": "/loss"}
CSV_LOCATOR = {"kind": "csv-cell", "row": 1, "column": 2, "header": "val_rmse"}

INIT_0_5_TEXT = (
    "ERROR: the existing manifest declares schema 0.5. init --force would regenerate the "
    "manifest and cannot carry over first-class values or locators. Nothing was written. "
    "Move the manifest aside, run init, then run runevidence migrate PATH --to 0.5 (unless "
    "the adapter writes 0.5 itself) and reapply the first-class values and locators."
)
CONDITIONS_0_5_TEXT = (
    "ERROR (UNSUPPORTED_VERSION): the conditions command requires an explicit "
    "schema 0.2 manifest. A schema 0.1 manifest can be migrated with "
    "runevidence migrate PATH --to 0.2; schema 0.3, 0.4 and 0.5 have no downgrade path. "
    "Nothing was written."
)
CONDITIONS_0_4_TEXT = (
    "ERROR (UNSUPPORTED_VERSION): the conditions command requires an explicit "
    "schema 0.2 manifest. A schema 0.1 manifest can be migrated with "
    "runevidence migrate PATH --to 0.2; schema 0.3 and 0.4 have no downgrade path. "
    "Nothing was written."
)
CONDITIONS_0_3_TEXT = (
    "ERROR (UNSUPPORTED_VERSION): the conditions command requires an explicit "
    "schema 0.2 manifest. A schema 0.1 manifest can be migrated with "
    "runevidence migrate PATH --to 0.2; schema 0.3 has no downgrade path. "
    "Nothing was written."
)
UNSUPPORTED_TARGET_TEXT = (
    "ERROR (UNSUPPORTED_TARGET): only --to 0.2, --to 0.3, --to 0.4, --to 0.5 or "
    "--to 0.6 is supported; downgrade and other versions are not available. Nothing "
    "was written."
)
LOCATOR_REFRESH_TEXT = (
    "ERROR (LOCATOR_REFRESH_UNSUPPORTED): rehash cannot refresh a manifest that "
    "contains an object locator. Nothing was written."
)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


def write(folder: Path, value: dict, name: str = MANIFEST) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(dump_raw(value).encode("utf-8"))
    return path


def artifact(
    artifact_id: str = "a", path: str = "data.json", **updates
) -> dict:
    value = {
        "id": artifact_id,
        "path": path,
        "sha256": "a" * 64,
        "size_bytes": 8,
    }
    value.update(updates)
    return value


def legacy_doc(
    version: str,
    *,
    locator=...,
    artifact_id="a",
    artifact_value=None,
) -> dict:
    """A minimal 0.1/0.2 document with one string locator."""
    document = {
        "schema_version": version,
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "run", "name": "Run"}],
        "artifacts": [artifact() if artifact_value is None else artifact_value],
        "results": [
            {
                "id": "r",
                "run_id": "run",
                "name": "accuracy",
                "value": 0.9,
                "artifact_id": artifact_id,
            }
        ],
        "evidence": [],
        "relationships": [],
    }
    if locator is not ...:
        document["results"][0]["locator"] = locator
    return document


def rich_doc(version: str) -> dict:
    """A 0.3/0.4 document with every 0.5-relevant field and odd key order."""
    artifacts = [
        artifact("a", "data.json", kind="dataset", dataset_id="toy-v1"),
        artifact("b", "data.jsonl", kind="metrics"),
    ]
    results = [
        {
            "name": "accuracy",
            "id": "string",
            "run_id": "run",
            "value": 0.9,
            "artifact_id": "a",
            "locator": "/v",
            "metric_definition": {
                "definition_id": "accuracy",
                "higher_is_better": True,
                "version": 3,
            },
        },
        {
            "id": "jsonl",
            "name": "loss",
            "value": 0.1,
            "artifact_id": "b",
            "run_id": "run",
            "locator": dict(JSONL_LOCATOR),
        },
        {
            "id": "count",
            "run_id": "run",
            "name": "count",
            "value": 7,
            "artifact_id": "a",
        },
    ]
    if version == "0.4":
        artifacts.append(artifact("c", "metrics/metrics.csv", kind="metrics"))
        results.append(
            {
                "id": "csv",
                "run_id": "run",
                "name": "rmse",
                "value": 0.5,
                "artifact_id": "c",
                "locator": dict(CSV_LOCATOR),
            }
        )
    return {
        "schema_version": version,
        "project": {"name": "P", "id": "p"},
        "runs": [
            {
                "name": "Run",
                "id": "run",
                "conditions": {
                    "evaluation_split": "test",
                    "environment": {"python": "3.12", "accelerator": "cpu"},
                },
            }
        ],
        "artifacts": artifacts,
        "results": results,
        "evidence": [
            {
                "id": "claim",
                "claim": "accuracy is recorded",
                "supports": ["string"],
                "metadata": {"source": "README", "rank": 1, "verified": True},
            }
        ],
        "relationships": [],
    }


def tb_doc(locator=None) -> dict:
    """A valid 0.5 document with a tensorboard locator by default."""
    return {
        "schema_version": "0.5",
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "run", "name": "Run"}],
        "artifacts": [
            artifact("a", "events.tfevents", kind="dataset", dataset_id="toy-v1")
        ],
        "results": [
            {
                "id": "r",
                "run_id": "run",
                "name": "accuracy",
                "value": 0.9,
                "artifact_id": "a",
                "locator": dict(TB_LOCATOR if locator is None else locator),
            }
        ],
        "evidence": [],
        "relationships": [],
    }


def string_only_0_5_doc(sha256: str) -> dict:
    return {
        "schema_version": "0.5",
        "project": {"id": "p", "name": "P"},
        "runs": [
            {
                "id": "run",
                "name": "Run",
                "conditions": {"evaluation_split": "test"},
            }
        ],
        "artifacts": [
            {
                "id": "a",
                "path": "data.json",
                "sha256": sha256,
                "size_bytes": len(b'{"v": 0.9}\n'),
            }
        ],
        "results": [
            {
                "id": "r",
                "run_id": "run",
                "name": "accuracy",
                "value": 0.9,
                "artifact_id": "a",
                "locator": "/v",
            }
        ],
        "evidence": [],
        "relationships": [],
    }


def snapshot(folder: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(folder.iterdir())}


def backups(folder: Path) -> list[str]:
    return sorted(
        path.name for path in folder.iterdir()
        if path.name == MANIFEST + ".bak" or path.name.startswith(MANIFEST + ".bak.")
    )


def assert_no_writes(folder: Path, before: dict[str, bytes]) -> None:
    assert snapshot(folder) == before
    assert backups(folder) == []


def tree_equal(left, right) -> None:
    """Assert identical JSON values, Python types, and object-key order."""
    assert type(left) is type(right), (left, right)
    if isinstance(left, dict):
        assert list(left) == list(right), (list(left), list(right))
        for key in left:
            tree_equal(left[key], right[key])
    elif isinstance(left, list):
        assert len(left) == len(right)
        for pair in zip(left, right):
            tree_equal(*pair)
    else:
        assert left == right


def assert_preserved(before: dict, after: dict, target: str) -> None:
    assert after["schema_version"] == target
    assert next(iter(after)) == "schema_version"
    tree_equal(
        {key: value for key, value in before.items() if key != "schema_version"},
        {key: value for key, value in after.items() if key != "schema_version"},
    )


@pytest.mark.parametrize("source", ["0.3", "0.4"])
def test_0_3_and_0_4_to_0_5_are_version_only(tmp_path, monkeypatch, source):
    folder = tmp_path / "exp"
    original = rich_doc(source)
    path = write(folder, original)
    before_bytes = path.read_bytes()
    before_folder = snapshot(folder)

    artifact_paths = {folder / item["path"] for item in original["artifacts"]}
    real_read_bytes = Path.read_bytes
    read_paths: list[Path] = []

    def tracked_read_bytes(self):
        read_paths.append(self)
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", tracked_read_bytes)

    code, out, err = run(["migrate", str(folder), "--to", "0.5", "--dry-run", "--json"])

    assert (code, err) == (0, "")
    result = json.loads(out)
    assert result == {
        "format": "runevidence-migrate-result-v1",
        "ok": True,
        "from": source,
        "to": "0.5",
        "changed": True,
        "dry_run": True,
        "backup": "none",
    }
    assert snapshot(folder) == before_folder
    assert path.read_bytes() == before_bytes

    code, out, err = run(["migrate", str(folder), "--to", "0.5", "--json"])

    assert (code, err) == (0, "")
    result = json.loads(out)
    assert result == {
        "format": "runevidence-migrate-result-v1",
        "ok": True,
        "from": source,
        "to": "0.5",
        "changed": True,
        "dry_run": False,
        "backup": "created",
    }
    migrated = json.loads(path.read_bytes())
    assert_preserved(original, migrated, "0.5")
    assert migrated["runs"][0]["conditions"] == original["runs"][0]["conditions"]
    assert migrated["artifacts"][0]["dataset_id"] == "toy-v1"
    assert migrated["results"][0]["metric_definition"] == original["results"][0]["metric_definition"]
    assert (folder / (MANIFEST + ".bak")).read_bytes() == before_bytes
    assert not (set(read_paths) & artifact_paths), "migration read an artifact"


@pytest.mark.parametrize("source", ["0.1", "0.2"])
def test_0_1_and_0_2_to_0_5_migrate_a_safe_string_locator(tmp_path, source):
    folder = tmp_path / "exp"
    original = legacy_doc(source, locator="/v")
    path = write(folder, original)
    before_bytes = path.read_bytes()

    code, out, err = run(["migrate", str(folder), "--to", "0.5", "--json"])

    assert (code, err) == (0, "")
    result = json.loads(out)
    assert (result["from"], result["to"], result["backup"]) == (source, "0.5", "created")
    migrated = json.loads(path.read_bytes())
    assert_preserved(original, migrated, "0.5")
    assert migrated["results"][0]["locator"] == "/v"
    assert (folder / (MANIFEST + ".bak")).read_bytes() == before_bytes


LEGACY_OFFENDERS = {
    "prefixed-pointer": {"locator": "jsonl:/x"},
    "invalid-pointer-escape": {"locator": "/x~2"},
    "missing-artifact-id": {"locator": "/x", "artifact_id": None},
    "non-json-string-target": {
        "artifact_value": artifact("a", "data.csv"),
    },
    "metrics-csv-string-target": {
        "artifact_value": artifact("a", "metrics/metrics.csv", kind="metrics"),
    },
    "volatile-artifact": {
        "artifact_value": artifact("a", "data.json", volatile=True),
    },
    "no-digest-artifact": {
        "artifact_value": {"id": "a", "path": "data.json", "sha256": None},
    },
    "directory-artifact": {
        "artifact_value": {
            "id": "a",
            "path": "data",
            "artifact_type": "directory",
            "digest_format": "runevidence-directory-v1",
        },
    },
}


@pytest.mark.parametrize("source", ["0.1", "0.2"])
@pytest.mark.parametrize("label", tuple(LEGACY_OFFENDERS))
def test_0_1_and_0_2_to_0_5_refuse_unsafe_locators_before_writes(
    tmp_path, source, label
):
    folder = tmp_path / "exp"
    value = legacy_doc(source, **LEGACY_OFFENDERS[label])
    # A second, valid result proves no partial migration is possible.
    value["results"].append(
        {
            "id": "valid",
            "run_id": "run",
            "name": "loss",
            "value": 0.1,
            "artifact_id": "a",
            "locator": "",
        }
    )
    write(folder, value)
    before = snapshot(folder)

    code, out, err = run(["migrate", str(folder), "--to", "0.5", "--json"])

    assert (code, err) == (2, "")
    assert json.loads(out)["error"]["code"] == "LOCATOR_MIGRATION_UNSUPPORTED"
    assert_no_writes(folder, before)


def test_0_5_precheck_text_names_schema_0_5_and_is_value_free(tmp_path):
    folder = tmp_path / "exp"
    write(folder, legacy_doc("0.2", locator="jsonl:/CANARY-LOCATOR"))
    before = snapshot(folder)

    code, out, err = run(["migrate", str(folder), "--to", "0.5"])

    assert (code, err) == (2, "")
    assert out == (
        "ERROR (LOCATOR_MIGRATION_UNSUPPORTED): the manifest contains a locator "
        "that schema 0.5 cannot accept or verify. Nothing was written.\n"
    )
    assert "CANARY-LOCATOR" not in out
    assert_no_writes(folder, before)


def test_0_5_to_0_5_is_the_idempotent_no_op(tmp_path):
    folder = tmp_path / "exp"
    path = write(folder, tb_doc())
    before_bytes = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns

    code, out, err = run(["migrate", str(folder), "--to", "0.5", "--json"])

    assert (code, err) == (0, "")
    assert json.loads(out) == {
        "format": "runevidence-migrate-result-v1",
        "ok": True,
        "from": "0.5",
        "to": "0.5",
        "changed": False,
        "dry_run": False,
        "backup": "none",
    }
    assert path.read_bytes() == before_bytes
    assert path.stat().st_mtime_ns == before_mtime
    assert backups(folder) == []
    assert json.loads(path.read_bytes())["results"][0]["locator"] == TB_LOCATOR


@pytest.mark.parametrize("target", ["0.2", "0.3", "0.4"])
def test_0_5_downgrades_are_refused_without_internal_error(tmp_path, target):
    folder = tmp_path / "exp"
    write(folder, tb_doc())
    before = snapshot(folder)

    code, out, err = run(["migrate", str(folder), "--to", target, "--json"])

    assert (code, err) == (2, "")
    assert json.loads(out)["error"]["code"] == "DOWNGRADE_UNSUPPORTED"
    assert_no_writes(folder, before)

    text_code, text_out, text_err = run(["migrate", str(folder), "--to", target])
    assert (text_code, text_err) == (2, "")
    assert text_out == (
        "ERROR (DOWNGRADE_UNSUPPORTED): downgrade is not supported. "
        "Nothing was written.\n"
    )
    assert_no_writes(folder, before)


def test_unknown_target_stays_unsupported_at_the_cli_layer(tmp_path):
    folder = tmp_path / "exp"
    write(folder, tb_doc())
    before = snapshot(folder)

    code, out, err = run(["migrate", str(folder), "--to", "0.7", "--json"])

    assert (code, err) == (2, "")
    assert json.loads(out)["error"]["code"] == "UNSUPPORTED_TARGET"
    assert_no_writes(folder, before)

    text_code, text_out, text_err = run(["migrate", str(folder), "--to", "0.7"])
    assert (text_code, text_err) == (2, "")
    assert text_out == UNSUPPORTED_TARGET_TEXT + "\n"
    assert_no_writes(folder, before)


def test_init_0_5_refusal_is_the_exact_rfc_text():
    assert cli.INIT_0_5_REFUSAL == INIT_0_5_TEXT


def test_init_over_a_declared_0_5_refuses_before_any_write(tmp_path):
    folder = tmp_path / "exp"
    document = tb_doc(
        locator={
            "kind": "tensorboard",
            "tag": "CANARY-TAG",
            "step": 40,
            "occurrence": 0,
        }
    )
    document["project"]["id"] = "CANARY-PROJECT-ID"
    document["results"][0]["name"] = "CANARY-METRIC-NAME"
    path = write(folder, document)
    before = path.read_bytes()

    code, out, err = run(["init", str(folder), "--force"])

    assert (code, err) == (2, "")
    assert out.endswith(INIT_0_5_TEXT + "\n")
    assert path.read_bytes() == before
    assert backups(folder) == []
    for canary in ("CANARY-PROJECT-ID", "CANARY-TAG", "CANARY-METRIC-NAME"):
        assert canary not in out


@pytest.mark.parametrize(
    "version,expected",
    [
        ("0.3", CONDITIONS_0_3_TEXT),
        ("0.4", CONDITIONS_0_4_TEXT),
        ("0.5", CONDITIONS_0_5_TEXT),
    ],
)
def test_conditions_version_texts_are_frozen_and_write_nothing(
    tmp_path, version, expected
):
    folder = tmp_path / "exp"
    if version == "0.5":
        document = tb_doc()
    elif version == "0.4":
        document = {
            "schema_version": "0.4",
            "project": {"id": "p", "name": "P"},
            "runs": [{"id": "run", "name": "Run"}],
            "artifacts": [artifact("a", "metrics.csv", kind="metrics")],
            "results": [
                {
                    "id": "r",
                    "run_id": "run",
                    "name": "rmse",
                    "value": 0.5,
                    "artifact_id": "a",
                    "locator": dict(CSV_LOCATOR),
                }
            ],
            "evidence": [],
            "relationships": [],
        }
    else:
        document = {
            "schema_version": "0.3",
            "project": {"id": "p", "name": "P"},
            "runs": [{"id": "run", "name": "Run"}],
            "artifacts": [artifact("a", "data.jsonl", kind="metrics")],
            "results": [
                {
                    "id": "r",
                    "run_id": "run",
                    "name": "loss",
                    "value": 0.1,
                    "artifact_id": "a",
                    "locator": dict(JSONL_LOCATOR),
                }
            ],
            "evidence": [],
            "relationships": [],
        }
    write(folder, document)
    before = snapshot(folder)

    code, out, err = run(
        ["conditions", str(folder), "--run", "run", "--clear", "environment"]
    )

    assert (code, err) == (2, "")
    assert out == expected + "\n"
    assert_no_writes(folder, before)


def test_rehash_refuses_a_tensorboard_locator_before_read_or_write(
    tmp_path, monkeypatch
):
    folder = tmp_path / "exp"
    path = write(folder, tb_doc())
    before = snapshot(folder)

    def denied(*args, **kwargs):
        raise AssertionError("rehash read an artifact before refusing")

    monkeypatch.setattr(edit_module, "sha256_file", denied)

    code, out, err = run(["rehash", str(folder), "--all"])

    assert (code, err) == (2, "")
    assert out == LOCATOR_REFRESH_TEXT + "\n"
    assert path.read_bytes() == before[MANIFEST]
    assert_no_writes(folder, before)


def test_rehash_still_accepts_a_string_only_0_5_manifest(tmp_path):
    folder = tmp_path / "exp"
    folder.mkdir()
    data = b'{"v": 0.9}\n'
    (folder / "data.json").write_bytes(data)
    document = string_only_0_5_doc("b" * 64)
    path = write(folder, document)
    recorded = json.loads(path.read_bytes())
    assert recorded["artifacts"][0]["sha256"] == "b" * 64

    code, out, err = run(["rehash", str(folder), "--all"])

    assert (code, err) == (0, "")
    assert "Re-recorded 1 file" in out
    migrated = json.loads(path.read_bytes())
    assert migrated["schema_version"] == "0.5"
    assert migrated["artifacts"][0]["sha256"] == hashlib.sha256(data).hexdigest()
    assert migrated["artifacts"][0]["size_bytes"] == len(data)
    assert migrated["results"][0]["locator"] == "/v"
    assert migrated["results"][0]["value"] == 0.9
    assert migrated["runs"][0]["conditions"] == {"evaluation_split": "test"}


def test_fingerprint_refuses_0_5_through_the_existing_v1_message(tmp_path):
    folder = tmp_path / "exp"
    path = write(folder, tb_doc())
    before = snapshot(folder)

    code, out, err = run(["fingerprint", str(folder), "--json"])

    assert (code, err) == (2, "")
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["exit_code"] == 2
    assert payload["error"] == (
        "fingerprint v1 only accepts manifest schema 0.1; "
        "explicit version decision required"
    )
    assert path.read_bytes() == before[MANIFEST]
    assert_no_writes(folder, before)


def test_0_4_to_0_5_is_version_only_and_preserves_object_locators(tmp_path):
    folder = tmp_path / "exp"
    document = {
        "schema_version": "0.4",
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "run", "name": "Run"}],
        "artifacts": [
            artifact("a", "data.jsonl", kind="metrics"),
            artifact("b", "metrics.csv", kind="metrics"),
        ],
        "results": [
            {
                "id": "jsonl",
                "run_id": "run",
                "name": "loss",
                "value": 0.1,
                "artifact_id": "a",
                "locator": dict(JSONL_LOCATOR),
            },
            {
                "id": "csv",
                "run_id": "run",
                "name": "rmse",
                "value": 0.5,
                "artifact_id": "b",
                "locator": dict(CSV_LOCATOR),
            },
        ],
        "evidence": [],
        "relationships": [],
    }
    path = write(folder, document)
    before_bytes = path.read_bytes()

    code, out, err = run(["migrate", str(folder), "--to", "0.5", "--json"])

    assert (code, err) == (0, "")
    assert json.loads(out)["to"] == "0.5"
    migrated = json.loads(path.read_bytes())
    assert_preserved(document, migrated, "0.5")
    assert migrated["results"][0]["locator"] == JSONL_LOCATOR
    assert migrated["results"][1]["locator"] == CSV_LOCATOR
    assert (folder / (MANIFEST + ".bak")).read_bytes() == before_bytes
