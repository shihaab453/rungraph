"""Long derivation chains (RE-08).

Cycle detection used to recurse once per derived_from step, so a valid
chain longer than Python's recursion limit (about 1,000) crashed validation
with RecursionError instead of returning findings. The search is iterative
now, and lineage for claims at risk stores one step per node instead of a
full chain per node.
"""

from __future__ import annotations

import json
import random
import sys
import tracemalloc
from collections import defaultdict
from pathlib import Path
from typing import Optional

import pytest

from runevidence.checks.graph import _find_cycle, check_graph
from runevidence.cli import main
from runevidence.findings import Code
from runevidence.models import Manifest

MANIFEST = "runevidence.json"


def chain_doc(n: int, *, close_loop: bool = False) -> dict:
    """Runs r00000 .. r<n-1>, each derived_from the next one. A search in
    sorted order starts at r00000 and must walk the whole chain."""
    runs = [{"id": f"r{i:05d}", "name": f"step {i}"} for i in range(n)]
    rels = [
        {"source": f"r{i:05d}", "target": f"r{i + 1:05d}", "type": "derived_from"}
        for i in range(n - 1)
    ]
    if close_loop:
        rels.append({"source": f"r{n - 1:05d}", "target": "r00000", "type": "derived_from"})
    return {
        "schema_version": "0.1",
        "project": {"id": "deep", "name": "deep"},
        "runs": runs,
        "relationships": rels,
    }


@pytest.mark.parametrize("n", [1_200, 5_000])
def test_long_valid_chain_returns_findings_instead_of_crashing(n):
    assert n > sys.getrecursionlimit()
    findings = check_graph(Manifest.model_validate(chain_doc(n)), set())
    assert [f.code for f in findings] == []


def test_a_cycle_closing_a_long_chain_is_reported_in_full():
    n = 5_000
    findings = check_graph(Manifest.model_validate(chain_doc(n, close_loop=True)), set())

    [finding] = findings
    assert finding.code is Code.derivation_cycle
    steps = finding.message.split(": ", 1)[1].split(" -> ")
    assert len(steps) == n + 1
    assert steps[0] == steps[-1] == "r00000"


def test_claim_at_risk_through_a_long_lineage_names_the_whole_path():
    n = 5_000
    doc = {
        "schema_version": "0.1",
        "project": {"id": "deep", "name": "deep"},
        "artifacts": [{"id": f"a{i:05d}", "path": f"data/{i}.csv"} for i in range(n)],
        "relationships": [
            {"source": f"a{i:05d}", "target": f"a{i - 1:05d}", "type": "derived_from"}
            for i in range(1, n)
        ],
        "evidence": [{"id": "claim", "claim": "the last file is right", "supports": [f"a{n - 1:05d}"]}],
    }

    manifest = Manifest.model_validate(doc)
    tracemalloc.start()
    try:
        findings = check_graph(manifest, {"a00000"})
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    # One full chain per reached node would need about n*n/2 list slots
    # (100 MB here); one step per node stays far below that.
    assert peak < 30_000_000
    [finding] = findings
    assert finding.code is Code.claim_at_risk
    via = finding.message.split("(via ", 1)[1].rstrip(")")
    assert via.split(" -> ") == [f"a{i:05d}" for i in range(n - 1, 0, -1)]


def test_cli_validate_handles_a_long_chain_end_to_end(tmp_path, capsys):
    (tmp_path / MANIFEST).write_text(json.dumps(chain_doc(1_500)), encoding="utf-8")

    assert main(["validate", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["findings"] == []


def _recursive_find_cycle(graph: dict[str, set[str]]) -> Optional[list[str]]:
    """The previous recursive implementation, kept as a reference."""
    color: dict[str, int] = defaultdict(int)
    stack: list[str] = []

    def visit(node: str) -> Optional[list[str]]:
        color[node] = 1
        stack.append(node)
        for nxt in sorted(graph.get(node, ())):
            if color[nxt] == 1:
                return stack[stack.index(nxt):] + [nxt]
            if color[nxt] == 0:
                found = visit(nxt)
                if found:
                    return found
        stack.pop()
        color[node] = 2
        return None

    for node in sorted(graph):
        if color[node] == 0:
            found = visit(node)
            if found:
                return found
    return None


def test_iterative_search_reports_exactly_the_cycle_the_recursive_one_did():
    rng = random.Random(20261026)
    for _ in range(500):
        nodes = [f"n{i}" for i in range(rng.randint(1, 12))]
        graph: dict[str, set[str]] = defaultdict(set)
        for _ in range(rng.randint(0, 24)):
            graph[rng.choice(nodes)].add(rng.choice(nodes))
        graph = dict(graph)
        assert _find_cycle(graph) == _recursive_find_cycle(graph)
