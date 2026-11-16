# RunGraph workspace RFC (v0.1)

Status: draft for independent review. No implementation is authorised until this contract is
accepted.

## 0. Changes in this version

- First draft. Defines a workspace document over existing manifests, qualified
  cross-manifest identity, a global graph, one consolidated report, cross-member impact and
  cross-member comparison.
- No manifest schema, locator or frozen reader change. The workspace is a separate versioned
  format consumed by a new `rungraph` command family.

## 1. Problem and scope

RunEvidence verifies one manifest and ships pairwise checks (batch validation, comparability,
fingerprint/diff gates). It cannot answer a project-level question: across a folder of
experiments, which claims still have intact evidence, what changed, how are the experiments
connected, and which claims does a change affect?

In scope:

- discovery of several `runevidence.json` manifests under a root;
- a versioned workspace document with stable member IDs, workspace claims and cross-member
  relationships;
- a read-only global graph, consolidated report, cross-member impact and comparison;
- authoring of the workspace document and its claims/relationships under the same safety
  rules as manifest authoring.

Out of scope: a server, GUI, database, network access, training, any manifest schema change,
and any claim that structural consistency establishes scientific truth.

## 2. Workspace document

File `rungraph.json` at the workspace root:

```json
{
  "format": "rungraph-workspace",
  "version": "0.1",
  "id": "study",
  "name": "Study",
  "description": null,
  "members": [
    {"id": "exp-a", "path": "experiments/a", "manifest": "runevidence.json"}
  ],
  "claims": [
    {
      "id": "a-beats-b",
      "claim": "Method A beats B on the shared test split.",
      "supports": [{"member": "exp-a", "id": "acc-a"}],
      "contradicts": [{"member": "exp-b", "id": "acc-b"}],
      "source": "REPORT.md#C1",
      "metadata": {}
    }
  ],
  "relationships": [
    {
      "source": {"member": "exp-a", "id": "run-a"},
      "target": {"member": "exp-b", "id": "run-b"},
      "type": "derived_from",
      "note": null
    }
  ]
}
```

Rules:

- `format` and `version` are literal. Unknown fields are rejected. `id` matches
  `^[A-Za-z0-9][A-Za-z0-9._-]*$`; `name` is non-blank.
- `members` is non-empty. Member `id` matches the same pattern and is unique. `path` is a
  relative folder with forward slashes, no `..`, no absolute path, and resolves inside the
  workspace root; two members that resolve to the same folder or the same manifest file are
  a duplicate. `manifest` defaults to `runevidence.json`.
- A member manifest is any supported schema 0.1–0.10. No migration is implied.
- `claims` and `relationships` are optional and default to empty.
- A qualified reference is `{"member": "<member-id>", "id": "<local-id>"}`. In text and on
  the command line the canonical form is `member#local-id`; `#` is not valid in either ID
  pattern.
- Workspace claims and relationships are validated, not interpreted as truth. `supports` and
  `contradicts` may cite any object in a member (project, run, artifact, result, evidence);
  a result or evidence target is required for a claim to count as supported evidence.
- Relationship types are the delivered manifest types. `input_to` keeps its manifest
  direction reversal.

## 3. Validation and failures

`rungraph validate` loads the workspace, loads every member manifest through the existing
loader, runs the existing per-member checks, and then checks the workspace layer.

Workspace-layer findings (fixed codes):

| Code | Meaning |
| --- | --- |
| `workspace-unreadable` | the document is missing, unreadable, or not valid JSON |
| `workspace-schema-invalid` | shape/type/unknown-field violation |
| `workspace-duplicate-member` | duplicate member ID, or two members resolving to one manifest |
| `workspace-member-missing` | member path or manifest does not exist |
| `workspace-member-invalid` | the member manifest cannot be loaded or validated |
| `workspace-reference-unresolved` | a qualified reference names an unknown member or local ID |
| `workspace-reference-cycle` | a relationship/claim graph cycle across members |
| `workspace-claim-unverified` | a claim cites no result/evidence target |

