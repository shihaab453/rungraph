"""Seeded fuzz and oracle differential for the strict CSV profile.

The oracle is the stdlib ``csv`` reader, used only on documents this profile
accepts (it is more permissive, so it must never judge a refusal). Fuzzing
asserts the closed refusal vocabulary, cell equality with the oracle, and a
checker pass whose findings never contain cell or header text.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import random
import tempfile
from pathlib import Path

import pytest

from runevidence.checks import run_checks
from runevidence.checks.csv import (
    CsvColumnOutOfRange,
    CsvNotNumeric,
    CsvNumberOverflow,
    CsvProfile,
    CsvProfileError,
    CsvRowOutOfRange,
    parse_number,
)
from runevidence.manifest import load_manifest

SEEDS = (0, 1, 2)
DOCS_PER_SEED = 80
ALPHABET = ("a", "b", " ", "é", "0", "1", "x-y")


def _quote(field: str) -> str:
    # An empty field is quoted: a bare empty line is a blank record, while
    # `""` is the empty cell under this profile.
    if field == "" or any(ch in field for ch in ',"\r\n'):
        return '"' + field.replace('"', '""') + '"'
    return field


def _document(rng: random.Random) -> tuple[str, list[list[str]]]:
    columns = rng.randint(1, 4)
    header = []
    while len(header) < columns:
        cell = "".join(rng.choice(ALPHABET) for _ in range(rng.randint(1, 4)))
        if cell not in header:
            header.append(cell)
    rows = []
    for _ in range(rng.randint(1, 6)):
        row = []
        for _ in range(columns):
            if rng.random() < 0.5:
                value = rng.choice(("0", "1", "-2", "3.5", "1e3", "-Infinity", "NaN"))
            else:
                value = "".join(rng.choice(ALPHABET) for _ in range(rng.randint(0, 4)))
            row.append(value)
        rows.append(row)
    newline = "\r\n" if rng.random() < 0.5 else "\n"
    lines = [",".join(_quote(cell) for cell in header)]
    lines += [",".join(_quote(cell) for cell in row) for row in rows]
    text = newline.join(lines) + (newline if rng.random() < 0.8 else "")
    return text, [header] + rows


def _malformed(rng: random.Random) -> bytes:
    return rng.choice(
        (
            b"a,b\n1,2\n\n",
            b"a,b\n1\n",
            b"a\x00,b\n1,2\n",
            b"a,b\r1,2\n",
            b'a,b\n"1,2\n',
            b'a,b\n"1"x,2\n',
            b"",
            b"a,b\r\n",
        )
    )


@pytest.mark.parametrize("seed", SEEDS)
def test_profile_matches_the_oracle_on_accepted_documents(seed):
    rng = random.Random(seed)
    for _ in range(DOCS_PER_SEED):
        text, expected = _document(rng)
        data = text.encode("utf-8")
        profile = CsvProfile(data, rows=range(1, len(expected)))
        assert list(profile.header) == expected[0]
        oracle = list(csv.reader(io.StringIO(text, newline="")))
        assert [list(row) for row in oracle] == expected
        for row_index, row in enumerate(expected[1:], start=1):
            for column_index, cell in enumerate(row, start=1):
                actual = profile.cell(row_index, column_index)
                assert actual == oracle[row_index][column_index - 1]
                expected_number = parse_number(actual)
                if expected_number is not None:
                    actual_number = profile.number(row_index, column_index)
                    if isinstance(expected_number, float) and expected_number != expected_number:
                        assert actual_number != actual_number  # NaN compares unequal
                    else:
                        assert actual_number == expected_number


@pytest.mark.parametrize("seed", SEEDS)
def test_profile_refuses_only_with_the_closed_vocabulary(seed):
    rng = random.Random(seed + 100)
    reasons = set()
    for _ in range(DOCS_PER_SEED):
        try:
            CsvProfile(_malformed(rng))
        except CsvProfileError as exc:
            reasons.add(exc.reason)
    assert reasons <= {
        "file-over-cap", "nul-byte", "invalid-utf8", "bare-cr", "empty-file",
        "blank-record", "quote-in-unquoted-field", "unclosed-quote",
        "data-after-closing-quote", "ragged-record", "header-only", "header-empty",
        "header-duplicate", "columns-over-cap", "record-over-cap", "field-over-cap",
        "rows-over-cap", "cells-over-cap",
    }


def test_selection_and_number_failures_are_typed():
    profile = CsvProfile(b"h\nabc\n")
    with pytest.raises(CsvRowOutOfRange):
        profile.cell(2, 1)
    with pytest.raises(CsvColumnOutOfRange):
        profile.cell(1, 2)
    with pytest.raises(CsvNotNumeric):
        profile.number(1, 1)
    assert CsvProfile(b"h\n1e999\n").number  # parse path is covered below
    with pytest.raises(CsvNumberOverflow):
        CsvProfile(b"h\n1e999\n").number(1, 1)


def _manifest_with_csv(root: Path, data: bytes, locators: list[dict]) -> None:
    (root / "metrics.csv").write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    results = [
        {
            "id": f"r{i}",
            "run_id": "run",
            "name": "metric",
            "value": 1,
            "artifact_id": "a",
            "locator": locator,
        }
        for i, locator in enumerate(locators)
    ]
    document = {
        "schema_version": "0.4",
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "run", "name": "Run"}],
        "artifacts": [
            {"id": "a", "path": "metrics.csv", "sha256": digest,
             "size_bytes": len(data), "kind": "dataset"}
        ],
        "results": results,
        "evidence": [],
    }
    (root / "runevidence.json").write_text(json.dumps(document), encoding="utf-8")


def test_checker_fuzz_findings_stay_in_the_closed_set_and_never_echo_cells(tmp_path):
    rng = random.Random("checker-fuzz")
    codes = {
        "locator-mismatch", "locator-unreadable", "locator-unsupported-value",
        "locator-no-digest", "locator-unverified", "locator-kind-mismatch",
        "result-value-mismatch", "directory-locator-unsupported",
    }
    text = "h0,h1,h2\n0,1,2\n3,4,5\n"
    data = text.encode("utf-8")
    header = ("h0", "h1", "h2")
    for _ in range(40):
        locators = []
        for _ in range(rng.randint(1, 5)):
            locators.append({
                "kind": "csv-cell",
                "row": rng.randint(1, 4),
                "column": rng.randint(1, 4),
                "header": rng.choice(header + ("nope",)),
            })
        folder = tmp_path / f"case{rng.random():.8f}"
        folder.mkdir()
        _manifest_with_csv(folder, data, locators)
        report = run_checks(load_manifest(folder / "runevidence.json"))
        assert {finding.code.value for finding in report.findings} <= codes
        for finding in report.findings:
            for token in ("h0", "h1", "h2", "nope", "0,1,2", "3,4,5"):
                assert token not in finding.message
            assert finding.hint is None or "h0" not in finding.hint
