# RunGraph workspace RFC (v0.8)

Status: draft for independent review. No implementation is authorised until this contract is
accepted.

## 0. Changes in this version

- v0.8 resolves the last predicate conflict: `supported` requires no unresolved ref of either kind
  for either claim kind, so a member claim with a dangling contradiction is `unresolved` only;
  the regression case asserts the full label set.
- v0.7 folds the final review: the `at-risk` and `supported` predicates are scoped per claim
  kind, so a member claim's broken contradiction target gives `unresolved` without `at-risk`;
  the combination list and the three asymmetries match; a regression case is added.
- v0.6 folds the fifth review: the §5 target scope is now per claim kind (workspace claims
  exclude context, member claims keep the delivered result/artifact/run set); "lineage
  parity" replaces the inaccurate "foundations parity"; the delivered `contradicts`
  asymmetry for member claims is stated.
- v0.5 folded the fourth review: context citations are explicitly excluded from every claim
  predicate; the member-claim `uncited` rule uses the delivered result/artifact/run target
  set; a cited artifact's closure includes its producing run and inputs; the intended
  member/workspace asymmetries and `contradicts` inheritance are stated; the combination list
  is exhaustive.
- v0.4 folded the third review: context citations retained but not edges; member mapping
  matched to delivered findings; `supported` excludes inherited/unresolved; closure defined
  once per object kind and matched to `lineage`; row identity is the result ID everywhere;
  workspace finding subjects and `uncited` pairs; `--expected-sha256` precedence and
  `init --force` staleness; declared members under ignored folders.

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

File `rungraph.json` at the workspace root. Every field is required unless it has a default
or is marked "optional"; unknown fields are rejected.

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

**Ref**: one of

- `{"member": "<member-id>", "id": "<local-id>"}` (both required strings; the local ID must
  match the manifest ID pattern and is not trimmed or case-folded), or
- `{"claim": "<workspace-claim-id>"}` (a workspace claim citing another workspace claim).

The canonical text form is `member#local-id` or `claim:<id>`. `#` and `:` are not in the
manifest ID pattern, so both are unambiguous.

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
- `manifest` is resolved as `member path / manifest`; it must be a regular `.json` file. A
  symlinked manifest file is refused (`lstat`), as is a manifest that escapes the member
  folder. A declared member under an ignored folder (for example `build/`) is honoured when
  it exists: the ignore list governs discovery only.
- A host is treated as case-insensitive when `os.path.normcase("A") == os.path.normcase("a")`;
  on such a host member paths are compared case-folded and a case-variant duplicate is
  refused. On a case-sensitive host, two different spellings are distinct paths but the same
  inode is still a duplicate.
- Discovery and validation never follow a symlinked folder and never descend into reference
  directories listed in §3.

## 3. Discovery

`rungraph discover ROOT [--check] [--directory-artifacts] [--json]` is read-only:

- Scans the whole tree under `ROOT` in sorted relative-path order, collecting every
  `runevidence.json` (or a declared `manifest` name), skipping `.git`, `.hg`, `.svn`,
  `.venv`, `venv`, `__pycache__`, `.pytest_cache`, `node_modules`, `.tox`, `build`, `dist`,
  and any folder that is a symlink or junction. It does not stop at the first member, so a
  manifest nested inside a member folder is found and reported as
  `workspace-member-nested` (error).
- If `ROOT/rungraph.json` exists and loads, each discovered folder that matches a declared
  member path takes that member's stable ID; every other folder gets a provisional ID.
  Declared members are included even when their folder is in the ignore list, so `init
  --force` does not silently drop a member under `build/` or a similar name. Without a
  document, all IDs are provisional.
- Provisional member ID: `prov-` + `valid_slug(relative folder path)`; empty path (root) is
  `prov-root`. Collisions are suffixed `-2`, `-3` in sorted relative-path order, skipping any
  suffix that is already a taken ID, so the result does not depend on traversal timing.
- Reports, for every found manifest: relative path, stable or provisional ID, whether it
  loads, its schema version, and a duplicate when another path resolves to the same file.
- Caps: at most 10,000 manifests and depth 64; beyond either, exit 2 with
  `discovery-limit` and no partial list.
- An empty discovery exits 2 and writes nothing, matching the empty-scan rule.
- `--check` runs the same per-member checks as `validate` (including
  `--directory-artifacts` when given) and includes their findings. Without it, discovery is
  metadata-only, but a nested member, duplicate, unreadable manifest or limit is still an
  error (exit 2); `--check` evidence errors exit 1.
