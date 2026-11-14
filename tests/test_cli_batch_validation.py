"""validate-batch --json: one standalone counts-only summary per input, plus an aggregate."""
import json
import shutil
from collections import Counter
from pathlib import Path

import pytest

import runevidence.cli as cli
from runevidence.cli import main
from runevidence.findings import Code, Severity

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "generic-experiment"
TOP_KEYS = ["batch_version", "results", "aggregate"]
RESULT_KEYS = ["manifest", "summary"]
SUMMARY_KEYS = ["summary_version", "total_findings", "by_severity", "by_code", "exit_code"]
AGGREGATE_KEYS = ["total_manifests", "total_findings", "by_severity", "by_code", "exit_code"]
MARKER = "private_marker_rgind11"


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def batch(capsys, *inputs, options=()):
    code, out, err = run(capsys, "validate-batch", "--json", *options, *inputs)
    assert not err
    return code, out, json.loads(out)


def standalone(capsys, path, *options):
    code, out, err = run(capsys, "validate", path, "--json", "--summary-only", *options)
    assert not err
    return code, out


def snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# Input builders. Each returns the input exactly as it is passed on the command line.

def clean(tmp_path: Path, name: str = "clean") -> str:
    dest = tmp_path / name
    shutil.copytree(EXAMPLE, dest)
    return str(dest)


def _edit(experiment: Path, change) -> None:
    manifest = experiment / "runevidence.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    change(data)
    manifest.write_text(json.dumps(data), encoding="utf-8")


def _drop_digest(data: dict) -> None:
    data["artifacts"][0].pop("sha256", None)
    data["artifacts"][0].pop("size", None)


def warning(tmp_path: Path, name: str = "warning") -> str:
    """Exactly one no-digest warning: exit 0, or 1 with --strict."""
    path = clean(tmp_path, name)
    _edit(Path(path), _drop_digest)
    return path


def broken(tmp_path: Path, name: str = "broken") -> str:
    """Missing file, changed file and missing digest: errors and a warning."""
    path = Path(clean(tmp_path, name))
    _edit(path, _drop_digest)
    data = json.loads((path / "runevidence.json").read_text(encoding="utf-8"))
    (path / data["artifacts"][1]["path"]).unlink()
    changed = path / data["artifacts"][2]["path"]
    changed.write_bytes(changed.read_bytes() + b"x")
    return str(path)


def missing(tmp_path: Path, name: str = f"{MARKER}_missing.json") -> str:
    return str(tmp_path / name)


def malformed(tmp_path: Path, name: str = f"{MARKER}_malformed.json", content: bytes = b"{not json") -> str:
    path = tmp_path / name
    path.write_bytes(content)
    return str(path)


def schema_invalid(tmp_path: Path, name: str = f"{MARKER}_schema.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps({
        "project": {"id": MARKER, "name": "Project", MARKER: MARKER},
        "results": [{"id": MARKER, "run_id": MARKER, "name": "result", "value": [MARKER]}],
    }), encoding="utf-8")
    return str(path)


def assert_batch_matches_standalone(capsys, inputs, options=()):
    """Every result equals the actual standalone summary for its input, in
    order, and the aggregate is derived independently from those summaries."""
    code, out, data = batch(capsys, *inputs, options=options)
    assert list(data) == TOP_KEYS
    assert data["batch_version"] == 1
    assert len(data["results"]) == len(inputs)
    redacted = "--redact-manifest-path" in options
    singles = [standalone(capsys, path, *(o for o in options if o == "--strict")) for path in inputs]
    for path, result, (single_code, single_out) in zip(inputs, data["results"], singles):
        assert list(result) == RESULT_KEYS
        assert result["manifest"] == ("<redacted>" if redacted else path)
        assert list(result["summary"]) == SUMMARY_KEYS
        # Byte-for-byte the summary standalone validate prints for this input.
        assert json.dumps(result["summary"], indent=2) + "\n" == single_out
        assert result["summary"]["exit_code"] == single_code

    summaries = [json.loads(out_) for _, out_ in singles]
    aggregate = data["aggregate"]
    assert list(aggregate) == AGGREGATE_KEYS
    assert aggregate["total_manifests"] == len(inputs)
    assert aggregate["total_findings"] == sum(s["total_findings"] for s in summaries)
    severities, codes = Counter(), Counter()
    for s in summaries:
        severities.update(s["by_severity"])
        codes.update(s["by_code"])
    assert aggregate["by_severity"] == {k: severities[k] for k in sorted(severities)}
    assert aggregate["by_code"] == {k: codes[k] for k in sorted(codes)}
    for key, allowed in (("by_severity", {s.value for s in Severity}), ("by_code", {c.value for c in Code})):
        assert list(aggregate[key]) == sorted(aggregate[key])
        assert set(aggregate[key]) <= allowed
        assert all(type(v) is int and v > 0 for v in aggregate[key].values())
        assert sum(aggregate[key].values()) == aggregate["total_findings"]
    expected_exit = max(single_code for single_code, _ in singles)
    assert code == aggregate["exit_code"] == expected_exit
    return code, out, data


