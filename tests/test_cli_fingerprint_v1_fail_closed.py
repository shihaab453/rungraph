"""D11R2R1: fail closed on Pydantic values outside frozen recorded-manifest-v1.

Future computed fields and allowed extras otherwise silently preserve a digest
although their values are visible in model_dump(). This is an ABI guard, not a
change to existing schema, persisted JSON, or artifact verification.
"""
from __future__ import annotations

import builtins
import io
import os
from pathlib import Path
import socket
import urllib.request

from pydantic import BaseModel, ConfigDict, computed_field
import pytest

from runevidence.models import (
    Artifact, EvidenceReference, Manifest, Project, Relationship,
    RelationshipType, Result, Run,
)
from runevidence.run_fingerprint import FingerprintError, manifest_fingerprint
from test_cli_fingerprint_golden import GOLDEN_V1, rich_record


def record(kind, value):
    """Put one extended model in its proper position in a live manifest."""
    m = Manifest(project=Project(id="p", name="demo"), runs=[Run(id="r")])
    if kind == "manifest":
        return value
    if kind == "project":
        m.project = value
    elif kind == "run":
        m.runs = [value]
    elif kind == "artifact":
        m.artifacts = [value]
    elif kind == "result":
        m.results = [value]
    elif kind == "evidence":
        m.evidence = [value]
    elif kind == "relationship":
        m.relationships = [value]
    else:
        raise AssertionError(kind)
    return m


_CASES = [
    ("manifest", Manifest, dict(project=Project(id="p", name="demo"), runs=[Run(id="r")])),
    ("project", Project, dict(id="p", name="demo")),
    ("run", Run, dict(id="r")),
    ("artifact", Artifact, dict(id="a", path="f")),
    ("result", Result, dict(id="x", run_id="r", name="loss", value=1.0)),
    ("evidence", EvidenceReference, dict(id="c", claim="claim")),
    ("relationship", Relationship, dict(source="r", target="r", type=RelationshipType.derived_from)),
]


def fingerprint(m):
    return manifest_fingerprint(m, manifest_path="example.json")["fingerprint"]["digest"]


@pytest.mark.parametrize("kind,parent,fields", _CASES, ids=[x[0] for x in _CASES])
def test_nonempty_allowed_extra_is_refused_for_all_record_kinds(kind, parent, fields):
    class Expanded(parent):
        model_config = ConfigDict(extra="allow")

    initial = record(kind, parent(**fields))
    future = Expanded(**fields, additional_meaning={"changed": 1})
    assert future.__pydantic_extra__ == {"additional_meaning": {"changed": 1}}
    assert "additional_meaning" in future.model_dump()
    with pytest.raises(FingerprintError, match=f"extra {kind} fields.*version bump required"):
        fingerprint(record(kind, future))
    # This must not modify the existing v1 digest for the same known fields.
    assert len(fingerprint(initial)) == 64


@pytest.mark.parametrize("kind,parent,fields", _CASES, ids=[x[0] for x in _CASES])
def test_computed_field_is_refused_without_evaluating_it(kind, parent, fields):
    class Expanded(parent):
        @computed_field
        @property
        def additional_meaning(self) -> str:
            raise AssertionError("computed getter must not run during ABI check")

    extended = Expanded(**fields)
    assert "additional_meaning" in type(extended).model_computed_fields
    with pytest.raises(FingerprintError, match=f"computed {kind} fields.*version bump required"):
        fingerprint(record(kind, extended))


def test_extra_allow_with_no_populated_extra_preserves_v1_digest():
    class ExpandedRun(Run):
        model_config = ConfigDict(extra="allow")

    a = Manifest(project=Project(id="p", name="demo"), runs=[Run(id="r")])
    b = Manifest(project=Project(id="p", name="demo"), runs=[ExpandedRun(id="r")])
    assert b.runs[0].__pydantic_extra__ == {}
    assert fingerprint(a) == fingerprint(b)


def test_model_copy_unvalidated_undeclared_attribute_is_refused():
    original = Run(id="r")
    injected = original.model_copy(update={"outside_schema": "not recorded"})
    assert injected.__dict__["outside_schema"] == "not recorded"
    doc = Manifest(project=Project(id="p", name="demo"), runs=[injected])
    with pytest.raises(FingerprintError, match="undeclared run fields.*version bump required"):
        fingerprint(doc)


def test_programmatic_nested_pydantic_value_remains_explicitly_refused():
    class Nested(BaseModel):
        label: str = "value"

    m = Manifest(project=Project(id="p", name="demo", metadata={"inside": Nested()}))
    with pytest.raises(FingerprintError, match="unsupported recorded fingerprint v1 value type"):
        fingerprint(m)


def test_live_rich_golden_and_default_projection_unchanged():
    assert fingerprint(rich_record()) == GOLDEN_V1["rich_record"]
    assert fingerprint(Manifest(project=Project(id="p", name="demo"))) == GOLDEN_V1["project_only"]


def test_fingerprint_does_not_invoke_filesystem_or_network_apis(monkeypatch, tmp_path):
    """Unlike an empty-directory assertion, a spy refuses attempted I/O itself.

    Construct live Pydantic values first. The trap covers common read/existence
    and network APIs; it is not an OS sandbox or comprehensive security audit.
    """
    artifact = Artifact(id="a", path=str(tmp_path / "missing-artifact"),
                        artifact_type="directory", digest_format="runevidence-directory-v1")
    m = Manifest(
        project=Project(id="p", name="demo"), artifacts=[artifact],
        evidence=[EvidenceReference(id="c", claim="test", source="https://example.invalid/no-network",
                                    supports=["a", "nonexistent"])],
    )
    expected = fingerprint(m)
    attempts = []

    def deny(*args, **kwargs):
        attempts.append((args, kwargs))
        raise AssertionError("fingerprinting performed forbidden filesystem or network I/O")

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", deny)
        patch.setattr(io, "open", deny)
        patch.setattr(os, "open", deny)
        patch.setattr(Path, "open", deny)
        patch.setattr(Path, "read_bytes", deny)
        patch.setattr(Path, "read_text", deny)
        patch.setattr(Path, "stat", deny)
        patch.setattr(Path, "exists", deny)
        patch.setattr(urllib.request, "urlopen", deny)
        patch.setattr(socket.socket, "connect", deny)
        got = fingerprint(m)
    assert not attempts
    assert got == expected
    assert not (tmp_path / "missing-artifact").exists()
