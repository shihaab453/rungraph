# Comparability schema 0.2

Status: schema 0.2 is stable (a compatibility promise); the comparison verdict
CLI remains experimental. Stable is not production qualification and does not
make `validate` prove comparability. The read-only preflight is the supported
readiness check. Date: 2026-11-12.

This guide describes the optional manifest schema 0.2 fields, their selector
rules and how the `comparability` command and its read-only preflight read
them. The binding
design is [Stage 3 comparability schema and versioning RFC
v0.3](https://github.com/shihaab453/rungraph/blob/main/docs/comparability-stage3-schema-rfc.md)
(sections 7, 8, 9 and 10). The published artifacts are
[`schema/runevidence.schema.json`](https://github.com/shihaab453/rungraph/blob/main/schema/runevidence.schema.json)
(0.1) and
[`schema/runevidence-0.2.schema.json`](https://github.com/shihaab453/rungraph/blob/main/schema/runevidence-0.2.schema.json)
(0.2).

## Compatibility and opt-in

Schema 0.1 is the default and existing behavior is unchanged. A manifest opts
into 0.2 only with an explicit `"schema_version": "0.2"`; an omitted
`schema_version` still means 0.1. A 0.1 manifest that contains any 0.2-only key
is a schema error even when that key is `null`, and unknown future versions are
rejected. A reader that only understands 0.1 rejects every 0.2 manifest,
including one that adds no 0.2 fields.

Reading never migrates or rewrites evidence. Serialization preserves the
declared version: 0.1 output omits every 0.2-only key, and 0.2 output keeps
omitted members omitted while preserving explicit `null` and explicit empty
values. No command infers a version from field presence.

## New optional fields

| Owner | Field | Meaning |
| --- | --- | --- |
| `runs[]` | `conditions` | First-class recorded evaluation context for the run. |
| `runs[].conditions` | `evaluation_split` | Evaluation split name, e.g. `test`. |
| `runs[].conditions` | `evaluation_protocol` | Object with `protocol_id` and optional `version`. |
| `runs[].conditions` | `evaluation_dataset_artifact_id` | Selector naming an `Artifact.id`. |
| `runs[].conditions` | `model_checkpoint_artifact_id` | Selector naming an `Artifact.id`. |
| `runs[].conditions` | `source_revision` | Recorded source revision. |
| `runs[].conditions` | `environment` | Bounded nested JSON value. |
| `results[]` | `metric_definition` | Object with `definition_id` and optional `higher_is_better`, `averaging`, `version`. |
| `artifacts[]` | `dataset_id` | Semantic dataset label; not an artifact ID. |

All are optional. Nested optional members (`version`, `higher_is_better`,
`averaging`) are optional and must be non-null when present. First-class
strings that name a split, revision, protocol, definition or label are
non-blank and are not trimmed or case-folded. `dataset_id` is a semantic label;
several artifacts may share one.

## Presence and null rules

The distinction between an absent field, an explicit `null` and an explicit
empty value is significant and survives dump/reload.

- `conditions` absent: no first-class context; every dimension falls back to
  the legacy metadata conventions independently.
- `conditions: {}`: valid; every child is absent and every dimension falls
  back independently.
- `conditions: null`: invalid.
- An absent child falls back to its legacy metadata counterpart. An explicit
  `null` child is a recorded **UNKNOWN** and never falls back.
- An absent dataset or checkpoint selector keeps the Stage 2 rule: zero
  matching attached artifacts means missing evidence, one is selected, and
  multiple require an explicit selector. An explicit selector of `null` is
  invalid.
- `evaluation_protocol: {}` and `metric_definition: {}` are invalid because
  their IDs are required.
- `environment: {}` is present-but-empty, which comparison rejects
  (`INPUT_SHAPE_UNSUPPORTED`); it does not fall back. Environment values are
  bounded nested JSON: depth 8, 16 KiB serialized, keys and strings up to
  1024 characters, finite numbers only.

An explicit `null` for `evaluation_split`, `evaluation_protocol`,
`source_revision`, `environment` or `metric_definition` is valid and means
UNKNOWN. A protocol, metric definition or environment resolves as a whole
value; members are never merged across first-class and legacy forms.

## Selector reference rules

`evaluation_dataset_artifact_id` and `model_checkpoint_artifact_id` name an
`Artifact.id` in the same manifest, not `Artifact.dataset_id`. Each selector
must:

1. name an artifact that exists in the same manifest;
2. name an artifact whose `kind` matches the selector (`dataset` or
   `checkpoint`);
3. have an `input_to` relationship from that artifact to the selected run.
   `Artifact.run_id` records production and never satisfies a selector.

An explicit selector never falls back to legacy selection. An unknown ID, a
wrong kind, a directory artifact or a missing `input_to` edge fails the
comparison. When several datasets or checkpoints are attached and no selector
is present, comparison cannot choose and reports unsupported input.

A selected directory artifact is unsupported. A selected volatile or unhashed
file artifact is not rejected by itself, but it never contributes a verified
version observation, so the affected dimension remains missing evidence rather
than becoming a verified match.

`validate` and `inspect` check every declared selector for **every** run,
including runs that are not compared. The findings use fixed sanitized codes
and never echo artifact IDs, run IDs, selector values, paths or metadata:

| Code | Meaning |
| --- | --- |
| `condition-reference-missing` | The selector names no artifact in the manifest. |
| `condition-reference-wrong-kind` | The artifact's `kind` does not match the selector. |
| `condition-reference-unsupported` | The artifact is a directory, which comparison does not support. |
| `condition-reference-not-consumed` | No `input_to` relationship attaches the artifact to that run. |

## Comparison semantics

For each dimension independently:

1. A present first-class value is used.
2. An explicit `null` is UNKNOWN; there is no legacy fallback.
3. An absent first-class value uses the legacy Stage 2 fallback.
4. No available source is MISSING.

If a first-class value and a matching legacy value are both present, they must
be canonically equal. Equal duplicates collapse to one observation. Unequal or
malformed duplicates are an inconsistent recording and fail with
`INPUT_SHAPE_UNSUPPORTED`.

Canonical equality follows the core rules: object key order is irrelevant,
array order is significant, booleans are distinct from numbers, strings are
compared exactly, and `1` equals `1.0` only when their exact numeric values are
equal.

Bounds are enforced on both candidates of a dimension before canonical
equality or duplicate-conflict checks, so an oversized value is never
canonicalized unbounded. The baseline side completes selection, shape, bounds
and conflict checks before the target side begins, so a baseline failure takes
precedence over a target failure. Mixed 0.1/0.2 operands follow the same order.

The CLI contract is unchanged: the same versioned JSON envelope with `--json`,
and exit `0` for comparable recorded conditions, `1` for different mandatory
recorded conditions and `2` for insufficient evidence, a rejected input, a
load failure, invalid arguments or an internal error.

## Published schemas

- `schema/runevidence.schema.json` remains the committed 0.1 artifact
  (`$id` `urn:runevidence:manifest:0.1`). `runevidence schema` still prints
  this 0.1 schema.
- `schema/runevidence-0.2.schema.json` is the distinct committed 0.2 artifact
  (`$id` `urn:runevidence:manifest:0.2`). It requires an explicit
  `"schema_version": "0.2"`.
- The schema files are repository artifacts. Packaging does not ship the
  `schema/` directory; the wheel and sdist publish the Python package and
  top-level project files only.

Both schemas use JSON Schema Draft 2020-12. Existence, object-kind resolution
and `input_to` attachment remain runtime graph checks; they are not expressible
in the published schema. Standard JSON Schema number semantics can treat some
mathematically integral numbers differently from the runtime's lexical typing;
runtime canonical equality follows the rules above.

## Worked example

The committed
[`examples/comparability-recorded-0.2/`](https://github.com/shihaab453/rungraph/tree/main/examples/comparability-recorded-0.2)
folder has four 0.2 manifests. Each run consumes `train-data`,
`validation-data` and `test-data`, and explicitly selects `test-data` for the
dataset dimension:

```bash
runevidence comparability examples/comparability-recorded-0.2/baseline.json \
  examples/comparability-recorded-0.2/target-comparable.json \
  --baseline-result baseline-run-result \
  --target-result target-comparable-run-result --json
# exit 0: COMPARABLE_RECORDED_CONDITIONS (ALL_MANDATORY_DIMENSIONS_MATCH)

runevidence comparability examples/comparability-recorded-0.2/baseline.json \
  examples/comparability-recorded-0.2/target-drifted.json \
  --baseline-result baseline-run-result \
  --target-result target-drifted-run-result --json
# exit 1: NOT_COMPARABLE_RECORDED_CONDITIONS
#         (MANDATORY_DIMENSION_DIFFERENT:evaluation_split)

runevidence comparability examples/comparability-recorded-0.2/baseline.json \
  examples/comparability-recorded-0.2/target-insufficient.json \
  --baseline-result baseline-run-result \
  --target-result target-insufficient-run-result --json
# exit 2: INSUFFICIENT_EVIDENCE
#         (MANDATORY_DIMENSION_INSUFFICIENT:evaluation_split)
```

`target-drifted.json` records `validation` where the baseline records `test`.
`target-insufficient.json` omits `evaluation_split`, so the target has no
observation for that mandatory dimension. The example folders are test data;
comparability never reads the CSV files they describe.

## Authoring limits

`init` and the bundled adapters still write schema 0.1. `migrate` is the
explicit opt-in to 0.2. There is no `--schema-version` flag, no automatic 0.2
inference and no automatic migration. A manifest that declares 0.2 but carries
no first-class fields has only been relabelled: it is a compatibility
declaration, not comparability readiness. It keeps every legacy fallback and
compares no differently from its 0.1 equivalent except that 0.1-only readers
reject it.

`init --force` refuses a manifest that declares schema 0.2, valid or not,
before adapter discovery, backup or save; nothing is written. Move the manifest
aside, run `init`, then `migrate --to 0.2` and reapply the first-class values
from the moved file. A manifest whose raw JSON does not parse keeps the older
backup-then-regenerate path. Because `init` regenerates from disk, treat
migrated 0.2 manifests as hand-authored for `artifact.dataset_id` and
`result.metric_definition`; `run.conditions` can be authored with the
`conditions` command or by hand. The legacy fingerprint path continues to
reject 0.2 unless a separate design changes it.

### Authoring conditions

```text
runevidence conditions PATH --run RUN_ID [--patch FILE|-] [--clear FIELD]...
  [--dry-run] [--json]
```

The `conditions` command authors the six `runs[].conditions` children. The
target must already declare `"schema_version": "0.2"`; a 0.1 target is refused
with instructions to run `runevidence migrate PATH --to 0.2` first. The
command never migrates, changes `schema_version` or creates a 0.2 field on a
0.1 manifest. Hand-editing the fields remains supported.

The patch is one strict JSON object read from a local file or from stdin with
`--patch -`; values never appear on the command line. At least one of
`--patch` or `--clear` is required. Its keys are exactly
`evaluation_split`, `evaluation_protocol`,
`evaluation_dataset_artifact_id`, `model_checkpoint_artifact_id`,
`source_revision` and `environment`. Unknown top-level keys, duplicate keys at
any depth, unknown protocol members, trailing data, invalid UTF-8, non-finite
numbers and values beyond the comparison bounds are refused. The patch is
untrusted data, not code; it is never printed, and the output never contains a
patch value, nested content, selector or artifact ID, or path in JSON. The patch
file itself is sensitive at rest and remains the caller's responsibility.

Set, clear and null follow the presence matrix:

| State | Result |
| --- | --- |
| Key absent | The child is unchanged. |
| Key present with a value | The child is set to that value. |
| Key present with `null` for `evaluation_split`, `evaluation_protocol`, `source_revision` or `environment` | The child is set to explicit `null`, a recorded UNKNOWN with no fallback. |
| Key present with `null` for either selector | Refused; selectors have no UNKNOWN state. |
| `evaluation_protocol.version` is `null` | Refused; a nested optional member is omitted or a value. |
| `evaluation_protocol` is `{}` | Refused; `protocol_id` is required. |
| `environment` is `{}` | Refused at authoring even though the schema accepts it; comparison rejects a present-empty environment. |
| `--clear FIELD` | The child key is removed and its legacy fallback is restored. |
| `conditions` absent and a child set | A new `conditions` object is created. |
| `conditions` absent and only absent children cleared | No change; no empty object is created. |
| The last child is cleared or otherwise removed | The `conditions` key is removed entirely. |

A patch key and `--clear FIELD` for the same field in one invocation are
refused; the patch is applied as a whole, and one invalid field refuses every
field. A clear of an absent child is harmless. After an operation that leaves
no children, the `conditions` key is removed entirely; `conditions: null` is
never written. This means a clear of an absent child from an existing
`conditions: {}` removes the empty parent and is a real change, not a no-op. An
empty patch with no clears does not normalize an existing `conditions: {}`.

For the two selectors, the named artifact must exist in the same manifest, have
kind `dataset` or `checkpoint`, not be a directory, and be attached to the
selected run by an `input_to` relationship. There is no fallback to legacy
selection and no relationship is synthesized. Only selectors supplied by the
patch are validated; an untouched pre-existing invalid selector does not block
an unrelated edit and remains a `validate`/`inspect` finding.

For a changed edit, `--dry-run` also performs the stale read; a no-op dry run
returns before it. It writes nothing. A real changed run creates or reuses one
`.bak` generation under the same rules as `migrate`, then replaces the
manifest through `save_raw`. Exit `0` means the update was written, was already
current, or was a dry run; exit `2` means a refusal or failure and no
replacement. Text output may show the resolved manifest and backup paths; JSON
output is path-free. The command does
not run `validate`, `inspect` or `comparability`, does not check legacy
duplicates, does not read artifact bytes or verify identity, and does not prove
comparability readiness or scientific validity. Use those commands as the
review path.

The full decision record is
[Stage 3d-3 conditions authoring RFC v0.3](https://github.com/shihaab453/rungraph/blob/main/docs/comparability-stage3d3-conditions-rfc.md).

### Readiness preflight (read-only)

`runevidence comparability BASELINE TARGET --preflight` is the text-only,
read-only readiness mode; it writes nothing and accepts no `--json`. With no
result selectors it lists both sides' result candidates in manifest order,
capped at 20 per side with a fixed truncation line. A side with duplicate IDs is
shown as `unavailable: DUPLICATE_ID` and the list still exits `0`. With both
selectors it reports exactly that pair: three recording states per mandatory
dimension (`present`, `recorded-unknown`, `missing`), the dataset attachment as
`mappable`/`not-mappable`, and a fixed reason when not ready. It uses the
bridge's own mapping/bounds and the core's own validation/projection, including
`SAME_RUN_ID` and `NUMBER_LIMIT`. Exit `0` means the list worked or the pair is
ready; `1` means not ready or a bridge mapping/bound refusal; `2` means a load
failure, unknown result ID or invalid arguments. Readiness does not establish
matching values, artifact integrity or a successful comparison, and a ready
pair can still be `INSUFFICIENT_EVIDENCE` for a case/whitespace difference.

## Authoring order and migration

The documented order is mandatory:

1. Write the manifest (`init` or an adapter writes schema 0.1).
2. Run `runevidence migrate PATH --to 0.2` (or, if an unusual layout cannot be
   loaded by `migrate`, an explicit hand edit that changes only
   `schema_version`).
3. Author `run.conditions` with the `conditions` command (or by hand), then
   add `artifact.dataset_id` and `result.metric_definition` by hand.
4. Review the result with `validate` and `inspect`, then use `comparability`
   to check recorded-condition readiness.

Adding any 0.2-only field while the manifest still declares 0.1 is a schema
error, including a field whose value is `null`. The safe order is therefore to
declare 0.2 first and add the fields after. Neither `init`, an adapter,
`validate` nor `comparability` changes the declared version.

Migration or relabelling changes only the declared version. It adds no
first-class field, does not make the manifest comparable and does not verify
artifacts. The consequences are:

- a reader that only understands 0.1 rejects every 0.2 declaration, including
  one that adds no 0.2 fields;
- the legacy metadata fallback still applies per dimension, so an absent
  first-class value produces the same observation before and after migration;
- unequal first-class and legacy duplicates are an inconsistent recording and
  fail comparison (`INPUT_SHAPE_UNSUPPORTED`), not a first-class win;
- selectors require the artifact ID, the expected artifact `kind` and an
  `input_to` attachment to that run; a selector never falls back to legacy
  selection.

### Explicit migration

```text
runevidence migrate PATH --to 0.2 [--dry-run] [--json]
```

`PATH` is the experiment folder or the manifest file. `--to` is required, and
only `0.2` is accepted. This is the explicit opt-in: `migrate` never infers a
version from field presence, and the schema version is supplied only through
`--to`. It loads one manifest with the normal validation rules and rewrites no
artifact.

Only the top-level `schema_version` value changes, and it is written first.
Every other decoded value, type, presence and object-key order is preserved,
including the relative order of the other top-level keys and all nested keys,
subject to the documented canonical writer normalization: number spelling and
formatting may change (for example `1e2` becomes `100.0`, an underflowing value
stays `0.0`, a float `-0.0` keeps its sign), and indentation is normalized.
The original bytes are preserved only in the backup.

Backups use the same generation scheme as `init --force`:
`runevidence.json.bak` first, then `.bak.2`, `.bak.3`, and so on, with the
highest number newest. Prior generations are never overwritten, modified or
deleted. A new backup is created with the manifest's POSIX permission bits,
narrowed by the process umask, and is written and read back before the manifest
is touched; ACLs, ownership and extended attributes are not copied or verified.
The newest generation is reused only when it is a regular non-symlink file, not
the manifest or an alias of it, with identical bytes and POSIX permission bits
no wider than the manifest's; otherwise the next generation is created. A
failed backup or replacement leaves the manifest unreplaced and removes only an
incomplete file created by that attempt.

`--dry-run` performs the load, the preservation proof and the pre-backup stale
read, then reports what would change. It writes nothing, creates no backup and
does not enumerate backup generations. A manifest that already declares 0.2 is
a successful no-op with or without `--dry-run`: exit `0`, no backup and no
write. A real successful migration also exits `0`.

A refusal exits `2`, prints one closed code in text or one fixed JSON error
document, and does not replace the manifest:

| Code | Meaning |
| --- | --- |
| `RESERVED_PATH` | The operand resolves to a backup name (`runevidence.json.bak` or `.bak.N`); choose the manifest. |
| `MANIFEST_LOAD_FAILED` | The target is missing, unreadable, malformed or not a valid manifest. |
| `SERIALIZATION_FAILED` | The migrated document cannot be serialized and re-read safely, for example because of a lone surrogate or excessive nesting. |
| `READ_FAILED` | The target could not be re-read before the backup stage. |
| `STALE_MANIFEST` | The bytes observed at a stale check differ from the loaded snapshot. |
| `WRITE_FAILED` | The backup or migrated manifest could not be written. |
| `INTERNAL_ERROR` | A preservation proof or candidate validation failed internally; no path, value or exception text is exposed. |
| `INVALID_ARGUMENTS` | The CLI grammar is invalid, such as a missing `PATH` or `--to`, an extra operand or an unknown option. |
| `UNSUPPORTED_TARGET` | `--to` names any version other than `0.2`, including a downgrade. |

Text output may show the resolved manifest and backup paths. JSON output is
path-free and contains no IDs, values or hashes. An operand that is a symlink is
resolved: the resolved target is written and the backup is created beside that
target. The pre-backup read and the final byte check in `save_raw` are stale
checks, not locks; another writer can still race between them, so run one
writer at a time per folder. `migrate` reads no artifact, calls no adapter and
uses no network or environment state. An empty 0.2 manifest remains only a
compatibility declaration, not comparability readiness.

The full decision record is
[Stage 3d authoring RFC v0.4](https://github.com/shihaab453/rungraph/blob/main/docs/comparability-stage3d-authoring-rfc.md).

## Privacy

`comparability` loads exactly two whole manifests and resolves references in
memory. It does not read artifacts, repositories, URLs or environment state,
does not write anything and does not use the network. The comparison verdict
output omits raw values, IDs, names, paths, hashes and metadata keys.

The read-only preflight is the documented exception for result IDs: successful
list output and completed pair readiness print result IDs taken from the loaded
manifests (a >1024-character ID becomes a fixed placeholder). List mode remains
successful output even when a candidate is shown unavailable. Pair error
output—load failures, unknown IDs, invalid arguments and pair bridge
errors—remains ID-free. Preflight never prints raw values, member names,
artifact paths, digests, metadata or OS text. The new selector findings from `validate` and
`inspect` use fixed messages and sanitize subject and path.

`migrate` loads one manifest, writes the migrated manifest when it changes, and
creates or reuses one backup generation. It does not read artifacts or
environment state and does not use the network. Its text output may show the
resolved manifest and backup paths; its JSON output is path-free and contains
no IDs, values or hashes.

That no-ID/no-raw-value promise is scoped to comparability and the new
sanitized findings. Ordinary `validate` and `inspect` output already exposes
other manifest details, including artifact paths and identifiers. The path
disclosure allowed to `migrate` text output does not widen the comparability
or sanitized-finding contract.