def test_single_clean_input(tmp_path, capsys):
    path = clean(tmp_path)
    code, _, data = assert_batch_matches_standalone(capsys, [path])
    assert code == 0
    assert data == {
        "batch_version": 1,
        "results": [{"manifest": path, "summary": {
            "summary_version": 1, "total_findings": 0, "by_severity": {}, "by_code": {}, "exit_code": 0}}],
        "aggregate": {"total_manifests": 1, "total_findings": 0, "by_severity": {}, "by_code": {}, "exit_code": 0},
    }


@pytest.mark.parametrize("kind", ["clean", "warning", "broken", "missing", "malformed", "schema"])
def test_each_input_kind_matches_standalone(tmp_path, capsys, kind):
    builders = {"clean": clean, "warning": warning, "broken": broken,
                "missing": missing, "malformed": malformed, "schema": schema_invalid}
    expected = {"clean": 0, "warning": 0, "broken": 1, "missing": 2, "malformed": 2, "schema": 2}
    code, _, _ = assert_batch_matches_standalone(capsys, [builders[kind](tmp_path)])
    assert code == expected[kind]


@pytest.mark.parametrize("content", [b"{not json", b"\xff\xfe\x00bad", b'{"a": 1, "a": 2}', b"[NaN]"])
def test_unreadable_variants(tmp_path, capsys, content):
    path = malformed(tmp_path, content=content)
    code, _, data = assert_batch_matches_standalone(capsys, [path, clean(tmp_path)])
    assert code == 2
    assert data["results"][0]["summary"]["by_code"] == {"manifest-unreadable": 1}
    assert data["results"][1]["summary"]["exit_code"] == 0


def test_order_preserved(tmp_path, capsys):
    inputs = [broken(tmp_path), clean(tmp_path), missing(tmp_path), warning(tmp_path)]
    for ordering in (inputs, list(reversed(inputs))):
        _, _, data = assert_batch_matches_standalone(capsys, ordering)
        assert [r["manifest"] for r in data["results"]] == ordering


def test_duplicates_each_get_a_result(tmp_path, capsys, monkeypatch):
    good, bad = broken(tmp_path), missing(tmp_path)
    inputs = [good, bad, good, good, bad]
    code, _, data = assert_batch_matches_standalone(capsys, inputs)
    assert code == 2
    assert [r["manifest"] for r in data["results"]] == inputs
    assert data["results"][0] == data["results"][2] == data["results"][3]
    assert data["results"][1] == data["results"][4]
    one = data["results"][0]["summary"]["total_findings"]
    assert data["aggregate"]["total_manifests"] == 5
    assert data["aggregate"]["total_findings"] == 3 * one + 2


@pytest.mark.parametrize("middle", [missing, malformed, schema_invalid])
def test_failed_middle_continues(tmp_path, capsys, middle):
    inputs = [clean(tmp_path, "first"), middle(tmp_path), warning(tmp_path, "last")]
    code, _, data = assert_batch_matches_standalone(capsys, inputs)
    assert code == 2
    assert [r["summary"]["exit_code"] for r in data["results"]] == [0, 2, 0]
    assert data["results"][2]["summary"]["by_code"] == {"no-digest": 1}


