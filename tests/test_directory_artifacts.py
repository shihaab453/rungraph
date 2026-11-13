"""Directory artifact foundation: the pure v1 encoder and fixed vectors.

These tests cover in-memory records only. Nothing here walks a real folder,
so they say nothing about symlinks, junctions, special files or concurrent
changes on disk; that is the job of a future scanner and its native tests.
"""

from __future__ import annotations

import ast
import hashlib
import itertools
import json
import struct
from pathlib import Path

import pytest

import runevidence.directory_hashing as dh
from runevidence.checks import run_checks
from runevidence.directory_hashing import (
    DEFAULT_LIMITS,
    HEADER,
    DirectoryLimits,
    DirectoryRecord,
    DirectoryRecordError,
    check_member_path,
    directory_digest,
    encode_directory,
)
from runevidence.manifest import load_manifest

VECTORS_PATH = Path(__file__).parent / "data" / "directory_digest_vectors.json"
# Recorded and independently checked outside this repository. A changed hash
# means someone re-recorded the vectors; never do that to make a test pass.
VECTORS_SHA256 = "cbdaf1e07db967f1083c8fa94cf1e293911a31f0a301e8e488ac874626b932f1"
VECTORS = json.loads(VECTORS_PATH.read_bytes().decode("utf-8"))["vectors"]


def d(path: str) -> DirectoryRecord:
    return DirectoryRecord("D", path)


def f(path: str, content: bytes = b"") -> DirectoryRecord:
    return DirectoryRecord("F", path, hashlib.sha256(content).digest(), len(content))


def frame(kind: bytes, path: bytes, payload: bytes = b"") -> bytes:
    return kind + struct.pack(">I", len(path)) + path + payload


def stream(*frames: bytes) -> bytes:
    return HEADER + struct.pack(">Q", len(frames)) + b"".join(frames)


def records_from_layout(layout: list) -> list[DirectoryRecord]:
    out = []
    for entry in layout:
        if entry["type"] == "D":
            out.append(d(entry["path"]))
        else:
            out.append(f(entry["path"], bytes.fromhex(entry["content_hex"])))
    return out


def reason(records, limits: DirectoryLimits = DEFAULT_LIMITS) -> str:
    with pytest.raises(DirectoryRecordError) as excinfo:
        encode_directory(records, limits)
    return excinfo.value.reason


# --- fixed vectors ----------------------------------------------------------


def test_vector_file_is_the_independently_checked_copy():
    assert hashlib.sha256(VECTORS_PATH.read_bytes()).hexdigest() == VECTORS_SHA256
    assert [v["name"] for v in VECTORS] == ["empty", "one-file", "nested-and-empty"]


@pytest.mark.parametrize("vector", VECTORS, ids=lambda v: v["name"])
def test_vector_digest_is_the_sha256_of_its_recorded_bytes(vector):
    # Checks the vector file against itself, without the encoder under test.
    assert hashlib.sha256(bytes.fromhex(vector["encoded_hex"])).hexdigest() == vector["sha256"]


@pytest.mark.parametrize("vector", VECTORS, ids=lambda v: v["name"])
def test_encoder_reproduces_each_vector_exactly(vector):
    records = records_from_layout(vector["layout"])

    assert encode_directory(records) == bytes.fromhex(vector["encoded_hex"])
    result = directory_digest(records)
    assert result.sha256 == vector["sha256"]
    assert result.size_bytes == vector["size_bytes"]
    assert result.members == len(records)


def test_vectors_match_the_written_byte_layout():
    # The same three trees, built straight from the format description.
    by_name = {v["name"]: bytes.fromhex(v["encoded_hex"]) for v in VECTORS}
    assert by_name["empty"] == b"RunEvidence.directory\x00v1\x00" + b"\x00" * 8
    assert by_name["one-file"] == stream(frame(b"F", b"a", hashlib.sha256(b"hello").digest()))
    assert by_name["nested-and-empty"] == stream(
        frame(b"D", b"empty"),
        frame(b"D", b"n"),
        frame(b"F", b"n/x.bin", hashlib.sha256(bytes([0, 1, 2, 255])).digest()),
    )


# --- ordering and sensitivity -----------------------------------------------


def test_record_order_does_not_change_the_bytes():
    records = records_from_layout(VECTORS[2]["layout"])
    expected = encode_directory(records)

    for order in itertools.permutations(records):
        assert encode_directory(order) == expected


