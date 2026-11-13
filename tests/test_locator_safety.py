"""Protected snapshot safety: parent containment, hint reads, permissions and
snapshot lifetime.

These tests are written so that reverting the invariant they cover makes them
fail, not merely "does not crash".
"""

from __future__ import annotations

import builtins
import gc
import hashlib
import json
import os
import signal
import weakref
from pathlib import Path

import pytest

from runevidence.checks import run_checks
from runevidence.checks import files as file_checks
from runevidence.checks import results as results_checks
from runevidence import locator_profiles as profiles
from runevidence.manifest import dump_manifest, load_manifest
from runevidence.models import Manifest

DATA = b'{"val": 0.9}\n'
POSIX_NONROOT = os.name == "posix" and os.geteuid() != 0


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _document(*, path="metrics.jsonl", sha256=None, size=None, locator=True, version="0.3"):
    artifact = {"id": "a", "path": path}
    if sha256 is not None:
        artifact["sha256"] = sha256
    if size is not None:
        artifact["size_bytes"] = size
    result = {
        "id": "res",
        "run_id": "run",
        "name": "val",
        "value": 0.9,
        "artifact_id": "a",
    }
    if locator:
        result["locator"] = {"kind": "jsonl", "line": 1, "pointer": "/val"}
    return {
        "schema_version": version,
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "run", "name": "Run"}],
        "artifacts": [artifact],
        "results": [result],
        "evidence": [{"id": "claim", "claim": "c", "supports": ["res"]}],
    }


def _write(root: Path, document: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "runevidence.json").write_text(dump_manifest(Manifest.model_validate(document)))


def _codes(report) -> list[str]:
    return [finding.code.value for finding in report.findings]


# ---------------------------------------------------------------- B1
def test_parent_swap_after_containment_is_artifact_changed(tmp_path, monkeypatch):
    root = tmp_path / "root"
    sub = root / "sub"
    sub.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_bytes = b'{"val": 0.9}\n'
    (outside / "metrics.jsonl").write_bytes(outside_bytes)
    (sub / "metrics.jsonl").write_bytes(b'{"val": 0.1}\n')
    _write(
        root,
        _document(path="sub/metrics.jsonl", sha256=_digest(outside_bytes), size=len(outside_bytes)),
    )

    outside_stat = os.stat(outside / "metrics.jsonl")
    real_stat_path = file_checks._stat_path
    real_fstat = os.fstat
    calls = {"n": 0}
    outside_fds: list[int] = []

    def swapping_stat_path(path):
        result = real_stat_path(path)
        calls["n"] += 1
        if calls["n"] == 1:
            # Swap the parent after containment resolution (the ``_within``
            # call before this stat), before the protected read.
            os.rename(sub, root / "sub-real")
            os.symlink(str(outside), sub)
        return result

    def counting_fstat(fd):
        stat = real_fstat(fd)
        if (stat.st_dev, stat.st_ino) == (outside_stat.st_dev, outside_stat.st_ino):
            outside_fds.append(fd)
        return stat

    monkeypatch.setattr(file_checks, "_stat_path", swapping_stat_path)
    monkeypatch.setattr(file_checks.os, "fstat", counting_fstat)
    report = run_checks(load_manifest(root))

    assert report.exit_code() == 1
    changed = [f for f in report.findings if f.code.value == "artifact-changed"]
    assert changed, _codes(report)
    # Classified by the descriptor walk itself, not by a pre-open resolve
    # check: the component that changed is now a symbolic link.
    assert changed[0].message == "a parent directory is a symbolic link; use the real path"
    assert "claim-at-risk" in _codes(report)
    # The outside inode was never opened, so it was never read or hashed.
    assert outside_fds == []


def test_protected_walk_parent_component_flags(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "metrics.jsonl").write_bytes(DATA)
    _write(root, _document(path="sub/metrics.jsonl", sha256=_digest(DATA), size=len(DATA)))

    parents: list[tuple[str, int]] = []
    real_open = file_checks.os.open

    def counting_open(path, flags, *args, **kwargs):
        if kwargs.get("dir_fd") is not None and flags & getattr(os, "O_DIRECTORY", 0):
            parents.append((os.fspath(path), flags))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(file_checks.os, "open", counting_open)
    report = run_checks(load_manifest(root))

    assert report.findings == []
    assert [name for name, _ in parents] == ["sub"]
    flags = parents[0][1]
    assert flags & getattr(os, "O_NOFOLLOW", 0)
    assert flags & getattr(os, "O_DIRECTORY", 0)


def test_targeted_symlinked_parent_is_not_followed(tmp_path):
    root = tmp_path / "root"
    real = root / "real"
    real.mkdir(parents=True)
    (real / "metrics.jsonl").write_bytes(DATA)
    (root / "alias").symlink_to(real, target_is_directory=True)
    _write(root, _document(path="alias/metrics.jsonl", sha256=_digest(DATA), size=len(DATA)))
    report = run_checks(load_manifest(root))
    assert report.exit_code() == 1
    assert "artifact-changed" in _codes(report)
    assert "claim-at-risk" in _codes(report)


@pytest.mark.skipif(not file_checks._DIR_FD, reason="parent components need the descriptor walk")
def test_symlinked_parent_has_its_own_fixed_message(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symbolic links are unsupported")
    root = tmp_path / "root"
    real = root / "run_2025"
    real.mkdir(parents=True)
    (real / "metrics.jsonl").write_bytes(DATA)
    (root / "latest").symlink_to(real, target_is_directory=True)
    _write(root, _document(path="latest/metrics.jsonl", sha256=_digest(DATA), size=len(DATA)))

    report = run_checks(load_manifest(root))
    changed = [f for f in report.findings if f.code.value == "artifact-changed"]
    assert len(changed) == 1
    # A parent link is not a race: say so instead of claiming the file changed
    # while it was being read.
    assert changed[0].message == "a parent directory is a symbolic link; use the real path"
    assert "changed while it was being read" not in changed[0].message
    assert changed[0].hint and "real folder" in changed[0].hint

    # The legacy in-root alias is still followed (documented difference), so
    # the distinct message is only a targeted-read refusal.
    document = json.loads((root / "runevidence.json").read_text())
    document["results"][0].pop("locator")
    (root / "runevidence.json").write_text(dump_manifest(Manifest.model_validate(document)))
    legacy = run_checks(load_manifest(root))
    assert "artifact-changed" not in _codes(legacy)
    assert legacy.findings[0].code.value == "result-unverified"


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symbolic links are unsupported")
def test_parent_link_with_an_outside_loop_keeps_path_escapes_root(tmp_path):
    """Blocker A + symlinked-parent precision, exact fixture.

    ``root/sub -> outside`` is a parent link, but its loop entry lives outside
    the folder: that is containment, never the generic mid-read text and never
    the fixed in-folder parent-link text.
    """
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "loop.jsonl").symlink_to("loop.jsonl")
    root.mkdir()
    (root / "sub").symlink_to(outside, target_is_directory=True)
    _write(
        root,
        _document(path="sub/loop.jsonl", sha256=_digest(DATA), size=len(DATA)),
    )

    report = run_checks(load_manifest(root))
    assert _codes(report) == ["path-escapes-root", "claim-at-risk"]
    blob = " ".join(finding.message for finding in report.findings)
    assert "changed while it was being read" not in blob
    assert "a parent directory is a symbolic link" not in blob


skip_if_no_symlinks = pytest.mark.skipif(
    not hasattr(os, "symlink"), reason="symbolic links are unsupported"
)