def test_aggregate_math(tmp_path, capsys):
    inputs = [broken(tmp_path), warning(tmp_path), missing(tmp_path), schema_invalid(tmp_path), clean(tmp_path)]
    code, _, data = assert_batch_matches_standalone(capsys, inputs)
    assert code == 2
    schema_count = data["results"][3]["summary"]["total_findings"]
    assert schema_count >= 2
    aggregate = data["aggregate"]
    assert aggregate["total_manifests"] == 5
    assert aggregate["total_findings"] == 5 + 1 + 1 + schema_count + 0
    assert aggregate["by_severity"] == {"error": 4 + 1 + schema_count, "warning": 2}
    assert aggregate["by_code"] == {"claim-at-risk": 2, "hash-mismatch": 1, "manifest-not-found": 1,
                                    "missing-file": 1, "no-digest": 2, "schema-invalid": schema_count}
    assert list(aggregate["by_code"]) == sorted(aggregate["by_code"])


def test_zero_findings_aggregate_maps_empty(tmp_path, capsys):
    code, out, data = assert_batch_matches_standalone(capsys, [clean(tmp_path, "a"), clean(tmp_path, "b")])
    assert code == 0
    assert data["aggregate"]["by_severity"] == {} and data["aggregate"]["by_code"] == {}
    for severity in ("error", "info", "warning"):
        assert f'"{severity}"' not in out


@pytest.mark.parametrize("kinds, expected", [
    (("clean", "clean"), 0),
    (("clean", "warning"), 0),
    (("warning", "broken", "clean"), 1),
    (("broken", "missing"), 2),
    (("missing", "broken", "clean"), 2),
    (("clean", "malformed"), 2),
])
def test_exit_is_max_individual(tmp_path, capsys, kinds, expected):
    builders = {"clean": clean, "warning": warning, "broken": broken, "missing": missing, "malformed": malformed}
    inputs = [builders[k](tmp_path, f"{k}_{i}") for i, k in enumerate(kinds)]
    code, _, _ = assert_batch_matches_standalone(capsys, inputs)
    assert code == expected


def test_strict_applies_to_every_input(tmp_path, capsys):
    inputs = [warning(tmp_path, "w1"), clean(tmp_path), warning(tmp_path, "w2")]
    code, _, data = assert_batch_matches_standalone(capsys, inputs)
    assert code == 0
    assert [r["summary"]["exit_code"] for r in data["results"]] == [0, 0, 0]
    code, _, data = assert_batch_matches_standalone(capsys, inputs, options=("--strict",))
    assert code == 1
    assert [r["summary"]["exit_code"] for r in data["results"]] == [1, 0, 1]
    # Strict never lowers a load failure, and errors stay 1.
    code, _, data = assert_batch_matches_standalone(
        capsys, [broken(tmp_path), missing(tmp_path)], options=("--strict",))
    assert [r["summary"]["exit_code"] for r in data["results"]] == [1, 2]
    assert code == 2


def test_redaction(tmp_path, capsys):
    root = tmp_path / MARKER
    root.mkdir()
    inputs = [broken(root), missing(root), malformed(root), schema_invalid(root), clean(root)]
    code, out, data = batch(capsys, *inputs)
    assert all(MARKER in r["manifest"] for r in data["results"])
    r_code, r_out, r_data = assert_batch_matches_standalone(capsys, inputs, options=("--redact-manifest-path",))
    assert r_code == code
    assert [r["manifest"] for r in r_data["results"]] == ["<redacted>"] * len(inputs)
    assert MARKER not in r_out
    assert str(tmp_path) not in r_out and str(tmp_path).replace("\\", "\\\\") not in r_out
    # Redaction touches only the echoed paths.
    for result in data["results"]:
        result["manifest"] = "<redacted>"
    assert r_data == data
    # Combined with --strict, also order-independent of the flags.
    s1 = batch(capsys, *inputs, options=("--strict", "--redact-manifest-path"))
    s2 = batch(capsys, *inputs, options=("--redact-manifest-path", "--strict"))
    assert s1[1] == s2[1] and s1[0] == s2[0]


def test_no_finding_details(tmp_path, capsys):
    root = tmp_path / MARKER
    root.mkdir()
    _, out, data = batch(capsys, broken(root), schema_invalid(root), missing(root),
                         options=("--redact-manifest-path",))
    assert MARKER not in out
    for word in ("findings", "message", "path", "subject", "hint", "issue_location", "counts", "ok"):
        assert f'"{word}"' not in out


