"""D2 read-only evidence-claim drilldown via real manifest models and CLI dispatch."""
import json
from pathlib import Path

import pytest

from runevidence.cli import main
from runevidence.manifest import dump_manifest
from runevidence.models import (
    Artifact, EvidenceReference, Manifest, Project, Relationship,
    RelationshipType as T, Result, Run,
)
from runevidence.run_claim import ClaimDetailsError, claim_details, format_claim_details


def make_manifest(*, runs=(), artifacts=(), results=(), claims=(), relationships=()):
    return Manifest(project=Project(id="project", name="Demo"), runs=list(runs),
                    artifacts=list(artifacts), results=list(results),
                    evidence=list(claims), relationships=list(relationships))


def claim(doc, identifier="c"):
    return claim_details(doc, identifier, manifest_path="synthetic/runevidence.json")


def fixture_dir(tmp_path, doc):
    root = tmp_path / "experiment"
    root.mkdir()
    file = root / "runevidence.json"
    file.write_text(dump_manifest(doc), encoding="utf-8", newline="\n")
    return root, file


def test_inline_targets_are_typed_and_keep_recorded_context():
    doc = make_manifest(
        runs=[Run(id="run", name="Experiment")],
        artifacts=[Artifact(id="file", path="weights.bin", run_id="run", kind="checkpoint")],
        results=[Result(id="score", run_id="run", name="accuracy", value=0.9, artifact_id="file")],
        claims=[EvidenceReference(id="c", claim="Strong accuracy", source="README.md#results",
                                  supports=["score", "run", "file"])],
    )
    data = claim(doc)
    assert [row["id"] for row in data["supports"]] == ["file", "run", "score"]
    assert [row["kind"] for row in data["supports"]] == ["artifact", "run", "result"]
    assert data["supports"][0]["details"]["path"] == "weights.bin"
    assert data["supports"][1]["details"]["name"] == "Experiment"
    assert data["supports"][2]["details"]["artifact_id"] == "file"
    assert data["claim"]["source"] == "README.md#results"
    assert all(row["via"] == ["inline"] for row in data["supports"])
    assert data["summary"]["supports"] == 3


def test_relationship_only_links_work_and_other_claim_edges_are_ignored():
    doc = make_manifest(
        runs=[Run(id="run"), Run(id="other")],
        claims=[EvidenceReference(id="c", claim="Claim"), EvidenceReference(id="other_claim", claim="Unrelated")],
        relationships=[
            Relationship(source="c", target="run", type=T.supports, note="an observed run"),
            Relationship(source="c", target="other", type=T.contradicts),
            Relationship(source="other_claim", target="run", type=T.contradicts),
            Relationship(source="run", target="c", type=T.derived_from),
        ],
    )
    data = claim(doc)
    assert [r["id"] for r in data["supports"]] == ["run"]
    assert data["supports"][0]["relationship_notes"] == ["an observed run"]
    assert [r["id"] for r in data["contradicts"]] == ["other"]
    assert data["summary"]["unresolved_links"] == 0


def test_duplicates_via_inline_and_typed_edges_deduplicate_per_stance():
    doc = make_manifest(
        runs=[Run(id="run")],
        claims=[EvidenceReference(id="c", claim="Claim", supports=["run", "run"])],
        relationships=[Relationship(source="c", target="run", type=T.supports, note="why"),
                       Relationship(source="c", target="run", type=T.supports, note="why"),
                       Relationship(source="c", target="run", type=T.supports, note="another")],
    )
    data = claim(doc)
    assert len(data["supports"]) == 1
    assert data["supports"][0]["via"] == ["inline", "relationship"]
    assert data["supports"][0]["relationship_notes"] == ["another", "why"]


def test_unknown_targets_and_unsupported_kinds_are_visible_not_fabricated():
    doc = make_manifest(
        runs=[Run(id="run")],
        claims=[EvidenceReference(id="c", claim="Claim", supports=["missing", "project", "other"]),
                EvidenceReference(id="other", claim="Other")],
    )
    data = claim(doc)
    rows = {row["id"]: row for row in data["supports"]}
    assert rows["missing"] == {"id": "missing", "kind": None, "status": "unresolved", "via": ["inline"]}
    assert rows["project"]["status"] == "unsupported_target_kind"
    assert rows["other"]["status"] == "unsupported_target_kind"
    assert "details" not in rows["other"]
    assert data["summary"]["unresolved_links"] == 1
    assert data["summary"]["unsupported_kind_links"] == 2