@skip_if_no_symlinks
def test_leaf_alias_with_an_in_root_parent_reference_verifies(tmp_path):
    """`sub/alias.jsonl -> ../metrics.jsonl` stays inside and is legal.

    Only a target that climbs above the folder (even one that lexically
    re-enters) is refused; the docs used to claim every `..` alias was.
    """
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "metrics.jsonl").write_bytes(DATA)
    (root / "sub" / "alias.jsonl").symlink_to("../metrics.jsonl")
    _write(
        root,
        _document(path="sub/alias.jsonl", sha256=_digest(DATA), size=len(DATA)),
    )
    report = run_checks(load_manifest(root))
    assert report.findings == []
    assert report.exit_code() == 0


@skip_if_no_symlinks
def test_leaf_alias_climbing_out_of_root_keeps_path_escapes_root(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "metrics.jsonl").write_bytes(DATA)
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "alias.jsonl").symlink_to("../../outside/metrics.jsonl")
    _write(
        root,
        _document(path="sub/alias.jsonl", sha256=_digest(DATA), size=len(DATA)),
    )
    report = run_checks(load_manifest(root))
    assert _codes(report) == ["path-escapes-root", "claim-at-risk"]


@skip_if_no_symlinks
def test_leaf_alias_climbing_above_root_keeps_path_escapes_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "metrics.jsonl").write_bytes(DATA)
    (root / "sub").mkdir()
    (root / "sub" / "alias.jsonl").symlink_to(f"../../{root.name}/metrics.jsonl")
    _write(
        root,
        _document(path="sub/alias.jsonl", sha256=_digest(DATA), size=len(DATA)),
    )
    report = run_checks(load_manifest(root))
    # The target climbs above the manifest folder and lexically re-enters, but
    # it is still a containment refusal, not a mid-read change.
    assert _codes(report) == ["path-escapes-root", "claim-at-risk"]
    blob = " ".join(finding.message for finding in report.findings)
    assert "changed while it was being read" not in blob


@skip_if_no_symlinks
def test_in_folder_parent_link_gets_the_fixed_message(tmp_path):
    """`root/sub -> root/run_2025` resolves inside, so it is the link message."""
    root = tmp_path / "root"
    (root / "run_2025").mkdir(parents=True)
    (root / "run_2025" / "metrics.jsonl").write_bytes(DATA)
    (root / "sub").symlink_to(root / "run_2025", target_is_directory=True)
    _write(
        root,
        _document(path="sub/metrics.jsonl", sha256=_digest(DATA), size=len(DATA)),
    )
    report = run_checks(load_manifest(root))
    changed = [f for f in report.findings if f.code.value == "artifact-changed"]
    assert len(changed) == 1, _codes(report)
    assert changed[0].message == "a parent directory is a symbolic link; use the real path"
    assert "path-escapes-root" not in _codes(report)
    assert "changed while it was being read" not in changed[0].message


@skip_if_no_symlinks
def test_parent_link_to_an_outside_regular_leaf_keeps_path_escapes_root(tmp_path):
    """`root/esc -> outside` with a regular outside leaf is containment."""
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "metrics.jsonl").write_bytes(DATA)
    root.mkdir()
    (root / "esc").symlink_to(outside, target_is_directory=True)
    _write(
        root,
        _document(path="esc/metrics.jsonl", sha256=_digest(DATA), size=len(DATA)),
    )
    report = run_checks(load_manifest(root))
    assert _codes(report) == ["path-escapes-root", "claim-at-risk"]
    blob = " ".join(finding.message for finding in report.findings)
    assert "a parent directory is a symbolic link" not in blob
    assert "changed while it was being read" not in blob


# ---------------------------------------------------------------- path spelling
@skip_if_no_symlinks
def test_in_root_dotdot_spelling_is_a_distinct_refusal(tmp_path):
    """A valid, unchanged path with `..` must not be called a mid-read change."""
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "x.jsonl").write_bytes(DATA)
    _write(
        root,
        _document(path="sub/../x.jsonl", sha256=_digest(DATA), size=len(DATA)),
    )
    report = run_checks(load_manifest(root))
    assert report.exit_code() == 1
    finding = next(f for f in report.findings if f.code.value == "locator-unreadable")
    assert finding.message == "artifact path uses '..'; write it without parent references"
    assert "changed while being read" not in finding.message
    assert "claim-at-risk" in _codes(report)

    # Without a locator the same spelling is hashed as the kernel resolves it
    # (inside, unchanged), and the canonical spelling still verifies.
    _write(root, _document(path="sub/../x.jsonl", sha256=_digest(DATA), size=len(DATA), locator=False))
    plain = run_checks(load_manifest(root))
    assert plain.exit_code() == 0
    assert "artifact-changed" not in _codes(plain)

    _write(root, _document(path="x.jsonl", sha256=_digest(DATA), size=len(DATA)))
    canonical = run_checks(load_manifest(root))
    assert canonical.findings == []


@skip_if_no_symlinks
def test_in_root_dotdot_with_symlinked_sub_is_not_normalised(tmp_path):
    """Opus counterexample: `sub -> deep/inner` makes normalisation lie.

    The kernel opens `deep/x.jsonl` for `sub/../x.jsonl`; normpath would pick
    root `x.jsonl`, a different file with a different value. The reader must
    refuse the spelling rather than verify something it did not open.
    """
    root = tmp_path / "root"
    (root / "deep" / "inner").mkdir(parents=True)
    (root / "deep" / "inner" / "x.jsonl").write_bytes(b'{"val": 0.5}\n')
    (root / "deep" / "x.jsonl").write_bytes(DATA)
    (root / "x.jsonl").write_bytes(b'{"val": 0.1}\n')
    (root / "sub").symlink_to(Path("deep") / "inner", target_is_directory=True)
    _write(
        root,
        _document(path="sub/../x.jsonl", sha256=_digest(DATA), size=len(DATA)),
    )
    report = run_checks(load_manifest(root))
    finding = next(f for f in report.findings if f.code.value == "locator-unreadable")
    assert finding.message == "artifact path uses '..'; write it without parent references"
    assert "result-value-mismatch" not in _codes(report)

    _write(root, _document(path="deep/x.jsonl", sha256=_digest(DATA), size=len(DATA)))
    kernel_path = run_checks(load_manifest(root))
    assert kernel_path.findings == []


# ---------------------------------------------------------------- leaf alias dotdot
#
# Round-4 blocker (Sol, Opus, foreman): `..` in a leaf-alias target, or in an
# absolute artifact spelling, was popped lexically. When a component before the
# `..` is a link, the kernel opens a different file than the normalised
# spelling names, so the reader verified the wrong bytes. All three spellings
# below must fail closed, and the two legal shapes must stay accepted.

def _wrong_file_layout(tmp_path: Path) -> Path:
    """`root/sub -> a/b`; `root/x.jsonl` is 0.9 and `root/a/x.jsonl` is 0.1.

    The manifest records the digest of `root/x.jsonl`, the file the lexical
    spelling names. The kernel reaches `root/a/x.jsonl` through the link for
    `sub/../x.jsonl`, so a normalised reader would verify bytes it never read.
    """
    root = tmp_path / "root"
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "x.jsonl").write_bytes(b'{"val": 0.1}\n')
    (root / "x.jsonl").write_bytes(DATA)
    (root / "sub").symlink_to("a/b", target_is_directory=True)
    return root


def _assert_refused_not_verified(report, message: str) -> None:
    assert report.exit_code() == 1, [f.code.value for f in report.findings]
    assert "locator-unreadable" in _codes(report)
    # The wrong file was never verified and nothing was read as a race.
    assert "result-value-mismatch" not in _codes(report)
    assert "artifact-changed" not in _codes(report)
    finding = next(f for f in report.findings if f.code.value == "locator-unreadable")
    assert finding.message == message
    assert "changed while being read" not in finding.message