def test_deterministic(tmp_path, capsys):
    inputs = [broken(tmp_path), missing(tmp_path), schema_invalid(tmp_path), warning(tmp_path)]
    first = batch(capsys, *inputs)
    second = batch(capsys, *inputs)
    assert first[0] == second[0]
    assert first[1] == second[1]


def test_input_bytes_unchanged(tmp_path, capsys):
    inputs = [broken(tmp_path), warning(tmp_path), clean(tmp_path), malformed(tmp_path), schema_invalid(tmp_path),
              missing(tmp_path)]
    before = snapshot(tmp_path)
    batch(capsys, *inputs)
    batch(capsys, *inputs, options=("--strict", "--redact-manifest-path"))
    assert snapshot(tmp_path) == before
    assert not Path(inputs[-1]).exists()


def test_one_load_and_check_per_input(tmp_path, capsys, monkeypatch):
    """Sequential reuse of the ordinary validate pipeline: one load per input
    (duplicates and failures included) and one default check per loaded input."""
    events = []
    real_load, real_checks = cli.load_manifest, cli.run_checks

    def load(target, *a, **k):
        events.append(("load", str(target)))
        return real_load(target, *a, **k)

    def checks(loaded, *a, **k):
        assert not a and k == {}  # validate's default: no untracked-file check
        events.append(("checks", str(loaded.path.parent)))
        return real_checks(loaded, *a, **k)

    monkeypatch.setattr(cli, "load_manifest", load)
    monkeypatch.setattr(cli, "run_checks", checks)
    good, bad, other = clean(tmp_path), missing(tmp_path), broken(tmp_path)
    batch(capsys, good, bad, other, good, malformed(tmp_path))
    paths = [Path(e[1]) for e in events]
    assert [e[0] for e in events] == ["load", "checks", "load", "load", "checks", "load", "checks", "load"]
    assert [paths[i] for i in (0, 1, 2, 3, 4, 5, 6)] == [Path(good), Path(good).resolve(), Path(bad), Path(other),
                                                         Path(other).resolve(), Path(good), Path(good).resolve()]
    assert Counter(e[0] for e in events) == {"load": 5, "checks": 3}


@pytest.mark.parametrize("argv, message", [
    (["validate-batch", "{p}"], "validate-batch requires --json"),
    (["validate-batch", "{p}", "--strict"], "validate-batch requires --json"),
    (["validate-batch", "{p}", "--redact-manifest-path"], "--redact-manifest-path requires --json"),
    (["validate-batch", "--json"], "MANIFEST"),
    (["validate-batch"], "MANIFEST"),
    (["validate-batch", "{p}", "--json", "--summary-only"], "--summary-only"),
    (["validate-batch", "{p}", "--json", "--include-untracked"], "--include-untracked"),
    (["validate-batch", "{p}", "--json", "--expect", "0" * 64], "--expect"),
    (["validate-batch", "{p}", "--json", "--explain"], "--explain"),
])
def test_unsupported_or_incomplete_arguments(tmp_path, capsys, monkeypatch, argv, message):
    path = clean(tmp_path)
    before = snapshot(tmp_path)

    def refuse(*a, **k):
        raise AssertionError("no manifest may be loaded for rejected arguments")

    monkeypatch.setattr(cli, "load_manifest", refuse)
    code, out, err = run(capsys, *[a.format(p=path) for a in argv])
    assert code == 2
    assert not out
    assert message in err
    assert snapshot(tmp_path) == before


def test_standalone_validate_unchanged(tmp_path, capsys):
    """Adding the batch route leaves the existing validate outputs as they were."""
    path = broken(tmp_path)
    loaded = cli.load_manifest(Path(path))
    report = cli.run_checks(loaded)
    code, out, err = run(capsys, "validate", path, "--json")
    assert (code, out, err) == (1, cli.to_json(report) + "\n", "")
    code, out, err = run(capsys, "validate", path, "--json", "--summary-only")
    assert (code, out, err) == (1, cli._validation_summary_json(report, 1) + "\n", "")
    assert list(json.loads(out)) == SUMMARY_KEYS
