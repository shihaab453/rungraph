"""0.4 ``csv-cell`` display and privacy (slice C phase 4b).

The RFC (section 9) renders a csv-cell locator as ``csv-cell:<row>,<column>``:
numbers only, never the recorded ``header``. Human run/claim/lineage output
must therefore use a display copy of each target/node ``details`` mapping,
while ``--json`` keeps the structured locator. The data dictionaries are never
mutated, and 0.3 human output stays byte-for-byte unchanged.
"""

from __future__ import annotations

import copy
import hashlib
import json

from runevidence.cli import main
from runevidence.manifest import dump_manifest
from runevidence.models import (
    Artifact,
    CsvCellLocator,
    EvidenceReference,
    JsonlLocator,
    Manifest,
    Manifest04,
    Project,
    Result,
    Result04,
    Run,
)
from runevidence.run_claim import (
    _display_details,
    _display_row,
    claim_details,
    format_claim_details,
)
from runevidence.run_details import format_run_details, run_details
from runevidence.run_lineage import claim_lineage, format_claim_lineage

MANIFEST_PATH = "exp/runevidence.json"
# Distinct from every name, value, ID and path used by the fixtures below.
HEADER_TOKEN = "HDR_9f3c2b7d"


def manifest_04() -> Manifest04:
    return Manifest04(
        schema_version="0.4",
        project=Project(id="proj", name="Proj"),
        runs=[Run(id="run", name="Run")],
        artifacts=[
            Artifact(id="art", path="metrics.csv", sha256="a" * 64, size_bytes=21)
        ],
        results=[
            Result04(
                id="res",
                run_id="run",
                name="score",
                value=0.5,
                artifact_id="art",
                locator=CsvCellLocator(
                    kind="csv-cell", row=1, column=2, header=HEADER_TOKEN
                ),
            )
        ],
        evidence=[EvidenceReference(id="claim", claim="claim text", supports=["res"])],
    )


def manifest_03() -> Manifest:
    return Manifest(
        schema_version="0.3",
        project=Project(id="proj", name="Proj"),
        runs=[Run(id="run", name="Run")],
        artifacts=[
            Artifact(id="json", path="metrics.json", sha256="a" * 64, size_bytes=10),
            Artifact(id="jsonl", path="metrics.jsonl", sha256="b" * 64, size_bytes=20),
        ],
        results=[
            Result(
                id="str",
                run_id="run",
                name="score",
                value=0.5,
                artifact_id="json",
                locator="/val",
            ),
            Result(
                id="obj",
                run_id="run",
                name="loss",
                value=0.25,
                artifact_id="jsonl",
                locator=JsonlLocator(kind="jsonl", line=2, pointer="/x"),
            ),
        ],
        evidence=[
            EvidenceReference(id="claim", claim="claim text", supports=["str", "obj"])
        ],
    )


def human_04() -> tuple[dict, dict, dict]:
    manifest = manifest_04()
    return (
        run_details(manifest, "run", manifest_path=MANIFEST_PATH),
        claim_details(manifest, "claim", manifest_path=MANIFEST_PATH),
        claim_lineage(manifest, "claim", manifest_path=MANIFEST_PATH),
    )


def write_experiment(tmp_path, manifest) -> str:
    folder = tmp_path / "exp"
    folder.mkdir()
    (folder / "runevidence.json").write_text(
        dump_manifest(manifest), encoding="utf-8", newline="\n"
    )
    return str(folder)


def call(argv, capsys) -> tuple[int, str]:
    code = main(argv)
    captured = capsys.readouterr()
    assert captured.err == ""
    return code, captured.out


def test_header_token_is_distinct_from_all_names_values_and_paths():
    manifest = manifest_04()
    haystack = " ".join(
        [
            manifest.project.id,
            manifest.project.name,
            manifest.runs[0].id,
            manifest.runs[0].name,
            manifest.artifacts[0].id,
            manifest.artifacts[0].path,
            manifest.results[0].id,
            manifest.results[0].name,
            repr(manifest.results[0].value),
            manifest.evidence[0].id,
            manifest.evidence[0].claim,
            MANIFEST_PATH,
        ]
    )
    assert HEADER_TOKEN not in haystack