@skip_if_no_symlinks
def test_absolute_dotdot_spelling_cannot_verify_the_wrong_file(tmp_path):
    root = _wrong_file_layout(tmp_path)
    recorded = str(root / "sub" / ".." / "x.jsonl")

    # Control: without a locator the legacy hash follows the kernel to
    # `root/a/x.jsonl` and reports the mismatch. The fixture discriminates.
    _write(root, _document(path=recorded, sha256=_digest(DATA), size=len(DATA), locator=False))
    plain = run_checks(load_manifest(root))
    assert plain.exit_code() == 1
    assert "hash-mismatch" in _codes(plain)

    _write(root, _document(path=recorded, sha256=_digest(DATA), size=len(DATA)))
    _assert_refused_not_verified(
        run_checks(load_manifest(root)),
        "artifact path uses '..'; write it without parent references",
    )


@skip_if_no_symlinks
def test_relative_alias_dotdot_over_a_link_cannot_verify_the_wrong_file(tmp_path):
    root = _wrong_file_layout(tmp_path)
    (root / "alias.jsonl").symlink_to("sub/../x.jsonl")

    _write(root, _document(path="alias.jsonl", sha256=_digest(DATA), size=len(DATA), locator=False))
    plain = run_checks(load_manifest(root))
    assert plain.exit_code() == 1
    assert "hash-mismatch" in _codes(plain)

    _write(root, _document(path="alias.jsonl", sha256=_digest(DATA), size=len(DATA)))
    _assert_refused_not_verified(
        run_checks(load_manifest(root)),
        "the artifact's symbolic link target is not verified; point the link at a "
        "plain path inside the folder",
    )


@skip_if_no_symlinks
def test_absolute_alias_dotdot_over_a_link_cannot_verify_the_wrong_file(tmp_path):
    root = _wrong_file_layout(tmp_path)
    (root / "alias.jsonl").symlink_to(str(root / "sub" / ".." / "x.jsonl"))

    _write(root, _document(path="alias.jsonl", sha256=_digest(DATA), size=len(DATA), locator=False))
    plain = run_checks(load_manifest(root))
    assert plain.exit_code() == 1
    assert "hash-mismatch" in _codes(plain)

    _write(root, _document(path="alias.jsonl", sha256=_digest(DATA), size=len(DATA)))
    _assert_refused_not_verified(
        run_checks(load_manifest(root)),
        "the artifact's symbolic link target is not verified; point the link at a "
        "plain path inside the folder",
    )


@skip_if_no_symlinks
def test_nested_dotdot_alias_over_real_directories_still_verifies(tmp_path):
    """`a/b/alias.jsonl -> ../../x.jsonl` climbs two verified real dirs."""
    root = tmp_path / "root"
    (root / "a" / "b").mkdir(parents=True)
    (root / "x.jsonl").write_bytes(DATA)
    (root / "a" / "b" / "alias.jsonl").symlink_to("../../x.jsonl")
    _write(
        root,
        _document(path="a/b/alias.jsonl", sha256=_digest(DATA), size=len(DATA)),
    )
    report = run_checks(load_manifest(root))
    assert report.findings == []
    assert report.exit_code() == 0


@skip_if_no_symlinks
def test_absolute_in_root_alias_target_without_dotdot_still_verifies(tmp_path):
    """Shape checking must not refuse a plain absolute alias target."""
    root = tmp_path / "root"
    root.mkdir()
    (root / "metrics.jsonl").write_bytes(DATA)
    (root / "alias.jsonl").symlink_to(root / "metrics.jsonl")
    _write(
        root,
        _document(path="alias.jsonl", sha256=_digest(DATA), size=len(DATA)),
    )
    report = run_checks(load_manifest(root))
    assert report.findings == []
    assert report.exit_code() == 0


# ---------------------------------------------------------------- path shape text
@pytest.mark.parametrize("spelling", ["sub//x.jsonl", "x.jsonl/"])
def test_non_dotdot_shape_refusal_has_a_neutral_fixed_message(tmp_path, spelling):
    """The refusal covers empty and trailing-slash shapes too, so it must not
    always blame `..`; without a locator the same spelling still hashes."""
    root = tmp_path / spelling.replace("/", "-").replace(".", "_")
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "x.jsonl").write_bytes(DATA)
    (root / "x.jsonl").write_bytes(DATA)

    _write(root, _document(path=spelling, sha256=_digest(DATA), size=len(DATA), locator=False))
    plain = run_checks(load_manifest(root))
    assert plain.exit_code() == 0, _codes(plain)

    _write(root, _document(path=spelling, sha256=_digest(DATA), size=len(DATA)))
    report = run_checks(load_manifest(root))
    assert report.exit_code() == 1
    assert "locator-unreadable" in _codes(report)
    assert "artifact-changed" not in _codes(report)
    finding = next(f for f in report.findings if f.code.value == "locator-unreadable")
    assert finding.message == (
        "artifact path has an empty, '.' or trailing-slash component; write the plain file path"
    )
    assert "uses '..'" not in finding.message


@pytest.mark.parametrize("shape", ["double-slash", "trailing-slash"])
def test_absolute_empty_component_is_refused(tmp_path, shape):
    """The same shape check guards the absolute branch that used ``relpath``."""
    root = tmp_path / "root"
    root.mkdir()
    (root / "x.jsonl").write_bytes(DATA)
    spelling = str(root) + ("//x.jsonl" if shape == "double-slash" else "/x.jsonl/")

    _write(root, _document(path=spelling, sha256=_digest(DATA), size=len(DATA), locator=False))
    plain = run_checks(load_manifest(root))
    assert plain.exit_code() == 0, _codes(plain)

    _write(root, _document(path=spelling, sha256=_digest(DATA), size=len(DATA)))
    report = run_checks(load_manifest(root))
    assert report.exit_code() == 1
    assert "locator-unreadable" in _codes(report)
    finding = next(f for f in report.findings if f.code.value == "locator-unreadable")
    assert finding.message == (
        "artifact path has an empty, '.' or trailing-slash component; write the plain file path"
    )


# ---------------------------------------------------------------- B3
def _missing_targeted_document(candidate_bytes: bytes) -> dict:
    return _document(sha256=_digest(candidate_bytes), size=len(candidate_bytes))


def test_hint_candidate_uses_protected_open_never_sha256_file(tmp_path, monkeypatch):
    root = tmp_path / "root"
    candidate = root / "other" / "metrics.jsonl"
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(DATA)
    _write(root, _missing_targeted_document(DATA))

    monkeypatch.setattr(
        file_checks,
        "sha256_file",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("sha256_file was used on the hint path")
        ),
    )
    opened: list[int] = []
    real_open = file_checks.os.open

    def counting_open(path, flags, *args, **kwargs):
        if os.fspath(path) == "metrics.jsonl" and kwargs.get("dir_fd") is not None:
            opened.append(flags)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(file_checks.os, "open", counting_open)
    report = run_checks(load_manifest(root))

    assert "missing-file" in _codes(report)
    assert opened, "the hint candidate was never read through the protected open"
    for flags in opened:
        assert flags & getattr(os, "O_NOFOLLOW", 0)
        assert flags & getattr(os, "O_NONBLOCK", 0)
        assert flags & getattr(os, "O_NOCTTY", 0)


class _HintBlocked(BaseException):
    """Raised from SIGALRM; deliberately outside OSError.

    ``_open_protected`` maps an ``OSError`` from a blocking open to a fixed
    state and returns, so a builtin ``TimeoutError`` (an ``OSError`` subclass)
    would be swallowed and the test would pass even while the FIFO blocked.
    This sentinel must escape to the test runner."""