def test_records_sort_by_raw_path_bytes_not_by_case_or_locale():
    # '-' 0x2d < '.' 0x2e < '/' 0x2f, and 'Z' 0x5a < '_' 0x5f < 'a' 0x61.
    records = [d("a"), f("a/b"), f("a.b"), f("a-b"), f("_"), f("Z")]
    empty = hashlib.sha256(b"").digest()

    assert encode_directory(records) == stream(
        frame(b"F", b"Z", empty),
        frame(b"F", b"_", empty),
        frame(b"D", b"a"),
        frame(b"F", b"a-b", empty),
        frame(b"F", b"a.b", empty),
        frame(b"F", b"a/b", empty),
    )


def test_content_path_kind_and_empty_folders_all_change_the_digest():
    base = directory_digest([d("n"), f("n/x", b"1")]).sha256
    variants = [
        [d("n"), f("n/x", b"2")],  # content
        [d("n"), f("n/y", b"1")],  # file name
        [d("m"), f("m/x", b"1")],  # folder name
        [d("n"), f("n/x", b"1"), d("empty")],  # added empty folder
        [d("n"), d("n/x")],  # file became a folder
        [d("n")],  # file removed
    ]
    digests = {directory_digest(v).sha256 for v in variants}

    assert base not in digests
    assert len(digests) == len(variants)


def test_ascii_case_is_preserved_in_the_bytes():
    assert directory_digest([f("A")]).sha256 != directory_digest([f("a")]).sha256


def test_directory_digest_is_separated_from_plain_file_digests():
    one = directory_digest([f("a", b"hello")])

    assert one.sha256 != hashlib.sha256(b"hello").hexdigest()
    assert directory_digest([]).sha256 != hashlib.sha256(b"").hexdigest()
    assert encode_directory([]).startswith(HEADER)
    assert HEADER == b"RunEvidence.directory\x00v1\x00"


def test_digest_reports_lowercase_hex_size_and_member_count():
    result = directory_digest([d("x"), f("x/a", b"abc"), f("b", b"de"), d("e")])

    assert result.sha256 == result.sha256.lower() and len(result.sha256) == 64
    assert result.size_bytes == 5
    assert result.members == 4


# --- names, collisions and parents -----------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "", "/a", "a/", "a//b", ".", "..", "./a", "a/./b", "a/../b",
        "a\\b", "C:/a", "a:b", "a<b", "a>b", 'a"b', "a|b", "a?b", "a*b",
        "a.", "a ", "sub/b.", "a\x00b", "a\tb", "a\nb", "a\x7fb",
        "caf\u00e9", "cafe\u0301", "\u212a", "a\ud800",
    ],
)
def test_names_outside_the_ascii_profile_are_rejected(path):
    assert reason([f(path)]) == "invalid-name"


@pytest.mark.parametrize("path", ["CON", "con.txt", "prn", "Aux.tar.gz", "nul", "COM1", "lpt9.log"])
def test_windows_device_names_are_rejected_at_any_depth(path):
    assert reason([f(path)]) == "reserved-name"
    assert reason([d("sub"), f("sub/" + path)]) == "reserved-name"


@pytest.mark.parametrize(
    "path",
    [
        ".git", ".GIT", ".hg", ".svn", ".venv", "venv", "__pycache__",
        "runevidence.json", "RunEvidence.JSON", "runevidence.json.bak", "runevidence.json.bak.12",
    ],
)
def test_protected_names_are_rejected_at_any_depth(path):
    assert reason([d(path)]) == "protected-name"
    assert reason([d("deep"), d("deep/er"), f("deep/er/" + path)]) == "protected-name"


@pytest.mark.parametrize(
    "path",
    ["com0", "console", "lpt", "conx.txt", ".gitignore", ".env", "venv2", "runevidence.json.bak.0",
     " leading-space", "a b", "~tilde"],
)
def test_ordinary_names_near_the_rules_are_accepted(path):
    assert check_member_path(path) == path.encode("ascii")
    encode_directory([f(path)])


def test_exact_duplicate_paths_collide():
    assert reason([f("a"), f("a", b"other")]) == "path-collision"
    assert reason([d("a"), f("a")]) == "path-collision"


def test_paths_differing_only_in_ascii_case_collide_on_every_platform():
    assert reason([f("Model.bin"), f("model.bin")]) == "path-collision"
    assert reason([d("data"), d("Data")]) == "path-collision"
    assert reason([d("x"), f("x/A"), f("x/a")]) == "path-collision"


def test_a_file_cannot_be_a_parent():
    assert reason([f("a"), f("a/b")]) == "path-collision"


def test_every_parent_folder_needs_its_own_record():
    assert reason([f("n/x.bin")]) == "missing-parent"
    assert reason([d("a"), f("a/b/c")]) == "missing-parent"
    # The parent must match exactly, not just ignoring case.
    assert reason([d("N"), f("n/x")]) == "missing-parent"


# --- record shape -----------------------------------------------------------