- JSON: `format: "rungraph-discover"`, `version: "0.1"`, `root`, `members` (ordered),
  `duplicates`, `errors`, `exit_code`.

`init` uses this discovery (not a second scanner). It refuses an empty discovery and uses the
stable IDs from an existing document when present.

## 4. Identity and graph

A global node ID is `<kind>:<member-id>#<local-id>`:

- `member:<member-id>` (a member node; no local ID),
- `project:<member>#<id>`, `run:<member>#<id>`, `artifact:<member>#<id>`,
  `result:<member>#<id>`, `evidence:<member>#<id>`,
- `claim:<workspace-id>#<claim-id>` for a workspace claim.

The `member#local` shorthand is accepted by `--from` and `impact`; it resolves only if exactly
one object in that member has the ID (manifest IDs are unique per member; duplicate local IDs
make the member invalid for these commands). Workspace claims use the full node ID
`claim:<workspace-id>#<claim-id>` on the command line; a document ref uses
`{"claim": "<claim-id>"}`.

- A member with duplicate local IDs is reported by the member's existing `duplicate-id`
  findings and is `workspace-member-invalid` for graph/report/impact; it is never resolved
  arbitrarily. Any workspace ref to that member is `workspace-reference-ambiguous`.
- Nodes are built from every loadable member: member, project, run, artifact, result,
  evidence, plus workspace claims. A member node has a `contains` edge to its project, runs,
  artifacts, results and evidence.
- Edges are the union of:
  - inline manifest edges (`result_of`, `reported_in`, `produced_by`, `input_to`, the latter
    reversed for traversal as in `lineage`),
  - **member-declared `relationships`** (the full delivered type set),
  - **member evidence citations** (`supports`/`contradicts` from each evidence node to its
    cited objects),
  - workspace relationships,
  - workspace claim citations, including `{"claim": ...}` refs.
- Endpoint kinds use the delivered rules: `checks/graph.py` `ALLOWED_ENDPOINTS` for
  relationships and evidence citations, `run_lineage.py` `_ALLOWED` for traversal. A
  workspace claim citation to a result, evidence or workspace claim is a traversed edge; a
  citation to an artifact, run or project is **context**, not an edge: it is retained in the
  document, listed in the graph's `context` array, and never affects states or impact. Any
  other target (or an unknown kind) is `workspace-edge-invalid` (error).
- Identical edges from inline, member and workspace data are merged. A workspace relationship
  that gives an artifact a second producing run, or contradicts a member's recorded
  provenance, is `workspace-provenance-conflict` (error) and not traversed.
- A relationship of type `supports` or `contradicts` is refused; claims are the only
  claim-citation mechanism.
- Cycles: only `derived_from` edges and claim-citation edges (member evidence or workspace
  claim, including `{"claim": ...}`) are cycle-checked, matching manifest semantics. One
  example path per strongly connected component, at most 100 examples; a cycle wholly inside
  one member is reported once by the member and once in the workspace list tagged with that
  member.
- The dependency closure is defined once in §5; report and impact both call the same
  function. `impact(X)` is exactly `{claim : X is in closure(claim)}`.
- `graph` output: `format: "rungraph-graph"`, `version: "0.1"`, `exit_code`, `nodes` (`id`,
  `kind`, `member`, `status`, `label`), `edges` (`source`, `target`, `relation`, `origin`),
  `context` (claim citations retained but not traversed), `cycles`, `unresolved`. Unresolved
  refs are nodes with `status: "unresolved"`, never dropped.
- `--from` traverses outgoing dependency edges (the lineage direction); `--depth` is 1–100,
  default full traversal; `--json` is the only machine format and `--format dot` renders the
  same model, it is not a second model.

## 5. Claim states

A workspace claim's `supports`/`contradicts` targets may be a member result, a member evidence
entry, or another workspace claim. A member artifact, run or project citation is context only:
it is shown but does not count as support and does not make the claim cited. A claim with no
result/evidence/claim support target is `uncited`.

For a member claim, the delivered manifest semantics apply unchanged (result, artifact or run
targets; project/evidence targets are invalid). Its labels combine every signal; a claim with
several findings carries several labels.

