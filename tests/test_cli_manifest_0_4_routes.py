"""One schema 0.4 load smoke per manifest-reading CLI route (slice C phase 4b).

Each manifest-reading route must accept a valid 0.4 manifest through the
dispatcher instead of failing the load (exit 2). ``fingerprint-compare`` is the
documented exception: fingerprint v1 still refuses every schema other than 0.1
through ``manifest_fingerprint``'s guard, so an otherwise valid 0.4 manifest is
a refusal, not a crash. The 26-route parser surface is unchanged.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from runevidence.cli import main
from runevidence.manifest import dump_manifest
from runevidence.models import Manifest04

CSV_DATA = b"metric,val_rmse\nx,0.5\n"
HEADER = "val_rmse"


def document() -> dict:
    digest = hashlib.sha256(CSV_DATA).hexdigest()
    locator = {"kind": "csv-cell", "row": 1, "column": 2, "header": HEADER}
    return {
        "schema_version": "0.4",
        "project": {"id": "proj", "name": "Proj"},
        "runs": [{"id": "run-a", "name": "A"}, {"id": "run-b", "name": "B"}],
        "artifacts": [
            {
                "id": "art",
                "path": "metrics.csv",
                "sha256": digest,
                "size_bytes": len(CSV_DATA),
            }
        ],
        "results": [
            {
                "id": "ra",
                "run_id": "run-a",
                "name": "score",
                "value": 0.5,
                "artifact_id": "art",
                "locator": locator,
            },
            {
                "id": "rb",
                "run_id": "run-b",
                "name": "score",
                "value": 0.5,
                "artifact_id": "art",
                "locator": dict(locator),
            },
        ],
        "evidence": [
            {"id": "claim", "claim": "claim text", "supports": ["ra"]}
        ],
    }


def experiment(tmp_path, name: str = "exp") -> str:
    folder = tmp_path / name
    folder.mkdir()
    (folder / "metrics.csv").write_bytes(CSV_DATA)
    (folder / "runevidence.json").write_text(
        dump_manifest(Manifest04.model_validate(document())),
        encoding="utf-8",
        newline="\n",
    )
    return str(folder)


def call(argv, capsys) -> tuple[int, str]:
    code = main(argv)
    captured = capsys.readouterr()
    assert captured.err == ""
    return code, captured.out


def test_minimal_04_manifest_is_clean(tmp_path, capsys):
    folder = experiment(tmp_path)
    code, output = call(["validate", folder, "--json"], capsys)
    assert code == 0
    payload = json.loads(output)
    assert payload["ok"] is True
    assert payload["findings"] == []


@pytest.mark.parametrize(
    "route,extra",
    [
        ("validate", []),
        ("inspect", []),
        ("compare", ["--base", "run-a", "--target", "run-b"]),
        ("leaderboard", ["--metric", "score", "--direction", "max"]),
        ("matrix", []),
        ("impact", ["--id", "art"]),
        ("coverage", []),
        ("coverage-gate", []),
        ("coverage-diff", None),
        ("review-diff", None),
        ("impact-diff", None),
        ("review-gate", None),
    ],
)
def test_manifest_reading_routes_load_04(tmp_path, capsys, route, extra):
    folder = experiment(tmp_path, "before")
    if extra is None:
        argv = [route, folder, experiment(tmp_path, "after"), "--json"]
    else:
        argv = [route, folder, *extra, "--json"]
    code, output = call(argv, capsys)
    assert code == 0
    # A load failure would be exit 2 and this fixed JSON error shape.
    payload = json.loads(output)
    assert payload.get("ok") is not False
    assert "error" not in payload


def test_fingerprint_compare_refuses_04_through_the_01_guard(tmp_path, capsys):
    before = experiment(tmp_path, "before")
    after = experiment(tmp_path, "after")
    code, output = call(
        ["fingerprint-compare", before, after, "--json"], capsys
    )
    assert code == 2
    payload = json.loads(output)
    assert payload["ok"] is False
    assert payload["exit_code"] == 2
    assert (
        payload["error"]
        == "fingerprint v1 only accepts manifest schema 0.1; "
        "explicit version decision required"
    )
