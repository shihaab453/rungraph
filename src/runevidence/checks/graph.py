"""Reference and relationship checks.

Every link in a manifest, whether written as a field (``artifact.run_id``,
``result.artifact_id``, ``evidence.supports``) or as an explicit entry in
``relationships``, is normalized into one typed edge list. All checks run on
that list, so both spellings behave the same.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from ..findings import Code, Finding, Severity
from ..models import Manifest, RelationshipType as T

ARTIFACT, RUN, RESULT, EVIDENCE, PROJECT = "artifact", "run", "result", "evidence", "project"

# Which (source kind, target kind) pairs each edge type allows.
ALLOWED_ENDPOINTS: dict[T, set[tuple[str, str]]] = {
    T.produced_by: {(ARTIFACT, RUN)},
    T.input_to: {(ARTIFACT, RUN)},
    T.result_of: {(RESULT, RUN)},
    T.reported_in: {(RESULT, ARTIFACT)},
    T.derived_from: {(ARTIFACT, ARTIFACT), (RUN, RUN)},
    T.supports: {(EVIDENCE, RESULT), (EVIDENCE, ARTIFACT), (EVIDENCE, RUN)},
    T.contradicts: {(EVIDENCE, RESULT), (EVIDENCE, ARTIFACT), (EVIDENCE, RUN)},
}


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    type: T
    origin: str  # where in the manifest this edge was written


def _article(word: str) -> str:
    return "an" if word[:1] in "aeiou" else "a"


def build_index(manifest: Manifest) -> tuple[dict[str, str], list[Finding]]:
    """Map every id to its kind; report ids used more than once."""
    kinds: dict[str, str] = {}
    findings: list[Finding] = []
    entries = [(manifest.project.id, PROJECT, "project")]
    for kind, items, name in (
        (RUN, manifest.runs, "runs"),
        (ARTIFACT, manifest.artifacts, "artifacts"),
        (RESULT, manifest.results, "results"),
        (EVIDENCE, manifest.evidence, "evidence"),
    ):
        entries += [(item.id, kind, f"{name}[{i}]") for i, item in enumerate(items)]
    for ident, kind, origin in entries:
        if ident in kinds:
            findings.append(
                Finding(
                    code=Code.duplicate_id,
                    severity=Severity.error,
                    subject=ident,
                    message=f"ID {ident!r} at {origin} is already used by {_article(kinds[ident])} {kinds[ident]}",
                    hint="IDs share one namespace across runs, artifacts, results and evidence.",
                )
            )
        else:
            kinds[ident] = kind
    return kinds, findings


def collect_edges(manifest: Manifest) -> list[Edge]:
    edges: list[Edge] = []
    for i, a in enumerate(manifest.artifacts):
        if a.run_id is not None:
            edges.append(Edge(a.id, a.run_id, T.produced_by, f"artifacts[{i}].run_id"))
    for i, r in enumerate(manifest.results):
        edges.append(Edge(r.id, r.run_id, T.result_of, f"results[{i}].run_id"))
        if r.artifact_id is not None:
            edges.append(Edge(r.id, r.artifact_id, T.reported_in, f"results[{i}].artifact_id"))
    for i, e in enumerate(manifest.evidence):
        for j, t in enumerate(e.supports):
            edges.append(Edge(e.id, t, T.supports, f"evidence[{i}].supports[{j}]"))
        for j, t in enumerate(e.contradicts):
            edges.append(Edge(e.id, t, T.contradicts, f"evidence[{i}].contradicts[{j}]"))
    for i, rel in enumerate(manifest.relationships):
        edges.append(Edge(rel.source, rel.target, rel.type, f"relationships[{i}]"))
    return edges


def _find_cycle(graph: dict[str, set[str]]) -> Optional[list[str]]:
    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[str, int] = defaultdict(int)
    stack: list[str] = []

    def visit(node: str) -> Optional[list[str]]:
        color[node] = GREY
        stack.append(node)
        for nxt in sorted(graph.get(node, ())):
            if color[nxt] == GREY:
                return stack[stack.index(nxt):] + [nxt]
            if color[nxt] == WHITE:
                found = visit(nxt)
                if found:
                    return found
        stack.pop()
        color[node] = BLACK
        return None

    for node in sorted(graph):
        if color[node] == WHITE:
            found = visit(node)
            if found:
                return found
    return None


def check_graph(manifest: Manifest, broken: set[str]) -> list[Finding]:
    """``broken`` holds ids of artifacts/results already known to be bad
    (missing, changed, value mismatch). Claims that rest on them are flagged."""
    kinds, findings = build_index(manifest)
    add = findings.append

    valid: list[Edge] = []
    for edge in collect_edges(manifest):
        missing = [x for x in (edge.source, edge.target) if x not in kinds]
        if missing:
            add(
                Finding(
                    code=Code.dangling_reference,
                    severity=Severity.error,
                    subject=edge.source,
                    message=f"{edge.origin} refers to unknown ID {missing[-1]!r} ({edge.type.value})",
                    hint="Fix the ID or add the missing entry to the manifest.",
                )
            )
            continue
        pair = (kinds[edge.source], kinds[edge.target])
        if pair not in ALLOWED_ENDPOINTS[edge.type]:
            allowed = ", ".join(f"{s} -> {t}" for s, t in sorted(ALLOWED_ENDPOINTS[edge.type]))
            add(
                Finding(
                    code=Code.invalid_relationship,
                    severity=Severity.error,
                    subject=edge.source,
                    message=(
                        f"{edge.origin}: '{edge.type.value}' cannot link a {pair[0]} "
                        f"({edge.source!r}) to a {pair[1]} ({edge.target!r})"
                    ),
                    hint=f"'{edge.type.value}' allows: {allowed}",
                )
            )
            continue
        valid.append(edge)

    by_type: dict[T, list[Edge]] = defaultdict(list)
    for edge in valid:
        by_type[edge.type].append(edge)

    # An artifact or result can come from only one run.
    producer: dict[str, str] = {}
    for etype, noun in ((T.produced_by, "artifact"), (T.result_of, "result")):
        owners: dict[str, set[str]] = defaultdict(set)
        for edge in by_type[etype]:
            owners[edge.source].add(edge.target)
        for ident, runs in sorted(owners.items()):
            if len(runs) > 1:
                add(
                    Finding(
                        code=Code.conflicting_provenance,
                        severity=Severity.error,
                        subject=ident,
                        message=f"{noun.capitalize()} {ident!r} is attributed to several runs: {sorted(runs)}",
                        hint="Each file or value has exactly one producing run; use input_to for consumers.",
                    )
                )
            else:
                producer[ident] = next(iter(runs))

    # A result read from a file another run wrote is suspicious.
    for edge in by_type[T.reported_in]:
        r_run, a_run = producer.get(edge.source), producer.get(edge.target)
        if r_run and a_run and r_run != a_run:
            add(
                Finding(
                    code=Code.conflicting_provenance,
                    severity=Severity.warning,
                    subject=edge.source,
                    message=(
                        f"Result {edge.source!r} belongs to run {r_run!r} but is reported in "
                        f"{edge.target!r}, which run {a_run!r} produced"
                    ),
                )
            )

    # The same run cannot report two different values for the same metric.
    seen: dict[tuple[str, str], tuple[str, object]] = {}
    for res in manifest.results:
        key = (res.run_id, res.name)
        if key in seen and seen[key][1] != res.value:
            other_id, other_value = seen[key]
            add(
                Finding(
                    code=Code.conflicting_evidence,
                    severity=Severity.error,
                    subject=res.id,
                    message=(
                        f"Run {res.run_id!r} reports {res.name!r} twice with different values: "
                        f"{other_id!r}={other_value!r}, {res.id!r}={res.value!r}"
                    ),
                )
            )
        seen.setdefault(key, (res.id, res.value))

    # Derivation must not loop.
    derived: dict[str, set[str]] = defaultdict(set)
    for edge in by_type[T.derived_from]:
        derived[edge.source].add(edge.target)
    cycle = _find_cycle(derived)
    if cycle:
        add(
            Finding(
                code=Code.derivation_cycle,
                severity=Severity.error,
                subject=cycle[0],
                message="derived_from forms a cycle: " + " -> ".join(cycle),
            )
        )

    # Evidence checks.
    supports: dict[str, set[str]] = defaultdict(set)
    contradicts: dict[str, set[str]] = defaultdict(set)
    for edge in by_type[T.supports]:
        supports[edge.source].add(edge.target)
    for edge in by_type[T.contradicts]:
        contradicts[edge.source].add(edge.target)

    produced_by_run: dict[str, set[str]] = defaultdict(set)
    for edge in by_type[T.produced_by]:
        produced_by_run[edge.target].add(edge.source)
    inputs_of_run: dict[str, set[str]] = defaultdict(set)
    for edge in by_type[T.input_to]:
        inputs_of_run[edge.target].add(edge.source)
    reported_in: dict[str, str] = {e.source: e.target for e in by_type[T.reported_in]}

    def foundations(target: str) -> set[str]:
        """Ids the target directly depends on: the thing itself, the file a
        result was read from, and the inputs of the run behind it."""
        kind = kinds.get(target)
        if kind == ARTIFACT:
            return {target}
        if kind == RESULT:
            out = {target}
            if target in reported_in:
                out.add(reported_in[target])
            run = producer.get(target)
            if run:
                out |= inputs_of_run.get(run, set())
            return out
        if kind == RUN:
            return produced_by_run.get(target, set()) | inputs_of_run.get(target, set())
        return set()

    def upstream_of(node: str) -> list[str]:
        """One step back along the recorded lineage."""
        kind = kinds.get(node)
        out: set[str] = set()
        if kind == RESULT and node in reported_in:
            out.add(reported_in[node])
        if kind in (RESULT, ARTIFACT) and node in producer:
            out.add(producer[node])
        if kind in (ARTIFACT, RUN):
            out |= derived.get(node, set())
        if kind == RUN:
            out |= inputs_of_run.get(node, set())
        return sorted(out)

    def lineage(cited: set[str]) -> dict[str, list[str]]:
        """Everything the cited ids transitively rest on, each mapped to the
        chain of ids that leads to it. A raw dataset two preprocessing steps
        upstream of a result still puts a claim about that result at risk."""
        chains: dict[str, list[str]] = {}
        queue = []
        for target in sorted(cited):
            for dep in sorted(foundations(target)):
                if dep not in chains:
                    chains[dep] = [target] if dep != target else []
                    queue.append(dep)
        while queue:
            node = queue.pop(0)
            for dep in upstream_of(node):
                if dep not in chains:  # also stops derivation cycles
                    chains[dep] = chains[node] + [node]
                    queue.append(dep)
        return chains

    for ev in manifest.evidence:
        if not ev.supports and not ev.contradicts:
            add(
                Finding(
                    code=Code.unsupported_claim,
                    severity=Severity.warning,
                    subject=ev.id,
                    message=f"Claim {ev.id!r} cites nothing: {ev.claim!r}",
                    hint="Add the result, artifact or run IDs that back it to 'supports'.",
                )
            )
            continue
        both = supports[ev.id] & contradicts[ev.id]
        for target in sorted(both):
            add(
                Finding(
                    code=Code.conflicting_evidence,
                    severity=Severity.error,
                    subject=ev.id,
                    message=f"Claim {ev.id!r} lists {target!r} as both supporting and contradicting it",
                )
            )
        if ev.supports and not supports[ev.id]:
            add(
                Finding(
                    code=Code.unsupported_claim,
                    severity=Severity.error,
                    subject=ev.id,
                    message=f"Claim {ev.id!r} has no valid support left: {ev.claim!r}",
                    hint="Every ID in 'supports' is unknown or of the wrong kind (see errors above).",
                )
            )
        direct = {f for t in supports[ev.id] for f in foundations(t)}
        chains = lineage(supports[ev.id])
        at_risk = sorted(set(chains) & broken)
        if at_risk:
            # Direct dependencies print bare; upstream ones show how they are reached.
            described = [
                ident if ident in direct else f"{ident} (via {' -> '.join(chains[ident])})"
                for ident in at_risk
            ]
            add(
                Finding(
                    code=Code.claim_at_risk,
                    severity=Severity.error,
                    subject=ev.id,
                    message=(
                        f"Claim {ev.id!r} rests on evidence that is missing or changed: "
                        f"{', '.join(described)}"
                    ),
                    hint=f"Claim: {ev.claim!r}",
                )
            )
    return findings