Exit codes: `0` no errors (warnings allowed), `1` evidence errors (workspace or member),
`2` workspace/manifest load or usage failure. `--strict` makes warnings fail. `--json` emits
one envelope with `workspace`, `members` (per-member counts and findings), `findings` and
`exit_code`. `--directory-artifacts` is passed through to member checks.

Member findings keep their existing codes, subjects and paths; a finding from member `m` is
qualified as `m#subject` in the workspace output, and its `path` is prefixed with the member
path. A member that fails to load contributes one `workspace-member-invalid` error and does
not stop the other members from being reported.

## 4. Identity and graph

- A global node identity is `(member-id, local-id)`; the canonical text form is
  `member#local-id`.
- Nodes: member, project, run, artifact, result, evidence. Edges: the delivered inline
  relationships (`result_of`, `reported_in`, `produced_by`, `input_to` reversed for
  traversal, `derived_from`), workspace relationships, and claim `supports`/`contradicts`.
- A reference to an unknown member or local ID is a node with `status: "unresolved"`, never
  dropped.
- Cycles are detected across members and reported with one example path per cycle.
- `graph` output: `format: "rungraph-graph"`, `version: "0.1"`, `nodes` (id, member, kind,
  status, label), `edges` (source, target, relation, origin), `cycles`, `unresolved`. DOT is
  an optional `--format dot` rendering of the same graph; it is not a second model.

## 5. Consumers

### `report`

One document with four explicitly labelled sections:

1. **Current evidence**: per-member check counts, then every error/warning.
2. **Claims**: each workspace and member claim classified as `supported` (all cited evidence
   resolves and no error-level finding touches it), `at-risk`, `contradicted` (recorded
   contradiction present), `unresolved` (a cited reference does not resolve), or `uncited`.
   A claim may have more than one state; all are printed. "Supported" is an evidence state,
   not scientific truth.
3. **Assertion changes**: only with `--baseline DIR`, where DIR is a prior copy of the
   workspace root. Each member's recorded assertions are compared with the baseline using the
   existing fingerprint projection; added, removed and changed objects are listed. Filesystem
   changes are not inferred from the baseline.
4. **Impact**: for every error-level file/result finding, the claims whose cited evidence
   depends on the affected object, with the dependency path.

Text and `--json` carry the same facts; JSON is the authoritative machine form.

### `impact`

`rungraph impact ROOT --member M --id ID [--json]` traverses from the qualified object to
every claim that depends on it, in any member, and prints the path. Unresolved references are
named, not followed. Exit `0` when the object resolves, `1` when it does not, `2` on load
failure.

### `compare`

`rungraph compare ROOT --metric NAME [--direction max|min] [--json]` aligns recorded results
by name across members, with explicit missing/non-numeric accounting. For each member pair
that has a comparability verdict available under the existing comparability surface, the pair
status is shown; otherwise the pair is `unavailable: schema <version>` or `unavailable:
adapter`. No ranking implies scientific validity or a fair comparison.

## 6. Command surface

A new console script `rungraph` in the core package (the `runevidence` CLI is unchanged):

```
rungraph init [ROOT] [--id ID] [--name NAME] [--force] [--dry-run]
rungraph validate [ROOT] [--json] [--strict] [--directory-artifacts]
rungraph graph [ROOT] [--from MEMBER#ID] [--depth N] [--format json|dot]
rungraph report [ROOT] [--baseline DIR] [--json]
rungraph impact [ROOT] --member M --id ID [--json]
rungraph compare [ROOT] --metric NAME [--direction max|min] [--json]
rungraph claim add|edit|remove ROOT --file PATCH [--dry-run] [--json]
rungraph relationship add|remove ROOT --file PATCH [--dry-run] [--json]
```

`init` discovers `runevidence.json` files under ROOT (skipping ignored directories and
symlinked folders), assigns member IDs from folder names with collision suffixes, and writes
a fresh document. `--force` backs up the previous document (`.bak`, `.bak.2`, …) and does not
merge hand-written claims or relationships; it prints that fact. `--dry-run` writes nothing.

Authoring uses a strict JSON patch document, plans before writing, refuses stale bytes,
takes the same cooperative admission lock where the host supports it, and never writes on a
refusal. `claim`/`relationship` IDs are unique within the workspace document.

## 7. Migration, refusals and fixed texts

