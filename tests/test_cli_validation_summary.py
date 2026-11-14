"""validate --json --summary-only: counts only, same exit codes as ordinary JSON."""
import json
from collections import Counter
from pathlib import Path

import pytest

import runevidence.cli as cli
from runevidence.cli import main
from runevidence.findings import Code, Severity

KEYS = ["summary_version", "total_findings", "by_severity", "by_code", "exit_code"]
MARKER = "private_marker_rgind10"


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def summary(capsys, path, *options):
    code, out, err = run(capsys, "validate", str(path), "--json", "--summary-only", *options)
    assert not err
    return code, out, json.loads(out)


def ordinary(capsys, path, *options):
    code, out, err = run(capsys, "validate", str(path), "--json", *options)
    assert not err
    return code, json.loads(out)


def assert_matches_ordinary(capsys, path, *options):
    """The summary must agree exactly with the ordinary JSON report."""
    plain_code, plain = ordinary(capsys, path, *options)
    code, out, data = summary(capsys, path, *options)
    assert list(data) == KEYS
    assert data["summary_version"] == 1
    assert code == plain_code == plain["exit_code"] == data["exit_code"]
    assert data["total_findings"] == len(plain["findings"])
    # Derived independently from the ordinary findings list, not from "counts".
    severities = Counter(f["severity"] for f in plain["findings"])
    assert data["by_severity"] == {s: severities[s] for s in sorted(severities)}
    # Cross-check against the ordinary "counts" block: positive entries only.
    assert data["by_severity"] == {s: n for s, n in sorted(plain["counts"].items()) if n > 0}
    assert list(data["by_severity"]) == sorted(data["by_severity"])
    assert set(data["by_severity"]) <= {s.value for s in Severity}
    assert data["by_code"] == dict(Counter(f["code"] for f in plain["findings"]))
    assert list(data["by_code"]) == sorted(data["by_code"])
    assert set(data["by_code"]) <= {c.value for c in Code}
    assert all(type(v) is int and v > 0 for v in data["by_severity"].values())
    assert all(type(v) is int and v > 0 for v in data["by_code"].values())
    assert sum(data["by_severity"].values()) == data["total_findings"]
    assert sum(data["by_code"].values()) == data["total_findings"]
    return code, out, data


def snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def break_experiment(experiment: Path) -> None:
    """One missing file, one changed file, one artifact without a digest."""
    manifest = experiment / "runevidence.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["artifacts"][0].pop("sha256", None)
    data["artifacts"][0].pop("size", None)
    manifest.write_text(json.dumps(data), encoding="utf-8")
    (experiment / data["artifacts"][1]["path"]).unlink()
    changed = experiment / data["artifacts"][2]["path"]
    changed.write_bytes(changed.read_bytes() + b"x")


def warning_only(experiment: Path) -> None:
    manifest = experiment / "runevidence.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["artifacts"][0].pop("sha256", None)
    data["artifacts"][0].pop("size", None)
    manifest.write_text(json.dumps(data), encoding="utf-8")


def error_only(experiment: Path) -> None:
    """One missing file: errors only, no warning or info."""
    data = json.loads((experiment / "runevidence.json").read_text(encoding="utf-8"))
    (experiment / data["artifacts"][1]["path"]).unlink()


def add_untracked(experiment: Path, count: int = 1) -> None:
    for i in range(count):
        (experiment / f"untracked_{i}.txt").write_bytes(b"extra")


@pytest.fixture
def untracked_checks(monkeypatch):
    """Isolated transformation: validate never reports untracked files, so the
    only info-producing check is switched on for both the ordinary and the
    summary run. Both outputs still come from the same check pipeline."""
    real = cli.run_checks

    def checks(loaded, *a, **k):
        k["include_untracked"] = True
        return real(loaded, *a, **k)

    monkeypatch.setattr(cli, "run_checks", checks)


def test_success(experiment, capsys):
    code, _, data = assert_matches_ordinary(capsys, experiment)
    assert code == 0
    assert data == {
        "summary_version": 1,
        "total_findings": 0,
        "by_severity": {},
        "by_code": {},
        "exit_code": 0,
    }


@pytest.mark.parametrize("options", [(), ("--strict",), ("--redact-manifest-path",)])
def test_zero_findings_empty_maps(experiment, capsys, options):
    code, out, data = assert_matches_ordinary(capsys, experiment, *options)
    assert code == 0
    assert data["by_severity"] == {} and data["by_code"] == {}
    assert '"by_severity": {}' in out
    for severity in ("error", "info", "warning"):
        assert f'"{severity}"' not in out


