"""CLI read paths over a schema 0.3 locator manifest.

A 0.3 JSONL object locator is verified through the protected snapshot. The
CLI must stay total: fixed findings and refusals, never a Pydantic repr,
traceback or a partial JSON document.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from runevidence import cli
from runevidence.manifest import _issue_location, dump_manifest
from runevidence.models import Manifest

OBJECT_LOCATOR = {"kind": "jsonl", "line": 1, "pointer": "/val"}
REFRESH_TEXT = (
    "ERROR (LOCATOR_REFRESH_UNSUPPORTED): rehash cannot refresh a manifest that "
    "contains an object locator. Nothing was written.\n"
)


def _doc(*, locator=OBJECT_LOCATOR, schema_version: str = "0.3", result_id="res", artifact_path="metrics.jsonl"):
    data = b'{"step": 1, "val": 0.9}\n'
    return data, {
        "schema_version": schema_version,
        "project": {"id": "proj", "name": "Project"},
        "runs": [{"id": "run", "name": "Run"}],
        "artifacts": [
            {
                "id": "metrics",
                "path": artifact_path,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
            }
        ],
        "results": [
            {
                "id": result_id,
                "run_id": "run",
                "name": "accuracy",
                "value": 0.9,
                "artifact_id": "metrics",
                "locator": locator,
            }
        ],
        "evidence": [{"id": "claim", "claim": "recorded", "supports": [result_id]}],
        "relationships": [],
    }


def _write(root: Path, *, locator=OBJECT_LOCATOR, result_id="res", artifact_path="metrics.jsonl") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    data, document = _doc(locator=locator, result_id=result_id, artifact_path=artifact_path)
    (root / artifact_path).write_bytes(data)
    path = root / "runevidence.json"
    path.write_text(dump_manifest(Manifest.model_validate(document)), encoding="utf-8")
    return path


def _run(capsys, argv):
    code = cli.main(argv)
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out + captured.err
    return code, captured.out, captured.err


def _one_json(text: str) -> dict:
    decoder = json.JSONDecoder()
    document, end = decoder.raw_decode(text)
    assert text[end:].strip() == ""
    return document


def test_validate_and_inspect_verify_object_locator(tmp_path, capsys):
    root = tmp_path / "exp"
    _write(root)

    for command in ("validate", "inspect"):
        code, out, err = _run(capsys, [command, str(root), "--json"])
        assert (code, err) == (0, "")
        document = _one_json(out)
        assert document["findings"] == []


def test_validate_mismatch_is_fixed_and_value_free(tmp_path, capsys):
    root = tmp_path / "exp"
    _write(root, locator={"kind": "jsonl", "line": 1, "pointer": "/missing"})
    code, out, err = _run(capsys, ["validate", str(root), "--json"])
    assert (code, err) == (1, "")
    finding = _one_json(out)["findings"][0]
    assert (finding["code"], finding["severity"], finding["subject"]) == (
        "locator-mismatch", "error", "res"
    )
    assert "0.9" not in finding["message"]


def test_validate_batch_reports_error_and_continues(tmp_path, capsys):
    bad = tmp_path / "bad"
    _write(bad, locator={"kind": "jsonl", "line": 1, "pointer": "/missing"})
    good = tmp_path / "good"
    good.mkdir()
    (good / "runevidence.json").write_text(
        json.dumps(
            {
                "schema_version": "0.2",
                "project": {"id": "good", "name": "Good"},
            }
        ),
        encoding="utf-8",
    )

    code, out, err = _run(
        capsys, ["validate-batch", str(bad), str(good), "--json"]
    )

    assert (code, err) == (1, "")
    document = _one_json(out)
    assert [row["summary"]["exit_code"] for row in document["results"]] == [1, 0]
    assert document["results"][0]["summary"]["by_code"] == {
        "locator-mismatch": 1,
        "claim-at-risk": 1,
    }
    assert document["aggregate"]["exit_code"] == 1


@pytest.mark.parametrize("artifact_path", ["metrics.json", "metrics.jsonl"])
@pytest.mark.parametrize("extra", [[], ["--dry-run"]])
def test_rehash_refuses_any_object_locator_before_writing(
    tmp_path, capsys, extra, artifact_path
):
    root = tmp_path / "exp"
    path = _write(root, artifact_path=artifact_path)
    before = path.read_bytes()
    names_before = sorted(p.name for p in root.iterdir())

    code, out, err = _run(capsys, ["rehash", str(root), "--all", *extra])

    assert (code, err) == (2, "")
    assert out == REFRESH_TEXT
    assert path.read_bytes() == before
    assert sorted(p.name for p in root.iterdir()) == names_before


def test_rehash_refuses_a_string_only_artifact_when_an_object_locator_elsewhere(
    tmp_path, capsys
):
    """``edit.plan_rehash`` refuses the whole manifest, not this artifact.

    ``a.json`` is targeted only by a string locator, so the artifact-scoped
    hint used to promise that rehash could record its digest. A second
    artifact with an object locator makes both the targeted and ``--all``
    rehash refuse, so the hint must say "in the manifest".
    """
    root = tmp_path / "exp"
    root.mkdir()
    (root / "a.json").write_bytes(b'{"val": 0.9}\n')
    (root / "b.jsonl").write_bytes(b'{"val": 0.9}\n')
    document = {
        "schema_version": "0.3",
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "run", "name": "Run"}],
        "artifacts": [
            {"id": "a", "path": "a.json", "size_bytes": len(b'{"val": 0.9}\n')},
            {
                "id": "b",
                "path": "b.jsonl",
                "sha256": hashlib.sha256(b'{"val": 0.9}\n').hexdigest(),
                "size_bytes": len(b'{"val": 0.9}\n'),
            },
        ],
        "results": [
            {
                "id": "ra", "run_id": "run", "name": "val", "value": 0.9,
                "artifact_id": "a", "locator": "/val",
            },
            {
                "id": "rb", "run_id": "run", "name": "val", "value": 0.9,
                "artifact_id": "b",
                "locator": {"kind": "jsonl", "line": 1, "pointer": "/val"},
            },
        ],
        "evidence": [],
    }
    (root / "runevidence.json").write_text(
        dump_manifest(Manifest.model_validate(document)), encoding="utf-8"
    )

    code, out, err = _run(capsys, ["validate", str(root), "--json"])
    # locator-no-digest is an error, so the digestless artifact exits 1.
    assert (code, err) == (1, "")
    finding = next(
        row for row in _one_json(out)["findings"]
        if row["code"] == "locator-no-digest"
    )
    assert "any object locator exists in the manifest" in finding["hint"]
    assert "only string locators target this artifact" not in finding["hint"]

    before = (root / "runevidence.json").read_bytes()
    for argv in (["rehash", str(root), "--all"], ["rehash", str(root), "a"]):
        code, out, err = _run(capsys, [*argv, "--dry-run"])
        assert (code, err) == (2, ""), argv
        assert out == REFRESH_TEXT, argv
        assert (root / "runevidence.json").read_bytes() == before


def test_run_claim_lineage_serialize_object_locator_json(tmp_path, capsys):
    root = tmp_path / "exp"
    _write(root)

    for command, identifier in (("run", "run"), ("claim", "claim"), ("lineage", "claim")):
        code, out, err = _run(
            capsys, [command, str(root), "--id", identifier, "--json"]
        )
        assert (code, err) == (0, "")
        document = _one_json(out)
        locators = []

        def walk(node):
            if isinstance(node, dict):
                if "locator" in node:
                    locators.append(node["locator"])
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(document)
        assert OBJECT_LOCATOR in locators
        assert "JsonlLocator(" not in out


def test_claim_and_lineage_human_output_renders_object_locator_safely(tmp_path, capsys):
    root = tmp_path / "exp"
    _write(root)
    for command in ("claim", "lineage"):
        code, out, err = _run(capsys, [command, str(root), "--id", "claim"])
        assert (code, err) == (0, "")
        assert "JsonlLocator(" not in out
        assert "Traceback" not in out


def test_all_26_routes_stay_total_on_object_locator(tmp_path, capsys):
    root = tmp_path / "exp"
    _write(root, locator={"kind": "jsonl", "line": 1, "pointer": "/missing"})
    p = str(root)
    argv_by_route = {
        "validate": ["validate", p, "--json"],
        "validate-batch": ["validate-batch", p, "--json"],
        "inspect": ["inspect", p, "--json"],
        "compare": ["compare", p, "--base", "run", "--target", "run", "--json"],
        "comparability": [
            "comparability", p, p, "--baseline-result", "res",
            "--target-result", "res", "--json",
        ],
        "leaderboard": ["leaderboard", p, "--metric", "accuracy", "--direction", "max", "--json"],
        "matrix": ["matrix", p, "--json"],
        "run": ["run", p, "--id", "run", "--json"],
        "claim": ["claim", p, "--id", "claim", "--json"],
        "lineage": ["lineage", p, "--id", "claim", "--json"],
        "impact": ["impact", p, "--id", "res", "--json"],
        "coverage": ["coverage", p, "--json"],
        "coverage-gate": ["coverage-gate", p, "--json"],
        "fingerprint": ["fingerprint", p, "--json"],
        "fingerprint-compare": ["fingerprint-compare", p, p, "--json"],
        "coverage-diff": ["coverage-diff", p, p, "--json"],
        "review-diff": ["review-diff", p, p, "--json"],
        "impact-diff": ["impact-diff", p, p, "--json"],
        "review-gate": ["review-gate", p, p, "--json"],
        "init": ["init", p],
        "migrate": ["migrate", p, "--to", "0.3", "--dry-run", "--json"],
        "conditions": ["conditions", p, "--run", "run", "--clear", "environment", "--dry-run"],
        "rehash": ["rehash", p, "--all", "--dry-run"],
        "add": ["add", p, "missing.txt", "--dry-run"],
        "schema": ["schema"],
        "adapters": ["adapters"],
    }
    assert len(argv_by_route) == 26

    expected = {"validate": 1, "inspect": 1, "rehash": 2}
    for route, argv in argv_by_route.items():
        code, out, err = _run(capsys, argv)
        assert code in (0, 1, 2), route
        if route in expected:
            assert code == expected[route], route
        if route == "comparability":
            assert _one_json(out)["error"]["code"] == "UNSUPPORTED_VERSION"
        if route == "fingerprint":
            assert code == 2
        if route == "conditions":
            assert "UNSUPPORTED_VERSION" in out
        if route == "rehash":
            assert out == REFRESH_TEXT
        if route in ("run", "claim", "lineage"):
            assert _one_json(out)


def test_scalar_locator_diagnostic_keeps_the_result_location():
    _, document = _doc(locator=5)
    with pytest.raises(ValidationError) as caught:
        Manifest.model_validate(document)
    (error,) = caught.value.errors()
    assert error["loc"] == ("results", 0, "locator")
    assert _issue_location(error["loc"]) == "/results/0/locator"