def test_same_target_can_be_both_supporting_and_contradicting():
    doc = make_manifest(runs=[Run(id="run")],
                        claims=[EvidenceReference(id="c", claim="Both", supports=["run"], contradicts=["run"])])
    data = claim(doc)
    assert data["dual_stance_target_ids"] == ["run"]
    assert data["summary"]["dual_stance_targets"] == 1
    assert data["supports"][0]["id"] == data["contradicts"][0]["id"] == "run"
    assert "BOTH stances" in format_claim_details(data)


def test_duplicate_global_ids_refused_even_when_claim_id_unique():
    doc = make_manifest(runs=[Run(id="collision")], artifacts=[Artifact(id="collision", path="a.txt")],
                        claims=[EvidenceReference(id="c", claim="Claim", supports=["collision"])])
    with pytest.raises(ClaimDetailsError, match="duplicate object ID"):
        claim(doc)


def test_unknown_or_wrong_kind_claim_refused():
    doc = make_manifest(runs=[Run(id="run")], claims=[EvidenceReference(id="c", claim="Claim")])
    for identifier in ("unknown", "run"):
        with pytest.raises(ClaimDetailsError, match="not found"):
            claim(doc, identifier)


def test_empty_links_have_clear_summary_and_no_false_provenance():
    doc = make_manifest(claims=[EvidenceReference(id="c", claim="No links", source=None)])
    data = claim(doc)
    assert data["supports"] == data["contradicts"] == data["dual_stance_target_ids"] == []
    assert data["summary"] == {"supports": 0, "contradicts": 0, "unresolved_links": 0,
                               "unsupported_kind_links": 0, "dual_stance_targets": 0}
    assert "not validated" in data["note"]


def test_nonfinite_result_value_is_flagged_instead_of_invalid_json():
    doc = make_manifest(
        results=[Result(id="inf", run_id="run", name="loss", value=float("inf")),
                 Result(id="null", run_id="run", name="unset", value=None)],
        runs=[Run(id="run")],
        claims=[EvidenceReference(id="c", claim="Claim", supports=["inf", "null"])],
    )
    data = claim(doc)
    result_by_id = {x["id"]: x for x in data["supports"]}
    assert result_by_id["inf"]["details"]["value"] is None
    assert result_by_id["null"]["details"]["value"] is None
    assert data["nonfinite_recorded_values"] == [
        {"path": "/supports/0/details/value", "kind": "Infinity"}
    ]
    assert "Nonfinite recorded values (1):" in format_claim_details(data)
    json.dumps(data, allow_nan=False)


def test_untrusted_strings_are_escaped_in_human_output():
    doc = make_manifest(
        runs=[Run(id="run", name="Name\nERROR forged")],
        claims=[EvidenceReference(id="c", claim="Claim\nFAIL forged", source="source\rline", supports=["run"])],
        relationships=[Relationship(source="c", target="run", type=T.supports, note="note\nWRONG fake")],
    )
    text = format_claim_details(claim(doc))
    assert "Claim\\nFAIL forged" in text
    assert "Name\\nERROR forged" in text
    assert "note\\nWRONG fake" in text
    assert "\nERROR forged" not in text and "\nFAIL forged" not in text and "\nWRONG fake" not in text
    assert "source\\rline" in text


def test_text_is_bounded_per_stance_json_is_complete():
    ids = [f"run{i:02d}" for i in range(25)]
    doc = make_manifest(runs=[Run(id=identifier) for identifier in ids],
                        claims=[EvidenceReference(id="c", claim="Claim", supports=ids, contradicts=ids)])
    data = claim(doc)
    assert len(data["supports"]) == len(data["contradicts"]) == 25
    assert len(data["dual_stance_target_ids"]) == 25
    text = format_claim_details(data)
    assert "and 5 more targets (use --json)" in text
    assert "and 5 more (use --json)" in text
    assert '"run24":' not in text