- The workspace format has its own `version`. A future version is refused with
  `workspace-schema-invalid` and the fixed text `unsupported rungraph.json version <v>`; no
  inference or best-effort load.
- No manifest is migrated or rewritten by any `rungraph` command.
- `init` refuses to overwrite an existing document without `--force`:
  `rungraph.json already exists; use --force to back it up and replace it`.
- A member manifest at a schema newer than the running core is
  `workspace-member-invalid` with the existing unknown-version text; it is never downgraded.
- Path escapes, absolute paths, `..`, drive-shaped paths and duplicate resolutions are
  refused before any write.
- No command loads model weights, executes manifest content, or follows a URI.

## 8. Module admission checklist

- New module `src/runevidence/workspace.py`: document models (Pydantic, `extra="forbid"`),
  loader/writer, discovery, validation, reference resolution, graph build.
- New module `src/runevidence/run_workspace.py` (or `workspace_consumers.py`): report,
  impact and compare projections over the loaded workspace; no artifact reads beyond the
  existing per-member checks.
- `src/runevidence/cli.py` unchanged; a new `src/runevidence/rungraph_cli.py` and a
  `rungraph` console script in `pyproject.toml`.
- `src/runevidence/checks/__init__.py`: reuse `run_checks` per member with the existing
  flags; no change to check behavior.
- Existing fingerprint/comparability modules are imported read-only; no change.
- Tests: `tests/test_workspace_document.py`, `test_workspace_discovery.py`,
  `test_workspace_validate.py`, `test_workspace_graph.py`, `test_workspace_report.py`,
  `test_workspace_impact.py`, `test_workspace_compare.py`, `test_workspace_cli.py`.
- Docs: `docs/rungraph-workspace.md`, README section and command reference; the docs-contract
  test is extended to require the `rungraph` routes.
- Packaging: the `rungraph` entry point is built and checked by the advisory packaging job.

## 9. Verification plan

- Document unit tests: every field, duplicate IDs/paths, path escapes, unknown fields,
  version refusal, qualified-reference grammar, claims/relationships round-trip.
- Discovery: nested roots, ignored dirs, symlinked folders, collision suffixing, `--dry-run`
  leaves the folder untouched, `--force` backups.
- Validate: a three-member workspace (generic, MLflow, TensorBoard) with one missing file, one
  changed value, one unresolved reference, one cycle; exit codes and JSON envelope.
- Graph: every node/edge class, unresolved nodes, a cross-member cycle, `--from`/`--depth`,
  DOT output.
- Report: all five claim states, baseline added/removed/changed, impact paths, text/JSON
  parity.
- Impact: cross-member propagation, unresolved refs, missing object.
- Compare: values, missing, non-numeric, schema-unavailable pairs.
- Authoring: add/edit/remove claim and relationship, dry-run, stale bytes, lock refusal,
  nothing written on any refusal.
- Mutation probes: duplicate detection, reference resolution, cycle detection, claim state,
  impact traversal, baseline diff, authoring refusal.
- End-to-end: a realistic workspace from the existing examples, then a deliberate evidence
  corruption, `report` and `impact`, `rehash` the member, `report` again.
- Full suite on 3.12 and 3.10/pydantic 2.6; wheel smoke with the `rungraph` entry point.

## 10. Decisions requested from review

1. A separate `rungraph` console script rather than `runevidence workspace` subcommands.
2. `rungraph.json` version `0.1` with the exact field set above; any missing field?
3. Qualified text form `member#local-id` (IDs may contain `:` and `/`, so `:` is unusable).
4. Workspace claims citing any object, with only result/evidence targets counting as support.
5. Baseline as a prior copy of the workspace root, compared through recorded assertions only.
6. Authoring in the same milestone, not a later slice.
7. DOT output as a rendering of the JSON graph, not a separate model.
8. Cross-member comparability initially `unavailable` for schemas 0.3–0.10 until the
   condition/comparability coverage slice lands.

## 11. Non-goals

No manifest schema change; no new locator or value kind; no server, database, GUI or network;
no automatic inference of claims or relationships; no scientific-truth claim; no change to the
existing `runevidence` command behavior or output.