def test_error_only(experiment, capsys):
    error_only(experiment)
    code, _, data = assert_matches_ordinary(capsys, experiment)
    assert code == 1
    assert list(data["by_severity"]) == ["error"]
    assert data["by_severity"]["error"] == data["total_findings"] >= 1
    assert "missing-file" in data["by_code"]


def test_warning_only(experiment, capsys):
    warning_only(experiment)
    code, _, data = assert_matches_ordinary(capsys, experiment)
    assert code == 0
    assert data["by_severity"] == {"warning": 1}
    assert data["by_code"] == {"no-digest": 1}


def test_info_only(experiment, capsys, untracked_checks):
    add_untracked(experiment, 2)
    for options in ((), ("--strict",)):
        code, _, data = assert_matches_ordinary(capsys, experiment, *options)
        assert code == 0  # info never fails, even with --strict
        assert data["by_severity"] == {"info": 2}
        assert data["by_code"] == {"untracked-file": 2}


def test_mixed_all_severities_sorted(experiment, capsys, untracked_checks):
    break_experiment(experiment)
    add_untracked(experiment)
    code, out, data = assert_matches_ordinary(capsys, experiment)
    assert code == 1
    assert data["total_findings"] == 6
    assert data["by_severity"] == {"error": 4, "info": 1, "warning": 1}
    assert list(data["by_severity"]) == ["error", "info", "warning"]
    assert out.index('"error"') < out.index('"info"') < out.index('"warning"')


def test_warning_and_info_without_errors(experiment, capsys, untracked_checks):
    warning_only(experiment)
    add_untracked(experiment)
    code, _, data = assert_matches_ordinary(capsys, experiment)
    assert code == 0
    assert data["by_severity"] == {"info": 1, "warning": 1}
    code, _, data = assert_matches_ordinary(capsys, experiment, "--strict")
    assert code == data["exit_code"] == 1


def test_invalid_evidence_multiple_counts(experiment, capsys):
    break_experiment(experiment)
    code, _, data = assert_matches_ordinary(capsys, experiment)
    assert code == 1
    assert data["total_findings"] == 5
    assert data["by_severity"] == {"error": 4, "warning": 1}
    assert data["by_code"] == {"claim-at-risk": 2, "hash-mismatch": 1, "missing-file": 1, "no-digest": 1}


@pytest.mark.parametrize("strict, expected", [(False, 0), (True, 1)])
def test_strict_exit_matches_existing_logic(experiment, capsys, strict, expected):
    warning_only(experiment)
    options = ("--strict",) if strict else ()
    code, _, data = assert_matches_ordinary(capsys, experiment, *options)
    assert code == data["exit_code"] == expected
    assert data["by_severity"]["warning"] == 1


def test_strict_with_errors_still_one(experiment, capsys):
    break_experiment(experiment)
    code, _, _ = assert_matches_ordinary(capsys, experiment, "--strict")
    assert code == 1


def test_missing_manifest(tmp_path, capsys):
    code, _, data = assert_matches_ordinary(capsys, tmp_path / f"{MARKER}.json")
    assert code == 2
    assert data["by_code"] == {"manifest-not-found": 1}
    assert data["by_severity"] == {"error": 1}


@pytest.mark.parametrize("content", [b"{not json", b"\xff\xfe\x00bad", b'{"a": 1, "a": 2}', b"[NaN]"])
def test_malformed_manifest(tmp_path, capsys, content):
    manifest = tmp_path / f"{MARKER}.json"
    manifest.write_bytes(content)
    code, _, data = assert_matches_ordinary(capsys, manifest)
    assert code == 2
    assert data["by_code"] == {"manifest-unreadable": 1}
    assert data["by_severity"] == {"error": 1}


def test_schema_invalid_counts_every_load_error(tmp_path, capsys):
    manifest = tmp_path / f"{MARKER}.json"
    manifest.write_text(json.dumps({
        "project": {"id": MARKER, "name": "Project", MARKER: MARKER},
        "results": [{"id": MARKER, "run_id": MARKER, "name": "result", "value": [MARKER]}],
    }), encoding="utf-8")
    code, out, data = assert_matches_ordinary(capsys, manifest, "--strict")
    assert code == 2
    assert set(data["by_code"]) == {"schema-invalid"}
    assert data["total_findings"] == data["by_code"]["schema-invalid"] >= 2
    assert data["by_severity"] == {"error": data["total_findings"]}
    assert MARKER not in out


def test_deterministic(experiment, capsys):
    break_experiment(experiment)
    first = summary(capsys, experiment)
    second = summary(capsys, experiment)
    assert first[1] == second[1]
    assert first[0] == second[0]


