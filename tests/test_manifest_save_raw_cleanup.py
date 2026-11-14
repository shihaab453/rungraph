"""save_raw commit invariant: a committed replacement wins over cleanup failure.

These tests cover only the staged temp-file cleanup in
``runevidence.manifest.save_raw``. A cleanup error after ``os.replace`` has
succeeded must not turn a committed write into a reported failure.
"""

from __future__ import annotations

import errno
import json
import os
import stat
import tempfile
from pathlib import Path

import pytest

from runevidence import cli
from runevidence import manifest as manifest_module
from runevidence.manifest import dump_raw, save_raw


def _make_target(root: Path, data: dict) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    target = root / "runevidence.json"
    target.write_text(dump_raw(data), encoding="utf-8", newline="\n")
    return target


def _temps(target: Path) -> list[str]:
    return sorted(p.name for p in target.parent.glob(f".{target.name}.*.tmp"))


class _StagedCleanup:
    """Instrument only the Path instance save_raw builds for its staged temp.

    ``manifest.save_raw`` constructs its staged path with ``Path(fh.name)``;
    the target manifest is supplied by the caller as an ordinary Path. Swapping
    the module's ``Path`` binding therefore affects the staged temp only, not
    every unlink in the process.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[Path] = []
        self.error: OSError | None = None
        outer = self

        class StagedPath(type(Path())):
            def unlink(self, missing_ok: bool = False) -> None:
                outer.calls.append(self)
                if outer.error is not None:
                    raise outer.error
                return super().unlink(missing_ok=missing_ok)

        monkeypatch.setattr(manifest_module, "Path", StagedPath)


def _save_raw_without_committed_guard(data: dict, path: Path, *, expected_bytes: bytes) -> None:
    """The pre-fix save_raw body: finally always unlinks, with no guard."""
    text = dump_raw(data)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as fh:
            staged = manifest_module.Path(fh.name)
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if os.name != "nt":
            os.chmod(staged, stat.S_IMODE(path.stat().st_mode))
        if path.read_bytes() != expected_bytes:
            raise OSError("Manifest changed since it was loaded. Review the current manifest and retry.")
        os.replace(staged, path)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def test_save_raw_replaces_target_preserves_mode_and_leaves_no_temp(tmp_path):
    target = _make_target(tmp_path, {"state": "old"})
    if os.name != "nt":
        target.chmod(0o640)
    old = target.read_bytes()
    new = {"state": "new", "artifacts": []}

    assert save_raw(new, target, expected_bytes=old) is None

    assert target.read_bytes() == dump_raw(new).encode("utf-8")
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert _temps(target) == []


def test_cleanup_failure_after_replace_keeps_the_committed_write(tmp_path, monkeypatch):
    target = _make_target(tmp_path, {"state": "old"})
    old = target.read_bytes()
    new = {"state": "new"}
    staged = _StagedCleanup(monkeypatch)
    staged.error = OSError(errno.EIO, "simulated cleanup failure")

    assert save_raw(new, target, expected_bytes=old) is None

    assert target.read_bytes() == dump_raw(new).encode("utf-8")
    # After a successful rename no temp can remain. A leftover temp is only
    # possible when cleanup fails before the replacement is committed.
    assert len(staged.calls) == 1


@pytest.mark.parametrize("failure", ["stale_bytes", "replace_error"])
def test_failed_commit_with_failing_cleanup_raises_and_leaves_target(
    tmp_path, monkeypatch, failure
):
    target = _make_target(tmp_path, {"state": "old"})
    old = target.read_bytes()
    new = {"state": "new"}
    staged = _StagedCleanup(monkeypatch)
    staged.error = OSError(errno.EIO, "simulated cleanup failure")

    if failure == "stale_bytes":
        expected = b"not the bytes on disk"
    else:
        expected = old

        def broken_replace(src, dst):
            raise OSError(errno.EIO, "simulated replace failure")

        monkeypatch.setattr(manifest_module.os, "replace", broken_replace)

    with pytest.raises(OSError) as excinfo:
        save_raw(new, target, expected_bytes=expected)

    # Pre-commit, the surfacing error is the cleanup failure, not the primary
    # stale-bytes or replace error: an unconditional swallow would surface the
    # other message (or no error at all).
    assert excinfo.value.errno == errno.EIO
    assert "simulated cleanup failure" in str(excinfo.value)
    assert staged.calls  # the failing cleanup was attempted
    assert target.read_bytes() == old


def test_stale_expected_bytes_still_refuses_and_does_not_replace(tmp_path, monkeypatch):
    target = _make_target(tmp_path, {"state": "old"})
    old = target.read_bytes()
    replace_calls = []
    real_replace = manifest_module.os.replace

    def recording_replace(src, dst):
        replace_calls.append((src, dst))
        return real_replace(src, dst)

    monkeypatch.setattr(manifest_module.os, "replace", recording_replace)

    with pytest.raises(OSError) as excinfo:
        save_raw({"state": "new"}, target, expected_bytes=b"stale bytes")

    assert "Manifest changed since it was loaded" in str(excinfo.value)
    assert replace_calls == []
    assert target.read_bytes() == old
    assert _temps(target) == []


def test_cleanup_after_replace_runs_and_succeeds(tmp_path, monkeypatch):
    target = _make_target(tmp_path, {"state": "old"})
    old = target.read_bytes()
    new = {"state": "new"}
    staged = _StagedCleanup(monkeypatch)  # no injected error

    assert save_raw(new, target, expected_bytes=old) is None

    assert target.read_bytes() == dump_raw(new).encode("utf-8")
    assert len(staged.calls) == 1
    assert not staged.calls[0].exists()
    assert _temps(target) == []


def test_rehash_and_add_still_commit_through_save_raw(experiment, monkeypatch):
    metrics = experiment / "runs" / "tuned" / "metrics.json"
    metrics.write_text(
        json.dumps({"eval": {"split": "test", "accuracy": 0.75, "f1": 0.7}}, indent=2) + "\n",
        encoding="utf-8", newline="\n",
    )
    (experiment / "new.txt").write_text("new\n", encoding="utf-8", newline="\n")
    manifest_path = (experiment / "runevidence.json").resolve()

    calls = []
    real_save_raw = manifest_module.save_raw

    def spy(data, path, *, expected_bytes):
        calls.append(path)
        return real_save_raw(data, path, expected_bytes=expected_bytes)

    monkeypatch.setattr(cli, "save_raw", spy)

    assert cli.main(["rehash", str(experiment), "tuned-metrics"]) == 0
    monkeypatch.chdir(experiment)
    assert cli.main(["add", ".", "new.txt"]) == 0

    assert calls == [manifest_path, manifest_path]
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "new.txt" in {a["path"] for a in doc["artifacts"]}
    assert any(
        r["id"] == "tuned-accuracy" and r["value"] == 0.75 for r in doc["results"]
    )


def test_negative_control_old_finally_would_have_raised_after_replace(tmp_path, monkeypatch):
    new = {"state": "new"}
    staged = _StagedCleanup(monkeypatch)
    staged.error = OSError(errno.EIO, "simulated cleanup failure")

    current = _make_target(tmp_path / "current", {"state": "old"})
    old = current.read_bytes()
    assert save_raw(new, current, expected_bytes=old) is None
    assert current.read_bytes() == dump_raw(new).encode("utf-8")

    legacy = _make_target(tmp_path / "legacy", {"state": "old"})
    legacy_old = legacy.read_bytes()
    with pytest.raises(OSError):
        _save_raw_without_committed_guard(new, legacy, expected_bytes=legacy_old)
    # The old code committed the replacement but still reported failure.
    assert legacy.read_bytes() == dump_raw(new).encode("utf-8")