def test_fifo_hint_candidate_cannot_block(tmp_path, monkeypatch):
    if os.name != "posix":
        pytest.skip("FIFO + SIGALRM coverage is POSIX only")
    root = tmp_path / "root"
    candidate = root / "other" / "metrics.jsonl"
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(DATA)
    index = file_checks._TreeIndex(root)
    artifact = type(
        "FakeArtifact", (), {"sha256": _digest(DATA), "size_bytes": len(DATA), "volatile": False}
    )()

    real_os_open, real_builtin_open = os.open, builtins.open
    swapped = {"done": False}

    def swap():
        if not swapped["done"]:
            swapped["done"] = True
            candidate.unlink()
            os.mkfifo(candidate)

    def swapping_os_open(path, flags, *args, **kwargs):
        # The protected walk opens the leaf by name relative to its parent.
        if os.fspath(path) == "metrics.jsonl":
            swap()
        return real_os_open(path, flags, *args, **kwargs)

    def swapping_builtin_open(file, *args, **kwargs):
        # The old hint path (sha256_file -> open) would block here.
        if os.fspath(file) == os.fspath(candidate):
            swap()
        return real_builtin_open(file, *args, **kwargs)

    def timeout(signum, frame):
        raise _HintBlocked("hint candidate blocked on a FIFO")

    old_handler = signal.signal(signal.SIGALRM, timeout)
    signal.setitimer(signal.ITIMER_REAL, 3)
    try:
        monkeypatch.setattr(file_checks.os, "open", swapping_os_open)
        monkeypatch.setattr(builtins, "open", swapping_builtin_open)
        assert index.same_content("other/metrics.jsonl", artifact) is False
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)


def test_moved_hint_reuses_the_targeted_snapshot_digest(tmp_path, monkeypatch):
    root = tmp_path / "root"
    sub = root / "sub"
    sub.mkdir(parents=True)
    data = b'{"val": 0.9}\n'
    (sub / "metrics.jsonl").write_bytes(data)
    document = {
        "schema_version": "0.3",
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "run", "name": "Run"}],
        "artifacts": [
            {"id": "a", "path": "sub/metrics.jsonl", "sha256": _digest(data), "size_bytes": len(data)},
            {"id": "b", "path": "metrics.jsonl", "sha256": _digest(data), "size_bytes": len(data)},
        ],
        "results": [
            {"id": "ra", "run_id": "run", "name": "val", "value": 0.9,
             "artifact_id": "a", "locator": {"kind": "jsonl", "line": 1, "pointer": "/val"}},
            {"id": "rb", "run_id": "run", "name": "other", "value": 0.9,
             "artifact_id": "b", "locator": {"kind": "jsonl", "line": 1, "pointer": "/val"}},
        ],
        "evidence": [],
    }
    _write(root, document)
    monkeypatch.setattr(
        file_checks,
        "_digest_protected",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("the remembered snapshot digest was not reused")
        ),
    )
    report = run_checks(load_manifest(root))
    missing = [f for f in report.findings if f.code.value == "missing-file"]
    assert len(missing) == 1
    assert "sub/metrics.jsonl" in (missing[0].hint or "")


def test_parent_swap_hint_candidate_is_refused(tmp_path, monkeypatch):
    root = tmp_path / "root"
    sub = root / "sub"
    sub.mkdir(parents=True)
    (sub / "metrics.jsonl").write_bytes(b'{"val": 0.1}\n')
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "metrics.jsonl").write_bytes(DATA)
    index = file_checks._TreeIndex(root)
    artifact = type(
        "FakeArtifact", (), {"sha256": _digest(DATA), "size_bytes": len(DATA), "volatile": False}
    )()

    real_os_open, real_builtin_open = os.open, builtins.open
    swapped = {"done": False}

    def swap():
        if not swapped["done"]:
            swapped["done"] = True
            os.rename(sub, root / "sub-real")
            os.symlink(str(outside), sub)

    def swapping_os_open(path, flags, *args, **kwargs):
        if os.fspath(path) == "sub":
            swap()
        return real_os_open(path, flags, *args, **kwargs)

    def swapping_builtin_open(file, *args, **kwargs):
        # Old code followed the swapped parent and hashed the outside file.
        if os.fspath(file) == os.fspath(sub / "metrics.jsonl"):
            swap()
        return real_builtin_open(file, *args, **kwargs)

    monkeypatch.setattr(file_checks.os, "open", swapping_os_open)
    monkeypatch.setattr(builtins, "open", swapping_builtin_open)
    assert index.same_content("sub/metrics.jsonl", artifact) is False


# ---------------------------------------------------------------- B4
@pytest.mark.skipif(not POSIX_NONROOT, reason="permission fixtures need a non-root POSIX user")
def test_targeted_unsearchable_parent_is_locator_unreadable(tmp_path):
    root = tmp_path / "root"
    parent = root / "sub"
    parent.mkdir(parents=True)
    (parent / "metrics.jsonl").write_bytes(DATA)
    _write(root, _document(path="sub/metrics.jsonl", sha256=_digest(DATA), size=len(DATA)))
    parent.chmod(0o000)
    try:
        report = run_checks(load_manifest(root))
    finally:
        parent.chmod(0o755)

    assert report.exit_code() == 1
    assert "locator-unreadable" in _codes(report)
    assert "missing-file" not in _codes(report)
    assert "claim-at-risk" in _codes(report)
    assert "Traceback" not in " ".join(f.message for f in report.findings)


@pytest.mark.skipif(not POSIX_NONROOT, reason="permission fixtures need a non-root POSIX user")
@pytest.mark.parametrize("version", ["0.1", "0.2"])
def test_legacy_unsearchable_parent_is_fixed_missing_file(tmp_path, version):
    root = tmp_path / "root"
    parent = root / "sub"
    parent.mkdir(parents=True)
    (parent / "metrics.jsonl").write_bytes(DATA)
    _write(
        root,
        _document(
            path="sub/metrics.jsonl", sha256=_digest(DATA), size=len(DATA),
            locator=False, version=version,
        ),
    )
    parent.chmod(0o000)
    try:
        report = run_checks(load_manifest(root))
    finally:
        parent.chmod(0o755)

    assert report.exit_code() == 1
    assert "missing-file" in _codes(report)
    assert "claim-at-risk" in _codes(report)
    blob = " ".join(finding.message for finding in report.findings)
    assert "Errno" not in blob and "Permission" not in blob and "Traceback" not in blob


@pytest.mark.skipif(not POSIX_NONROOT, reason="permission fixtures need a non-root POSIX user")
def test_targeted_no_digest_unreadable_file_is_locator_unreadable(tmp_path):
    root = tmp_path / "root"
    target = root / "metrics.jsonl"
    target.parent.mkdir(parents=True)
    target.write_bytes(DATA)
    _write(root, _document(sha256=None, size=len(DATA)))
    target.chmod(0o000)
    try:
        report = run_checks(load_manifest(root))
    finally:
        target.chmod(0o644)

    assert "locator-unreadable" in _codes(report)
    assert "locator-no-digest" not in _codes(report)
    assert "no-digest" not in _codes(report)
    assert "claim-at-risk" in _codes(report)


