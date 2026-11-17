# RunGraph workspaces

RunEvidence verifies one experiment manifest. A **workspace** composes several manifests into
one project-level view: `rungraph.json` at a folder root names the members and can carry
cross-member claims and relationships. Everything stays offline and file-based.

It never rewrites a member manifest. The workspace document itself is authored through
`rungraph patch`, `rungraph member`, `rungraph claim` and `rungraph relationship`.

## Quick start

```bash
# 1. See which manifests a folder contains, without writing anything.
rungraph discover path/to/project

# 2. Write a workspace document from that discovery.
rungraph init path/to/project --id study --name "Study"

# 3. Validate every member and the workspace layer.
rungraph validate path/to/project

# 4. Ask project-level questions.
rungraph report path/to/project
rungraph graph path/to/project
rungraph impact path/to/project --from artifact:exp-a#test-data
rungraph compare path/to/project --metric accuracy
rungraph matrix path/to/project
rungraph leaderboard path/to/project --metric accuracy --direction max
```

`discover` exits `2` when it finds no manifests, a duplicate or nested member, an unreadable
manifest, or hits the 10,000-manifest, 10,000-folder or depth-64 cap. `init` refuses an empty discovery and
writes nothing; it validates the ID and name before writing, holds the workspace lock, writes
one canonical document through an atomic replace, and only then reports success. A failed
`--force` leaves the previous document intact; the backup is a byte copy, so a write failure
cannot lose it. `compare` takes an optional `--run MEMBER#RUN` to restrict the metric rows to
one run, and `leaderboard --top N` limits the ranking (default 10).

## The document

```json
{
  "format": "rungraph-workspace",
  "version": "0.1",
  "id": "study",
  "name": "Study",
  "members": [
    {"id": "exp-a", "path": "experiments/a"}
  ],
  "claims": [
    {
      "id": "a-beats-b",
      "claim": "Method A beats B on the shared split.",
      "supports": [{"member": "exp-a", "id": "acc-a"}],
      "contradicts": [{"member": "exp-b", "id": "acc-b"}]
    }
  ],
  "relationships": []
}
```

- `members` is required and non-empty. Member IDs are stable and unique; paths are relative
  and may not escape the root. A member path whose folder or any parent is a symbolic link is
  refused, as is a folder that resolves outside the workspace. Two members resolving to the
  same manifest (including hardlinks) and nested members are refused.
- A member's optional `manifest` filename is the authority for loading, checking, graphing,
  comparing and reporting that member. The default is `runevidence.json`; a declared
  `custom.json` is used everywhere instead of falling back to the default, and `init --force`
  preserves the declaration.
- Member manifests are read through the protected descriptor walk, so a folder or parent
  swapped for an outside link after the containment check cannot be read. The exact bytes
  parsed for the graph become the member's snapshot: verification re-reads through the same
  walk and fails if the digest changed, so checks can never pass against a replacement
  manifest while the graph still describes the original.
- A claim cites member results/evidence, another workspace claim (`{"claim": "id"}`), or
  context objects (artifact/run/project). Only result/evidence/claim citations count as
  support.
- Relationship IDs are unique; the type set is the manifest relationship set. Claims are the
  only claim-citation mechanism.
- Unknown fields, duplicate JSON keys and non-finite numbers are refused.

## Claim states

A claim is classified from its recorded structure and the member checks:

| Label | Meaning |
| --- | --- |
| `supported` | cited evidence resolves and no error touches it |
| `at-risk` | cited evidence or its dependency closure has an error, or a reference is unresolved |
| `contradiction-recorded` | a `contradicts` target is recorded (not a truth judgement) |
| `unresolved` | a cited member, local ID or claim does not resolve |
| `uncited` | no result/evidence/claim support target |

A member whose manifest cannot be re-read, changed since it loaded, or checked is an error,
never a silent pass: its findings include the load failure (or `member-check-failed`) and every
claim that depends on that member is `at-risk` — its own evidence claims and any workspace claim
that cites its results, artifacts or runs. `validate` and `report` then exit `1`, and no member
or workspace claim can remain `supported`.

A claim can carry several labels. `supported` never appears with `at-risk` or `unresolved`;
`contradiction-recorded` may accompany `supported`. A member claim keeps its delivered
semantics: artifact and run targets are valid support, and a broken contradiction target does
not make it `at-risk`.

"Supported" is an evidence state, not scientific truth. Recorded conditions, artifact
integrity and claim support remain distinct guarantees.

## Graph and identity

