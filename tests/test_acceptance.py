"""Owner acceptance checks for the weekend MVP."""

import json
import re
import shutil
from pathlib import Path

import pydantic
import pytest

from runevidence.checks import run_checks
from runevidence.cli import main
from runevidence.manifest import load_manifest
from runevidence.models import json_schema

from conftest import EXAMPLE, REPO


def codes(path: Path) -> list[str]:
    return [f.code.value for f in run_checks(load_manifest(path)).findings]


def test_committed_example_is_clean():
    report = run_checks(load_manifest(EXAMPLE), include_untracked=True)
    assert report.findings == []
    assert report.exit_code() == 0


def test_missing_file_and_hash_change_are_both_reported(experiment, capsys):
    (experiment / "data" / "test.csv").unlink()
    metrics = experiment / "runs" / "tuned" / "metrics.json"
    metrics.write_bytes(metrics.read_bytes().replace(b'"accuracy": 1.0', b'"accuracy": 0.75'))

    exit_code = main(["validate", str(experiment)])
    out = capsys.readouterr().out

    assert exit_code == 1
    assert "missing-file [test-data]" in out
    assert "'data/test.csv' does not exist" in out
    assert "hash-mismatch [tuned-metrics]" in out
    assert "changed since it was recorded" in out
    # Root causes are listed before the claims they put at risk.
    assert out.index("missing-file") < out.index("claim-at-risk")
    assert out.index("hash-mismatch") < out.index("claim-at-risk")


def test_relocated_folder_still_validates(experiment, tmp_path):
    moved = tmp_path / "somewhere" / "else"
    moved.parent.mkdir()
    shutil.move(str(experiment), str(moved))
    assert codes(moved) == []


def test_absolute_path_broken_by_relocation_gets_a_hint(experiment):
    manifest_path = experiment / "runevidence.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    old_root = "C:/old/location/experiment" if Path("C:/").exists() else "/old/location/experiment"
    data["artifacts"][0]["path"] = f"{old_root}/data/train.csv"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")

    findings = run_checks(load_manifest(experiment)).findings
    by_code = {f.code.value: f for f in findings}
    assert "absolute-path" in by_code
    assert "missing-file" in by_code
    assert "data/train.csv" in by_code["missing-file"].hint


REGENERATE = "regenerate with: runevidence schema -o schema/runevidence.schema.json"
# The committed schema is generated with Pydantic 2.11 or newer.
EXACT_SCHEMA_PYDANTIC = (2, 11)


def _semantic(node):
    """Drop spellings that do not change which manifests a schema accepts.

    Pydantic 2.11+ writes ``"additionalProperties": true`` on free-form
    dict fields (the JSON Schema default anyway) and ``"type": "string"``
    beside a string ``const``. Older supported 2.x versions omit both, so
    only these two spellings are ignored; any other difference still fails."""
    if isinstance(node, list):
        return [_semantic(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {k: _semantic(v) for k, v in node.items()}
    if out.get("additionalProperties") is True:
        del out["additionalProperties"]
    if "const" in out and isinstance(out["const"], str) and out.get("type") == "string":
        del out["type"]
    return out


def test_committed_schema_matches_models():
    # Holds on every supported Pydantic version (pydantic>=2.6,<3).
    committed = json.loads((REPO / "schema" / "runevidence.schema.json").read_text(encoding="utf-8"))
    assert _semantic(committed) == _semantic(json_schema()), REGENERATE


@pytest.mark.skipif(
    tuple(int(p) for p in pydantic.VERSION.split(".")[:2]) < EXACT_SCHEMA_PYDANTIC,
    reason="the committed schema is spelled as Pydantic 2.11+ writes it",
)
def test_committed_schema_is_exactly_what_current_pydantic_writes():
    committed = json.loads((REPO / "schema" / "runevidence.schema.json").read_text(encoding="utf-8"))
    assert committed == json_schema(), REGENERATE


def test_semantic_comparison_still_catches_real_schema_changes():
    schema = json_schema()
    changed = json.loads(json.dumps(schema))
    changed["$defs"]["Artifact"]["required"] = ["id"]
    assert _semantic(changed) != _semantic(schema)
    loosened = json.loads(json.dumps(schema))
    loosened["$defs"]["Artifact"]["additionalProperties"] = True
    assert _semantic(loosened) != _semantic(schema)


FORBIDDEN = re.compile(r"\b(celeste|steam|rooms?|ppo|pilots?)\b", re.IGNORECASE)


def test_core_has_no_domain_specific_vocabulary():
    offenders = []
    for path in sorted((REPO / "src").rglob("*")):
        if path.is_file() and path.suffix in {".py", ".json", ".md", ".txt", ".toml"}:
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if FORBIDDEN.search(line):
                    offenders.append(f"{path.relative_to(REPO)}:{n}: {line.strip()}")
    for path in [REPO / "schema" / "runevidence.schema.json", *sorted((REPO / "examples").rglob("*"))]:
        if path.is_file() and FORBIDDEN.search(path.read_text(encoding="utf-8", errors="replace")):
            offenders.append(str(path.relative_to(REPO)))
    assert offenders == []