# ---------------------------------------------------------------- post-read identity
def test_post_read_replacement_is_artifact_changed(tmp_path, monkeypatch):
    root = tmp_path / "root"
    target = root / "metrics.jsonl"
    root.mkdir(parents=True)
    target.write_bytes(DATA)
    _write(root, _document(sha256=_digest(DATA), size=len(DATA)))

    real_read = os.read
    swapped = {"done": False}

    def swapping_read(fd, count):
        chunk = real_read(fd, count)
        if chunk and not swapped["done"]:
            swapped["done"] = True
            replacement = root / "replacement.jsonl"
            replacement.write_bytes(DATA)
            os.replace(replacement, target)
        return chunk

    monkeypatch.setattr(file_checks.os, "read", swapping_read)
    report = run_checks(load_manifest(root))

    assert swapped["done"] is True
    assert "artifact-changed" in _codes(report)
    assert "claim-at-risk" in _codes(report)


# ---------------------------------------------------------------- N1
def test_snapshot_is_released_after_each_artifact(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "one").mkdir(parents=True)
    (root / "two").mkdir()
    (root / "one" / "a.jsonl").write_bytes(b'{"val": 0.9}\n')
    (root / "two" / "b.jsonl").write_bytes(b'{"val": 0.9}\n')
    document = {
        "schema_version": "0.3",
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "run", "name": "Run"}],
        "artifacts": [
            {"id": "a", "path": "one/a.jsonl", "sha256": _digest(b'{"val": 0.9}\n')},
            {"id": "b", "path": "two/b.jsonl", "sha256": _digest(b'{"val": 0.9}\n')},
        ],
        "results": [
            {"id": "ra", "run_id": "run", "name": "val", "value": 0.9,
             "artifact_id": "a", "locator": {"kind": "jsonl", "line": 1, "pointer": "/val"}},
            {"id": "rb", "run_id": "run", "name": "val", "value": 0.9,
             "artifact_id": "b", "locator": {"kind": "jsonl", "line": 1, "pointer": "/val"}},
        ],
        "evidence": [],
    }
    _write(root, document)

    refs: list[weakref.ReferenceType] = []
    original = file_checks.Snapshot

    def tracking_snapshot(*args, **kwargs):
        # Peak memory is one snapshot: the previous artifact's snapshot must
        # already be dead when the next one is constructed, not merely by the
        # time run_checks returns.
        assert all(ref() is None for ref in refs), "a previous snapshot was still alive"
        snapshot = original(*args, **kwargs)
        refs.append(weakref.ref(snapshot))
        return snapshot

    monkeypatch.setattr(file_checks, "Snapshot", tracking_snapshot)
    report = run_checks(load_manifest(root))
    gc.collect()

    assert len(refs) == 2
    assert all(ref() is None for ref in refs)
    assert report.findings == []
    for finding in report.findings:
        assert not any(isinstance(value, bytes) for value in finding.__dict__.values())


# ---------------------------------------------------------------- refusal release
def _refusal_document(first_name: str, first_locator) -> dict:
    data = b'{"val": 0.9}\n'
    return {
        "schema_version": "0.3",
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "run", "name": "Run"}],
        "artifacts": [
            {"id": "a1", "path": first_name, "sha256": _digest(b"first")},
            {"id": "a2", "path": "second.jsonl", "sha256": _digest(data), "size_bytes": len(data)},
        ],
        "results": [
            {"id": "r1", "run_id": "run", "name": "val", "value": 0.9,
             "artifact_id": "a1", "locator": first_locator},
            {"id": "r2", "run_id": "run", "name": "val", "value": 0.9,
             "artifact_id": "a2", "locator": {"kind": "jsonl", "line": 1, "pointer": "/val"}},
        ],
        "evidence": [],
    }


REFUSAL_CASES = [
    ("selected-line-parse", "first.jsonl", b'{"val": 0.9}\nnot json\n',
     {"kind": "jsonl", "line": 2, "pointer": "/val"}),
    ("file-wide-profile", "first.jsonl", b'{"val": 0.9}\n\x00\n',
     {"kind": "jsonl", "line": 1, "pointer": "/val"}),
    ("line-out-of-range", "first.jsonl", b'{"val": 0.9}\n',
     {"kind": "jsonl", "line": 5, "pointer": "/val"}),
    ("duplicate-keys", "first.jsonl", b'{"val": 0.9, "val": 1.0}\n',
     {"kind": "jsonl", "line": 1, "pointer": "/val"}),
    ("document-refusal", "first.json", b'{"val": 0.9, "val": 1.0}\n', "/val"),
]


def _live_new(cls, baseline: set[int]):
    return [obj for obj in gc.get_objects() if isinstance(obj, cls) and id(obj) not in baseline]


