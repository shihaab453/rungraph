"""Read-only workspace graph, claim states and consumer projections."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Iterable, Optional

from .checks import run_checks
from .checks.graph import ALLOWED_ENDPOINTS
from .findings import Code, Finding, Severity
from .models import Manifest, RelationshipType as T
from .workspace import LoadedMember, LoadedWorkspace, Ref, WorkspaceError

OBJECT_KINDS = ("project", "run", "artifact", "result", "evidence")
NODE_KINDS = ("member", "project", "run", "artifact", "result", "evidence", "claim")
CYCLE_TYPES = {T.derived_from}
CLAIM_TYPES = {T.supports, T.contradicts}
AT_RISK_CODES = {Code.claim_at_risk, Code.dangling_reference}


@dataclass(frozen=True)
class Node:
    id: str
    kind: str
    member: Optional[str]
    local: Optional[str]
    status: str = "recorded"
    label: str = ""


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    relation: str
    origin: str


@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    context: list[dict[str, str]] = field(default_factory=list)
    cycles: list[list[str]] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    invalid_edges: list[dict[str, str]] = field(default_factory=list)
    ambiguous_members: set[str] = field(default_factory=set)

    def outgoing(self) -> dict[str, list[Edge]]:
        out: dict[str, list[Edge]] = {}
        for edge in self.edges:
            out.setdefault(edge.source, []).append(edge)
        return out


def node_id(kind: str, member: Optional[str], local: Optional[str] = None) -> str:
    if kind == "member":
        return f"member:{member}"
    if kind == "claim":
        return f"claim:{member}#{local}"
    return f"{kind}:{member}#{local}"


def _index(manifest: Manifest) -> tuple[dict[str, tuple[str, Any]], set[str]]:
    found: dict[str, tuple[str, Any]] = {}
    duplicates: set[str] = set()
    entries = [("project", manifest.project)]
    entries += [("run", item) for item in manifest.runs]
    entries += [("artifact", item) for item in manifest.artifacts]
    entries += [("result", item) for item in manifest.results]
    entries += [("evidence", item) for item in manifest.evidence]
    for kind, obj in entries:
        if obj.id in found:
            duplicates.add(obj.id)
        else:
            found[obj.id] = (kind, obj)
    return found, duplicates


def _resolve_local(graph: Graph, member: str, local: str, index: dict[str, tuple[str, Any]]) -> str:
    if local in index:
        kind = index[local][0]
        return node_id(kind, member, local)
    unresolved = f"unresolved:{member}#{local}"
    graph.nodes.setdefault(unresolved, Node(unresolved, "unresolved", member, local, "unresolved"))
    if unresolved not in graph.unresolved:
        graph.unresolved.append(unresolved)
    return unresolved


def _add_edge(graph: Graph, source: str, target: str, relation: T, origin: str) -> None:
    source_node = graph.nodes.get(source)
    target_node = graph.nodes.get(target)
    if source_node is None or target_node is None:
        return
    if source_node.status == "unresolved" or target_node.status == "unresolved":
        graph.edges.append(Edge(source, target, relation.value, origin))
        return
    if source_node.kind == "claim" and relation in CLAIM_TYPES and target_node.kind in (
        "result",
        "evidence",
        "claim",
    ):
        graph.edges.append(Edge(source, target, relation.value, origin))
        return
    allowed = ALLOWED_ENDPOINTS.get(relation)
    if allowed is not None and (source_node.kind, target_node.kind) not in allowed:
        graph.invalid_edges.append(
            {"source": source, "target": target, "relation": relation.value, "origin": origin}
        )
        return
    graph.edges.append(Edge(source, target, relation.value, origin))


def build_graph(loaded: LoadedWorkspace) -> Graph:
    graph = Graph()
    indexes: dict[str, dict[str, tuple[str, Any]]] = {}
    for member_id, item in loaded.members.items():
        graph.nodes[node_id("member", member_id)] = Node(
            node_id("member", member_id), "member", member_id, None, label=member_id
        )
        if item.manifest is None:
            continue
        index, duplicates = _index(item.manifest)
        indexes[member_id] = index
        if duplicates:
            graph.ambiguous_members.add(member_id)
            continue
        manifest = item.manifest
        for kind, obj in (
            [("project", manifest.project)]
            + [("run", run) for run in manifest.runs]
            + [("artifact", art) for art in manifest.artifacts]
            + [("result", res) for res in manifest.results]
            + [("evidence", ev) for ev in manifest.evidence]
        ):
            graph.nodes[node_id(kind, member_id, obj.id)] = Node(
                node_id(kind, member_id, obj.id), kind, member_id, obj.id, label=getattr(obj, "name", obj.id)
            )
            graph.edges.append(
                Edge(node_id("member", member_id), node_id(kind, member_id, obj.id), "contains", "workspace")
            )
        for result in manifest.results:
            source = node_id("result", member_id, result.id)
            _add_edge(graph, source, node_id("run", member_id, result.run_id), T.result_of, "inline")
            if result.artifact_id is not None:
                _add_edge(
                    graph, source, node_id("artifact", member_id, result.artifact_id), T.reported_in, "inline"
                )
        for artifact in manifest.artifacts:
            if artifact.run_id is not None:
                _add_edge(
                    graph,
                    node_id("artifact", member_id, artifact.id),
                    node_id("run", member_id, artifact.run_id),
                    T.produced_by,
                    "inline",
                )
        for relationship in manifest.relationships:
            _add_edge(
                graph,
                _resolve_local(graph, member_id, relationship.source, index),
                _resolve_local(graph, member_id, relationship.target, index),
                relationship.type,
                "member-relationship",
            )
        for evidence in manifest.evidence:
            for target in list(evidence.supports) + list(evidence.contradicts):
                relation = T.supports if target in evidence.supports else T.contradicts
                _add_edge(
                    graph,
                    node_id("evidence", member_id, evidence.id),
                    _resolve_local(graph, member_id, target, index),
                    relation,
                    "member-evidence",
                )
    for claim in loaded.document.claims:
        claim_node = node_id("claim", loaded.document.id, claim.id)
        graph.nodes[claim_node] = Node(claim_node, "claim", loaded.document.id, claim.id, label=claim.id)
    for claim in loaded.document.claims:
        claim_node = node_id("claim", loaded.document.id, claim.id)
        for ref in claim.supports:
            _claim_ref(graph, loaded, claim_node, ref, T.supports, "workspace-claim")
        for ref in claim.contradicts:
            _claim_ref(graph, loaded, claim_node, ref, T.contradicts, "workspace-claim")
    for relationship in loaded.document.relationships:
        source = _claim_ref_target(graph, loaded, relationship.source)
        target = _claim_ref_target(graph, loaded, relationship.target)
        _add_edge(graph, source, target, relationship.type, "workspace-relationship")
    _provenance_conflicts(graph)
    graph.cycles = _cycles(graph)
    return graph


def _claim_ref_target(graph: Graph, loaded: LoadedWorkspace, ref: Ref) -> str:
    if ref.claim is not None:
        target = node_id("claim", loaded.document.id, ref.claim)
        if target not in graph.nodes:
            graph.nodes[target] = Node(target, "claim", loaded.document.id, ref.claim, "unresolved")
            graph.unresolved.append(target)
        return target
    if ref.member not in loaded.members or loaded.members[ref.member].manifest is None:
        unresolved = f"unresolved:{ref.member}#{ref.id}"
        graph.nodes.setdefault(
            unresolved, Node(unresolved, "unresolved", ref.member, ref.id, "unresolved")
        )
        if unresolved not in graph.unresolved:
            graph.unresolved.append(unresolved)
        return unresolved
    index = _index(loaded.members[ref.member].manifest)[0]
    return _resolve_local(graph, ref.member, ref.id, index)


def _claim_ref(graph: Graph, loaded: LoadedWorkspace, source: str, ref: Ref, relation: T, origin: str) -> None:
    target = _claim_ref_target(graph, loaded, ref)
    node = graph.nodes.get(target)
    if node is not None and node.kind in ("artifact", "run", "project"):
        graph.context.append({"source": source, "target": target, "relation": relation.value})
        return
    _add_edge(graph, source, target, relation, origin)


def _provenance_conflicts(graph: Graph) -> None:
    producers: dict[str, set[str]] = {}
    for edge in graph.edges:
        if edge.relation == T.produced_by.value:
            producers.setdefault(edge.source, set()).add(edge.target)
    for artifact, runs in producers.items():
        if len(runs) > 1:
            graph.invalid_edges.append(
                {"source": artifact, "target": ",".join(sorted(runs)), "relation": "provenance-conflict", "origin": "workspace"}
            )


def _cycles(graph: Graph) -> list[list[str]]:
    allowed = {T.derived_from.value, T.supports.value, T.contradicts.value}
    adjacency: dict[str, list[str]] = {}
    for edge in graph.edges:
        if edge.relation in allowed:
            adjacency.setdefault(edge.source, []).append(edge.target)
    for key in adjacency:
        adjacency[key] = sorted(set(adjacency[key]))
    color: dict[str, int] = {}
    cycles: list[list[str]] = []
    seen: set[frozenset[str]] = set()
    for start in sorted(adjacency):
        if start in color:
            continue
        color[start] = 1
        path = [start]
        on_path = {start}
        frames = [(start, iter(adjacency.get(start, ())))]
        while frames:
            node, children = frames[-1]
            advanced = False
            for child in children:
                if child not in color:
                    color[child] = 1
                    path.append(child)
                    on_path.add(child)
                    frames.append((child, iter(adjacency.get(child, ()))))
                    advanced = True
                    break
                if child in on_path:
                    index = path.index(child)
                    cycle = path[index:]
                    key = frozenset(cycle)
                    if key not in seen:
                        seen.add(key)
                        cycles.append(sorted(cycle))
                        if len(cycles) >= 100:
                            return cycles
            if advanced:
                continue
            frames.pop()
            on_path.discard(path.pop())
            color[node] = 2
    return cycles


@dataclass
class ClaimState:
    id: str
    member: Optional[str]
    text: str
    labels: list[str]
    path: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        order = ["unresolved", "contradiction-recorded", "at-risk", "supported", "uncited"]
        for item in order:
            if item in self.labels:
                return item
        return "uncited"


def _finding_touches(findings: dict[str, list[Finding]], node: str) -> bool:
    return any(item.severity == Severity.error for item in findings.get(node, ()))


def _closure(graph: Graph, start: str) -> set[str]:
    seen: set[str] = set()
    stack = [start]
    outgoing = graph.outgoing()
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        for edge in outgoing.get(node, ()):
            if edge.relation not in ("contains",):
                stack.append(edge.target)
    return seen


def claim_support_closure(graph: Graph, start: str, relations: set[str]) -> set[str]:
    """The support closure reached from one claim node through named relations."""
    spans: set[str] = set()
    for edge in graph.edges:
        if edge.source == start and edge.relation in relations:
            spans |= _closure(graph, edge.target)
    return spans


def claim_states(
    loaded: LoadedWorkspace,
    graph: Graph,
    findings_by_member: dict[str, list[Finding]],
) -> list[ClaimState]:
    by_node: dict[str, list[Finding]] = {}
    for member_id, findings in findings_by_member.items():
        for finding in findings:
            if finding.subject is not None:
                local = finding.subject
                if local.startswith("claim:"):
                    by_node.setdefault(local, []).append(finding)
                else:
                    for kind in ("evidence", "result", "artifact", "run", "project"):
                        candidate = node_id(kind, member_id, local)
                        if candidate in graph.nodes:
                            by_node.setdefault(candidate, []).append(finding)
                            break
    states: list[ClaimState] = []
    cache: dict[str, ClaimState] = {}
    for member_id, item in loaded.members.items():
        if item.manifest is None or member_id in graph.ambiguous_members:
            continue
        index = _index(item.manifest)[0]
        for evidence in item.manifest.evidence:
            state = _member_state(member_id, evidence, index, graph, by_node)
            states.append(state)
            cache[node_id("evidence", member_id, evidence.id)] = state
    visiting: set[str] = set()

    def workspace_state(claim_id: str) -> ClaimState:
        key = node_id("claim", loaded.document.id, claim_id)
        if key in cache:
            return cache[key]
        claim = next(item for item in loaded.document.claims if item.id == claim_id)
        if key in visiting:
            return ClaimState(claim_id, None, claim.claim, ["unresolved", "at-risk"])
        visiting.add(key)
        labels: set[str] = set()
        closure: set[str] = set()
        cited = False
        for ref in claim.supports + claim.contradicts:
            target = _claim_ref_target(graph, loaded, ref)
            if target is None or graph.nodes.get(target, Node("", "", None, None)).status == "unresolved":
                labels.add("unresolved")
                labels.add("at-risk")
                continue
            node = graph.nodes[target]
            if node.kind in ("artifact", "run", "project"):
                continue
            cited = True
            closure |= _closure(graph, target)
            if node.kind == "claim":
                inherited = workspace_state(node.local or "") if (node.local or "") in {c.id for c in loaded.document.claims} else cache.get(target)
                if inherited is not None:
                    labels |= set(inherited.labels)
            elif any(_finding_touches(by_node, item) for item in closure):
                labels.add("at-risk")
            if ref in claim.contradicts and node.status == "recorded":
                labels.add("contradiction-recorded")
        whole = claim_support_closure(graph, key, {"supports", "contradicts"})
        if any(
            graph.nodes.get(node) is not None and graph.nodes[node].status == "unresolved"
            for node in whole
        ):
            labels.add("unresolved")
            labels.add("at-risk")
        if any(_finding_touches(by_node, item) for item in whole):
            labels.add("at-risk")
        if labels & {"unresolved", "at-risk"}:
            labels.discard("supported")
        if not cited:
            labels.add("uncited")
        if not labels:
            labels.add("supported")
        state = ClaimState(claim_id, None, claim.claim, sorted(labels))
        cache[key] = state
        visiting.discard(key)
        return state

    for claim in loaded.document.claims:
        states.append(workspace_state(claim.id))
    return states


def _member_state(member_id, evidence, index, graph, by_node) -> ClaimState:
    labels: set[str] = set()
    subjects = by_node.get(node_id("evidence", member_id, evidence.id), [])
    for finding in subjects:
        if finding.code is Code.dangling_reference:
            labels.add("unresolved")
        if finding.code is Code.claim_at_risk:
            labels.add("at-risk")
        if finding.code is Code.unsupported_claim:
            labels.add("uncited" if finding.severity == Severity.warning else "at-risk")
        if finding.code is Code.conflicting_evidence:
            labels.add("contradiction-recorded")
    if evidence.contradicts:
        labels.add("contradiction-recorded")
    if any(ref not in index for ref in evidence.supports):
        labels.add("at-risk")
    start = node_id("evidence", member_id, evidence.id)
    whole = claim_support_closure(graph, start, {"supports"})
    if any(
        graph.nodes.get(node) is not None and graph.nodes[node].status == "unresolved"
        for node in whole
    ):
        labels.add("unresolved")
        labels.add("at-risk")
    if any(_finding_touches(by_node, item) for item in whole):
        labels.add("at-risk")
    if not evidence.supports:
        labels.add("uncited")
    if labels & {"unresolved", "at-risk"}:
        labels.discard("supported")
    if not labels:
        labels.add("supported")
    return ClaimState(evidence.id, member_id, evidence.claim, sorted(labels))


def run_member_checks(loaded: LoadedWorkspace, *, directory_artifacts: bool = False) -> dict[str, Any]:
    reports = {}
    findings = {}
    from .manifest import load_manifest

    for member_id, item in loaded.members.items():
        if item.manifest is None:
            continue
        try:
            report = run_checks(load_manifest(item.manifest_path), enable_directory_artifacts=directory_artifacts)
        except Exception:
            continue
        reports[member_id] = report
        findings[member_id] = list(report.findings)
    return {"reports": reports, "findings": findings}