def test_real_cli_json_and_text_preserve_synthetic_manifest_bytes(tmp_path, capsys):
    doc = make_manifest(
        runs=[Run(id="run")], artifacts=[Artifact(id="ghost", path="not-present.bin", sha256="a" * 64)],
        claims=[EvidenceReference(id="c", claim="Claim", supports=["ghost"], contradicts=["missing"])],
    )
    root, manifest_path = fixture_dir(tmp_path, doc)
    original = manifest_path.read_bytes()
    code = main(["claim", str(root), "--id", "c", "--json"])
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert code == 0 and captured.err == ""
    assert data["supports"][0]["details"]["path"] == "not-present.bin"
    assert data["contradicts"][0]["status"] == "unresolved"
    assert manifest_path.read_bytes() == original and not (root / "not-present.bin").exists()
    code = main(["claim", str(root), "--id", "c"])
    output = capsys.readouterr().out
    assert code == 0 and "Supports (1 recorded targets)" in output
    assert "not validated" in output or "validate" in output
    assert sorted(path.name for path in root.iterdir()) == ["runevidence.json"]


def test_cli_error_is_exit_two_and_valid_json(tmp_path, capsys):
    root, _ = fixture_dir(tmp_path, make_manifest(claims=[EvidenceReference(id="c", claim="Claim")]))
    code = main(["claim", str(root), "--id", "other", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert code == data["exit_code"] == 2
    assert data["ok"] is False and "not found" in data["error"]
    code = main(["claim", str(tmp_path / "not-here"), "--id", "c", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert code == data["exit_code"] == 2


def test_existing_run_matrix_compare_paths_still_work(tmp_path, capsys):
    doc = make_manifest(runs=[Run(id="a"), Run(id="b")],
                        results=[Result(id="ra", run_id="a", name="accuracy", value=.9)],
                        claims=[EvidenceReference(id="c", claim="Claim", supports=["ra"])])
    root, _ = fixture_dir(tmp_path, doc)
    for args in (["run", str(root), "--id", "a", "--json"],
                 ["matrix", str(root), "--json"],
                 ["compare", str(root), "--base", "a", "--target", "b", "--json"]):
        assert main(args) == 0
        assert isinstance(json.loads(capsys.readouterr().out), dict)


def test_very_long_text_is_bounded_and_json_remains_complete():
    noisy = "line\nESC\x1b[31m" + ("Z" * 30000)
    ids = [f"r{i:02d}" for i in range(25)]
    doc = make_manifest(
        runs=[Run(id=identifier, name=noisy) for identifier in ids],
        claims=[EvidenceReference(id="c", claim=noisy, source=noisy,
                                  supports=ids, contradicts=ids)],
        relationships=[Relationship(source="c", target=identifier, type=T.supports,
                                    note=noisy) for identifier in ids],
    )
    data = claim(doc)
    output = format_claim_details(data)
    assert len(output) <= 16384
    assert "text output truncated" in output
    assert output.endswith("Recorded assertions only. Run 'runevidence validate' separately before trusting evidence.")
    assert "\x1b" not in output and "\\nESC" in output
    assert data["claim"]["text"] == noisy
    assert data["supports"][0]["relationship_notes"] == [noisy]
    assert len(data["supports"]) == len(data["contradicts"]) == 25
    assert json.loads(json.dumps(data, ensure_ascii=True))["claim"]["text"] == noisy


def test_one_oversized_claim_remains_readable_and_retains_other_link_info():
    long_text = "starts\r\n" + ("X" * 60000)
    doc = make_manifest(runs=[Run(id="run", name="short")],
                        claims=[EvidenceReference(id="c", claim=long_text,
                                                  source=long_text, supports=["run"])])
    data = claim(doc)
    text = format_claim_details(data)
    assert "chars omitted; use --json" in text
    assert "Supports (1 recorded targets):" in text
    assert "run" in text and "short" in text
    assert "\r" not in text and "\x1b" not in text
    assert data["claim"]["text"] == long_text
    assert len(text) <= 16384
