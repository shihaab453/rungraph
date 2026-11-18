"""Opt-in schema-0.5 TensorBoard scalar byte refresh."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import tensorboard_builder as tb
from runevidence import edit
from runevidence.cli import main
from runevidence.manifest import validate_manifest


def event(tag: bytes = b"loss", value: float = 0.5, step: int = 1) -> bytes:
    return tb.records(
        tb.event(step=step, summary_bytes=tb.summary(tb.value(tag=tag, simple_value=value)))
    )


def make_root(
    tmp_path: Path,
    *,
    data: bytes | None = None,
    value: float = 0.5,
    schema: str = "0.5",
    artifact: dict | None = None,
    extra_results: list[dict] | None = None,
) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    data = event() if data is None else data
    (tmp_path / "e.tfevents").write_bytes(data)
    art = {
        "id": "a",
        "path": "e.tfevents",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }
    if artifact:
        art.update(artifact)
    results = [
        {
            "id": "r",
            "run_id": "run",
            "name": "loss",
            "value": value,
            "artifact_id": "a",
            "locator": {
                "kind": "tensorboard",
                "tag": "loss",
                "step": 1,
                "occurrence": 0,
            },
        }
    ]
    results.extend(extra_results or [])
    document = {
        "schema_version": schema,
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "run", "name": "R"}],
        "artifacts": [art],
        "results": results,
    }
    validate_manifest(document)
    (tmp_path / "runevidence.json").write_text(
        json.dumps(document), encoding="utf-8", newline="\n"
    )
    return tmp_path


def test_appended_bytes_refresh_without_rewriting_values(tmp_path: Path, capsys):
    root = make_root(tmp_path, artifact={"volatile": False})
    before = json.loads((root / "runevidence.json").read_text())
    (root / "e.tfevents").write_bytes(event() + event(value=0.6, step=2))

    assert main(["rehash", str(root), "a", "--refresh-locators"]) == 0
    out = capsys.readouterr().out
    after = json.loads((root / "runevidence.json").read_text())
    assert after["results"] == before["results"]
    assert after["artifacts"][0]["volatile"] is False
    assert after["artifacts"][0]["sha256"] != before["artifacts"][0]["sha256"]
    assert after["artifacts"][0]["size_bytes"] > before["artifacts"][0]["size_bytes"]
    assert "NOTE a: accepted new event bytes" in out
    assert "Re-recorded 1 file" in out
    assert main(["validate", str(root), "--strict"]) == 0


def test_changed_value_requires_result_edit_first(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    (root / "e.tfevents").write_bytes(event(value=0.75))
    before = (root / "runevidence.json").read_bytes()

    assert main(["rehash", str(root), "a", "--refresh-locators"]) == 2
    out = capsys.readouterr().out
    assert "recorded value does not match" in out
    assert out.count("Nothing was written.") == 1
    assert (root / "runevidence.json").read_bytes() == before

    entry = root / "r.json"
    entry.write_text('{"id":"r","value":0.75}', encoding="utf-8")
    assert main(["result", "edit", str(root), "--file", str(entry)]) == 0
    capsys.readouterr()
    assert main(["rehash", str(root), "a", "--refresh-locators"]) == 0
    assert main(["validate", str(root), "--strict"]) == 0


def test_missing_locator_refuses_and_writes_nothing(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    (root / "e.tfevents").write_bytes(event(tag=b"other"))
    before = (root / "runevidence.json").read_bytes()
    assert main(["rehash", str(root), "a", "--refresh-locators"]) == 2
    assert "was not found" in capsys.readouterr().out
    assert (root / "runevidence.json").read_bytes() == before


def test_mixed_locator_styles_are_refused(tmp_path: Path, capsys):
    root = make_root(
        tmp_path,
        extra_results=[
            {
                "id": "j",
                "run_id": "run",
                "name": "json",
                "value": 1,
                "artifact_id": "a",
                "locator": "/loss",
            }
        ],
    )
    assert main(["rehash", str(root), "a", "--refresh-locators"]) == 2
    assert "mixes locator styles" in capsys.readouterr().out


def test_schema_0_6_is_refused(tmp_path: Path, capsys):
    root = make_root(tmp_path, schema="0.6")
    assert main(["rehash", str(root), "a", "--refresh-locators"]) == 2
    assert "not schema 0.5" in capsys.readouterr().out


def test_noop_and_size_only_output(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    assert main(["rehash", str(root), "a", "--refresh-locators"]) == 0
    assert "Verified 1 file; no changes." in capsys.readouterr().out

    document = json.loads((root / "runevidence.json").read_text())
    document["artifacts"][0]["size_bytes"] = 1
    (root / "runevidence.json").write_text(json.dumps(document), encoding="utf-8")
    assert main(["rehash", str(root), "a", "--refresh-locators"]) == 0
    out = capsys.readouterr().out
    assert "size 1 ->" in out
    assert "sha256 unchanged" in out
    assert "NOTE" not in out


def test_exact_id_selectors_and_flag_conflicts(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    for target in ("run:x", "path:x", "folder:x", "glob:*", "id:"):
        assert main(["rehash", str(root), target, "--refresh-locators"]) == 2
        assert "only exact artifact IDs" in capsys.readouterr().out
    for args in (
        ["--all"],
        ["--volatile"],
        ["--no-volatile"],
        ["--directory"],
    ):
        before = (root / "runevidence.json").read_bytes()
        assert main(["rehash", str(root), "a", "--refresh-locators", *args]) == 2
        assert (root / "runevidence.json").read_bytes() == before


def test_volatile_directory_and_null_digest_are_refused(tmp_path: Path, capsys):
    for artifact, reason in (
        ({"volatile": True}, "it is volatile"),
        ({"artifact_type": "directory", "digest_format": "runevidence-directory-v1"}, "directory"),
        ({"sha256": None}, "no recorded digest"),
    ):
        root = make_root(tmp_path / reason.replace(" ", "-"), artifact=artifact)
        assert main(["rehash", str(root), "a", "--refresh-locators"]) == 2
        assert reason in capsys.readouterr().out


def test_cap_is_checked_before_reads(tmp_path: Path, capsys, monkeypatch):
    results = [
        {
            "id": f"r{index}",
            "run_id": "run",
            "name": f"m{index}",
            "value": 0.5,
            "artifact_id": "a",
            "locator": {"kind": "tensorboard", "tag": "loss", "step": 1, "occurrence": 0},
        }
        for index in range(10_001)
    ]
    root = make_root(tmp_path, extra_results=results[1:])
    called = {"read": False}

    def fail_read(*args, **kwargs):
        called["read"] = True
        raise AssertionError("read must not run after cap refusal")

    monkeypatch.setattr(edit, "_read_snapshot_parts", fail_read)
    assert main(["rehash", str(root), "a", "--refresh-locators"]) == 2
    assert "tensorboard verification limit" in capsys.readouterr().out
    assert called["read"] is False


def test_refresh_uses_one_snapshot_and_no_sha256_file(tmp_path: Path, monkeypatch):
    root = make_root(tmp_path)
    count = {"reads": 0}
    real_read = edit._read_snapshot_parts

    def counted(*args, **kwargs):
        count["reads"] += 1
        return real_read(*args, **kwargs)

    monkeypatch.setattr(edit, "_read_snapshot_parts", counted)
    monkeypatch.setattr(edit, "sha256_file", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("sha256_file")))
    (root / "e.tfevents").write_bytes(event() + event(value=0.6, step=2))
    assert main(["rehash", str(root), "a", "--refresh-locators"]) == 0
    assert count["reads"] == 1


def test_digest_change_warns_about_dependent_claims(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    document = json.loads((root / "runevidence.json").read_text())
    document["evidence"] = [
        {"id": "c", "claim": "loss is stable", "supports": ["r"], "contradicts": []}
    ]
    (root / "runevidence.json").write_text(json.dumps(document), encoding="utf-8")
    (root / "e.tfevents").write_bytes(event() + event(value=0.6, step=2))
    assert main(["rehash", str(root), "a", "--refresh-locators"]) == 0
    assert "Re-read these claims" in capsys.readouterr().out


def test_dry_run_refreshes_nothing(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    before = (root / "runevidence.json").read_bytes()
    (root / "e.tfevents").write_bytes(event() + event(value=0.6, step=2))
    assert main(["rehash", str(root), "a", "--refresh-locators", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Would re-record 1 file" in out
    assert "Dry run: nothing was written." in out
    assert (root / "runevidence.json").read_bytes() == before


def test_unknown_id_and_default_refusal(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    assert main(["rehash", str(root), "missing", "--refresh-locators"]) == 2
    assert "no artifact has ID 'missing'" in capsys.readouterr().out
    assert main(["rehash", str(root), "a"]) == 2
    assert "LOCATOR_REFRESH_UNSUPPORTED" in capsys.readouterr().out


def test_missing_event_file_uses_the_state_message(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    (root / "e.tfevents").unlink()
    before = (root / "runevidence.json").read_bytes()
    assert main(["rehash", str(root), "a", "--refresh-locators"]) == 2
    assert "Cannot refresh event bytes for 'a': the event file is missing" in capsys.readouterr().out
    assert (root / "runevidence.json").read_bytes() == before


def test_late_second_artifact_failure_writes_nothing(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    document = json.loads((root / "runevidence.json").read_text())
    second = event(tag=b"other")
    (root / "e2.tfevents").write_bytes(second)
    document["artifacts"].append(
        {
            "id": "b",
            "path": "e2.tfevents",
            "sha256": hashlib.sha256(second).hexdigest(),
            "size_bytes": len(second),
        }
    )
    document["results"].append(
        {
            "id": "r2",
            "run_id": "run",
            "name": "other",
            "value": 0.5,
            "artifact_id": "b",
            "locator": {"kind": "tensorboard", "tag": "other", "step": 1, "occurrence": 0},
        }
    )
    (root / "runevidence.json").write_text(json.dumps(document), encoding="utf-8")
    (root / "e.tfevents").write_bytes(event() + event(value=0.6, step=2))
    (root / "e2.tfevents").write_bytes(event(tag=b"changed"))
    before = (root / "runevidence.json").read_bytes()
    assert main(["rehash", str(root), "a", "b", "--refresh-locators"]) == 2
    assert "was not found" in capsys.readouterr().out
    assert (root / "runevidence.json").read_bytes() == before


def test_non_numeric_recorded_value_is_refused(tmp_path: Path, capsys):
    root = make_root(tmp_path, value=True)
    assert main(["rehash", str(root), "a", "--refresh-locators"]) == 2
    assert "is not a TensorBoard scalar" in capsys.readouterr().out


def test_same_size_digest_change_updates_only_the_digest(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    (root / "e.tfevents").write_bytes(event(value=0.75))
    entry = root / "r.json"
    entry.write_text('{"id":"r","value":0.75}', encoding="utf-8")
    assert main(["result", "edit", str(root), "--file", str(entry)]) == 0
    capsys.readouterr()
    assert main(["rehash", str(root), "a", "--refresh-locators"]) == 0
    out = capsys.readouterr().out
    assert "sha256" in out
    assert "-> " in out
    assert "size " not in out