def test_default_output_unchanged(experiment, capsys):
    break_experiment(experiment)
    loaded = cli.load_manifest(experiment)
    expected = cli.to_json(cli.run_checks(loaded))
    code, out, err = run(capsys, "validate", str(experiment), "--json")
    assert code == 1 and not err
    assert out == expected + "\n"
    code, text, err = run(capsys, "validate", str(experiment))
    assert code == 1 and not err
    assert text.startswith("Checking ") and "summary_version" not in text


def test_requires_json(experiment, capsys):
    for extra in ((), ("--strict",), ("--redact-manifest-path",)):
        code, out, err = run(capsys, "validate", str(experiment), "--summary-only", *extra)
        assert code == 2
        assert not out
        assert "requires --json" in err


@pytest.mark.parametrize("argv", [
    ["inspect", "{p}", "--json", "--summary-only"],
    ["fingerprint", "{p}", "--json", "--summary-only"],
    ["coverage", "{p}", "--json", "--summary-only"],
    ["compare", "{p}", "--base", "a", "--target", "b", "--json", "--summary-only"],
    ["fingerprint-compare", "{p}", "{p}", "--json", "--summary-only"],
    ["rehash", "{p}", "--all", "--dry-run", "--summary-only"],
])
def test_unrelated_commands_reject(experiment, capsys, argv):
    before = snapshot(experiment)
    code, out, err = run(capsys, *[a.format(p=experiment) for a in argv])
    assert code == 2
    assert not out
    assert "--summary-only" in err
    assert snapshot(experiment) == before


@pytest.mark.parametrize("scenario", ["success", "broken", "error", "warning", "missing", "schema"])
def test_redaction_combination_identical(experiment, tmp_path, capsys, scenario):
    path = experiment
    if scenario == "broken":
        break_experiment(experiment)
    elif scenario == "error":
        error_only(experiment)
    elif scenario == "warning":
        warning_only(experiment)
    elif scenario == "missing":
        path = tmp_path / f"{MARKER}.json"
    elif scenario == "schema":
        path = tmp_path / f"{MARKER}.json"
        path.write_text(json.dumps({"project": {MARKER: 1}}), encoding="utf-8")
    plain = summary(capsys, path)
    redacted = summary(capsys, path, "--redact-manifest-path")
    assert redacted[0] == plain[0]
    assert redacted[1] == plain[1]


def test_input_bytes_unchanged(experiment, capsys):
    break_experiment(experiment)
    before = snapshot(experiment)
    summary(capsys, experiment)
    summary(capsys, experiment, "--strict", "--redact-manifest-path")
    assert snapshot(experiment) == before


def test_input_bytes_unchanged_zero_findings(experiment, capsys):
    before = snapshot(experiment)
    summary(capsys, experiment)
    summary(capsys, experiment, "--strict")
    assert snapshot(experiment) == before


def test_no_sensitive_fields(tmp_path, capsys):
    import shutil
    example = Path(__file__).resolve().parents[1] / "examples" / "generic-experiment"
    experiment = tmp_path / MARKER
    shutil.copytree(example, experiment)
    manifest = experiment / "runevidence.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["project"]["id"] = MARKER
    data["artifacts"][1]["path"] = f"{MARKER}/missing.bin"
    manifest.write_text(json.dumps(data).replace('"test-data"', f'"{MARKER}-artifact"'), encoding="utf-8")
    plain_code, plain = ordinary(capsys, experiment)
    assert MARKER in json.dumps(plain)
    code, out, data = assert_matches_ordinary(capsys, experiment)
    assert code == plain_code == 1
    assert MARKER not in out
    assert str(tmp_path) not in out and str(tmp_path).replace("\\", "\\\\") not in out
    for word in ("manifest", "findings", "message", "path", "subject", "hint", "issue_location"):
        assert f'"{word}"' not in out


def test_loads_and_checks_once(experiment, capsys, monkeypatch):
    """The summary reuses the ordinary pipeline: one load and one check run."""
    calls = Counter()
    real_load, real_checks = cli.load_manifest, cli.run_checks

    def load(*a, **k):
        calls["load"] += 1
        return real_load(*a, **k)

    def checks(*a, **k):
        calls["checks"] += 1
        assert k.get("include_untracked", False) is False
        return real_checks(*a, **k)

    monkeypatch.setattr(cli, "load_manifest", load)
    monkeypatch.setattr(cli, "run_checks", checks)
    summary(capsys, experiment)
    assert calls == {"load": 1, "checks": 1}