@pytest.mark.parametrize("case", REFUSAL_CASES, ids=lambda case: case[0])
def test_refusal_paths_release_the_snapshot_before_the_next_artifact(tmp_path, monkeypatch, case):
    """A cached exception must never keep an artifact's bytes alive.

    Caching the exception (rather than a marker) leaves its traceback owning
    the reader frame and therefore the snapshot bytes until a later gc pass.
    The probe runs at every new Snapshot construction, i.e. before the next
    artifact is read, and again after run_checks returns without gc.collect().
    """
    name, first_name, first_data, first_locator = case
    root = tmp_path / name
    root.mkdir()
    (root / first_name).write_bytes(first_data)
    second = b'{"val": 0.9}\n'
    (root / "second.jsonl").write_bytes(second)
    document = _refusal_document(first_name, first_locator)
    document["artifacts"][0]["sha256"] = _digest(first_data)
    document["artifacts"][0]["size_bytes"] = len(first_data)
    _write(root, document)

    baseline_values = {id(obj) for obj in gc.get_objects() if isinstance(obj, results_checks._LocatorValues)}
    baseline_documents = {id(obj) for obj in gc.get_objects() if isinstance(obj, profiles.JsonlDocument)}
    original = file_checks.Snapshot
    constructions: list[int] = []

    def tracking_snapshot(*args, **kwargs):
        constructions.append(1)
        assert _live_new(results_checks._LocatorValues, baseline_values) == [], (
            "the previous artifact's _LocatorValues is still alive"
        )
        assert _live_new(profiles.JsonlDocument, baseline_documents) == [], (
            "the previous artifact's parsed JSONL document is still alive"
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(file_checks, "Snapshot", tracking_snapshot)
    report = run_checks(load_manifest(root))

    assert constructions == [1, 1]
    assert report.exit_code() == 1
    assert _live_new(results_checks._LocatorValues, baseline_values) == []
    assert _live_new(profiles.JsonlDocument, baseline_documents) == []
    for finding in report.findings:
        assert not any(isinstance(value, bytes) for value in finding.__dict__.values())


# ------------------------------------------------- host-absolute alias targets
#
# Round-5 blocker (Sol, Opus): `_is_absolute` used Windows syntax, so on POSIX
# a drive/UNC-shaped link target was classified absolute and sent to
# `os.path.relpath`, which resolves it against the process cwd. The kernel
# reads a relative target against the link's own directory, so validation
# either verified a different file (cwd = root) or changed its verdict when
# run from another directory. Absoluteness must follow the host: only a
# target starting with "/" is absolute on POSIX, and backslashes are literal.

DRIVE_RECORDED = b'{"val": 0.9}\n'  # root/C:x.jsonl, the recorded digest
DRIVE_KERNEL = b'{"val": 0.1}\n'  # <parents>/C:x.jsonl, what the link reads


def _drive_alias_layout(tmp_path: Path, target: str, parents: str = "sub") -> Path:
    root = tmp_path / "root"
    (root / parents).mkdir(parents=True)
    (root / "C:x.jsonl").write_bytes(DRIVE_RECORDED)
    (root / parents / "C:x.jsonl").write_bytes(DRIVE_KERNEL)
    (root / parents / "alias.jsonl").symlink_to(target)
    _write(
        root,
        _document(
            path=f"{parents}/alias.jsonl",
            sha256=_digest(DRIVE_RECORDED),
            size=len(DRIVE_RECORDED),
        ),
    )
    return root


def _verdict_from(root: Path, monkeypatch, cwd: Path) -> tuple[int, list[str]]:
    monkeypatch.chdir(cwd)
    report = run_checks(load_manifest(root))
    return report.exit_code(), [f.code.value for f in report.findings]


@skip_if_no_symlinks
@pytest.mark.parametrize(
    "target, expected",
    [
        # A literal relative name: the kernel opens <parents>/C:x.jsonl, whose
        # value (0.1) differs from the recorded one (0.9). Never a clean exit 0.
        ("C:x.jsonl", ["hash-mismatch", "result-value-mismatch", "claim-at-risk"]),
        # The same spellings as POSIX relative names: the file does not exist
        # under <parents>/, so the artifact is missing, not verified elsewhere.
        ("C:/x.jsonl", ["missing-file", "claim-at-risk"]),
        ("\\\\srv\\share\\x.jsonl", ["missing-file", "claim-at-risk"]),
    ],
)
@pytest.mark.parametrize("parents", ["sub", "a/b"])
def test_windows_shaped_alias_target_is_cwd_independent(
    tmp_path, monkeypatch, target, expected, parents
):
    root = _drive_alias_layout(tmp_path, target, parents)

    at_root = _verdict_from(root, monkeypatch, root)
    assert at_root == (1, expected)
    # Same verdict from an unrelated directory: nothing may be resolved
    # against the process cwd.
    assert _verdict_from(root, monkeypatch, tmp_path) == at_root


@skip_if_no_symlinks
@pytest.mark.parametrize("parents", ["sub", "a/b"])
def test_windows_shaped_alias_target_verifies_the_kernel_file(tmp_path, monkeypatch, parents):
    """A literal `C:x.jsonl` target reads <parents>/C:x.jsonl, like the kernel."""
    root = tmp_path / "root"
    (root / parents).mkdir(parents=True)
    (root / "C:x.jsonl").write_bytes(DRIVE_KERNEL)
    (root / parents / "C:x.jsonl").write_bytes(DRIVE_RECORDED)
    (root / parents / "alias.jsonl").symlink_to("C:x.jsonl")
    _write(
        root,
        _document(
            path=f"{parents}/alias.jsonl",
            sha256=_digest(DRIVE_RECORDED),
            size=len(DRIVE_RECORDED),
        ),
    )
    for cwd in (root, tmp_path):
        assert _verdict_from(root, monkeypatch, cwd) == (0, [])


@skip_if_no_symlinks
def test_windows_shaped_alias_target_without_a_locator_reads_the_kernel_file(
    tmp_path, monkeypatch
):
    """Control: the fixture discriminates, the kernel file is not the recorded one."""
    root = _drive_alias_layout(tmp_path, "C:x.jsonl")
    _write(
        root,
        _document(
            path="sub/alias.jsonl",
            sha256=_digest(DRIVE_RECORDED),
            size=len(DRIVE_RECORDED),
            locator=False,
        ),
    )
    for cwd in (root, tmp_path):
        exit_code, codes = _verdict_from(root, monkeypatch, cwd)
        assert exit_code == 1
        assert "hash-mismatch" in codes, codes
        assert codes == _verdict_from(root, monkeypatch, tmp_path)[1]


def test_absolute_parts_refuses_a_non_host_absolute_spelling(tmp_path):
    """`relpath` must never see a cwd-relative name."""
    for spelling in ("C:/x.jsonl", "C:x.jsonl", "\\\\srv\\share\\x.jsonl"):
        assert file_checks._absolute_parts(tmp_path, spelling) == (None, "refused-path")


@skip_if_no_symlinks
def test_resolve_alias_keeps_host_relative_targets_literal(tmp_path):
    root = tmp_path / "root"
    assert file_checks._resolve_alias(root, ("sub",), "C:x.jsonl") == (
        ("sub", "C:x.jsonl"),
        "ok",
    )
    # Backslashes are literal on POSIX: one component, not a UNC path.
    assert file_checks._resolve_alias(root, ("sub",), "\\\\srv\\share\\x.jsonl") == (
        ("sub", "\\\\srv\\share\\x.jsonl"),
        "ok",
    )
    # With '/' the same relative spelling is walked component by component.
    assert file_checks._resolve_alias(root, ("sub",), "C:/x.jsonl") == (
        ("sub", "C:", "x.jsonl"),
        "ok",
    )


@skip_if_no_symlinks
def test_drive_letter_artifact_path_is_cwd_independent(tmp_path, monkeypatch):
    """`C:/x.jsonl` is a literal `C:` directory inside the folder on POSIX."""
    root = tmp_path / "root"
    (root / "C:").mkdir(parents=True)
    (root / "C:" / "x.jsonl").write_bytes(DATA)
    _write(root, _document(path="C:/x.jsonl", sha256=_digest(DATA), size=len(DATA)))

    decoy = tmp_path / "decoy"
    (decoy / "C:").mkdir(parents=True)
    (decoy / "C:" / "x.jsonl").write_bytes(b'{"val": 0.1}\n')

    for cwd in (root, decoy):
        monkeypatch.chdir(cwd)
        report = run_checks(load_manifest(root))
        assert report.findings == []
        assert report.exit_code() == 0


@skip_if_no_symlinks
def test_drive_letter_artifact_path_does_not_read_the_cwd_decoy(tmp_path, monkeypatch):
    """With no `C:` folder in the manifest folder, the cwd copy is not used."""
    root = tmp_path / "root"
    root.mkdir()
    _write(root, _document(path="C:/x.jsonl", sha256=_digest(DATA), size=len(DATA)))

    decoy = tmp_path / "decoy"
    (decoy / "C:").mkdir(parents=True)
    (decoy / "C:" / "x.jsonl").write_bytes(DATA)

    for cwd in (root, decoy):
        assert _verdict_from(root, monkeypatch, cwd) == (1, ["missing-file", "claim-at-risk"])


def test_drive_relative_artifact_path_is_literal_and_cwd_independent(tmp_path, monkeypatch):
    """`C:x.jsonl` is drive-relative, not Windows-absolute: no warning, no cwd."""
    root = tmp_path / "root"
    root.mkdir()
    (root / "C:x.jsonl").write_bytes(DATA)
    _write(
        root,
        _document(path="C:x.jsonl", sha256=_digest(DATA), size=len(DATA), locator=False),
    )

    decoy = tmp_path / "decoy"
    decoy.mkdir()
    (decoy / "C:x.jsonl").write_bytes(b'{"val": 0.1}\n')

    for cwd in (root, decoy):
        report = _report_from(root, monkeypatch, cwd)
        assert report.exit_code() == 0
        assert "absolute-path" not in _codes(report)


def test_unc_artifact_path_verdict_is_cwd_independent(tmp_path, monkeypatch):
    """A UNC-shaped spelling is never a cwd-relative path either."""
    root = tmp_path / "root"
    root.mkdir()
    _write(root, _document(path="\\\\srv\\share\\x.jsonl", sha256=_digest(DATA), size=len(DATA)))

    at_root = _verdict_from(root, monkeypatch, root)
    assert at_root == (
        1,
        ["path-escapes-root", "claim-at-risk", "absolute-path", "non-posix-path"],
    )
    assert _verdict_from(root, monkeypatch, tmp_path) == at_root


def test_unc_artifact_spelling_is_normalised_and_never_read_literally(tmp_path, monkeypatch):
    r"""Artifact backslashes are normalised, so a UNC name is host-absolute.

    This is the artifact-side counterpart of the literal link-target rule: a
    file named `\\srv\share\x.jsonl` in the folder is not the file the
    normalised `//srv/share/x.jsonl` spelling names, and must not be read."""
    root = tmp_path / "root"
    root.mkdir()
    literal = "\\\\srv\\share\\x.jsonl"
    (root / literal).write_bytes(DATA)
    _write(root, _legacy_document(literal, DATA))

    at_root = _verdict_from(root, monkeypatch, root)
    assert at_root == (
        1,
        ["path-escapes-root", "absolute-path", "non-posix-path"],
    )
    assert _verdict_from(root, monkeypatch, tmp_path) == at_root


# --------------------------------------- legacy Windows-absolute diagnostics
#
# Round-6 blocker (Opus): after absoluteness followed the host, a Windows-
# written 0.1/0.2 manifest lost the `absolute-path` warning and the
# "(folder looks relocated)" hint. Resolution must stay host-native -- never
# Path(raw) or the cwd -- but the legacy diagnostics must still recognise a
# drive-root spelling.

LEGACY_DATA = b"a,b\n"


def _legacy_document(path: str, data: bytes) -> dict:
    """A 0.2 manifest: no locators, no results."""
    return {
        "schema_version": "0.2",
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "run", "name": "Run"}],
        "artifacts": [
            {
                "id": "a",
                "path": path,
                "sha256": _digest(data),
                "size_bytes": len(data),
            }
        ],
        "results": [],
        "relationships": [],
    }


