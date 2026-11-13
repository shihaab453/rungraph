"""D11R2: fingerprint v1 ABI survives future additive schema defaults.

All new-model scenarios use in-memory synthetic subclasses only. The production
schema, CLI and P7 persistence remain untouched; original D11R1 goldens stay fixed.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import random

from pydantic import create_model
import pytest

from runevidence.models import Artifact, EvidenceReference, Manifest, Project, Relationship, RelationshipType, Result, Run
from runevidence.run_fingerprint import FingerprintError, manifest_fingerprint
from runevidence.run_impact_diff import compare_claim_impact


def document(*, artifacts=(), runs=(), results=(), evidence=(), relationships=()):
    return Manifest(project=Project(id="p", name="base"), artifacts=list(artifacts),
                    runs=list(runs), results=list(results), evidence=list(evidence),
                    relationships=list(relationships))


def digest(m):
    return manifest_fingerprint(m, manifest_path="manifest.json")["fingerprint"]["digest"]


def test_original_rich_v1_golden_unchanged_under_frozen_projector():
    from test_cli_fingerprint_golden import rich_record, GOLDEN_V1
    assert digest(rich_record()) == GOLDEN_V1["rich_record"]


def test_additional_artifact_default_preserves_old_v1_digest():
    Extended = create_model("ExtendedArtifact", __base__=Artifact, future_field=(int, 0))
    older = Artifact(id="a", path="file.txt")
    future = Extended(id="a", path="file.txt")
    before = document(artifacts=[older])
    after = document(artifacts=[future])
    assert digest(before) == digest(after)
    # These direct API models mimic a future validated schema, no manual dump.
    assert "future_field" in future.model_dump()


def test_explicit_new_default_is_also_a_noop():
    Extended = create_model("ExtendedArtifactExplicit", __base__=Artifact, future_field=(int, 0))
    old = document(artifacts=[Artifact(id="a", path="f")])
    new = document(artifacts=[Extended(id="a", path="f", future_field=0)])
    assert digest(old) == digest(new)


def test_nondefault_future_artifact_field_must_fail_closed():
    Extended = create_model("ExtendedArtifactNondefault", __base__=Artifact, future_field=(int, 0))
    doc = document(artifacts=[Extended(id="a", path="f", future_field=3)])
    with pytest.raises(FingerprintError, match="non-default new artifact field 'future_field'.*version bump"):
        digest(doc)


def test_new_required_artifact_field_requires_new_format_even_with_default_value_supplied():
    Extended = create_model("ExtendedRequiredArtifact", __base__=Artifact, future_field=(int, ...))
    doc = document(artifacts=[Extended(id="a", path="f", future_field=0)])
    with pytest.raises(FingerprintError, match="new required artifact field 'future_field'.*version bump"):
        digest(doc)


def test_future_optional_default_project_run_result_evidence_stable():
    kinds = [(Project, "project", dict(id="p", name="base")),
             (Run, "run", dict(id="r")),
             (Result, "result", dict(id="x", run_id="r", name="score", value=1)),
             (EvidenceReference, "evidence", dict(id="c", claim="ok"))]
    for klass, kind, kwargs in kinds:
        Extended = create_model("Future" + klass.__name__, __base__=klass, novel_field=(str, ""))
        base = klass(**kwargs)
        extra = Extended(**kwargs)
        if kind == "project":
            first = document()
            second = document()
            second.project = extra
        elif kind == "run":
            first = document(runs=[base])
            second = document(runs=[extra])
        elif kind == "result":
            first = document(runs=[Run(id="r")], results=[base])
            second = document(runs=[Run(id="r")], results=[extra])
        else:
            first = document(evidence=[base])
            second = document(evidence=[extra])
        assert digest(first) == digest(second), kind
        nondefault = Extended(**kwargs, novel_field="nondefault")
        if kind == "project":
            second.project = nondefault
        elif kind == "run":
            second.runs = [nondefault]
        elif kind == "result":
            second.results = [nondefault]
        else:
            second.evidence = [nondefault]
        with pytest.raises(FingerprintError, match="version bump required"):
            digest(second)


def test_new_manifest_root_default_ignored_but_nondefault_rejected():
    Extended = create_model("FutureManifest", __base__=Manifest, new_root=(str, ""))
    a = document(runs=[Run(id="r")])
    b = Extended.model_validate(a.model_dump())
    assert digest(a) == digest(b)
    b.new_root = "semantically meaningful"
    with pytest.raises(FingerprintError, match="new manifest field 'new_root'.*version bump"):
        digest(b)


def test_future_relationship_default_ignored_nondefault_rejected():
    Extended = create_model("FutureRelationship", __base__=Relationship, new_note=(str, ""))
    r = Relationship(source="r", target="r", type=RelationshipType.derived_from)
    a = document(runs=[Run(id="r")], relationships=[r])
    b = document(runs=a.runs, relationships=[Extended(source="r", target="r", type=RelationshipType.derived_from)])
    assert digest(a) == digest(b)
    b.relationships[0].new_note = "a new meaning"
    with pytest.raises(FingerprintError, match="new relationship field 'new_note'.*version bump"):
        digest(b)


def test_future_schema_version_never_silently_uses_v1():
    m = document()
    m = m.model_copy(update={"schema_version": "0.2"})
    with pytest.raises(FingerprintError, match="schema 0.1"):
        digest(m)


def test_unknown_future_relationship_type_rejected():
    class NewType:
        value = "new-edge"
    r = Relationship(source="r", target="r", type=RelationshipType.derived_from)
    r = r.model_copy(update={"type": NewType()})
    with pytest.raises(FingerprintError, match="does not know relationship type.*version bump"):
        digest(document(runs=[Run(id="r")], relationships=[r]))


def test_d5_helpers_can_change_without_changing_d11_abi(monkeypatch):
    import runevidence.run_impact_diff as d5
    before = document(runs=[Run(id="r", started_at=datetime(2026, 11, 4, tzinfo=timezone.utc))])
    expected = digest(before)
    def refuse(*args, **kwargs):
        raise RuntimeError("D5 private helper deliberately changed")
    for name in ("_frozen", "_semantic_object", "_relationship_keys", "_relationship_sort_key"):
        monkeypatch.setattr(d5, name, refuse)
    assert digest(before) == expected


def test_valid_old_schema_numeric_types_still_distinct():
    values = [True, 1, 1.0, 0.0, -0.0, float("nan"), float("inf"), float("-inf"), None]
    actual = [digest(document(runs=[Run(id="r")], results=[Result(id="x", run_id="r", name="x", value=x)]))
              for x in values]
    assert len(set(actual)) == len(values)


def test_d5_current_unchanged_and_modified_semantics_remain_equivalent():
    rng = random.Random(11122026)
    for _ in range(120):
        n = rng.randrange(1, 5)
        runs = [Run(id=f"r{i}", params={"seed": i, "ready": bool(i & 1)}) for i in range(n)]
        edges = [Relationship(source=f"r{rng.randrange(n)}", target=f"r{rng.randrange(n)}",
                              type=RelationshipType.derived_from, note=rng.choice((None, "", "x")))
                 for _ in range(rng.randrange(1, 8))]
        a = document(runs=runs, relationships=edges)
        b = document(runs=list(reversed([r.model_copy(deep=True) for r in runs])), relationships=edges[::-1] + edges[:1])
        d5 = compare_claim_impact(a, b, before_path="a", after_path="b")
        assert d5["changes"] == {"objects": [], "relationships": []}
        assert digest(a) == digest(b)
        if rng.random() < .5:
            b.runs[0].params["seed"] = 500
            assert digest(a) != digest(b)
            assert compare_claim_impact(a, b, before_path="a", after_path="b")["summary"]["object_changes"] == 1


def test_notes_nullable_and_duplicate_elision_stable():
    base = document(runs=[Run(id="a"), Run(id="b")])
    r1 = Relationship(source="a", target="b", type=RelationshipType.derived_from, note=None)
    r2 = Relationship(source="a", target="b", type=RelationshipType.derived_from, note="")
    x = document(runs=base.runs, relationships=[r1, r1, r2]); y = document(runs=base.runs, relationships=[r2, r1]); z = document(runs=base.runs, relationships=[r2])
    assert digest(x) == digest(y) != digest(z)


def test_no_artifact_io_for_directory_or_unresolved_references(tmp_path, monkeypatch):
    a = document(artifacts=[Artifact(id="a", path=str(tmp_path / "not_present"), artifact_type="directory",
                                     digest_format="runevidence-directory-v1")],
                 evidence=[EvidenceReference(id="c", claim="unverified", supports=["ghost"])])
    d = digest(a)
    assert len(d) == 64
    assert list(tmp_path.iterdir()) == []


def test_version_field_is_not_silently_rewritten_on_good_manifest():
    x = manifest_fingerprint(document(), manifest_path="x.json")
    assert x["fingerprint"]["semantic_format"] == "recorded-manifest-v1"
    assert x["fingerprint"]["algorithm"] == "sha256"
    json.dumps(x, allow_nan=False)
