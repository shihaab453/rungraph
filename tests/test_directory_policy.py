"""Tests for the internal, not-yet-connected directory policy."""
import pytest

from runevidence.checks.directory_policy import check_directory_policy
from runevidence.directory_hashing import DirectoryDigest
from runevidence.directory_scanner import DirectoryScan, DirectoryScanError
from runevidence.models import Artifact


def artifact(**changes):
    fields = dict(id="dataset", path="data", artifact_type="directory",
                  digest_format="runevidence-directory-v1", sha256="a" * 64, size_bytes=4)
    fields.update(changes)
    return Artifact(**fields)


def scan(digest="a" * 64, size=4, members=1):
    return DirectoryScan((), DirectoryDigest(digest, size, members))


def test_matching_and_mismatching_values():
    assert check_directory_policy(artifact(), scan()).status == "match"
    result = check_directory_policy(artifact(), scan(size=5))
    assert result.broken and result.digest_matches and result.size_matches is False
    assert check_directory_policy(artifact(), scan(digest="b" * 64)).broken
    assert check_directory_policy(artifact(), scan(members=0)).members == 0


@pytest.mark.parametrize("missing", [dict(sha256=None), dict(sha256=None, size_bytes=None)])
def test_missing_metadata_is_incomplete(missing):
    result = check_directory_policy(artifact(**missing), scan())
    assert result.status == "incomplete" and not result.broken


def test_optional_size_is_not_required_for_matching_digest():
    result = check_directory_policy(artifact(size_bytes=None), scan())
    assert result.status == "match" and result.size_matches is None


def test_wrong_size_without_digest_is_changed():
    result = check_directory_policy(artifact(sha256=None), scan(size=5))
    assert result.status == "changed" and result.broken
    assert result.digest_matches is None and result.size_matches is False


@pytest.mark.parametrize("reason", ["missing", "unsafe", "unreadable", "unstable", "unsupported-platform"])
def test_scan_errors_never_pass(reason):
    result = check_directory_policy(artifact(), DirectoryScanError(reason, "failure"))
    assert result.status == "scan_error" and result.broken and result.reason == reason


def test_volatile_requires_safe_root_check():
    result = check_directory_policy(artifact(volatile=True), None)
    assert result.status == "needs_root_check" and result.broken
    assert check_directory_policy(artifact(volatile=True), scan()).status == "needs_root_check"
    assert check_directory_policy(artifact(volatile=True), DirectoryScanError("unsafe", "link")).broken


def test_invalid_inputs_and_no_mutation():
    original = artifact()
    before = original.model_dump()
    check_directory_policy(original, scan())
    assert original.model_dump() == before
    with pytest.raises(ValueError):
        check_directory_policy(Artifact(id="file", path="file"), scan())
    with pytest.raises(TypeError):
        check_directory_policy(original, "not a scan")