def _report_from(root: Path, monkeypatch, cwd: Path):
    monkeypatch.chdir(cwd)
    return run_checks(load_manifest(root))


@pytest.mark.parametrize(
    "spelling, expected",
    [
        ("C:/Users/me/exp/data/train.csv", ["missing-file", "absolute-path"]),
        (
            "C:\\Users\\me\\exp\\data\\train.csv",
            ["missing-file", "absolute-path", "non-posix-path"],
        ),
    ],
)
def test_legacy_windows_absolute_keeps_the_old_diagnostics(
    tmp_path, monkeypatch, spelling, expected
):
    """The exact round-6 reproducer, at cwd=root and cwd=elsewhere."""
    root = tmp_path / "exp"
    (root / "data").mkdir(parents=True)
    (root / "data" / "train.csv").write_bytes(LEGACY_DATA)
    _write(root, _legacy_document(spelling, LEGACY_DATA))

    for cwd in (root, tmp_path):
        report = _report_from(root, monkeypatch, cwd)
        assert report.exit_code() == 1
        assert _codes(report) == expected
        missing = next(f for f in report.findings if f.code.value == "missing-file")
        assert missing.message == (
            f"Artifact 'a' not found at {spelling!r} (folder looks relocated)"
        )
        assert missing.hint == (
            "A file matching it exists at 'data/train.csv'; update the path to that."
        )
        warning = next(f for f in report.findings if f.code.value == "absolute-path")
        assert warning.message == (
            f"Absolute path breaks when the folder is moved or shared: {spelling!r}"
        )
        assert warning.hint == (
            "Store it relative to the manifest folder, e.g. 'data/train.csv'"
        )


def test_legacy_drive_spelling_with_no_relocated_file_does_not_escape(
    tmp_path, monkeypatch
):
    """Without a match the legacy warning stays, but nothing may escape."""
    root = tmp_path / "exp"
    root.mkdir()
    spelling = "C:/old/location/experiment/data/train.csv"
    _write(root, _legacy_document(spelling, LEGACY_DATA))

    for cwd in (root, tmp_path):
        report = _report_from(root, monkeypatch, cwd)
        assert report.exit_code() == 1
        # The old code reported path-escapes-root from /tmp because Path(raw)
        # was cwd-relative; the fix keeps one verdict from every cwd.
        assert _codes(report) == ["missing-file", "absolute-path"]
        missing = next(f for f in report.findings if f.code.value == "missing-file")
        assert missing.message == (
            f"Artifact 'a' is missing: {spelling!r} does not exist"
        )
        assert missing.hint is None
        warning = next(f for f in report.findings if f.code.value == "absolute-path")
        assert warning.hint == "Store paths relative to the manifest folder."


def test_targeted_drive_spelling_that_only_matches_a_suffix_fails_closed(
    tmp_path, monkeypatch
):
    """A 0.3 locator must not turn the legacy relocation hint into a read."""
    root = tmp_path / "root"
    root.mkdir()
    (root / "x.jsonl").write_bytes(DATA)
    _write(
        root,
        _document(
            path="C:/Users/me/exp/x.jsonl",
            sha256=_digest(DATA),
            size=len(DATA),
        ),
    )

    for cwd in (root, tmp_path):
        report = _report_from(root, monkeypatch, cwd)
        assert report.exit_code() == 1
        assert _codes(report) == ["missing-file", "claim-at-risk"]
        missing = next(f for f in report.findings if f.code.value == "missing-file")
        assert "(folder looks relocated)" not in missing.message
        assert "absolute-path" not in _codes(report)


# ------------------------------------ Windows-shaped targets with a real file
#
# The round-5 fixtures never created `<parents>/C:` or the literal UNC file,
# so they could only assert missing-file. With the kernel's file present, the
# same targets must verify (or report the mismatch) identically at both cwds;
# a blanket refusal would fail these.

WINDOWS_KERNEL_TARGETS = ["C:/x.jsonl", "\\\\srv\\share\\x.jsonl"]


def _kernel_target_file(parents_path: Path, target: str) -> Path:
    if "\\" in target:
        # Backslashes are literal filename bytes on POSIX: one component.
        return parents_path / target
    return parents_path.joinpath(*target.split("/"))


@skip_if_no_symlinks
@pytest.mark.parametrize("target", WINDOWS_KERNEL_TARGETS)
@pytest.mark.parametrize("parents", ["sub", "a/b"])
def test_windows_shaped_alias_target_verifies_a_present_kernel_file(
    tmp_path, monkeypatch, target, parents
):
    root = tmp_path / "root"
    (root / parents).mkdir(parents=True)
    recorded = b'{"val": 0.9}\n'
    kernel = _kernel_target_file(root / parents, target)
    kernel.parent.mkdir(parents=True, exist_ok=True)
    kernel.write_bytes(recorded)
    (root / parents / "alias.jsonl").symlink_to(target)
    _write(
        root,
        _document(
            path=f"{parents}/alias.jsonl",
            sha256=_digest(recorded),
            size=len(recorded),
        ),
    )

    for cwd in (root, tmp_path):
        assert _verdict_from(root, monkeypatch, cwd) == (0, [])


@skip_if_no_symlinks
@pytest.mark.parametrize("target", WINDOWS_KERNEL_TARGETS)
@pytest.mark.parametrize("parents", ["sub", "a/b"])
def test_windows_shaped_alias_target_reports_a_present_kernel_file_mismatch(
    tmp_path, monkeypatch, target, parents
):
    root = tmp_path / "root"
    (root / parents).mkdir(parents=True)
    recorded = b'{"val": 0.9}\n'
    kernel_bytes = b'{"val": 0.1}\n'
    kernel = _kernel_target_file(root / parents, target)
    kernel.parent.mkdir(parents=True, exist_ok=True)
    kernel.write_bytes(kernel_bytes)
    (root / parents / "alias.jsonl").symlink_to(target)
    _write(
        root,
        _document(
            path=f"{parents}/alias.jsonl",
            sha256=_digest(recorded),
            size=len(recorded),
        ),
    )

    expected = ["hash-mismatch", "result-value-mismatch", "claim-at-risk"]
    expected_verdict = (1, expected)
    assert _verdict_from(root, monkeypatch, root) == expected_verdict
    assert _verdict_from(root, monkeypatch, tmp_path) == expected_verdict