| Delivered signal | Label |
| --- | --- |
| `dangling-reference` on a cited object | `unresolved` |
| `unsupported-claim` (error severity) | `at-risk` |
| `unsupported-claim` (warning severity, cites nothing) | `uncited` |
| `claim-at-risk` | `at-risk` |
| `claim-unverified` | no state change; it is shown as a warning beside `supported` |
| `conflicting-evidence` on the claim (same target in both lists) | `contradiction-recorded` |
| `conflicting-evidence` on a result subject (a run reports one metric twice) | no claim state; a result-level finding |
| any resolved `contradicts` target | `contradiction-recorded` |
| no support target at all (the `unsupported-claim` warning) | `uncited` |
| no result/artifact/run support target with a contradiction present | `uncited` + `contradiction-recorded` |
| otherwise | `supported` |

The workspace-only rule "result/evidence/claim support target" does **not** apply to member
claims: a member claim supported only by an artifact or run is `supported`, exactly as the
delivered `ALLOWED_ENDPOINTS` allows.

Structural coverage flags cannot imply `at-risk`; only the delivered `claim-at-risk` finding,
an inherited state, or the closure rule can.

The dependency closure is defined once, here, and used by both report and impact:

- cited result: the result, its artifact, its run, the run's input artifacts and their
  producing runs recursively, and `derived_from` ancestors of the artifact and inputs;
- cited run: the run, its input artifacts recursively, and the artifacts it produced (the
  delivered `lineage` for a run);
- cited artifact: the artifact, its `derived_from` ancestors, its producing run, and that
  run's input artifacts and their producing runs recursively (the delivered `lineage` rule);
- cited evidence or claim: its citations recursively;
- every closure follows `derived_from` and member/workspace relationship edges.

This matches the delivered `lineage` per object kind; a result's closure does **not**
include the run's other outputs, so Impact and Claims cannot disagree.

A workspace claim citing a member claim or another workspace claim inherits that claim's
`unresolved`, `at-risk` and `contradiction-recorded` labels. Inheritance is applied before
classification: an inherited `unresolved` or `at-risk` makes the citing claim ineligible for
`supported`.

An error-level finding whose subject is any object in the closure, or any file finding for an
artifact in the closure, "touches" the claim. Workspace findings carry a subject: a member ID,
a `claim:<workspace>#<id>` node ID, or the qualified reference text; a finding with no subject
touches the member only.

Predicates. For a workspace claim, "target" means a traversed citation (result, evidence or
workspace claim); an artifact, run or project citation is context and never satisfies a
workspace-claim predicate. For a member claim, "target" uses the delivered set (result,
artifact or run), and the closure above applies. A workspace claim's `at-risk` follows both
support and contradiction targets; a member claim's `at-risk` follows its support targets and
the delivered `claim-at-risk` only, so a broken `contradicts` target does not make a member
claim `at-risk` (the delivered asymmetry).

- `unresolved`: any support/contradicts target names a missing member, a missing local ID, an
  ambiguous duplicate local ID, or a missing workspace claim; or a cited claim carries
  `unresolved`.
- `contradiction-recorded`: at least one `contradicts` target resolves, or a cited claim
  carries `contradiction-recorded`.
- `at-risk`: for a workspace claim, any support/contradicts target or closure is touched by
  an error-level finding, or any support/contradicts ref is unresolved, or a cited claim
  carries `at-risk`. For a member claim, a support target or its closure is touched by an
  error-level finding, a support ref is unresolved, or a cited claim carries `at-risk`; a
  dangling `contradicts` ref gives `unresolved` only, not `at-risk`. `unresolved` therefore
  implies `at-risk` for a workspace claim, and for a member claim only when the unresolved
  ref is a support target.
- `supported`: for a workspace claim, at least one support target is a result, evidence or
  claim; for a member claim, any delivered support target (result, artifact or run). Every
  support target resolves, and **no ref of either kind is unresolved for either claim kind**.
  For a workspace claim, no support or contradiction target or closure is touched by an
  error; for a member claim, only support targets and their closure matter for error-touch,
  so an existing contradiction target with an error does not block `supported`. A dangling
  contradiction target therefore makes a member claim `unresolved` but not `supported`. No
  cited claim carries `unresolved` or `at-risk`. A recorded
  contradiction does **not** block `supported`.
- `uncited`: for a workspace claim, no result/evidence/claim support target; for a member
  claim, no support target at all (context-only or contradiction-only workspace claims are
  `uncited`).

