import functools
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "generic-experiment"

# Six experimental R5 modules need Windows-only evidence prerequisites
# (R5_EVIDENCE_ROOT under LOCALAPPDATA/Temp). A module-level pytestmark is avoided
# because tests/test_experimental_staged_reader.py is SHA-256-pinned by the parser
# contract, and this hook stays deliberately basename-scoped to these modules.
R5_EVIDENCE_MODULES = frozenset({
    "test_experimental_versioned_manifest.py",
    "test_experimental_staged_reader.py",
    "test_experimental_source_authority_conflicts.py",
    "test_experimental_staged_validation.py",
    "test_experimental_legacy_writer_synthetic.py",
    "test_experimental_save_manifest_existing.py",
})
R5_SKIP_REASON = "R5 evidence root not configured (requires R5_EVIDENCE_ROOT under LOCALAPPDATA/Temp)"


def pytest_collection_modifyitems(config, items):
    if os.environ.get("R5_EVIDENCE_ROOT") and os.environ.get("LOCALAPPDATA"):
        return
    skip = pytest.mark.skip(reason=R5_SKIP_REASON)
    for item in items:
        if item.path.name not in R5_EVIDENCE_MODULES:
            continue
        if "scratch" in item.fixturenames:
            item.add_marker(skip)


# --- S2a facilities + private qualification seam test gate -------------------
#
# The S1 backend is shipped but RELEASE_QUALIFIED_FSTYPES stays empty, so
# native_scan_support().supported is deliberately False and production scans
# stay fail-closed. These helpers let the *internal* directory pipeline tests
# run on a Linux host whose backend facilities probe succeeds, using the
# private candidate-only policy that S1 already exposes. They never promote a
# filesystem and never change the public runtime policy.

DIRECTORY_NATIVE_PIPELINE_MODULES = frozenset({
    "test_directory_check_pipeline.py",
    "test_directory_checker.py",
    "test_directory_authoring.py",
    "test_directory_apply.py",
    "test_directory_preview.py",
    "test_directory_plan.py",
    "test_directory_audit.py",
    "test_directory_batch.py",
    "test_directory_present.py",
    "test_directory_cooperative_apply.py",
    "test_directory_review.py",
    "test_directory_review_path.py",
    "test_directory_writer_races.py",
    "test_directory_s2b_races.py",
})


@functools.lru_cache(maxsize=1)
def directory_native_gate() -> tuple:
    """Return ``(ready, reason)`` for the Windows backend or Linux facilities.

    Linux readiness is the POSIX facility probe, not the release allowlist:
    the calling tests enter the private candidate policy. The public
    ``native_scan_support().supported`` value remains False."""
    try:
        import runevidence.directory_scanner as ds
    except Exception as exc:  # noqa: BLE001 - collection must not fail
        return False, f"scanner import failed: {type(exc).__name__}: {exc}"
    if sys.platform == "win32":
        support = ds.native_scan_support()
        return support.supported, support.reason
    if not sys.platform.startswith("linux"):
        return False, "no native scanner on this platform"
    try:
        facilities = ds._posix_capabilities()
    except Exception as exc:  # noqa: BLE001 - a probe failure is a skip, not an error
        return False, f"native POSIX facilities unavailable: {type(exc).__name__}: {exc}"
    missing = [key for key in ds._POSIX_CAPABILITY_KEYS if not facilities.get(key)]
    if missing:
        return False, "native POSIX facilities unavailable: " + ", ".join(missing)
    return True, "ok"


def directory_native_root_gate_reason(path) -> str:
    """The ancestor-aware root-filesystem gate under the test candidate policy."""
    if not sys.platform.startswith("linux"):
        return ""
    import runevidence.directory_scanner as ds
    try:
        with ds._qualification_for_tests(ds.CANDIDATE_FSTYPES):
            ds._preflight_mounts(
                ds._root_components(os.fspath(path)), ds._SCAN_POLICY.get()
            )
    except ds.DirectoryScanError as exc:
        return f"native root filesystem gate: {exc.reason}: {exc}"
    except Exception as exc:  # noqa: BLE001 - a host gap skips, never errors setup
        return f"native root filesystem gate: {type(exc).__name__}: {exc}"
    return ""


def native_test(func):
    """Mark a test as native evidence and gate it on the S2a facilities+seam.

    The ``native`` marker is what the autouse seam fixture looks for; the
    skipif keeps non-native hosts reporting an explicit reason."""
    ready, reason = directory_native_gate()
    func = pytest.mark.native(func)
    skip = pytest.mark.skipif(not ready, reason=f"no native scanner here: {reason}")
    return skip(func)


def pytest_configure(config):
    config.addinivalue_line("markers", "native: native directory scanner evidence")


@pytest.fixture(autouse=True)
def _directory_native_pipeline_seam(request):
    """Run internal ``@native`` pipeline tests under the private S2a seam.

    Only the S2a integration modules and only tests carrying the ``native``
    marker are affected. The candidate policy is scoped with a context token
    and reset afterwards; the release policy is untouched."""
    if request.path.name not in DIRECTORY_NATIVE_PIPELINE_MODULES:
        yield
        return
    if request.node.get_closest_marker("native") is None:
        yield
        return
    ready, _ = directory_native_gate()
    if not ready:
        yield
        return
    if not sys.platform.startswith("linux"):
        yield
        return
    reason = directory_native_root_gate_reason(request.getfixturevalue("tmp_path"))
    if reason:
        pytest.skip(reason)
    import runevidence.directory_scanner as ds
    with ds._qualification_for_tests(ds.CANDIDATE_FSTYPES):
        yield


def make_dir_link(link: Path, target: Path) -> bool:
    """Create a directory link: a junction on Windows (no privilege needed),
    a symbolic link elsewhere. Returns False when the platform refuses."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        return False
    return True


def is_junction(path: Path) -> bool:
    """True when ``path`` is a Windows junction, using lstat metadata so the
    assertion also works on Python versions without os.path.isjunction."""
    if os.name != "nt":
        return False
    try:
        st = os.lstat(path)
    except OSError:
        return False
    tag = getattr(st, "st_reparse_tag", None)
    if tag is not None:
        return tag == stat.IO_REPARSE_TAG_MOUNT_POINT
    return bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def remove_dir_link(link: Path) -> None:
    """Remove a directory link without touching the target it points at."""
    if os.name == "nt":
        try:
            os.rmdir(link)
            return
        except OSError:
            subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True)
            return
    try:
        link.unlink()
    except FileNotFoundError:
        pass


@pytest.fixture
def experiment(tmp_path: Path) -> Path:
    """A fresh, writable copy of the generic example experiment.

    Tests mutate this copy (delete files, edit bytes, move the folder) and
    never touch the committed example.
    """
    dest = tmp_path / "experiment"
    shutil.copytree(EXAMPLE, dest)
    return dest