A node ID is `kind:member#local` (`result:exp-a#acc`), `member:<id>`, or
`claim:<workspace>#<claim-id>`. `impact --from` accepts these forms, and the `member#local`
shorthand when it resolves unambiguously.

The graph includes inline manifest edges, member-declared relationships, member evidence
citations, workspace relationships and workspace claim citations. Artifact/run/project claim
citations are context, not traversed edges. Unresolved references are named, not dropped;
cycles are reported per strongly connected component with a bounded example list.

## Report, impact and comparison

- `report` prints current evidence per member, every claim's states, and the claims affected
  by each error-level finding. `--directory-artifacts` opts into directory checks and is
  stated in the header when absent; `--strict` makes warnings fail.
- `impact` returns the claims whose dependency closure contains the requested node.
- `compare` prints recorded values for one metric; `matrix` lays members against metrics;
  `leaderboard` ranks finite numeric values with ties sharing a rank. Rows are identified by
  `(member, run, result ID)`, so duplicate result names are distinct rows and an ambiguous
  matrix cell is counted, never averaged.
- `compare` reports each row pair's recorded-condition status (`comparable`,
  `not-comparable` or `unavailable`) through the experimental comparability surface.
  `leaderboard` shows `conditions: available`, meaning only that the route can carry a
  recorded-condition verdict; it does not mean the runs have populated conditions, and
  `matrix` has no conditions field. Use `compare` or `comparability` for the pair-level
  answer. No ranking implies a fair comparison or scientific validity. Pairwise comparison is
  bounded at 50 rows per metric.

## JSON and exit codes

`discover`, `init`, `validate` and `report` print text by default and accept `--json` for a
`rungraph-<command>` envelope with `version: "0.1"` and `exit_code`. `compare`, `matrix` and
`leaderboard` always emit JSON; `--json` is accepted for symmetry, not required. `graph`
selects JSON or DOT with `--format`; `impact` always emits JSON. Errors use
`{"format": "rungraph-error", "command": ...}`.

| Command | 0 | 1 | 2 |
| --- | --- | --- | --- |
| `discover` | listed | `--check` found errors | empty, duplicate/nested, limits, unreadable |
| `init` | written or dry-run | — | refusal |
| `validate` | no errors | evidence errors or strict warnings | workspace/member load, usage |
| `graph` | built | `--fail-on-unresolved` and unresolved | load/usage |
| `report` | no errors | evidence errors or strict warnings | workspace/member load, usage |
| `impact` | object resolves | — | object/workspace load failure |
| `compare`/`matrix`/`leaderboard` | rendered | — | load/usage |
| `patch`/`member`/`claim`/`relationship` | written or dry-run | — | refusal, stale SHA, unreadable file |

## Authoring

```bash
rungraph member add path/to/project --id exp-c --path experiments/c \
  [--manifest name.json] [--role ROLE] \
  --expected-sha256 "$(sha256sum path/to/project/rungraph.json | cut -d" " -f1)"
rungraph member move path/to/project --id exp-c --path experiments/c2
rungraph member rename path/to/project --id exp-c --new-id exp-d
rungraph claim add path/to/project --file claim.json --expected-sha256 ...
rungraph patch path/to/project --file patch.json --expected-sha256 ...
```

Every authoring command requires `--expected-sha256` once the document exists (the flag wins
over the patch file's field), takes a cooperative POSIX folder lock, validates all references
before writing, and stages/fsyncs/replaces the document atomically. A refusal writes nothing.
`--dry-run` prints the plan and writes nothing. `member rename` updates every qualified
reference in the same transaction; `set-workspace` is available only through `patch`.

## Baseline

`rungraph report path/to/project --baseline prior-copy/` compares each member's recorded
assertions with a prior copy of the workspace root. The projection covers every manifest
schema, reports added/removed/changed objects by `(kind, id)`, and reports added, removed,
re-pathed and re-keyed members plus workspace claim/relationship changes. It is a recorded
assertion diff, not a claim that files on disk changed. The text report prints the active
directory-check policy and the baseline diff; `--json` carries the same data in
`check_policy` and `baseline`.

## Limits and non-goals

No manifest schema, locator or reader change; no network, server, database or GUI; no
manifest rewrite. The workspace document is limited to 16 MiB, 10,000 members, 10,000 claims
and 10,000 relationships. Workspace claim states are evaluated with an explicit stack, so a
valid 10,000-claim chain does not hit Python's recursion limit. Recursive file scans, including
workspace discovery and the MLflow file-store walk, are bounded at depth 64, 10,000 folders and
1,000,000 files; exceeding a cap refuses the scan instead of returning a partial list.