Allowed combinations: `unresolved` with `at-risk` or `contradiction-recorded`, or alone for a
member claim whose only unresolved ref is a contradiction target;
`contradiction-recorded` with `supported`, `at-risk` or `uncited`; `supported` with
`contradiction-recorded`; `uncited` with `at-risk` (a broken contradiction target or inherited
state) or `unresolved`. Never `supported` with `at-risk` or `unresolved`; never `uncited`
with `supported`. The list is exhaustive for the labels above. For a single sort label the
precedence is
`unresolved` > `contradiction-recorded` > `at-risk` > `supported` > `uncited`. All applicable
labels are printed in text and JSON.

Three asymmetries are intended. A member claim's state is its own recorded structure: a
same-target `conflicting-evidence` error makes it `contradiction-recorded` while it stays
`supported`. A workspace claim citing it is `at-risk`, because that error is on an object in
the workspace claim's closure. Likewise, a result-subject `conflicting-evidence` makes a
workspace claim citing the result `at-risk`, while the member claim citing it stays
`supported`. Third, a member claim's dangling contradiction target gives `unresolved` (no
`at-risk`), and an existing error-bearing contradiction target gives
`contradiction-recorded` (no `at-risk`), while a workspace claim in either position is
`at-risk`. Inheritance applies through `contradicts` as well as `supports`: contradicting an
at-risk claim makes the contradicting claim `at-risk`.

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

- Row identity is `(member, run, result ID)`; the display name is the result name. A result
  that appears with the same name as another result in the same run is a distinct row, and a
  matrix cell with more than one row is `ambiguous` and counted, never silently averaged.
  `--run` accepts `member#run` (repeatable); `--result` accepts `member#result` for an exact
  row.
- `compare`: the selected rows (all, one metric, or `--result`) with recorded values and
  explicit missing/non-numeric accounting. No evidence-integrity check (C7 is separate).
- `matrix`: rows = `(member, run)`, columns = metric names; missing/non-numeric/ambiguous cells
  are counted.
- `leaderboard`: only finite numeric values; `--direction max|min` required; `--top` 1–1000;
  equal values share a rank; excluded rows are counted by reason. This is a recorded-value
  ranking, not a fairness or validity claim.
- Comparability: per result pair, use the existing experimental comparability surface when
  both members are schema 0.1 or 0.2 and the pair can be selected; statuses are `comparable`,
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
new read-only projection, not `fingerprint` (which is 0.1-only). `data` is canonical JSON
(sorted keys, UTF-8, no NaN/Infinity); a member whose schema version changed between baseline
and current is reported as `schema-changed` and still compared by object ID. Added, removed
and changed objects are listed by `(kind, id)`. Members: added (no baseline entry), removed
(no current entry), re-pathed (same ID, different path, reported as re-pathed not
added/removed), and re-keyed (same path, different ID, reported as renamed, not
added/removed). Workspace claims and relationships are compared by ID, including added,
removed, edited and re-ordered refs. A missing or unreadable baseline member is reported
(`workspace-baseline-missing`, warning), not guessed.

## 8. Command surface

New console script `rungraph` (the `runevidence` CLI is unchanged):

```
rungraph discover ROOT [--check] [--directory-artifacts] [--json]
rungraph init [ROOT] [--id ID] [--name NAME] [--force] [--dry-run] [--json]
rungraph validate [ROOT] [--json] [--strict] [--directory-artifacts] [--redact-manifest-path]
rungraph graph [ROOT] [--from NODE] [--depth N] [--json] [--format dot] [--fail-on-unresolved]
rungraph report [ROOT] [--baseline DIR] [--json] [--strict] [--directory-artifacts] [--redact-manifest-path]
rungraph impact [ROOT] --from NODE [--json]
rungraph compare ROOT --metric NAME [--run MEMBER#RUN]... [--result MEMBER#RESULT]... [--json]
rungraph matrix ROOT [--run MEMBER#RUN]... [--metric NAME]... [--json]
rungraph leaderboard ROOT --metric NAME --direction max|min [--top N] [--run MEMBER#RUN]... [--json]
rungraph patch ROOT --file PATCH [--dry-run] [--json]
rungraph member add|remove|move|rename ROOT [flags] [--dry-run] [--json]
rungraph claim add|edit|remove ROOT [flags] [--dry-run] [--json]
rungraph relationship add|remove ROOT [flags] [--dry-run] [--json]
```

All authoring commands accept `--expected-sha256 HEX` and refuse when the loaded document
does not match it; `rungraph patch --file` is the only multi-operation route and the only one
that applies `set-workspace`. The convenience commands build one-operation patches:

