"""One artifact-level check path: locator parity property test.

For every fixture, adding a locator must not remove artifact-level findings,
breakage or claim risk. Only the two documented mappings are allowed:

* a targeted artifact's ``no-digest`` warning is replaced by the result-level
  ``locator-no-digest``;
* a legacy read error may surface as ``locator-unreadable`` /
  ``artifact-changed`` on the targeted path instead of ``missing-file``.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from runevidence.checks import run_checks
from runevidence.manifest import dump_manifest, load_manifest
from runevidence.models import Manifest

DATA = b'{"val": 0.9}\n'
ARTIFACT = "a"
RESULT = "res"
CLAIM = "claim"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _target(root: Path, outside: Path, kind: str, path_form: str) -> tuple[str, Path]:
    """Return (recorded path spelling, real filesystem location)."""
    if kind == "file":
        name = "metrics.jsonl"
    else:
        name = "data"
    if path_form == "relative":
        return name, root / name
    if path_form == "absolute-in-root":
        return str(root / name), root / name
    if path_form == "absolute-outside":
        return str(outside / name), outside / name
    if path_form == "backslash":
        recorded = name.replace("/", "\\")
        return f"sub\\{recorded}", root / "sub" / name
    if path_form == "case":
        return name.upper(), root / name
    raise AssertionError(path_form)


def _build(
    root: Path,
    outside: Path,
    *,
    kind: str,
    digest_present: bool,
    size_mode: str,
    volatile: bool,
    file_state: str,
    path_form: str,
    with_locator: bool,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    outside.mkdir(parents=True, exist_ok=True)
    recorded_path, actual = _target(root, outside, kind, path_form)
    actual.parent.mkdir(parents=True, exist_ok=True)
    if file_state == "present" or file_state == "unreadable":
        if kind == "file":
            actual.write_bytes(DATA)
        else:
            actual.mkdir(exist_ok=True)
    if file_state == "unreadable":
        actual.chmod(0o000)

    artifact = {
        "id": ARTIFACT,
        "path": recorded_path,
    }
    if kind == "directory":
        artifact["artifact_type"] = "directory"
        artifact["digest_format"] = "runevidence-directory-v1"
        artifact["sha256"] = "0" * 64 if digest_present else None
        if size_mode == "mismatch":
            artifact["size_bytes"] = 999
    else:
        artifact["sha256"] = _digest(DATA) if digest_present else None
        if size_mode == "absent":
            pass
        elif size_mode == "match":
            artifact["size_bytes"] = len(DATA)
        else:
            artifact["size_bytes"] = 999
    if volatile:
        artifact["volatile"] = True

    result = {
        "id": RESULT,
        "run_id": "run",
        "name": "val",
        "value": 0.9,
        "artifact_id": ARTIFACT,
    }
    if with_locator:
        result["locator"] = {"kind": "jsonl", "line": 1, "pointer": "/val"}
    document = {
        "schema_version": "0.3",
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "run", "name": "Run"}],
        "artifacts": [artifact],
        "results": [result],
        "evidence": [{"id": CLAIM, "claim": "c", "supports": [RESULT]}],
    }
    (root / "runevidence.json").write_text(dump_manifest(Manifest.model_validate(document)))
    return actual


def _report(root: Path, flag: bool):
    return run_checks(load_manifest(root), enable_directory_artifacts=flag)

def _codes(report) -> set[str]:
    return {finding.code.value for finding in report.findings}


def _broken_artifacts(report) -> set[str]:
    return {
        finding.subject
        for finding in report.findings
        if finding.subject == ARTIFACT and finding.severity.value == "error"
    }


def _claims_at_risk(report) -> set[str]:
    return {finding.subject for finding in report.findings if finding.code.value == "claim-at-risk"}


def _artifact_pairs(report) -> set[tuple[str, str]]:
    return {
        (finding.code.value, finding.severity.value)
        for finding in report.findings
        if finding.subject == ARTIFACT
    }


CASES = [
    (kind, digest, size, volatile, state, path, flag)
    for kind in ("file", "directory")
    for digest in (True, False)
    for size in (("absent", "match", "mismatch") if kind == "file" else ("absent", "mismatch"))
    for volatile in (False, True)
    for state in ("present", "missing", "unreadable")
    for path in ("relative", "absolute-in-root", "absolute-outside", "backslash", "case")
    for flag in (False, True)
]


@pytest.mark.parametrize("case", CASES, ids=lambda case: "-".join(str(part) for part in case))
def test_locator_cannot_remove_artifact_findings(tmp_path, case):
    kind, digest, size, volatile, state, path_form, flag = case
    if state == "unreadable" and (os.name != "posix" or os.geteuid() == 0):
        pytest.skip("permission fixtures need a non-root POSIX user")

    without_root = tmp_path / "without"
    with_root = tmp_path / "with"
    outside_without = tmp_path / "outside-without"
    outside_with = tmp_path / "outside-with"
    actual_without = _build(
        without_root, outside_without,
        kind=kind, digest_present=digest, size_mode=size, volatile=volatile,
        file_state=state, path_form=path_form, with_locator=False,
    )
    actual_with = _build(
        with_root, outside_with,
        kind=kind, digest_present=digest, size_mode=size, volatile=volatile,
        file_state=state, path_form=path_form, with_locator=True,
    )
    try:
        without = _report(without_root, flag)
        with_locator = _report(with_root, flag)
    finally:
        for actual in (actual_without, actual_with):
            try:
                actual.chmod(0o755 if kind == "directory" else 0o644)
            except OSError:
                pass

    assert _broken_artifacts(with_locator) >= _broken_artifacts(without)
    assert _claims_at_risk(with_locator) >= _claims_at_risk(without)

    pairs_without = _artifact_pairs(without)
    pairs_with = _artifact_pairs(with_locator)
    codes_with = _codes(with_locator)
    allowed_additions = {("locator-unreadable", "error"), ("artifact-changed", "error")}
    assert pairs_with - pairs_without <= allowed_additions, (case, pairs_without, pairs_with)

    for pair in pairs_without - pairs_with:
        if pair == ("no-digest", "warning"):
            assert "locator-no-digest" in codes_with, case
            continue
        if pair == ("missing-file", "error") and (
            {"locator-unreadable", "artifact-changed"} & codes_with
        ):
            continue
        pytest.fail(f"{case}: artifact finding {pair} disappeared when a locator was added")


# ---------------------------------------------------------------------------
# Extension/kind mismatch: a reference refusal must not eat the artifact warning
# ---------------------------------------------------------------------------
EXTENSION_DATA = b'{"v": 1}\n'
POSIX_NONROOT = os.name == "posix" and os.geteuid() != 0


def _build_named(
    root: Path,
    *,
    name: str,
    locator_form: str,
    digest_present: bool,
    volatile: bool,
    with_locator: bool,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_bytes(EXTENSION_DATA)
    artifact = {"id": ARTIFACT, "path": name}
    artifact["sha256"] = _digest(EXTENSION_DATA) if digest_present else None
    if volatile:
        artifact["volatile"] = True
    result = {
        "id": RESULT,
        "run_id": "run",
        "name": "v",
        "value": 1,
        "artifact_id": ARTIFACT,
    }
    if with_locator:
        result["locator"] = (
            "/v" if locator_form == "string" else {"kind": "jsonl", "line": 1, "pointer": "/v"}
        )
    document = {
        "schema_version": "0.3",
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "run", "name": "Run"}],
        "artifacts": [artifact],
        "results": [result],
        "evidence": [{"id": CLAIM, "claim": "c", "supports": [RESULT]}],
    }
    (root / "runevidence.json").write_text(dump_manifest(Manifest.model_validate(document)))


EXTENSION_CASES = [
    (name, form, digest, volatile)
    for name in ("a.txt", "a.json", "a.jsonl")
    for form in ("string", "object")
    for digest in (False, True)
    for volatile in (False, True)
]


@pytest.mark.parametrize(
    "case", EXTENSION_CASES, ids=lambda case: "-".join(str(part) for part in case)
)
def test_extension_mismatch_cannot_remove_artifact_findings(tmp_path, case):
    name, form, digest_present, volatile = case
    without = tmp_path / "without"
    with_locator = tmp_path / "with"
    _build_named(
        without, name=name, locator_form=form, digest_present=digest_present,
        volatile=volatile, with_locator=False,
    )
    _build_named(
        with_locator, name=name, locator_form=form, digest_present=digest_present,
        volatile=volatile, with_locator=True,
    )
    plain = _report(without, False)
    targeted = _report(with_locator, False)

    assert _broken_artifacts(targeted) >= _broken_artifacts(plain)
    assert _claims_at_risk(targeted) >= _claims_at_risk(plain)
    pairs_plain = _artifact_pairs(plain)
    pairs_targeted = _artifact_pairs(targeted)
    codes_targeted = _codes(targeted)
    assert pairs_targeted - pairs_plain <= {
        ("locator-unreadable", "error"),
        ("artifact-changed", "error"),
    }, (case, pairs_plain, pairs_targeted)
    for pair in pairs_plain - pairs_targeted:
        if pair == ("no-digest", "warning"):
            assert "locator-no-digest" in codes_targeted, case
            continue
        pytest.fail(f"{case}: artifact finding {pair} disappeared when a locator was added")


def test_digestless_txt_string_locator_keeps_no_digest(tmp_path):
    """Exact reproducer: a string locator on a digestless `.txt` artifact.

    The extension refusal short-circuits the locator layer, so no
    `locator-no-digest` replacement is ever emitted; the artifact-level warning
    must stay exactly as it is without a locator.
    """
    without = tmp_path / "without"
    with_locator = tmp_path / "with"
    for root, flag in ((without, False), (with_locator, True)):
        _build_named(
            root, name="a.txt", locator_form="string", digest_present=False,
            volatile=False, with_locator=flag,
        )

    plain = _report(without, False)
    assert plain.exit_code() == 0
    assert [finding.code.value for finding in plain.findings] == [
        "no-digest", "result-unverified", "claim-unverified",
    ]

    targeted = _report(with_locator, False)
    assert targeted.exit_code() == 1
    assert [finding.code.value for finding in targeted.findings] == [
        "locator-kind-mismatch", "claim-at-risk", "no-digest",
    ]
    assert "locator-no-digest" not in _codes(targeted)


def test_digestless_jsonl_string_locator_keeps_no_digest(tmp_path):
    without = tmp_path / "without"
    with_locator = tmp_path / "with"
    for root, flag in ((without, False), (with_locator, True)):
        _build_named(
            root, name="metrics.jsonl", locator_form="string", digest_present=False,
            volatile=False, with_locator=flag,
        )
    plain = _report(without, False)
    assert plain.exit_code() == 0
    assert [finding.code.value for finding in plain.findings] == [
        "no-digest", "result-unverified", "claim-unverified",
    ]
    targeted = _report(with_locator, False)
    assert targeted.exit_code() == 1
    assert [finding.code.value for finding in targeted.findings] == [
        "locator-kind-mismatch", "claim-at-risk", "no-digest",
    ]


# ---------------------------------------------------------------------------
# Escaping spellings behind an unsearchable parent or a lexical leaf loop
#
# ``os.path.realpath`` is diagnosis only: it decides whether an ELOOP/EACCES
# path that is spelled inside the folder resolves inside it. A loop whose
# entry physically lives outside must keep ``path-escapes-root``; only a loop
# entry inside the folder is a mid-read change.
# ---------------------------------------------------------------------------
_ESCAPE_SPELLING_CASES = [
    (spelling, failure, "object")
    for spelling in ("absolute", "relative-dotdot")
    for failure in ("searchable", "unsearchable-parent", "leaf-loop")
]

ESCAPE_LOOP_LAYOUTS = (
    "absolute-leaf-link",
    "relative-leaf-link",
    "parent-link-escape",
    "in-root-self-loop",
)
_ESCAPE_LOOP_CASES = [
    (layout, "in-root-control" if layout == "in-root-self-loop" else "escaping-loop", form)
    for layout in ESCAPE_LOOP_LAYOUTS
    for form in ("object", "string")
]

ESCAPE_CASES = _ESCAPE_SPELLING_CASES + _ESCAPE_LOOP_CASES


def _escape_target(tmp_path: Path, failure: str) -> tuple[Path, Path | None]:
    outside = tmp_path / "outside"
    outside.mkdir()
    if failure == "searchable":
        target = outside / "m.jsonl"
        target.write_bytes(b'{"val": 0.9}\n')
        return target, None
    if failure == "unsearchable-parent":
        locked = outside / "locked"
        locked.mkdir()
        target = locked / "m.jsonl"
        target.write_bytes(b'{"val": 0.9}\n')
        # Without this the case is the *searchable* outside parent, and a
        # mutation that only reverts the EACCES guard still passes the suite.
        locked.chmod(0o000)
        return target, locked
    target = outside / "loop.jsonl"
    target.symlink_to(target)
    return target, None


def _build_escape(
    root: Path, recorded: str, *, with_locator: bool, locator_form: str = "object"
) -> None:
    result = {"id": RESULT, "run_id": "run", "name": "val", "value": 0.9, "artifact_id": ARTIFACT}
    if with_locator:
        result["locator"] = (
            "/val"
            if locator_form == "string"
            else {"kind": "jsonl", "line": 1, "pointer": "/val"}
        )
    document = {
        "schema_version": "0.3",
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "run", "name": "Run"}],
        "artifacts": [
            {
                "id": ARTIFACT,
                "path": recorded,
                "sha256": _digest(b'{"val": 0.9}\n'),
                "size_bytes": len(b'{"val": 0.9}\n'),
            }
        ],
        "results": [result],
        "evidence": [{"id": CLAIM, "claim": "c", "supports": [RESULT]}],
    }
    (root / "runevidence.json").write_text(dump_manifest(Manifest.model_validate(document)))


@pytest.mark.parametrize(
    "case", _ESCAPE_SPELLING_CASES, ids=lambda case: "-".join(case)
)
def test_escaping_path_keeps_path_escapes_root_with_a_locator(tmp_path, case):
    spelling, failure, locator_form = case
    if failure == "unsearchable-parent" and not POSIX_NONROOT:
        pytest.skip("permission fixtures need a non-root POSIX user")
    target, locked = _escape_target(tmp_path, failure)
    try:
        without = tmp_path / "without"
        with_locator = tmp_path / "with"
        without.mkdir()
        with_locator.mkdir()
        for root, flag in ((without, False), (with_locator, True)):
            recorded = str(target) if spelling == "absolute" else os.path.relpath(target, root)
            _build_escape(root, recorded, with_locator=flag, locator_form=locator_form)
        plain = _report(without, False)
        targeted = _report(with_locator, False)
    finally:
        if locked is not None:
            locked.chmod(0o755)

    codes_plain = _codes(plain)
    codes_targeted = _codes(targeted)
    assert "path-escapes-root" in codes_plain, (case, codes_plain)
    assert "path-escapes-root" in codes_targeted, (case, codes_targeted)
    # The path was never opened, so "changed while being read" is a false
    # diagnosis as well as a lost containment finding.
    assert "artifact-changed" not in codes_targeted, (case, codes_targeted)
    assert _artifact_pairs(targeted) >= _artifact_pairs(plain)
    assert _broken_artifacts(targeted) >= _broken_artifacts(plain)


def _record_loop_layout(root: Path, outside: Path, layout: str) -> str:
    if layout == "absolute-leaf-link":
        (root / "l.jsonl").symlink_to(outside / "loop.jsonl")
        return "l.jsonl"
    if layout == "relative-leaf-link":
        (root / "l.jsonl").symlink_to(os.path.relpath(outside / "loop.jsonl", root))
        return "l.jsonl"
    if layout == "parent-link-escape":
        (root / "esc").symlink_to(outside)
        return "esc/loop.jsonl"
    (root / "self.jsonl").symlink_to("self.jsonl")
    return "self.jsonl"


@pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX-only evidence: symlink loops and containment follow POSIX path semantics",
)
@pytest.mark.parametrize("case", _ESCAPE_LOOP_CASES, ids=lambda case: "-".join(case))
def test_physically_escaping_loop_keeps_path_escapes_root(tmp_path, case):
    """A loop spelled inside the folder but resolving outside is containment.

    The three escaping layouts (absolute leaf link, relative leaf link, and a
    parent link whose loop entry lives outside) were reported as
    ``artifact-changed`` once a locator was added, losing the containment
    finding. The fourth layout is the accepted control: a self-loop whose
    entry really is in the folder stays a mid-read change.
    """
    layout, failure, locator_form = case
    outside = tmp_path / "outside"
    outside.mkdir()
    if layout != "in-root-self-loop":
        # The loop's entry file, shared by both roots.
        (outside / "loop.jsonl").symlink_to("loop.jsonl")
    without = tmp_path / "without"
    with_locator = tmp_path / "with"
    without.mkdir()
    with_locator.mkdir()
    for root, flag in ((without, False), (with_locator, True)):
        recorded = _record_loop_layout(root, outside, layout)
        _build_escape(root, recorded, with_locator=flag, locator_form=locator_form)
    plain = _report(without, False)
    targeted = _report(with_locator, False)

    if failure == "in-root-control":
        # The legacy outcome for an in-folder self-loop is version-dependent
        # (``Path.resolve`` raises RuntimeError on 3.10/3.12 and, per the
        # documented limitation, reports missing on 3.13/3.14). The targeted
        # control is the same on every version: artifact-changed.
        assert {"path-escapes-root", "missing-file"} & set(_codes(plain)), (
            case,
            _codes(plain),
        )
        assert "artifact-changed" in _codes(targeted), (case, _codes(targeted))
        assert "path-escapes-root" not in _codes(targeted), (case, _codes(targeted))
        return
    assert "path-escapes-root" in _codes(plain), (case, _codes(plain))
    assert "path-escapes-root" in _codes(targeted), (case, _codes(targeted))
    changed = [
        finding.message
        for finding in targeted.findings
        if finding.code.value == "artifact-changed"
    ]
    # Neither the mid-read text nor the in-folder symlinked-parent text is a
    # truthful diagnosis for a link whose entry is outside the folder.
    assert changed == [], (case, changed)
    assert _artifact_pairs(targeted) >= _artifact_pairs(plain)
    assert _broken_artifacts(targeted) >= _broken_artifacts(plain)