def test_human_run_claim_and_lineage_hide_the_header_and_json_keeps_it(
    tmp_path, capsys
):
    folder = write_experiment(tmp_path, manifest_04())
    for route, identifier in (("run", "run"), ("claim", "claim"), ("lineage", "claim")):
        code, text = call([route, folder, "--id", identifier], capsys)
        assert code == 0
        assert HEADER_TOKEN not in text
        assert "csv-cell:1,2" in text
        code, payload = call([route, folder, "--id", identifier, "--json"], capsys)
        assert code == 0
        assert HEADER_TOKEN in payload


def test_lineage_roots_and_nodes_still_record_the_header_for_json():
    _, _, lineage = human_04()
    root = lineage["roots"]["supports"][0]
    assert root["details"]["locator"] == {
        "kind": "csv-cell",
        "row": 1,
        "column": 2,
        "header": HEADER_TOKEN,
    }
    node = next(item for item in lineage["nodes"] if item["id"] == "res")
    assert node["details"]["locator"]["header"] == HEADER_TOKEN
    # Human lineage prints node details and never the root details; the
    # display copy used for both must replace the locator and leave the
    # recorded rows untouched.
    for row in (root, node):
        assert _display_row(row)["details"]["locator"] == "csv-cell:1,2"
        assert row["details"]["locator"]["header"] == HEADER_TOKEN
    text = format_claim_lineage(lineage)
    assert HEADER_TOKEN not in text
    assert "csv-cell:1,2" in text


def test_display_details_replaces_dict_and_model_csv_locators_only():
    model = CsvCellLocator(kind="csv-cell", row=1, column=2, header=HEADER_TOKEN)
    model_details = {"name": "score", "locator": model}
    displayed = _display_details(model_details)
    assert displayed == {"name": "score", "locator": "csv-cell:1,2"}
    assert model_details["locator"] is model  # the recorded data is untouched

    dict_details = {
        "name": "score",
        "locator": {"kind": "csv-cell", "row": 1, "column": 2, "header": HEADER_TOKEN},
    }
    assert _display_details(dict_details) == {
        "name": "score",
        "locator": "csv-cell:1,2",
    }
    assert dict_details["locator"]["header"] == HEADER_TOKEN

    for unchanged in (
        {"locator": "/val"},
        {"locator": {"kind": "jsonl", "line": 2, "pointer": "/x"}},
        {"name": "no locator"},
    ):
        assert _display_details(unchanged) is unchanged


def test_human_formatters_do_not_mutate_the_recorded_data():
    run_data, claim_data, lineage = human_04()
    for data, formatter in (
        (run_data, format_run_details),
        (claim_data, format_claim_details),
        (lineage, format_claim_lineage),
    ):
        before = copy.deepcopy(data)
        formatter(data)
        assert data == before
        assert HEADER_TOKEN in json.dumps(data)


# Exact SHA-256 pins of the delivered 0.3 human bytes for the fixture above.
# Only csv-cell locators are rewritten for 0.4; string and jsonl locators must
# keep this output byte-identical.
GOLDEN_03_HUMAN = {
    "run": "5737ef6559a50e2ec6585f0f926c797675890c58fcfa1095c8edf36b6d8a0851",
    "claim": "4cf307f41866f166c80ba4cb36fb2d38e82eed4b6aece6a73bb9cbc2f9d70d3e",
    "lineage": "9aabd7057554e913238f438ce295934cf60688f852f1f549f026c6d19500caba",
}


def test_03_human_output_bytes_are_unchanged():
    manifest = manifest_03()
    rendered = {
        "run": format_run_details(
            run_details(manifest, "run", manifest_path=MANIFEST_PATH)
        ),
        "claim": format_claim_details(
            claim_details(manifest, "claim", manifest_path=MANIFEST_PATH)
        ),
        "lineage": format_claim_lineage(
            claim_lineage(manifest, "claim", manifest_path=MANIFEST_PATH)
        ),
    }
    for name, text in rendered.items():
        assert hashlib.sha256(text.encode("utf-8")).hexdigest() == GOLDEN_03_HUMAN[name]
    # Both 0.3 locator spellings remain visible exactly as delivered.
    assert '"json#/val"' in rendered["run"]
    assert '"jsonl:2#/x"' in rendered["run"]
    assert '"locator": "/val"' in rendered["claim"]
    assert '"locator": {"kind": "jsonl", "line": 2, "pointer": "/x"}' in rendered["claim"]
    assert "csv-cell" not in "".join(rendered.values())