# ------------------------------------------------------------ link target shape


def test_resolve_alias_refuses_empty_target_components(tmp_path):
    """`_resolve_alias` must not silently drop an empty component."""
    root = tmp_path / "root"
    root.mkdir()
    for target in ("x.jsonl/", "sub//x.jsonl", "sub/", "", "./x.jsonl"):
        assert file_checks._resolve_alias(root, ("sub",), target) == (
            None,
            "refused-alias",
        ), target


@skip_if_no_symlinks
def test_doubled_separator_in_a_link_target_is_refused_before_it_verifies(tmp_path):
    """The kernel opens `sub//x.jsonl`; the walk still refuses the shape.

    Dropping the empty component would verify a file through a spelling the
    shape rules do not accept, so this fails closed with the link-target text."""
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "x.jsonl").write_bytes(DATA)
    (root / "alias.jsonl").symlink_to("sub//x.jsonl")
    _write(root, _document(path="alias.jsonl", sha256=_digest(DATA), size=len(DATA)))

    report = run_checks(load_manifest(root))
    assert report.exit_code() == 1
    assert _codes(report) == ["locator-unreadable", "claim-at-risk"]
    finding = next(f for f in report.findings if f.code.value == "locator-unreadable")
    assert finding.message == (
        "the artifact's symbolic link target is not verified; point the "
        "link at a plain path inside the folder"
    )


@skip_if_no_symlinks
def test_plain_absolute_spelling_is_not_blamed_for_a_component_it_lacks(
    tmp_path, monkeypatch
):
    """An outside alias to the root resolves inside but escapes by prefix.

    `_absolute_parts` reports escapes-root, not a bad shape, so the finding
    must be containment, never the empty/'.' component message."""
    base = tmp_path / "base"
    root = base / "root"
    root.mkdir(parents=True)
    other = base / "other"
    other.mkdir()
    (other / "link").symlink_to(root, target_is_directory=True)
    (root / "x.jsonl").write_bytes(DATA)
    spelling = str(other / "link" / "x.jsonl")
    _write(root, _document(path=spelling, sha256=_digest(DATA), size=len(DATA)))

    report = _report_from(root, monkeypatch, tmp_path)
    assert report.exit_code() == 1
    assert _codes(report) == ["path-escapes-root", "claim-at-risk", "absolute-path"]
    finding = next(f for f in report.findings if f.code.value == "path-escapes-root")
    assert "empty" not in finding.message
    assert "trailing-slash" not in finding.message


# ------------------------------------------------------------- dot components
#
# The docs and the shape message say a `.` component is refused, but
# `_parts_for` dropped it, so `./sub/./x.jsonl` verified. A trailing `/.` after
# a regular file is ENOTDIR for the kernel while a walk that dropped the `.`
# would open and verify the file. The targeted reader now refuses any `.`
# component, and the shape refusal precedes the extension/kind test so the
# kind message never names the wrong defect.

DOT_SHAPES = ["x.jsonl/.", "x.jsonl/", "./x.jsonl", "sub/./x.jsonl"]


@pytest.mark.parametrize("spelling", DOT_SHAPES)
def test_dot_component_is_refused_before_the_digest_is_accepted(tmp_path, spelling):
    root = tmp_path / "dot"
    (root / "sub").mkdir(parents=True)
    (root / "x.jsonl").write_bytes(DATA)
    (root / "sub" / "x.jsonl").write_bytes(DATA)
    _write(root, _document(path=spelling, sha256=_digest(DATA), size=len(DATA)))

    report = run_checks(load_manifest(root))
    assert report.exit_code() == 1
    # Only the shape refusal: no locator-kind-mismatch, and no accepted digest.
    assert _codes(report) == ["locator-unreadable", "claim-at-risk"]
    finding = next(f for f in report.findings if f.code.value == "locator-unreadable")
    assert finding.message == (
        "artifact path has an empty, '.' or trailing-slash component; write the plain file path"
    )
    assert "uses '..'" not in finding.message


def test_shape_refusal_precedes_a_string_locator_kind_test(tmp_path):
    """A string locator on a `.jsonl/.` spelling must not add kind-mismatch."""
    root = tmp_path / "string-kind"
    root.mkdir()
    (root / "m.jsonl").write_bytes(DATA)
    document = _document(path="m.jsonl/.", sha256=_digest(DATA), size=len(DATA))
    document["results"][0]["locator"] = "/val"
    _write(root, document)

    report = run_checks(load_manifest(root))
    assert report.exit_code() == 1
    assert _codes(report) == ["locator-unreadable", "claim-at-risk"]


def test_absolute_dot_component_is_refused(tmp_path):
    root = tmp_path / "abs-dot"
    root.mkdir()
    (root / "x.jsonl").write_bytes(DATA)
    spelling = str(root) + "/./x.jsonl"
    _write(root, _document(path=spelling, sha256=_digest(DATA), size=len(DATA)))

    report = run_checks(load_manifest(root))
    assert report.exit_code() == 1
    codes = _codes(report)
    assert "locator-unreadable" in codes, codes
    assert "locator-kind-mismatch" not in codes, codes
    assert codes == ["locator-unreadable", "claim-at-risk", "absolute-path"]


@pytest.mark.parametrize(
    "spelling", ["./x.jsonl", "sub/./x.jsonl", "x.jsonl/", "x.jsonl/."]
)
def test_dot_component_without_a_locator_still_hashes_the_kernel_file(tmp_path, spelling):
    """Legacy artifacts keep ordinary Path semantics; only the targeted reader refuses.

    `Path` drops a leading `./`, `.` components and a trailing slash, so a
    locator-less artifact of these spellings still opens and verifies its file
    (exit 0). The targeted reader is the one that refuses the spelling."""
    root = tmp_path / "dot-legacy"
    (root / "sub").mkdir(parents=True)
    (root / "x.jsonl").write_bytes(DATA)
    (root / "sub" / "x.jsonl").write_bytes(DATA)
    _write(
        root,
        _document(path=spelling, sha256=_digest(DATA), size=len(DATA), locator=False),
    )
    report = run_checks(load_manifest(root))
    assert report.exit_code() == 0, _codes(report)


@skip_if_no_symlinks
@pytest.mark.parametrize(
    "target, expected",
    [
        # Kernel-openable target, but its shape is not verified by the walk.
        ("./x.jsonl", ["locator-unreadable", "claim-at-risk"]),
        # The kernel itself cannot resolve `x.jsonl/.` or `x.jsonl/` (ENOTDIR)
        # or a missing `C:` component (ENOENT), so the pre-stat reports the
        # artifact missing. This is the kernel-unresolvable case, distinct from
        # the shape-refused `./x.jsonl` above.
        ("x.jsonl/.", ["missing-file", "claim-at-risk"]),
        ("x.jsonl/", ["missing-file", "claim-at-risk"]),
        ("C:/x.jsonl/.", ["missing-file", "claim-at-risk"]),
    ],
)
def test_dot_component_in_a_link_target_is_refused(tmp_path, target, expected):
    root = tmp_path / "alias-dot"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "x.jsonl").write_bytes(DATA)
    (root / "sub" / "alias.jsonl").symlink_to(target)
    _write(
        root,
        _document(path="sub/alias.jsonl", sha256=_digest(DATA), size=len(DATA)),
    )
    report = run_checks(load_manifest(root))
    assert report.exit_code() == 1
    assert _codes(report) == expected
    if expected[0] == "locator-unreadable":
        finding = next(f for f in report.findings if f.code.value == "locator-unreadable")
        assert finding.message == (
            "the artifact's symbolic link target is not verified; point the "
            "link at a plain path inside the folder"
        )