@pytest.mark.parametrize(
    "record",
    [
        DirectoryRecord("d", "a"),
        DirectoryRecord("X", "a"),
        DirectoryRecord("D", "a", b"\x00" * 32),
        DirectoryRecord("D", "a", b"", 1),
        DirectoryRecord("F", "a"),
        DirectoryRecord("F", "a", b"\x00" * 31),
        DirectoryRecord("F", "a", b"\x00" * 33),
        DirectoryRecord("F", "a", "00" * 32),  # hex text, not raw bytes
        DirectoryRecord("F", "a", b"\x00" * 32, -1),
        DirectoryRecord("F", "a", b"\x00" * 32, True),
        DirectoryRecord("F", "a", b"\x00" * 32, 1.0),
    ],
)
def test_malformed_records_are_rejected(record):
    assert reason([record]) == "invalid-record"


def test_non_record_input_is_rejected():
    assert reason([("F", "a", b"\x00" * 32)]) == "invalid-record"


# --- limits -----------------------------------------------------------------


def test_default_limits_are_the_fixed_v1_bounds():
    assert DEFAULT_LIMITS == DirectoryLimits(
        depth=64,
        members=10_000,
        total_file_bytes=1_073_741_824,
        file_bytes=268_435_456,
        path_bytes=1024,
        total_path_bytes=4_194_304,
        encoded_bytes=8_388_608,
    )


@pytest.mark.parametrize("field", ["depth", "members", "file_bytes", "encoded_bytes"])
def test_limits_can_be_tightened_but_never_raised(field):
    default = getattr(DEFAULT_LIMITS, field)
    DirectoryLimits(**{field: default - 1})
    for bad in (default + 1, -1, True, float(default)):
        with pytest.raises(ValueError):
            DirectoryLimits(**{field: bad})


def test_member_limit():
    assert reason([d("a"), d("b"), d("c")], DirectoryLimits(members=2)) == "limit-exceeded"
    many = [d(f"m{i:05d}") for i in range(10_001)]
    assert directory_digest(many[:10_000]).members == 10_000
    assert reason(many) == "limit-exceeded"


def test_depth_limit():
    chain = ["/".join(["a"] * n) for n in range(1, 66)]
    assert directory_digest([d(p) for p in chain[:64]]).members == 64
    assert reason([d(p) for p in chain]) == "limit-exceeded"


def test_path_length_limits():
    assert check_member_path("a" * 1024)
    encode_directory([f("a" * 1024)])
    assert reason([f("a" * 1025)]) == "limit-exceeded"
    assert reason([f("ab"), f("cd")], DirectoryLimits(total_path_bytes=3)) == "limit-exceeded"


def test_file_size_limits_use_the_recorded_sizes():
    digest = b"\x00" * 32
    at_cap = DirectoryRecord("F", "a", digest, DEFAULT_LIMITS.file_bytes)
    assert directory_digest([at_cap]).size_bytes == DEFAULT_LIMITS.file_bytes
    assert reason([DirectoryRecord("F", "a", digest, DEFAULT_LIMITS.file_bytes + 1)]) == "limit-exceeded"
    four = [DirectoryRecord("F", f"f{i}", digest, DEFAULT_LIMITS.file_bytes) for i in range(4)]
    assert directory_digest(four).size_bytes == DEFAULT_LIMITS.total_file_bytes
    assert reason(four + [f("extra", b"x")]) == "limit-exceeded"


def test_encoded_length_limit_counts_header_and_count():
    assert len(encode_directory([])) == 33
    assert encode_directory([], DirectoryLimits(encoded_bytes=33))
    assert reason([], DirectoryLimits(encoded_bytes=32)) == "limit-exceeded"


# --- scope guards -----------------------------------------------------------


def test_encoder_module_imports_nothing_that_touches_the_filesystem():
    tree = ast.parse(Path(dh.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add("." * node.level + (node.module or ""))
    assert imported == {"__future__", "hashlib", "re", "struct", "dataclasses", "typing", ".models"}
    calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "open" not in calls


def test_validate_still_treats_a_directory_record_as_an_unsupported_file(experiment):
    # Deliberately incomplete: the model accepts a directory record, but no
    # checker reads it yet, so validation reports the path as not a file.
    doc = json.loads((experiment / "runevidence.json").read_text(encoding="utf-8"))
    doc["artifacts"].append(
        {
            "id": "data-folder",
            "path": "data",
            "artifact_type": "directory",
            "digest_format": "runevidence-directory-v1",
        }
    )
    (experiment / "runevidence.json").write_text(json.dumps(doc), encoding="utf-8")

    findings = run_checks(load_manifest(experiment)).findings

    assert [x.code.value for x in findings if x.subject == "data-folder"] == ["not-a-file"]
