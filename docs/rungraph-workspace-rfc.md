# RunGraph workspace RFC (v0.2)

Status: draft for independent review. No implementation is authorised until this contract is
accepted.

## 0. Changes in this version

- Adds the read-only `discover` surface and full membership authoring (D0/D1).
- Replaces the fingerprint-based baseline with a schema-independent workspace assertion
  projection, and defines baseline member matching.
- Defines typed global node identities, duplicate-local-ID policy and workspace claim nodes.
- Completes the document contract (requiredness, IDs, caps, duplicate keys, path rules).
- Defines edge validation, conflict rules, bounded cycle reporting and traversal limits.
- Defines decisive claim-state predicates, including the dependency closure and member-claim
  mapping; `contradicted` becomes the output label `contradiction-recorded`.
- Adds `compare`, `matrix` and `leaderboard`, row identity, ranking/ties and comparability
  availability, and marks D6 BOUNDED until the A5/C6 coverage slice lands.
- Defines the patch grammar, all-or-nothing write protocol, lock order, commit point and
  backup/cleanup outcomes.
- Pins finding severities, exit codes and JSON envelopes for every command.
- Expands the verification plan with the named failure cases from review.

## 1. Problem and scope

RunEvidence verifies one manifest and ships pairwise checks. It cannot answer a project-level
question across a folder of experiments: which claims still have intact evidence, what
changed, how experiments connect, and which claims a change affects.

In scope: discovery of several manifests; a versioned workspace document with stable member
IDs, workspace claims and cross-member relationships; a read-only graph, consolidated report,
cross-member impact and comparison; authoring of the document and its claims/relationships.

Out of scope: a server, GUI, database, network access, training, any manifest schema change,
and any claim that structural consistency establishes scientific truth.

## 2. Workspace document

File `rungraph.json` at the workspace root. Every field is required unless marked
"optional"; unknown fields are rejected.

| Field | Type | Rule |
| --- | --- | --- |
| `format` | string | literal `rungraph-workspace` |
| `version` | string | literal `0.1` |
| `id` | string | `^[A-Za-z0-9][A-Za-z0-9._-]*$` |
| `name` | string | non-blank, max 512 chars |
| `description` | string or null | optional, max 4 KiB |
| `metadata` | object | optional, default `{}`, JSON values only |
| `members` | array | min 1, max 10,000 |
| `claims` | array | optional, default `[]`, max 10,000 |
| `relationships` | array | optional, default `[]`, max 10,000 |