- `member add --id ID --path PATH [--manifest NAME] [--role ROLE]`
- `member remove|move|rename --id ID` (`move` adds `--path`, `rename` adds `--new-id`)
- `claim add|edit|remove --file CLAIM_JSON` (or `--id` for remove)
- `relationship add|remove --file RELATIONSHIP_JSON` (or `--id` for remove)

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
    {"op": "add-claim", "claim": {"id": "c1", "claim": "...", "supports": [], "contradicts": []}},
    {"op": "edit-claim", "id": "c1", "claim": {"claim": "..."}},
    {"op": "remove-claim", "id": "c1"},
    {"op": "add-relationship", "relationship": {"id": "r1", "type": "derived_from", "source": {}, "target": {}}},
    {"op": "remove-relationship", "id": "r1"},
    {"op": "set-workspace", "id": "...", "name": "...", "description": null}
  ]
}
```

- `add-member` takes a complete member object (`id`, `path`, optional `manifest`/`role`);
  `remove-member`/`move-member`/`rename-member` take the existing `id`.
- `edit-claim` is a full-object patch of the fields present: a field that is present replaces
  the stored value; a field that is absent is retained; `supports`/`contradicts` replace the
  whole list when present; `id` is immutable.
- `set-workspace` is available only through `rungraph patch`; it changes the workspace `id`,
  `name`, `description` or `metadata` and updates nothing else. Changing the workspace `id`
  changes the `claim:` node IDs in output, but stored refs use only the claim ID, so no
  document rewrite is implied.
- Operations apply in order to one in-memory document; all references are validated after the
  last operation. Any refusal writes nothing.
- `expected_sha256` is required whenever the document exists: the `--expected-sha256` flag
  wins over a `expected_sha256` field in the patch, and if neither is present the command
  refuses. This is a stale-byte check, not compare-and-swap against non-cooperating writers.
  `init --force` loads the document, backs it up, re-reads the target before replacement, and
  refuses if the bytes changed.
- Lock the workspace folder before loading and hold it through the save where the host
  supports POSIX directory `flock`; a second writer exits 2 (`BUSY`) and writes nothing. Where
  the host has no such lock, the delivered unlocked-writer boundary applies: no `BUSY`, no
  serialization guarantee, and the docs say so.
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
`workspace-provenance-conflict` (error), `workspace-claim-uncited` (warning),
`workspace-baseline-missing` (warning).

Exit codes:

| Command | 0 | 1 | 2 |
| --- | --- | --- | --- |
| `discover` | listed | `--check` found evidence errors | empty, limits, nested/duplicate, unreadable root |
| `init` | written or dry-run | — | refusal, empty discovery |
| `validate` | no errors | evidence errors | workspace or any member manifest unreadable/schema-invalid, usage |
| `graph` | built | `--fail-on-unresolved` and unresolved | load/usage |
| `report` | no errors | evidence errors | workspace/member load, usage |
| `impact` | object resolves | — | object/workspace load failure |
| `compare`/`matrix`/`leaderboard` | rendered | — | load/usage/no rows after filters |
| `member`/`claim`/`relationship`/`patch` | written or dry-run | — | refusal |

`--strict` promotes warnings to exit 1 for validate and report. Every command with `--json`
uses one envelope: `format: "rungraph-<command>"`, `version: "0.1"`, `exit_code`, and its
payload. Formats: `rungraph-discover`, `rungraph-init`, `rungraph-validate`,
`rungraph-graph`, `rungraph-report`, `rungraph-impact`, `rungraph-compare`,
`rungraph-matrix`, `rungraph-leaderboard`, `rungraph-member`, `rungraph-claim`,
`rungraph-relationship`, `rungraph-patch`.

Payloads:

- `discover`: `root`, ordered `members` (`path`, `id`, `stable`, `loads`, `schema_version`,
  `duplicate`), `duplicates`, `errors`.
- `init`: `path`, `members`, `backup` or null, `written`.
- `validate`: `workspace`, per-member counts/findings, `findings`, `check_policy`
  (`directory_artifacts`).
- `graph`: `nodes`, `edges`, `cycles`, `unresolved`.
- `report`: the four sections plus `check_policy` and `baseline`.
- `impact`: `source`, `claims` (each with `path`), `unresolved`.
- `compare`: `metric`, `rows`, `missing`, `non_numeric`, `comparability`.
- `matrix`: `metrics`, `rows`, `cells`, `missing`, `non_numeric`, `ambiguous`.
- `leaderboard`: `metric`, `direction`, `rows` (rank, member, run, result, value), `excluded`
  by reason, `conditions`.
- `member`/`claim`/`relationship`/`patch`: `changed` (a list of operations applied), `path`,
  `dry_run`, `written`.

Error envelope: `{"format": "rungraph-error", "version": "0.1", "command": "<command>",
"error": {"code": "...", "message": "..."}, "exit_code": 2}`.

`workspace-claim-uncited` is the warning for a workspace claim with no
result/evidence/claim support target (the `uncited` state) and is not an error.
`discovery-limit` is returned in the `rungraph-error` envelope with `command: "discover"` and
the cap named. `BUSY` is a CLI refusal code from the admission lock, reported in the error
envelope, not a finding.

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

Discovery: deterministic order and IDs under reordered traversal, stable IDs from an
existing document, collision suffixes that skip taken IDs, declared member under an ignored
folder, ignored dirs, symlinked folders, nested manifest found by the full scan, empty
discovery, 10,001-manifest cap, depth-64 cap, `--check` parity with `validate`, metadata
errors exit 2 with and without `--check`, no files created.

Validate: a three-member workspace (generic, MLflow, TensorBoard) with missing file, changed
value, unresolved ref, ambiguous duplicate local ID, invalid endpoint kind, cross-member
`provenance-conflict`, and one cycle; exit codes and JSON; a member load failure alongside
another member's evidence errors (highest code); `--redact-manifest-path`.

Graph: every node/edge class including member-declared relationships and member evidence
citations; member nodes; workspace-claim nodes; `{"claim": ...}` citations; unresolved nodes;
a cross-member derived cycle; a claim-to-claim cycle; `--from`/`--depth`; DOT;
`--fail-on-unresolved`.

Claims: every state and allowed combination, including `supported` + `contradiction-recorded`,
`unresolved` + `at-risk`, `uncited` + `at-risk`, `uncited` + `unresolved`; context-only
citations retained but not traversed (an error on a context artifact does not make `at-risk`);
a cited at-risk member claim inherits its state and is never `supported`; an evidence closure;
the per-kind closure (`lineage` parity: a cited result does not include the run's other
outputs, a cited run does, a cited artifact includes its producing run and inputs); a broken
upstream artifact (A0 -> run X -> A1) is in the closure and listed by impact; an
output-artifact break where report and impact agree; duplicate
local ID refusal; the member mapping table for `claim-unverified`, `unsupported-claim`
warning/error and same-target `conflicting-evidence`; an ordinary member contradiction; a
member claim with a dangling `contradicts` target is `unresolved` only (no `supported`, no
`at-risk`), while a workspace claim in the same position is `unresolved` + `at-risk`; a
member claim with an existing error-bearing contradiction target is `supported` +
`contradiction-recorded`; a claim with several findings; a claim with no subject finding.

Baseline: each schema 0.1–0.10, canonical `data`, schema-changed member, added/removed/
re-pathed/re-keyed members, missing baseline member, workspace claim/relationship edits,
no `fingerprint` use.

Impact/compare/matrix/leaderboard: cross-member propagation, unresolved refs, multiple runs
per metric, duplicate result names with distinct IDs (`--result` selection, `ambiguous` matrix
cells), ties, missing/non-numeric/non-finite, 0.1 and 0.2 comparable pairs, schema-unavailable
pairs, `--run` filters.

Authoring: add/remove/move/rename member through the convenience flags; `rungraph patch` with
mixed operations and `set-workspace`; `edit-claim` merge/replace semantics; every operation
kind; a multi-op patch failing at operation N writes nothing; stale bytes; missing
`--expected-sha256` when a document exists; patch-file/flag precedence; lock refusal; dry-run;
backup failure; replace failure; post-replace cleanup failure; two `init` runs at once;
`init --force` stale-byte refusal.

JSON: every `--json` route emits its named envelope and payload; the error envelope is
`rungraph-error` with `command`; `--strict` and exit codes match §10.

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
6. `compare`/`matrix`/`leaderboard` as three routes, row = `(member, run, result ID)`.
7. Patch grammar as above, all-or-nothing, stage/fsync/replace.
8. Authoring staged after read-only in the same milestone; D6 BOUNDED until A5/C6.

## 14. Non-goals

No manifest schema change; no new locator or value kind; no server, database, GUI or network;
no automatic claim/relationship inference; no scientific-truth claim; no change to existing
`runevidence` command behavior or output.