**Member**: `id` (same pattern, unique), `path` (relative folder, forward slashes, no `..`,
no absolute path, no empty component, max 4 KiB), `manifest` (optional; default
`runevidence.json`; one file name with no `/` or `\`, no `.`/`..`, must end `.json`), `role`
(optional string or null, max 128 chars).

**Claim**: `id` (`^[A-Za-z0-9][A-Za-z0-9._-]*$`, unique in the workspace), `claim`
(non-blank, max 64 KiB), `supports` and `contradicts` (arrays of refs, default `[]`, max 1,024
each), `source` (optional string or null, max 4 KiB), `metadata` (optional object).

**Relationship**: `id` (`^[A-Za-z0-9][A-Za-z0-9._-]*$`, unique), `type` (the delivered
manifest relationship enum, excluding `supports`/`contradicts`), `source` and `target` (refs),
`note` (optional string or null, max 4 KiB).

**Ref**: `{"member": "<member-id>", "id": "<local-id>"}` with both required strings. The local
ID must match the manifest ID pattern; it is not trimmed or case-folded.

Rules:

- Duplicate JSON keys and non-finite numbers are refused (the manifest loader already does
  this); the document is limited to 16 MiB and is read as UTF-8.
- Member IDs are unique. Two members whose resolved manifest path is the same regular file
  (same device and inode, including hardlinks) are `workspace-duplicate-member`. On a
  case-insensitive host, paths are compared case-folded.
- A member path is resolved lexically inside the workspace root; `realpath` is used only for
  duplicate detection and containment. A path whose real target escapes the root is refused.
  Symlinked member folders are refused by discovery and by validation.
- A member at `path: "."` must be the only member. A member nested inside another member's
  folder is `workspace-member-nested` (error).
- `manifest` is resolved as `member path / manifest`; it must be a regular `.json` file.
- Discovery and validation never follow a symlinked folder and never descend into reference
  directories listed in §3.

## 3. Discovery

`rungraph discover ROOT [--check] [--directory-artifacts] [--json]` is read-only and works
without a workspace document:

- Scans `ROOT` depth-first in sorted relative-path order, skipping `.git`, `.hg`, `.svn`,
  `.venv`, `venv`, `__pycache__`, `.pytest_cache`, `node_modules`, `.tox`, `build`, `dist`,
  and any folder that is a symlink or junction.
- A folder containing `manifest` (default `runevidence.json`) is a member. It is not
  descended into further; a manifest found inside another member is
  `workspace-member-nested` (error).
- Provisional member ID: `prov-` + `valid_slug(relative folder path)`; empty path (root) is
  `prov-root`. Collisions are suffixed `-2`, `-3` in sorted relative-path order, so the result
  does not depend on traversal timing.
- Reports, for every found manifest: relative path, provisional ID, whether it loads, its
  schema version, and a duplicate when another path resolves to the same file.
- Caps: at most 10,000 manifests and depth 64; beyond either, exit 2 with
  `discovery-limit` and no partial list.
- An empty discovery exits 2 and writes nothing, matching the empty-scan rule.
- `--check` runs the same per-member checks as `validate` (including
  `--directory-artifacts` when given) and includes their findings; without it, discovery is
  metadata-only. It never writes.
- JSON: `format: "rungraph-discover"`, `version: "0.1"`, `root`, `members` (ordered),
  `duplicates`, `errors`, `exit_code`.

`init` uses this discovery (not a second scanner). It refuses an empty discovery.

## 4. Identity and graph

A global node ID is `<kind>:<member-id>#<local-id>`:

- `member:<member-id>` (a member node; no local ID),
- `project:<member>#<id>`, `run:<member>#<id>`, `artifact:<member>#<id>`,
  `result:<member>#<id>`, `evidence:<member>#<id>`,
- `claim:<workspace-id>#<claim-id>` for a workspace claim.

The `member#local` shorthand is accepted by `--from` and `impact`; it resolves only if exactly
one object in that member has the ID (manifest IDs are unique per member; duplicate local IDs
make the member invalid for these commands). Workspace claims must use the `claim:` form.

- A member with duplicate local IDs is reported by the member's existing `duplicate-id`
  findings and is `workspace-member-invalid` for graph/report/impact; it is never resolved
  arbitrarily. Any workspace ref to that member is `workspace-reference-ambiguous`.
- Nodes are built from every loadable member: member, project, run, artifact, result,
  evidence, plus workspace claims. Edges:
  - inline manifest edges (`result_of`, `reported_in`, `produced_by`, `input_to`, the latter
    reversed for traversal as in `lineage`),
  - workspace relationships,
  - claim `supports`/`contradicts` edges from the claim node to each ref.
- Endpoint kinds are checked against the delivered manifest rules (`checks/graph.py`
  `_ALLOWED`): an edge with a wrong endpoint kind is `workspace-edge-invalid` (error) and is
  not traversed.
- Identical edges from inline and workspace data are merged. A workspace relationship that
  gives an artifact a second producing run, or contradicts a member's recorded provenance, is
  `workspace-provenance-conflict` (error) and not traversed.
- A relationship of type `supports` or `contradicts` is refused; claims are the only
  claim-citation mechanism.
- Cycles: only `derived_from` edges and claim-to-claim citations are cycle-checked, matching
  manifest semantics. One example path per strongly connected component, at most 100
  examples; a cycle wholly inside one member is reported once by the member and once in the
  workspace list tagged with that member.
- `graph` output: `format: "rungraph-graph"`, `version: "0.1"`, `nodes` (`id`, `kind`,
  `member`, `status`, `label`), `edges` (`source`, `target`, `relation`, `origin`), `cycles`,
  `unresolved`. Unresolved refs are nodes with `status: "unresolved"`, never dropped.
- `--from` traverses outgoing dependency edges (the lineage direction); `--depth` is 1–100,
  default full traversal; `--format dot` renders the same model, it is not a second model.

## 5. Claim states

A workspace claim's `supports`/`contradicts` targets must be results or evidence. Other
object kinds are context citations: they are shown but do not satisfy support and do not count
as cited evidence. A claim with no result/evidence targets is `uncited`.

For a member claim, the delivered manifest semantics apply unchanged (result, artifact or run
targets; project/evidence targets are invalid), and its delivered `coverage` flags are mapped
to the labels below rather than recomputed.

The dependency closure of a cited result/evidence is: the object itself; its artifact and run;
the run's input artifacts and their producing runs, recursively; and the artifacts the run
produced (outputs). This union covers both the lineage direction and the delivered
at-risk check. An error-level finding whose subject is any object in the closure, or any file
finding for an artifact in the closure, "touches" the claim. A finding with no subject touches
the member only.

Predicates:

- `unresolved`: any support/contradicts ref names a missing member, a missing local ID, or an
  ambiguous duplicate local ID.
- `contradiction-recorded`: at least one `contradicts` ref resolves to a result/evidence object
  whose closure has no error-level finding. (The label is a recorded relation, not truth.)
- `at-risk`: any support/contradicts ref, or any object in its closure, is touched by an
  error-level finding, or a ref is unresolved.
- `supported`: at least one support target resolves; every support target resolves; no
  support/contradicts target or closure is touched by an error; no contradiction-recorded.
- `uncited`: no support targets (contradicts may still exist).

Allowed combinations: `unresolved` may accompany any other state; `contradiction-recorded`
may accompany `at-risk` or `supported`; `supported` and `at-risk` are mutually exclusive;
`uncited` and `supported` are mutually exclusive. For a single sort label the precedence is
`unresolved` > `contradiction-recorded` > `at-risk` > `supported` > `uncited`. All applicable
labels are printed in text and JSON.

`report` runs checks with the same default as `validate`: `--directory-artifacts` is an
explicit flag; without it the report states that directory artifacts were not checked. It
also accepts `--strict` for exit semantics and `--redact-manifest-path`.

## 6. Consumers

### `report`

Sections, each labelled with its baseline:

1. **Current evidence**: per-member check counts and every finding; the check policy
   (`--directory-artifacts` or not) is stated in the header.
2. **Claims**: every workspace and member claim with all applicable states from §5.
3. **Assertion changes** (`--baseline DIR` only): a schema-independent projection of each
   member manifest compared by member ID; see §7.
4. **Impact**: every error-level file/result finding with the claims whose closure contains
   the affected object, and the dependency path.

### `impact`

`rungraph impact ROOT --from <node-id> [--json]` traverses to every claiming object that
depends on it, in any member, and prints the path. It accepts the typed form or the
`member#local` shorthand. Exit `0` when the object resolves, `2` when it does not or the
workspace fails to load (matching the delivered `runevidence impact`).

### `compare`, `matrix`, `leaderboard`

- Row identity is `(member, run, result name)`. A metric that appears in several runs yields
  several rows; there is no implicit selection. `--run` accepts `member#run` (repeatable).
- `compare`: two selected rows or all rows for one metric, with recorded values and explicit
  missing/non-numeric accounting. No evidence-integrity check (C7 is separate).
- `matrix`: rows = `(member, run)`, columns = metric names; missing/non-numeric cells counted.
- `leaderboard`: only finite numeric values; `--direction max|min` required; `--top` 1–1000;
  ties share a rank; excluded rows are counted by reason. This is a recorded-value ranking,
  not a fairness or validity claim.
- Comparability: per result pair, use the existing experimental comparability surface when
  both members are schema 0.2 and the pair can be selected; statuses are `comparable`,
  `not-comparable`, `unavailable: schema`, `unavailable: insufficient-evidence`,
  `unavailable: mapping`. Matrix and leaderboard show a per-member
  `conditions: available|unavailable` column and never claim fairness. Schemas 0.3–0.10 are
  `unavailable: schema` until the A5/C6 coverage slice lands; D6 is therefore **BOUNDED**
  until then.

## 7. Baseline

`--baseline DIR` names a prior copy of the workspace root. The baseline is read through
`DIR/rungraph.json` when present; otherwise members are matched by their current relative
paths. Matching is by stable member ID first, then by path for a member absent from the
baseline document.

For each member, the projection is a canonical list of recorded objects
(`kind`, `id`, `data`) built from the manifest model for **every schema 0.1–0.10**; it is a
new read-only projection, not `fingerprint` (which is 0.1-only). Added, removed and changed
objects are listed by `(kind, id)`. Members: added (no baseline entry), removed (no current
entry), re-pathed (same ID, different path, reported as re-pathed not added/removed).
Workspace claims and relationships are compared by ID, including added, removed, edited and
re-ordered refs. A missing or unreadable baseline member is reported, not guessed.

## 8. Command surface

New console script `rungraph` (the `runevidence` CLI is unchanged):

```
rungraph discover ROOT [--check] [--directory-artifacts] [--json]
rungraph init [ROOT] [--id ID] [--name NAME] [--force] [--dry-run]
rungraph validate [ROOT] [--json] [--strict] [--directory-artifacts] [--redact-manifest-path]
rungraph graph [ROOT] [--from NODE] [--depth N] [--format json|dot] [--fail-on-unresolved]
rungraph report [ROOT] [--baseline DIR] [--json] [--strict] [--directory-artifacts] [--redact-manifest-path]
rungraph impact [ROOT] --from NODE [--json]
rungraph compare ROOT --metric NAME [--run MEMBER#RUN]... [--json]
rungraph matrix ROOT [--run MEMBER#RUN]... [--metric NAME]... [--json]
rungraph leaderboard ROOT --metric NAME --direction max|min [--top N] [--run MEMBER#RUN]... [--json]
rungraph member add|remove|move|rename ROOT ... [--dry-run] [--json]
rungraph claim add|edit|remove ROOT --patch FILE [--dry-run] [--json]
rungraph relationship add|remove ROOT --patch FILE [--dry-run] [--json]
```

`init` writes a fresh document from discovery; `--force` backs up to `.bak`, `.bak.2`, …
and does not merge hand-written claims or relationships (it says so). `init` on an empty
discovery refuses with exit 2 and writes nothing. `--dry-run` writes nothing.

`member` operations edit membership without regenerating the document. `rename` updates every
qualified reference in the same transaction. `move` changes the path; references are by member
ID and remain valid.

## 9. Patch and write protocol

Authoring patches are JSON:

```json
{
  "format": "rungraph-patch",
  "version": "0.1",
  "expected_sha256": "<hex or null>",
  "operations": [
    {"op": "add-member", "member": {"id": "exp-a", "path": "experiments/a"}},
    {"op": "remove-member", "id": "exp-b"},
    {"op": "move-member", "id": "exp-a", "path": "experiments/a2"},
    {"op": "rename-member", "id": "exp-a", "new_id": "exp-a2"},
    {"op": "add-claim", "claim": {"id": "c1", "claim": "...", "supports": []}},
    {"op": "edit-claim", "id": "c1", "claim": {"claim": "..."}},
    {"op": "remove-claim", "id": "c1"},
    {"op": "add-relationship", "relationship": {"id": "r1", "type": "derived_from", "source": {}, "target": {}}},
    {"op": "remove-relationship", "id": "r1"},
    {"op": "set-workspace", "id": "...", "name": "...", "description": null}
  ]
}
```

- Operations apply in order to one in-memory document; all references are validated after the
  last operation. Any refusal writes nothing.
- `expected_sha256` must equal the loaded document bytes when the document exists; if it is
  null or missing, the command refuses when the document exists. This is a stale-byte check,
  not compare-and-swap against non-cooperating writers.
- Lock the workspace folder before loading and hold it through the save where the host
  supports POSIX directory `flock`; a second writer exits 2 (`BUSY`) and writes nothing. On
  hosts without it, the existing unlock/no-serialization boundary applies and is printed by
  `--version`-level docs, not this command.
- Dry-run takes no lock and writes nothing.
- Write: stage a complete replacement in the same folder, fsync it, replace atomically, then
  remove the staging file. `init --force` backs up first; if backup fails nothing is replaced;
  if replace fails the original and backup remain; if post-replace cleanup fails the command
  exits 2 and names the staging path, with the write committed.
- The patch refusal texts are fixed in the docs and tests, including duplicate ID, unknown
  member, invalid endpoint kind, stale bytes, and a failed operation.

## 10. Findings, severities and exit codes

Workspace findings: `workspace-unreadable` (error), `workspace-schema-invalid` (error),
`workspace-duplicate-member` (error), `workspace-member-missing` (error),
`workspace-member-nested` (error), `workspace-member-invalid` (error),
`workspace-reference-unresolved` (error), `workspace-reference-ambiguous` (error),
`workspace-reference-cycle` (error), `workspace-edge-invalid` (error),
`workspace-provenance-conflict` (error), `workspace-claim-unverified` (warning),
`workspace-baseline-missing` (warning).

Exit codes:

| Command | 0 | 1 | 2 |
| --- | --- | --- | --- |
| `discover` | listed | `--check` found errors | empty, limits, unreadable root |
| `init` | written or dry-run | — | refusal, empty discovery |
| `validate` | no errors | evidence errors | workspace or any member manifest unreadable/schema-invalid, usage |
| `graph` | built | `--fail-on-unresolved` and unresolved | load/usage |
| `report` | no errors | evidence errors | workspace/member load, usage |
| `impact` | object resolves | — | object/workspace load failure |
| `compare`/`matrix`/`leaderboard` | rendered | — | load/usage/no rows after filters |
| `member`/`claim`/`relationship` | written or dry-run | — | refusal |

`--strict` promotes warnings to exit 1 for validate and report. JSON envelopes:
`format` (`rungraph-validate`, `rungraph-graph`, `rungraph-report`, `rungraph-compare`,
`rungraph-impact`, `rungraph-discover`), `version: "0.1"`, `exit_code`, and the payload;
errors use `{"format": "<command>", "version": "0.1", "error": {"code": "...", "message": "..."}, "exit_code": 2}`.
`--redact-manifest-path` replaces workspace and member paths in validate/report output.

## 11. Admission checklist

- `src/runevidence/workspace.py`: document models (`extra="forbid"`, duplicate-key and
  finite checks), discovery scanner with caps, loader/writer, reference resolution, graph,
  claim-state engine, baseline projection.
- `src/runevidence/workspace_consumers.py`: report, impact, compare/matrix/leaderboard
  projections.
- `src/runevidence/rungraph_cli.py` and a `rungraph` console script; `src/runevidence/cli.py`
  untouched.
- Reuse `manifest.load_manifest`, `checks.run_checks`, `models`, `locator_profiles`; no change
  to their behavior. The dependency closure is computed from the loaded models and the
  `run_checks` findings plus their subjects, not from internal check state that is not
  exposed.
- Tests: `test_workspace_document.py`, `test_workspace_discovery.py`,
  `test_workspace_validate.py`, `test_workspace_graph.py`, `test_workspace_claims.py`,
  `test_workspace_report.py`, `test_workspace_baseline.py`, `test_workspace_impact.py`,
  `test_workspace_compare.py`, `test_workspace_authoring.py`, `test_workspace_cli.py`,
  `test_workspace_e2e.py`.
- Docs: `docs/rungraph-workspace.md`, README section and command reference; docs-contract test
  requires every `rungraph` route.
- Packaging: the core wheel declares both scripts; the advisory job asserts both; a named
  wheel-smoke job installs the wheel and runs `rungraph discover`/`validate`.

## 12. Verification plan

Document: every field/type/default, unknown fields, duplicate keys, non-finite numbers, size
caps, ID grammar, duplicate member IDs, hardlink duplicate, case variant, `.` member alone,
nested member, symlinked member, manifest escaping, version refusal, claim/relationship
round-trip, duplicate citation, duplicate relationship.

Discovery: deterministic order and IDs under reordered traversal, collision suffixes,
ignored dirs, symlinked folders, nested manifest, empty discovery, 10,001-manifest cap,
depth-64 cap, `--check` parity with `validate`, no files created.

Validate: a three-member workspace (generic, MLflow, TensorBoard) with missing file, changed
value, unresolved ref, ambiguous duplicate local ID, invalid endpoint kind, cross-member
`provenance-conflict`, and one cycle; exit codes and JSON; a member load failure alongside
another member's evidence errors (highest code); `--redact-manifest-path`.

Graph: every node/edge class, member nodes, workspace-claim nodes, unresolved nodes, a
cross-member derived cycle, `--from`/`--depth`, DOT, `--fail-on-unresolved`.

Claims: all five states and their allowed combinations; a cited at-risk member claim; a
claim-to-claim cycle; a contradiction-only claim; a context-only citation; an output-artifact
break where report and impact agree; duplicate local ID refusal.

Baseline: each schema 0.1–0.10, added/removed/re-pathed members, missing baseline member,
workspace claim/relationship edits, no `fingerprint` use.

Impact/compare/matrix/leaderboard: cross-member propagation, unresolved refs, multiple runs
per metric, ties, missing/non-numeric/non-finite, schema-unavailable pairs, `--run` filters.

Authoring: add/remove/move/rename member, add/edit/remove claim and relationship, multi-op
patch failing at operation N writes nothing, stale bytes, lock refusal, dry-run, backup
failure, replace failure, post-replace cleanup failure, two `init` runs at once.

Read-only commands create no files (folder tree before/after). `runevidence --help` and golden
output unchanged. Full suite on 3.12 and 3.10/pydantic 2.6; wheel smoke with both scripts.

Mutation probes: duplicate detection, ID sanitization, reference resolution, ambiguous
duplicate, endpoint-kind check, provenance conflict, cycle bound, claim predicates, closure,
baseline matching, patch atomicity, each write refusal.

## 13. Decisions requested from review

1. `discover` as the read-only D1 surface, with `--check` matching `validate`.
2. Provisional IDs `prov-` + path slug with sorted collision suffixes.
3. Typed node IDs (`kind:member#local`) plus the `member#local` shorthand.
4. A workspace-only claim/relationship namespace with its own IDs.
5. Baseline as a prior workspace copy plus the schema-independent assertion projection.
6. `compare`/`matrix`/`leaderboard` as three routes, row = `(member, run, name)`.
7. Patch grammar as above, all-or-nothing, stage/fsync/replace.
8. Authoring staged after read-only in the same milestone; D6 BOUNDED until A5/C6.

## 14. Non-goals

No manifest schema change; no new locator or value kind; no server, database, GUI or network;
no automatic claim/relationship inference; no scientific-truth claim; no change to existing
`runevidence` command behavior or output.
