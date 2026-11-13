# Stage 3d-3 conditions authoring RFC (v0.3)

Status: **IMPLEMENTED (3d3, 2026-11-11).** The contract below is unchanged.
Revision: v0.3 (2026-11-11). Changelog: incorporates both v0.2 ACCEPT WITH
CHANGES re-reviews. Adds first-occurrence duplicate-artifact resolution and
its equivalence fixtures; moves patch-supplied selector validation before the
no-op return so a same-value invalid selector is `SELECTOR_INVALID`; proves an
existing `conditions` parent keeps its key position; replaces the second stale
`ROUTES`-based 22-route pin line; corrects the 0.2-guide/README citation,
constants-source description, per-value node nuance, CPython 3.10.7+ integer
limit wording, I/O allowlist, candidate-schema attribution, failure-table order,
`INTERNAL_ERROR` wording and the >64 KiB read wording; adds omitted-version and
explicit-0.3 load fixtures. The accepted matrix, CLI surface, privacy, write
path and closed envelopes are otherwise unchanged.
Date: 2026-11-11.
Authority: `docs/comparability-stage3d-authoring-rfc.md` v0.4, section 4.3
(the non-normative command sketch) and sections 1.3-1.5, 3.5-3.9 (trust,
atomicity, backup and output rules); `docs/comparability-stage3-schema-rfc.md`
v0.3, sections 3-8 (model, presence matrix, selectors, serialization,
privacy); `docs/comparability-stage2-privacy-cli-rfc.md` v0.3 (closed
envelopes and the sanitized failure boundary); `docs/comparability-schema-0.2.md`
(the accepted 0.2 guide). Source references: `src/runevidence/migrate.py`,
`src/runevidence/manifest.py`, `src/runevidence/models.py`,
`src/runevidence/checks/conditions.py`, `src/runevidence/comparability.py`,
`src/runevidence/cli.py`.
Baseline: Stage 3a/3b/3c/3d are complete and accepted. Schema 0.2 exists, 0.1
remains the default, the 25-route CLI includes `migrate` directly after `init`,
migration is explicit and value-preserving, and the `save_raw` commit invariant
and hardened `init` backups are implemented.

This document decides the gate for `3d-3` (conditions authoring), which the
Stage 3d RFC explicitly deferred. It does **not** implement anything. The
contract here is not authorization to start code; 3d3-1 begins only after this
RFC is reviewed and accepted.

## Decision summary

| Question | Decision |
|---|---|
| Is `--patch` required? | **No.** `--patch` is optional; `--clear` alone is allowed; at least one of the two is required. |
| Is `--clear` alone allowed? | **Yes.** Clearing restores fallback and carries no values on argv. |
| Patch source | One strict JSON object from a local file or `-` (binary stdin). `--patch` stays a raw string: the exact token `-` is stdin; `./-` is a file. Never values on argv. |
| Patch keys | Exactly `evaluation_split`, `evaluation_protocol`, `evaluation_dataset_artifact_id`, `model_checkpoint_artifact_id`, `source_revision`, `environment`. Duplicate, unknown and nested unknown keys are rejected. |
| Patch bounds | 64 KiB raw bytes, 9 container levels including the root object, 1024-character strings and keys, UTF-8-encodable strings only, finite numbers within the comparison limits, container width <= 128 and <= 4096 nodes per supplied value. |
| Target version | The target must already declare `"schema_version": "0.2"`. A 0.1 target is refused with `UNSUPPORTED_VERSION` ("migrate first"). No automatic migration. |
| Null versus clear | A patch key set to `null` is an explicit recorded UNKNOWN for split/protocol/source_revision/environment and invalid for the two selectors. `--clear FIELD` removes the key and restores fallback. |
| Empty result | If all six children are absent after an operation, remove `conditions` entirely; never write `conditions: null`. |
| Selector validation | Validate only the selectors supplied by the patch (patch-present, non-null) **before the no-op return**: existence, non-directory, expected kind and an `input_to` edge to the selected run, resolving duplicate artifact IDs by first occurrence (`setdefault`, never last-wins). Untouched/cleared selectors are not validated; no fallback and no edge synthesis. |
| Comparison-time bounds | Enforce the accepted string, depth, size, number, container-width and per-value node bounds on every value supplied by the patch before writing. Existing untouched values and the total-input node count across all dimensions remain comparison-time concerns. |
| No-op ordering | Patch-supplied selector validation runs before `changed` is computed. A no-op returns success after candidate construction, schema validation, patch-value bounds and supplied-selector validation, but before preservation, serialization and the pre-backup read. |
| Key order | Mutate the target run's `conditions` dict in place; never rebuild it. Existing children keep relative order, a same-value set preserves position, new children append in fixed order, a new parent is the target run's last key, and an existing parent keeps its key index. |
| Candidate validation | Revalidate the complete candidate as a `Manifest`; one invalid patch field refuses the whole patch; no partial write. |
| Reused errors | Translate reused `MigrationError.code` values unchanged into `ConditionsError`; never match exception text. |
| Write path | Reuse `save_raw` with expected bytes and the reviewed migration backup helper (`.bak` generation, alias/permission guards, mode preservation, stale checks, atomic staging). Dry run writes nothing and creates no backup. |
| Reserved target | A resolved target in the `.bak` namespace is refused before load, exactly as migration does. |
| Route | The 26th route, registered directly after `migrate`: `init, migrate, conditions, rehash, add, ...`. Removing it recovers the exact 25-route sequence. |
| Output | Closed JSON envelopes plus fixed text. Success text may show the supplied run ID, field names, counts and resolved manifest/backup paths; JSON never carries a path. The text path disclosure is a deliberate deviation from the 3d sketch, scoped exactly like `migrate`. |
| Exit codes | `0` success, no-op, dry run or committed-replacement-with-cleanup-only-success; `2` every refusal or failure. No exit `1`. |
| Pins/docs | One reviewed CLI LF/CRLF pin refresh, one reader digest refresh, preserve the current 25-route pair as history, correct the 25/24/23/22 derivations, replace both stale current-`ROUTES` 24- and 22-route lines, update the 26-route route sets, README, 0.2 guide and help text. No private route. |

## 1. Scope, non-goals and why this is a separate gate

### 1.1 Scope

This gate decides and, in later implementation slices, implements one command:

```text
runevidence conditions PATH --run RUN_ID [--patch FILE|-] [--clear FIELD]...
  [--dry-run] [--json]
```

It authors the six first-class children of `runs[i].conditions` on a manifest
that already declares schema 0.2. It is a deliberate, trusted-local write.

The command:

- reads one manifest with the accepted `load_manifest`;
- reads one optional patch from a local file or stdin;
- applies the requested set/clear operations to the selected run's
  `conditions` object in an independently decoded document;
- validates the complete candidate as a 0.2 manifest, validates the selector
  graph with the accepted selector checks, and applies the comparison-time
  bounds to every value the patch supplied;
- writes through the accepted `save_raw` path with one backup generation;
- prints a closed, value-free result or a closed, path-free error.

It does not create, migrate or relabel a manifest, and it does not run
`validate`, `inspect` or `comparability`.

### 1.2 Non-goals

Explicitly out of scope:

1. **No automatic inference.** No split, protocol, dataset, checkpoint,
   revision, environment or metric is inferred from legacy metadata, file
   names, folder layout, adapters, artifact bytes, URLs, environment
   variables or run/result names.
2. **No migration side effects.** The command never changes
   `schema_version`, never adds 0.2 fields to a 0.1 manifest, never invokes
   `migrate`, and never migrates on load.
3. **No automatic migration.** A 0.1 target is refused. The documented remedy
   is a separate explicit `runevidence migrate PATH --to 0.2`.
4. **No conditions authoring on a 0.1 manifest.** A 0.1 manifest cannot carry
   any 0.2-only key; the command refuses before reading the patch and before
   writing.
5. **No artifact bytes.** The command does not open, stat, hash or scan
   artifact files, does not resolve artifact locators and does not read
   artifact directories. Selector validation uses recorded manifest facts
   only (`Artifact.id`, `Artifact.kind`, `Artifact.artifact_type` and
   `input_to` relationships).
6. **No network or environment discovery.** No URL/DNS, subprocess, adapter,
   repository inspection, check run, hash, application configuration or
   environment-variable lookup. Normal argparse/locale/terminal reads and
   Python startup are outside this restriction, exactly as in Stage 3d.
7. **No other 0.2 authoring.** `dataset_id` and `metric_definition` remain
   hand-authored in this gate. There is no generic field editor.
8. **No selector discovery or listing.** The command never discovers or
   enumerates artifact IDs, run IDs, field values or candidate choices; the
   only identifier value it prints is the supplied run ID on success. Resolved
   path components in success text are path disclosure, not ID disclosure.
   Exact selectors are supplied by the caller or left absent for the accepted
   fallback.
9. **No edge synthesis.** The command never adds, removes or rewrites an
   `input_to`, `produced_by`, `derived_from` or any other relationship.
10. **No legacy-conflict preflight.** Unequal first-class/legacy duplicates
    remain a comparison-time `INPUT_SHAPE_UNSUPPORTED`; `comparability` is the
    readiness check. The command does not claim to make a run comparable.
11. **No schema, SQLite, publication or release change.** JSON remains the
    authority; SQLite stays dormant; no Git, release or version bump.
12. **No proof of comparability, reproducibility or scientific validity.**
    The command records declared values; it does not verify them.

### 1.3 Why this is a separate gate

The Stage 3d RFC deferred conditions authoring for five reasons: value
privacy, six-way presence complexity, selector-reference validation,
multi-field atomicity, and no demonstrated need at that time. Migration and
its write primitives are now accepted, but none of those five reasons has
disappeared:

- This command has its own input format (a JSON patch) and its own privacy
  boundary; it must not be smuggled into `migrate`, whose contract is "exactly
  one semantic change" and whose pins, tests and output are stable.
- Six dimensions each distinguish absent, explicit `null` and explicit
  non-empty value. `--clear` and patch `null` are different operations and
  need one reviewed matrix.
- Selectors reuse the M15 graph rules with no fallback and no edge synthesis.
  That is a validation surface `migrate` does not have.
- It is a multi-field writer with a backup, stale checkpoints and
  partial-failure rules. Reusing `save_raw` makes that small, but it still
  deserves its own failure table and tests.
- It adds a 26th route and a pin refresh. Keeping it separate keeps the
  accepted 25-route `migrate` packet and its historical evidence intact.

Keeping the gate small means: one command, one patch format, one closed
envelope, one pin refresh, and no general editing surface.

### 1.4 Trust and authorization

The trusted-local contract from Stage 3d section 1.3 applies unchanged:

- The caller owns or is authorized to modify the chosen manifest, its folder
  and the patch.
- The local process, interpreter, installed package and filesystem are
  trusted. Shared hosts need account/file/process isolation; Unix permissions
  alone are not organizational consent.
- Running `conditions` without `--dry-run` is sufficient opt-in. No
  `--allow-write` or `--yes` flag is added; such a flag would not establish
  ownership.
- The command is not a service, is not remotely callable and is not safe for
  attacker-controlled adaptive invocation.
- Hostile Python objects, monkeypatched modules, `model_construct`
  substitutes, concurrent mutation and manually forged snapshots remain out
  of contract, exactly as in Stage 2.
- The patch is untrusted **data**. It is parsed as JSON, never executed, and
  its values are bounded before use.

### 1.5 Local-only and deterministic

Every invocation may read only the manifest, the target folder's backup
namespace, one explicitly supplied patch file or stdin, and nothing else. It
does not scan artifact directories, read artifact bytes or locators, inspect
repository state, resolve URLs, call adapters or use the network. It does not
run checks or hashes. It does not discover application configuration from the
environment.

Output is deterministic for identical inputs, except for resolved paths and
filesystem errors. Field arrays use the fixed field order, not patch key order.
No random IDs, timestamps or hash-randomized ordering are used.

### 1.6 Failure and atomicity model

All writes reuse the reviewed `manifest.py` and migration discipline:

- stage a complete new file beside the target, `flush` + `fsync`, then
  `os.replace`;
- preserve the target file's POSIX mode bits;
- verify the source bytes are still the loaded snapshot before replacing;
- create or reuse one `.bak` generation before replacing the manifest;
- remove a staged file on a handled pre-commit failure; after a committed
  replace, cleanup is best-effort and a cleanup `OSError` is suppressed (a
  temp file may remain);
- treat a committed replacement whose only later error is post-replace
  cleanup as a success, never as `STALE_MANIFEST` or `WRITE_FAILED`;
- do not claim an atomic compare-and-swap or a lock. A concurrent writer
  between the checks and `os.replace` can be lost, exactly as with `rehash`,
  `add` and `migrate`.

Replacement and backup create new inodes. POSIX permission bits are preserved
or reduced under the process umask; owner, group, ACLs, security descriptors
and extended attributes are not preserved. A backup is owned by the invoking
process. Secure erasure is not claimed. Parent-directory `fsync` is absent, so
backup durability is not guaranteed across a crash.

### 1.7 Preservation contract

The only decoded-value change is the target run's `conditions` subtree, and
only as the set/clear operations request. Everything else is preserved:
`project`, every other run, every other `conditions` object, `artifacts`,
`results`, `evidence`, `relationships`, metadata, top-level key order, nested
object key order, array order, explicit nulls, empty objects/arrays/strings,
`false` and `0`.

The promise is the loader's **decoded Python values, types, presence and
decoded object-key order**, not lexical JSON spelling or original bytes. The
canonical writer (`dump_raw`) may normalize indentation and number spelling;
the backup preserves the original bytes exactly. Equality uses the accepted
`strict_equal` rules from `migrate.py`: exact `dict`/`list` types, ordered
object keys, ordered arrays, `bool` distinct from `int`, `int` distinct from
`float`, NaN equal to NaN, infinities by sign, and signed zeros distinct.

Within the authored `conditions` object, the candidate is built by mutating
the target run's existing `conditions` dict **in place** in the independently
decoded source; it is never rebuilt into a new dict. Clears are applied first,
then sets in fixed field order:

- existing children keep their relative order;
- a set on an existing child keeps its position, including a same-value set;
- a new child is appended in the fixed order below;
- a cleared child is removed;
- a new `conditions` parent is created and assigned as the last key of the
  target run;
- an existing `conditions` parent keeps its index in the target run's ordered
  key list;
- if the object ends with no children, the parent is removed entirely and is
  absent from the target run.

The preservation proof asserts the exact ordered `conditions` key list, the
existing parent's key index (or its absence after removal), and not only set
membership or values (section 5.6).

Fixed child order (also the output array order):

```text
evaluation_split
evaluation_protocol
evaluation_dataset_artifact_id
model_checkpoint_artifact_id
source_revision
environment
```

This ordering is deterministic and independent of the patch object's key order.

## 2. CLI surface

### 2.1 Command

```text
runevidence conditions PATH --run RUN_ID [--patch FILE|-] [--clear FIELD]...
  [--dry-run] [--json]
```

- `PATH` is an experiment folder containing `runevidence.json` or a manifest
  file, resolved exactly as `load_manifest`/`resolve_manifest_path` do.
- `--run RUN_ID` is required and matches exactly one `runs[i].id`.
- `--patch FILE|-` supplies one strict JSON object. At most one `--patch`
  occurrence is accepted. The option value stays a **raw string**: the exact
  string `-` means binary stdin; any other string, including `./-`, is a file
  path. The patch path is never printed.
- `--clear FIELD` is repeatable and names one of the six child fields. The
  parent `conditions` is not a clearable field.
- At least one of `--patch` or `--clear` is required. An explicit empty patch
  (`{}`) with no clears is a no-op; the at-least-one rule is satisfied by the
  `--patch` itself, so an invocation with neither option cannot be mistaken
  for an edit.
- `--dry-run` performs the same checks as a real run through patch-supplied
  selector validation and the changed computation. For a changed run it also
  performs preservation, serialization and the pre-backup stale read. It
  writes nothing and creates no backup.
- `--json` is recognized from the raw argv even when parsing later fails.
- No `--force`, `--yes`, `--allow-*`, `--strict`, `--redact-manifest-path`,
  `--summary-only`, `--schema-version`, `--set`, `--value` or selector
  option exists.

### 2.2 Decisions and justification

#### `--patch` is optional; `--clear` alone is allowed

The 3d sketch showed `--patch` as required. This RFC narrows the no-value rule
rather than the operation surface: clearing a field restores fallback and is a
complete, useful, value-free operation. Requiring an empty patch file or
stdin for it would make a safe operation depend on an artificial file and
would invite an interactive stdin block. The command instead requires **at
least one** of `--patch` or `--clear`; a clears-only run reads no patch at all.

The sketch's other rules stand: values never appear on argv, the target must
already be 0.2, and the output redaction contract is unchanged.

#### `-` means binary stdin, exactly

- The exact raw string `-` means stdin. A file literally named `-` is
  addressed as `./-`.
- `--patch` must be parsed as a **raw string**, not `type=Path`.
  `Path("-") == Path("./-")` and both stringify to `-`, so a `Path`-typed
  option would erase the distinction and could send a `./-` file to stdin.
  The implementation compares the raw token exactly to `-` and converts to
  `Path` only for a non-`-` value.
- Do **not** introduce a custom argparse `type=` callable or helper for
  `--patch`; the pinned parser-structure test enumerates the allowed call
  names in `build_parser`. A plain string option keeps that pin intact.
- The command reads at most `MAX_PATCH_BYTES + 1` bytes from
  `sys.stdin.buffer`; it never decodes with the locale and never reads text
  mode. A test can inject a binary stream.
- A read error is `READ_FAILED`. More than `MAX_PATCH_BYTES` is
  `PATCH_INVALID`; the command does not read further. The same bound applies
  to a patch file, not only stdin.
- Stdin is read only when `--patch -` is supplied. A clears-only invocation,
  a grammar failure, a reserved path, a load failure, a 0.1 target or a failed
  run selection does not consume stdin.
- The command reads stdin to EOF. It has no timeout and does not detect a TTY;
  providing input is the caller's responsibility.
- `--patch ./-` reads the file named `-` in the current folder; it is never
  stdin.

#### Path versus manifest-file operands

`PATH` accepts either an experiment folder (resolved to
`PATH/runevidence.json`) or a manifest file, exactly like `migrate`. The
reserved-backup check and `load_manifest` both operate on the resolved
manifest path. Text output shows the resolved manifest and backup paths; JSON
never carries a path. A folder named like a backup is not reserved; the
resolved **manifest file** basename is what matters.

#### Run selection is exact

`--run` is an exact string match against `runs[i].id`. There is no trimming,
case folding, prefix matching, fuzzy matching or name-based selection. Exactly
one match is required. Zero matches and multiple matches both refuse with
`RUN_SELECTION_INVALID`; the supplied ID is not echoed in errors.

Run selection happens after the manifest loads and the 0.2 check passes, but
before the patch is read. A bad run ID therefore does not consume stdin and
does not disclose patch errors. This also means a valid grammar with a
manifest-dependent selection failure is not reported as `INVALID_ARGUMENTS`.

#### Route placement: 26th route, directly after `migrate`

The route order becomes:

```text
validate, validate-batch, inspect, compare, comparability, leaderboard, matrix, run,
claim, lineage, impact, coverage, coverage-gate, fingerprint, fingerprint-compare,
coverage-diff, review-diff, impact-diff, review-gate, init, migrate, conditions,
rehash, add, schema, adapters
```

`conditions` is the documented next step after `migrate`, and grouping it
with the write commands keeps the help text readable. Removing `conditions`
recovers the exact accepted 25-route sequence, which is the pin invariant.

### 2.3 Fixed check order

1. **Argument grammar and argument cardinality.** Missing/extra operands,
   unknown options, duplicate `--patch`, no `--patch` and no `--clear`,
   unknown or duplicate `--clear` field. No manifest or patch read.
2. **Reserved target path.** Resolve the manifest path and refuse a resolved
   basename in the `.bak` namespace. No load, no read, no write.
3. **Manifest load.** One `load_manifest` read, including resolve/stat/decode/
   schema validation. Failure is `MANIFEST_LOAD_FAILED`.
4. **Version precondition.** The loaded manifest must declare exactly `"0.2"`.
   A loaded 0.1 manifest is `UNSUPPORTED_VERSION`; no patch read and no write.
   (A 0.1 manifest carrying a 0.2-only key fails load first and is
   `MANIFEST_LOAD_FAILED`.)
5. **Exact run selection.** Exactly one matching `runs[i].id`, else
   `RUN_SELECTION_INVALID`; no patch read and no write.
6. **Patch acquisition and parsing.** Read the supplied patch once (unless
   `--patch` is absent), enforcing `MAX_PATCH_BYTES + 1` on the file or stdin,
   decode UTF-8, parse strict JSON (catching parse-time `RecursionError`),
   enforce the container-depth limit, reject duplicate/unknown keys,
   non-UTF-8-encodable strings and integers beyond the interpreter parse
   limit, and reject a patch/`--clear` conflict. Read failure is
   `READ_FAILED`; content failure is `PATCH_INVALID`.
7. **Candidate construction.** Two independent strict decodes of the loaded
   source bytes; build the candidate from the second; mutate the target run's
   `conditions` dict in place (clears first, then sets); remove an empty
   `conditions` parent.
8. **Complete candidate schema validation.** `Manifest.model_validate`; any
   failure is `PATCH_INVALID` because the loaded source and the patch are the
   only inputs and the source already validated. Assert `"0.2"`.
9. **Patch-supplied bounds.** Enforce string/key length, UTF-8 encodability,
   protocol shape, environment depth/size, integer/float magnitude, container
   width and per-value node count on every value the patch supplied; failure
   is `PATCH_INVALID`.
10. **Patch-supplied selector validation.** For each patch-present, non-null
    selector, resolve artifact IDs by **first occurrence** in manifest order
    (`artifacts.setdefault(artifact.id, artifact)`; never last-wins) and
    require existence, non-directory, expected kind and an `input_to` edge to
    the selected run; any failure is `SELECTOR_INVALID`. This runs **before**
    the no-op computation, so re-supplying an existing invalid selector with
    its current value is still `SELECTOR_INVALID`. Untouched and cleared
    selectors are not validated.
11. **Changed computation and no-op.** `changed = not strict_equal(source_a,
    candidate)`. A no-op is computed only after the supplied values passed
    schema, bounds and patch-supplied selector validation, so re-supplying an
    out-of-bounds existing value is still `PATCH_INVALID` and an invalid
    same-value selector is `SELECTOR_INVALID`. If `changed` is false, return
    the no-op success **before** preservation, serialization and the
    pre-backup read; no backup, no write, no backup enumeration.
12. **Preservation proof.** Prove that the source and candidate differ only
    in the target run's `conditions` subtree, that the subtree is exactly the
    requested result, that the ordered `conditions` key list is exact, that
    an existing `conditions` parent keeps its key index (or is absent after
    removal), and that no mutable container is shared between the independent
    source decode and the candidate.
13. **Serialization eligibility.** `dump_raw(candidate)` + UTF-8 encode +
    strict decode + `strict_equal(candidate, emitted)`. Encoding/recursion
    failure is `SERIALIZATION_FAILED`; an emitted mismatch is
    `INTERNAL_ERROR`.
14. **Pre-backup stale read.** Re-read the manifest and compare with the
    loaded bytes. Read failure is `READ_FAILED`; a difference is
    `STALE_MANIFEST`. A dry run stops here.
15. **Write path.** Target mode, backup create/reuse, `save_raw`, and the
    accepted late-failure classification for a non-committed raise.

Patch acquisition deliberately follows the version and run-selection checks:
a manifest that cannot accept the edit must not cause the command to read
secret patch values or block on stdin.

### 2.4 Sanitized parser boundary

`cli.py` gets a new `_conditions_main(raw)` mirroring the reviewed
`_migrate_main` and `_comparability_main` pattern:

- `as_json = "--json" in raw`;
- `build_parser()`, capture stderr with `redirect_stderr`, call
  `parse_args(raw)`;
- `SystemExit` code 0 (help) returns 0; any other `SystemExit` emits exactly
  one fixed `INVALID_ARGUMENTS` document/text and returns 2;
- otherwise dispatch `args.func(args)`.

`main()` dispatches `raw[0] == "conditions"` to that boundary before the
normal parse. Do **not** refactor `_migrate_main` or `_comparability_main`;
leaving them byte-identical keeps their pins and tests stable.

The parser diagnostics are captured and discarded before argparse can write
them. On a refusal, the output never contains a submitted token, an option
name, a usage line or a traceback. `--help` keeps normal stdout behavior and
exit 0; `conditions --version` is an unknown option and becomes
`INVALID_ARGUMENTS`, exactly as with `migrate`.

### 2.5 Help text

Subparser help:

```text
set or clear recorded run conditions on a schema 0.2 manifest from a local JSON
patch (no condition values on the command line)
```

The root parser description and module docstring list the writers as
`init, migrate, conditions, rehash and add`, and state that `init --force`,
`migrate` and `conditions` keep a backup generation. No command description
claims comparability readiness or artifact verification.

## 3. Patch format and privacy

### 3.1 One strict JSON object

The patch is one JSON object at the top level. Arrays, scalars, `null`, an
empty file, a UTF-8 BOM, invalid UTF-8 and trailing data are rejected with
`PATCH_INVALID`.

Allowed top-level keys are exactly:

```text
evaluation_split
evaluation_protocol
evaluation_dataset_artifact_id
model_checkpoint_artifact_id
source_revision
environment
```

The patch is **not** a `conditions` object wrapper. `{"conditions": {...}}`
and any other unknown key are `PATCH_INVALID`. There is no nested
`conditions`, `run`, `schema_version` or `relationships` surface. The
`evaluation_protocol` object forbids unknown members. The one deliberate
free-form nested surface is `environment`, whose members are data under the
0.2 model; its keys are bounded and never echoed. No other nested unknown field
is accepted.

Parsing rules:

- strict JSON, no comments, no single quotes, no trailing commas, no `NaN`,
  `Infinity` or `-Infinity` tokens;
- duplicate object keys are rejected at **every** depth, not only the top
  level;
- a JSON float literal that overflows to a non-finite value is rejected;
- a `RecursionError` raised by `json.loads` on deeply nested input is caught
  and mapped to `PATCH_INVALID` before the iterative depth check can run. A
  64 KiB deep-nesting input must therefore fail closed, not crash;
- every string value and object key must be UTF-8-encodable. A lone surrogate
  escape such as `"\ud800"` is rejected as `PATCH_INVALID` at patch validation,
  not deferred to manifest serialization;
- an integer literal beyond CPython's integer string conversion limit (4300
  digits by default on CPython 3.10.7+ and 3.12; interpreter-dependent)
  raises during `json.loads` and is `PATCH_INVALID`; a 5000-digit integer is
  the named test case. If a runtime raises or disables that parse limit, the
  integer still exceeds `MAX_SAFE_INT` and is `PATCH_INVALID` at the magnitude
  check;
- `null` is preserved as Python `None`;
- top-level patch key order is accepted but does not affect operation order,
  candidate child order or the result's `set`/`cleared` arrays (all use fixed
  field order); nested object key order inside a supplied protocol or
  environment is part of the supplied value and is written as given;
- the patch is read once and is not re-read or stale-checked. Both a patch
  file and stdin are read with a `MAX_PATCH_BYTES + 1` bound.

### 3.2 Value shapes

| Key | Accepted patch value | Notes |
|---|---|---|
| `evaluation_split` | nonblank string, or `null` | `null` is explicit UNKNOWN. No trimming or case folding. |
| `evaluation_protocol` | object `{protocol_id[, version]}`, or `null` | `protocol_id` required, nonblank string. `version` is a non-bool integer or nonblank string; `null` is invalid. Unknown members invalid. `{}` invalid. |
| `evaluation_dataset_artifact_id` | selector string only | **`null` is invalid.** The string must match the 0.2 `SelectorId` pattern and identify an existing artifact of kind `dataset` attached by `input_to`. |
| `model_checkpoint_artifact_id` | selector string only | **`null` is invalid.** Same rules, required kind `checkpoint`. |
| `source_revision` | nonblank string, or `null` | `null` is explicit UNKNOWN. No trimming. |
| `environment` | non-empty JSON object, or `null` | `null` is explicit UNKNOWN. `{}` is refused at authoring time even though it is schema-valid, because comparison rejects it. |

A `null` member **inside** an `environment` object is an ordinary JSON null
value, not the UNKNOWN marker. The UNKNOWN marker applies only to the four
whole-value fields (`evaluation_split`, `evaluation_protocol`,
`source_revision`, `environment`).

### 3.3 Bounds

The patch parser and value validator enforce:

- **Raw bytes:** at most 65,536 bytes (64 KiB) read from the file or stdin.
  Exactly 65,536 is accepted; 65,537 is `PATCH_INVALID`. The limit is measured
  before UTF-8 decoding and before JSON parsing.
- **Container depth:** at most 9 container levels including the root patch
  object. Depth counts dictionaries and lists only; scalars do not add a
  level. Derivation: root patch object = 1, environment root = 2, and the
  environment's own contract allows 8 container levels counting its root
  (`2 + 8 - 1 = 9`). Exactly 9 is accepted; 10 is `PATCH_INVALID`.
- **Strings and keys:** every string value and every object key is at most
  1,024 characters (Python `len`, Unicode code points), matching the accepted
  comparison bound. This applies to split, protocol ID/version, source
  revision, selector IDs and every environment key/string. Every string value
  and key must also be UTF-8-encodable; a lone surrogate is `PATCH_INVALID`.
- **Environment depth:** at most 8 container levels counting the environment
  object as 1.
- **Environment size:** at most 16,384 bytes of compact JSON using
  `ensure_ascii=True`, `separators=(",", ":")` and `allow_nan=False`,
  matching the accepted comparison size measurement. Exactly 16,384 is
  accepted; 16,385 is `PATCH_INVALID`.
- **Number magnitude:** every supplied integer must satisfy
  `abs(value) <= MAX_SAFE_INT` with `MAX_SAFE_INT = 9007199254740991`; every
  supplied float must be finite and satisfy
  `abs(value) <= MAX_ABS_FLOAT` with `MAX_ABS_FLOAT = 1e100`. Booleans are not
  integers for this bound. This applies to `evaluation_protocol.version`
  integers and every environment number. A value of `9007199254740992` or
  `1e300` is `PATCH_INVALID`.
- **Container width:** every supplied `dict` or `list` has at most
  `MAX_CONTAINER_WIDTH = 128` members. A 129-key environment object is
  `PATCH_INVALID`.
- **Node count:** each patch-supplied top-level value's subtree has at most
  `MAX_NODES = 4096` nodes. The value root counts as one node; every scalar
  and container counts; dict keys do not count as value nodes. A subtree with
  4097 nodes is `PATCH_INVALID`. The comparison module's `MAX_DEPTH = 16` is
  looser than the patch/environment depth limits for supplied values.
- **Environment shape:** object root, non-empty, JSON-representable values
  only (`dict`, `list`, `str`, `bool`, `int`, `None`, finite `float`), with no
  cycles (a parsed JSON document cannot contain cycles).

The authoring module defines `MAX_SAFE_INT = 9007199254740991`,
`MAX_ABS_FLOAT = 1e100`, `MAX_CONTAINER_WIDTH = 128` and `MAX_NODES = 4096`
to mirror the **inline literals** in `comparability._bounded`; those names do
not exist in `comparability.py`. Only `MAX_DEPTH = 16` and
`MAX_INPUT_BYTES = 65536` are module-level names there. The 16 KiB environment
bound comes from the 0.2 guide and the bridge, not from a `comparability.py`
constant. The authoring module re-expresses all of these rather than importing
the comparison core, and a test asserts agreement for the same fixtures.

**Honest scope of the node count.** The checks are per patch-supplied value:
each value's own subtree cannot exceed the width/node/number limits by itself.
Even a single value with 4096 nodes plus wrapper nodes (the comparison
envelope, other dimensions or both sides) can exceed the core's total. The
comparison core also counts all nodes across the complete comparison input,
including untouched dimensions and both sides; that total remains a
comparison-time concern. The core's 64 KiB total input-byte limit across all
dimensions is likewise comparison-time. The authoring command does not
reconstruct the full comparison document, does not scan existing dimensions,
and does not claim that a patch with several individually valid values cannot
make the total input exceed 4096 nodes or 64 KiB. `comparability` remains the
readiness check.

The patch-wide 64 KiB limit already bounds a valid environment (16 KiB) plus
the other five fields and formatting. The comparison-time bounds are applied
to **patch-supplied values only**. An untouched existing value that already
exceeds a comparison bound is not re-bounded; re-supplying that value in the
patch does apply the bounds and is `PATCH_INVALID`.

### 3.4 Only patch values are new values

The manifest is read as the validation and preservation baseline. No condition
value is sourced from legacy metadata, defaults, artifact data, the
environment, the run name or any inference. The only new values are the patch
values. A field not named by the patch and not cleared is left exactly as it
was, including an explicit `null` or an absent key.

The patch and `--clear` are the only operation sources. `--clear` field names
are not values and may appear on argv. Values never do.

### 3.5 Output redaction

| May appear | Must never appear |
|---|---|
| command name `conditions` | any patch value or nested protocol/environment content |
| supplied run ID (success JSON and text) | any selector or artifact ID |
| field names and `set`/`cleared` arrays (success) | artifact paths, locators, URLs or hashes |
| set/cleared counts and booleans (success) | metadata keys or values |
| `"0.2"`, `"created"`, `"reused"`, `"none"` | manifest, backup or patch path in JSON |
| resolved manifest path (success/no-op/dry-run text only) | resolved backup path in error text or JSON |
| resolved backup path (real changed success text only) | raw argparse, loader, OS, exception or traceback text |
| fixed error codes and fixed prose | any field name in an error document |
| | project/run names other than the supplied run ID |
| | the patch path in any output |

The success JSON may contain the supplied run ID and field names because the
caller supplied them and the 3d RFC explicitly allows them. Errors are
code-only: no run ID, field name, path, side or submitted token. Text errors
use fixed prose and no path. Success/no-op/dry-run text shows the **resolved**
manifest path (and, for a real changed run, the actual reused/created backup
path); a symlink operand is shown resolved, not as the supplied alias.

The patch path is never printed. It is an input that may be more sensitive
than the manifest, and printing it adds no recovery value.

The text-only resolved manifest/backup path disclosure is scoped exactly like
`migrate`; it does not widen the comparability no-path contract or authorize
paths in machine output.

### 3.6 Patch privacy residual

The patch file itself is sensitive at rest. The command does not delete it,
shrink it, encrypt it or warn about its permissions. The caller must protect
or remove it. The output redaction does not protect shell history (which may
contain the patch path, run ID and field names but no values), process lists,
terminal capture, CI command echoes, backups, the resulting manifest, process
memory, debuggers or host audit records.

## 4. Set, clear and null semantics

### 4.1 Operation model

For the selected run:

- a patch key **absent** means no change to that child;
- a patch key **present** means set that child to the supplied value,
  including `null` for the four nullable dimensions;
- `--clear FIELD` means remove that child key, restoring the legacy fallback;
- `--clear FIELD` and a patch key for the same field are mutually exclusive
  and refuse the whole invocation with `PATCH_INVALID`;
- a duplicate `--clear FIELD` or an unknown field is `INVALID_ARGUMENTS`;
- clearing an absent child changes nothing for that child;
- setting a child to the value it already has changes nothing for that child.

No operation merges members. A protocol or environment is replaced as a whole
value when set, never merged with the previous value or with legacy metadata.

### 4.2 Presence matrix

| State | Result |
|---|---|
| child absent, not patched, not cleared | unchanged (stays absent) |
| child absent, patched with a value | created with that value |
| child absent, patched with `null` (split/protocol/source_revision/environment) | created with explicit `null` (recorded UNKNOWN) |
| child absent, patched with `null` (either selector) | `PATCH_INVALID` |
| child present, patched with a value | replaced |
| child present, patched with `null` (four nullable dimensions) | replaced with explicit `null` (UNKNOWN) |
| child present, patched with `null` (either selector) | `PATCH_INVALID` |
| child present, cleared | key removed (fallback restored) |
| child absent, cleared | no change for that child |
| `conditions` absent, any patch value | new `conditions` object created |
| `conditions` absent, only clears of absent children | unchanged; no empty object is created |
| `conditions: {}` and a set | child added |
| `conditions: {}` and a clear of an absent child | operation requested; if the result is empty, `conditions` is removed |
| `conditions` loses its last child | `conditions` key removed entirely |
| `conditions` would be `null` or `{}` after at least one operation | never written as `null`; an empty object is removed |

`conditions` itself is never patchable and never clearable. `conditions: null`
is invalid under the 0.2 model and cannot be produced by this command.

### 4.3 Null meanings

- `evaluation_split: null` — recorded UNKNOWN; no legacy fallback.
- `evaluation_protocol: null` — recorded UNKNOWN; no legacy fallback.
- `source_revision: null` — recorded UNKNOWN; no legacy fallback.
- `environment: null` — recorded UNKNOWN; no legacy fallback.
- `evaluation_dataset_artifact_id: null` — **invalid**; selectors have no
  UNKNOWN state and never fall back.
- `model_checkpoint_artifact_id: null` — **invalid**; same reason.
- `evaluation_protocol.version: null` — **invalid**; a nested optional member
  is omitted or a value, never null.
- `evaluation_protocol: {}` — **invalid**; `protocol_id` is required.
- `environment: {}` — **refused by authoring** even though the published
  schema accepts it; comparison rejects present-empty environments with
  `INPUT_SHAPE_UNSUPPORTED`.

These are the accepted Stage 3 presence rules, reused by reference. This RFC
does not redefine them.

### 4.4 Empty-parent removal

After applying at least one set or clear operation, if the resulting
`conditions` object has no children, remove the `conditions` key from the
target run entirely. Never write `conditions: null` and never leave
`conditions: {}` as a result of an operation.

If **no** operation is requested (an empty patch with no clears), the command
is a no-op and does not normalize an existing `conditions: {}` to absent.
This avoids an empty invocation rewriting a manifest.

Consequence: a clear of an absent child from an existing `conditions: {}` is
a requested operation that leaves zero children, so it removes the parent and
is reported as `changed: true`. This is the only structural normalization and
is documented in the README and the 0.2 guide.

### 4.5 No-op

A no-op is a successful outcome, not an error:

- an empty patch with no clears (only reachable when `--patch` is supplied);
- every requested set already equals the current value;
- the resulting document is value-identical to the loaded snapshot.

A no-op returns exit 0, `changed: false`, `backup: "none"`, and no write, no
backup and no backup enumeration. It is computed after candidate construction,
schema validation, patch-supplied bounds and patch-supplied selector
validation, then returns before preservation, serialization and the pre-backup
stale read, like `migrate`'s already-0.2 no-op. Requested field arrays still
report the requested operations; `changed` reports whether the document
actually changed. `conditions: {}` plus a clear of an absent child removes the
empty parent and is therefore `changed: true`, not a no-op.

### 4.6 Target version and no auto-migration

The target must already declare `"schema_version": "0.2"`. If it declares or
omits schema 0.1 (omission means 0.1), the command refuses with
`UNSUPPORTED_VERSION` and fixed text that says to run
`runevidence migrate PATH --to 0.2` first. It does not change the version, does
not read the patch, does not write and does not create a backup.

A 0.1 manifest that already contains any 0.2-only key (including `null`) fails
`load_manifest` first and is `MANIFEST_LOAD_FAILED`; the remedy is to remove
the key, migrate, then re-add it. A future or unknown version fails load for
the same reason and is not reported as `UNSUPPORTED_VERSION`.

There is no `--schema-version`, no `--migrate`, no implicit upgrade and no
conditions authoring on a 0.1 manifest.

## 5. Validation

### 5.1 Schema and model rules

The complete candidate is revalidated with `Manifest.model_validate`. This
reuses every accepted 0.2 rule:

- strict `bool`/`int`/`str` typing (no numeric/string coercion);
- nonblank split, protocol ID, source revision and dataset label;
- selector syntax through `SelectorId` (`ID_PATTERN`) for the two selectors;
- protocol requires `protocol_id`; `version` is a non-bool integer or
  nonblank string and is nonnull when present;
- unknown keys are forbidden at every model level;
- `conditions` is an object, never null;
- 0.2-only keys require `schema_version: "0.2"`;
- explicit null is allowed only where the Stage 3 matrix allows it.

Any candidate schema failure is `PATCH_INVALID`: the loaded source already
validated, so the patch introduced the invalidity. The command does not
attempt a partial repair or a field-by-field write.

**Attribution choice.** The command does not run a separate construction or
preservation self-check before mapping a candidate schema failure. A
construction defect that produces a schema-invalid candidate is therefore
reported as `PATCH_INVALID` with no write; it is not reclassified as
`INTERNAL_ERROR` at this stage. Pre-serialization preservation-proof
mismatches and post-serialization round-trip mismatches remain
`INTERNAL_ERROR` after the schema gate.

### 5.2 Selector validation

The command validates **only the selectors supplied by the patch**:
`evaluation_dataset_artifact_id` and `model_checkpoint_artifact_id` when the
patch presents them with a non-null value. Untouched selectors and cleared
selectors are not validated. An untouched pre-existing invalid selector in the
target run or another run therefore does not block an unrelated edit; it
remains a `validate`/`inspect` finding and a `comparability` concern.

For each supplied selector, the command applies the accepted rules exactly:

1. the selector names an artifact that exists in the same manifest;
2. the artifact is not a directory;
3. the artifact's recorded `kind` matches (`dataset` or `checkpoint`);
4. an `input_to` relationship attaches that artifact to the selected run.

**Duplicate artifact IDs resolve by first occurrence.** The selector resolver
builds `artifacts` with `artifacts.setdefault(artifact.id, artifact)` in
manifest order, exactly as `checks/conditions.py` does, and uses that first
artifact for the existence, directory and `kind` checks. It is never
last-wins. If the first artifact with the ID has the wrong kind, the selector
is `SELECTOR_INVALID` even when a later artifact with the same ID has the
right kind; if the first has the right kind, the selector is accepted even
when a later duplicate has the wrong kind. The `input_to` check uses the
artifact ID and selected run, so duplicates do not change the edge test.

There is no fallback to legacy single-attached selection when an explicit
selector is invalid, and no `input_to` edge is synthesized. `Artifact.run_id`
(produced-by) never satisfies a selector. Volatile or unhashed file artifacts
are not rejected by this check; they simply do not contribute a verified
version observation at comparison time.

Patch-supplied selector validation runs **before** the changed/no-op
computation (section 2.3 step 10). A patch that re-supplies an existing
invalid selector with its current value is `SELECTOR_INVALID`, matching the
bounds rule for re-supplying an out-of-bounds existing value. Untouched and
cleared selectors are still not validated, so an unrelated pre-existing
invalid selector does not block a no-op or another edit.

This is not a full `validate`: duplicate IDs outside selector resolution,
unrelated dangling references, artifact bytes and other graph problems are not
checked. `validate`/`inspect` remain the review path. Selector syntax/type
failures are caught by schema validation and reported as `PATCH_INVALID`;
existence/directory/kind/attachment failures are reported as
`SELECTOR_INVALID`.

The implementation applies the accepted four rules and the first-occurrence
resolver against the same manifest fields rather than calling
`check_conditions` over the complete candidate. An equivalence suite compares
the authoring helper's verdict with `check_conditions` for a manifest whose
only declared selector is the same selector, covering all four rules and both
duplicate-ID directions: (a) first artifact wrong kind, second same ID right
kind -> `SELECTOR_INVALID`; (b) first artifact right kind, second wrong ->
accepted.

### 5.3 Comparison-time bounds at authoring

Every value supplied by the patch is checked against the comparison bounds in
section 3.3 before writing. This prevents the command from creating a
schema-valid value that `comparability` would reject on that value's own
terms:

- split/source revision length <= 1024 and UTF-8-encodable;
- protocol ID/version length <= 1024 and UTF-8-encodable;
- selector ID length <= 1024 and UTF-8-encodable;
- every environment key/string <= 1024 and UTF-8-encodable;
- environment depth <= 8 counting its root;
- environment compact JSON <= 16 KiB;
- integer magnitude <= 9007199254740991 and float magnitude <= 1e100;
- every supplied dict/list width <= 128;
- every supplied value's subtree <= 4096 nodes;
- finite numbers within the integer/float magnitude limits;
- environment object non-empty;
- protocol/`version` shape already enforced by the model.

Existing untouched values are not re-bounded. A target that already contains
an out-of-bounds environment remains editable in other fields; `comparability`
continues to report the bound failure. Re-supplying that out-of-bounds value
in the patch does apply the bounds and is `PATCH_INVALID`, even if the value is
unchanged.

The scope is deliberately per supplied value, not the whole candidate. Each
value cannot exceed the limits by itself, but the comparison core's total node
count and 64 KiB total input size across all dimensions and both sides remain
comparison-time concerns. The command does not reconstruct the comparison
document or scan untouched dimensions, so it does not claim that several
individually valid values cannot push the total over those limits.

The authoring module defines `MAX_PATCH_BYTES = 65536`,
`MAX_PATCH_DEPTH = 9`, `MAX_STRING_LENGTH = 1024`,
`MAX_ENVIRONMENT_DEPTH = 8`, `MAX_ENVIRONMENT_BYTES = 16 * 1024`,
`MAX_SAFE_INT = 9007199254740991`, `MAX_ABS_FLOAT = 1e100`,
`MAX_CONTAINER_WIDTH = 128` and `MAX_NODES = 4096`. The last four mirror the
inline literals in `comparability._bounded`; they are not named constants in
`comparability.py`. Only `MAX_DEPTH = 16` and `MAX_INPUT_BYTES = 65536` are
module-level names there, and the 16 KiB environment bound comes from the 0.2
guide/bridge. The accepted bridge module
(`_experimental_comparability_bridge.py`) is not imported by production
authoring code, and the bridge's import-boundary test remains intact. A test
asserts that the authoring bounds, string size, container width, node count and
environment size/depth measurement agree with the accepted comparison
behavior for the same fixtures. The bounds are re-expressed, not redefined.

Even a single supplied value with 4096 nodes plus wrapper nodes (the comparison
envelope, other dimensions or both sides) can exceed the core total; the
per-value check only proves that value is not the sole cause.

### 5.4 One invalid field refuses the whole patch

The patch is applied as a whole. If any field is invalid, the whole invocation
refuses and no field is written. There is no "best effort", no partial
candidate, no per-field success report and no write of the valid subset.

### 5.5 What is not validated

The command does not:

- compare a new first-class value with a matching legacy metadata value
  (unequal duplicates remain `INPUT_SHAPE_UNSUPPORTED` at comparison time);
- validate `dataset_id` or `metric_definition`;
- run `validate`, `inspect` or `comparability`;
- read artifact bytes or verify that a selected dataset/checkpoint is the
  intended file;
- check duplicate IDs outside selector resolution, unrelated dangling
  references, provenance cycles or other graph findings;
- prove that the recorded values are true, fresh, reproducible or
  scientifically valid.

These remain documented residual risks and are reported by the existing review
commands.

### 5.6 Preservation proof

The proof runs only for a changed edit; a no-op returns at section 2.3 step 11
before this proof, serialization and the stale read. The proof has five parts,
all read-only:

1. **Independent source snapshots.** `source_a` and `source_b` are separate
   `decode_snapshot` calls over the same loaded bytes. The candidate is built
   from `source_b` by mutating the target run's existing `conditions` dict in
   place (a new dict is created and assigned only when the parent is absent);
   `source_a` is never mutated. A structural walk asserts that `source_a` and
   the candidate share no mutable `dict` or `list` by identity.
2. **Only the target conditions subtree changed.** Build a shallow masked
   projection of `source_a` and of the candidate: copy the top-level dict, the
   `runs` list and the target run dict, and remove the `conditions` member from
   both target run dicts. `strict_equal(source_masked, candidate_masked)` must
   hold. This compares every other value, every key list in order, every array
   in order, every explicit null and every empty value. The projection shares
   nested containers but never mutates them.
3. **The subtree is exactly the requested result.** For each of the six fixed
   children:
   - a cleared child is absent;
   - a patched child is present and `strict_equal` to the patch value
     (including `None` for an explicit null);
   - an untouched child is present with the baseline value if it was present,
     otherwise absent;
   - no other child key exists.
   If at least one set or clear was requested and the result has zero children,
   the `conditions` key is absent from the target run.
4. **The ordered key list and parent position are exact.** Compute the
   expected list as the baseline `conditions` keys in their original order,
   excluding cleared keys, followed by patch-present keys not already present,
   in fixed field order. Assert `list(candidate_conditions) == expected`. This
   catches a rebuild that reorders, a same-value set that moves a child, and
   new-child order drift. If the baseline target run has a `conditions` key,
   record its index in `list(baseline_target_run)` and assert the candidate
   target run has `conditions` at the same index; if the parent was removed,
   assert `"conditions" not in candidate_target_run`. Never index the parent
   without first asserting existence/absence. If a new `conditions` parent is
   created, assert it is the last key of the target run:
   `list(target_run)[-1] == "conditions"`. A negative control that moves an
   existing parent is rejected by the proof.
5. **Serialization preserves the candidate.** `dump_raw(candidate)` is encoded
   as UTF-8, decoded with `decode_snapshot`, and compared to the candidate with
   `strict_equal`. A mismatch is `INTERNAL_ERROR`; an encoding or recursion
   failure is `SERIALIZATION_FAILED`.

Together these prove that the emitted manifest differs from the loaded
snapshot only where the patch and clears requested. They do not prove lexical
or byte preservation; the backup keeps the original bytes.

## 6. Write path

### 6.1 Reused primitives

The command reuses, without duplicating behavior:

- `manifest.load_manifest` for the one validated read and `source_bytes`;
- `migrate.decode_snapshot` for independent strict decodes (same duplicate-key
  rejection, finite-float parsing and `results[*].value` non-finite
  resolution as the loader);
- `migrate.strict_equal` for the decoded-value proof;
- `migrate._read_pre_backup` for the pre-backup stale read;
- `migrate._target_mode` for the POSIX mode bits;
- `migrate._backup_for_migration` for the `.bak` generation, reuse
  eligibility, exclusive create, mode/readback verification and permission
  subset rules;
- `migrate._classify_late_failure` for the non-committed late-failure
  classification;
- `manifest.save_raw(candidate, loaded.path, expected_bytes=loaded.source_bytes)`
  for staging, mode preservation, the final byte check, `os.replace` and the
  committed-cleanup invariant.

These helpers are imported unchanged from `migrate.py`. A reused helper may
raise `MigrationError`; the direct manifest load raises `ManifestLoadError`.
`MigrationError` is translated to `ConditionsError` using the **code only**,
never the exception text or a message match; `ManifestLoadError` maps to
`MANIFEST_LOAD_FAILED` without reading its text. The code mapping is:

| Source code | Conditions code |
|---|---|
| `ManifestLoadError` or `MigrationError("MANIFEST_LOAD_FAILED")` | `MANIFEST_LOAD_FAILED` |
| `MigrationError("READ_FAILED")` | `READ_FAILED` |
| `MigrationError("STALE_MANIFEST")` | `STALE_MANIFEST` |
| `MigrationError("WRITE_FAILED")` | `WRITE_FAILED` |
| `MigrationError("RESERVED_PATH")` | `RESERVED_PATH` |
| `MigrationError("SERIALIZATION_FAILED")` | `SERIALIZATION_FAILED` |
| `MigrationError("INTERNAL_ERROR")` | `INTERNAL_ERROR` |

A code outside the conditions closed set is mapped defensively to
`INTERNAL_ERROR`; `UNSUPPORTED_TARGET` is never produced by these helpers and
is not part of the conditions catalogue. No `str(exc)`, traceback, path or
value travels through the translation.

If reviewers reject cross-module use of private helpers, the alternative is a
behavior-preserving extraction into a shared internal write-support module in
3d3-1; that extraction must keep every migrate test green and is a HOLD item.
The design prefers the smaller change: no edits to accepted `migrate.py`.

### 6.2 Stale checkpoints

There are two stale checkpoints, as in migration:

1. **Before backup.** Re-read the target and compare with
   `loaded.source_bytes`. A difference is `STALE_MANIFEST`; a read failure is
   `READ_FAILED`. Neither creates a backup or a temp file. A dry run performs
   this read too.
2. **Inside `save_raw`.** Its final source-byte check runs immediately before
   `os.replace`. Under the accepted commit invariant, a cleanup-only error
   after a committed replace is suppressed and is success. If `save_raw`
   raises, no commit was recorded; classify without matching exception text
   by re-reading the target:
   - bytes differ -> `STALE_MANIFEST`;
   - re-read fails -> `WRITE_FAILED`;
   - bytes unchanged -> `WRITE_FAILED`.

The late code is an observation of the bytes present at the diagnostic
re-read, not reliable identification of the original failure. The
counterexamples and the write-safety conclusion are the same as migration
section 3.7. A `save_raw` `OSError` is classified by that byte re-read; any
non-`OSError` exception from `save_raw` is `INTERNAL_ERROR`, matching
migration.

### 6.3 Backup, permissions and reserved paths

- Backups use the existing `runevidence.json.bak`, `.bak.2`, `.bak.3`, ...
  namespace. No new suffix is introduced; `is_own_file()` and
  `backup_generation()` already exclude those names from artifact tracking.
- The newest generation is reused only when it is a regular non-symlink file,
  not the target or a hardlink/same-file alias, with bytes equal to the loaded
  source and POSIX permission bits no wider than the source. Otherwise the
  next generation is created with `O_CREAT | O_EXCL | O_RDWR` and
  `mode=source_mode`, read back and verified. Prior generations are never
  overwritten, modified or deleted.
- A new backup is owned by the invoking process; owner, group, ACLs and
  extended attributes are not copied. The "not more permissive" guarantee is
  scoped to POSIX permission bits under the process umask.
- The target folder is enumerated exactly once, and only for a real changed
  run. Dry run, no-op and every refusal before the backup stage do not
  enumerate it.
- The resolved target must not itself be a backup generation. The check runs
  before load and covers a regular `runevidence.json.bak`/`.bak.N`, a symlink
  resolving into that namespace, and any alias whose resolved basename is a
  backup name. A symlink named `.bak` that resolves to the manifest is
  allowed; the symlink itself is never reused as a backup, and a new
  generation is created beside the resolved manifest.
- A folder operand named like a backup is not reserved; resolution appends
  `runevidence.json`, and the check applies to that resolved file basename.
- No conditions authoring is allowed when the resolved target is a backup
  name; `RESERVED_PATH` is returned without a load or write.

### 6.4 Dry run

`--dry-run` performs the same steps as a real run through patch-supplied
selector validation and the changed computation. For a changed run it also
performs preservation, serialization and the pre-backup stale read, then
prints the would-be outcome and writes nothing. A dry-run no-op returns at the
no-op point after selector validation. A patch-supplied invalid selector is
`SELECTOR_INVALID` even when the edit would otherwise be a no-op.

- no manifest change;
- no backup;
- no temp file;
- no backup-generation enumeration;
- no `_target_mode`, `_backup_for_migration` or `save_raw` call.

A dry run with changes returns exit 0, `changed: true`, `dry_run: true`,
`backup: "none"`. A dry-run no-op returns exit 0, `changed: false`,
`dry_run: true`, `backup: "none"`. Any invalid patch, a patch-supplied
selector failure, serialization, stale or read failure returns the same exit 2
and code as a real run.

### 6.5 Failure side effects by stage

| Stage | Manifest | Backup | Temp |
|---|---|---|---|
| grammar, argument cardinality | unchanged | none | none |
| reserved path | unchanged | none | none |
| load, version, run selection | unchanged | none | none |
| patch read/parse, candidate, schema, bounds | unchanged | none | none |
| patch-supplied selector validation | unchanged | none | none |
| no-op (changed false) | unchanged | none | none |
| preservation proof | unchanged | none | none |
| serialization eligibility | unchanged | none | none |
| pre-backup stale or `READ_FAILED` | unchanged | none | none |
| backup create/verify/fsync failure | unchanged | only prior generations; incomplete new file removed | none |
| backup success, then `save_raw` stage/fsync/final-check/replace failure (no commit) | unchanged | completed backup may remain | temp removed when cleanup succeeds; may remain if cleanup fails or crashes |
| replace committed, then post-replace cleanup `unlink` raises `OSError` | **new manifest** | completed backup remains | temp may remain; **SUCCESS, exit 0** |
| late stale at `save_raw` final check (no commit) | unchanged | completed backup may remain | temp removed when cleanup succeeds; may remain |
| crash during backup | unchanged | incomplete backup may remain | none |
| crash during staged write | unchanged | completed backup may remain | temp may remain |
| crash after `os.replace` | new manifest | completed backup exists | none (renamed away) |

Refusals before the backup stage create nothing. A later non-committed
failure does not replace the manifest but may leave the backup; this is
recoverable evidence, not a rollback. A committed replacement whose only later
error is post-replace cleanup is success, not a refusal or write failure.
Once `save_raw` returns, the replacement is committed and the command performs
no further manifest I/O, validation or write; it only builds the success
result. Parent-directory `fsync` is absent, so directory-entry and backup
durability are not guaranteed across a crash. No lock is taken.

## 7. Output and exit codes

### 7.1 Exit codes

- `0` — the candidate was written; the candidate was already current (no-op);
  a dry run completed; or a committed replacement whose only later error was
  post-replace cleanup (a temp file may remain).
- `2` — invalid arguments, unsupported target version, reserved path, manifest
  load failure, run selection failure, invalid patch, invalid selector,
  serialization failure, read failure, stale observation, non-committed write
  failure or internal error.

Exit `1` is not used. This command has no "evidence problems found" verdict.

### 7.2 Success JSON

Exactly one complete document, closed key set:

```json
{
  "format": "runevidence-conditions-result-v1",
  "ok": true,
  "run": "baseline-run",
  "changed": true,
  "dry_run": false,
  "backup": "created",
  "set": ["evaluation_split", "source_revision"],
  "cleared": ["environment"],
  "set_count": 2,
  "cleared_count": 1
}
```

- `run` is the supplied, exactly matched run ID. It is the only ID in the
  document and is allowed because the caller supplied it.
- `set` lists patch keys in fixed field order. A patch key set to `null` is
  listed as set.
- `cleared` lists `--clear` fields in fixed field order.
- `set_count` and `cleared_count` are integers equal to the array lengths.
- `changed` is a real boolean: true when the candidate differs from the loaded
  snapshot under `strict_equal`.
- `dry_run` is a real boolean.
- `backup` is `"created"`, `"reused"` or `"none"`. It is `"none"` for a no-op
  or dry run; a real changed run never reports `"none"`.
- No manifest, backup or patch path; no values; no nested content; no selector
  or artifact IDs; no hashes; no metadata.

Requested operations are listed even when the child value is unchanged.
`changed` is false only when the complete resulting document is
value-identical to the loaded snapshot; a structural normalization such as
removing an empty parent or the conditions key is a change. Patch-supplied
selector validation runs before `changed`, so an invalid selector re-supplied
with its current value is `SELECTOR_INVALID`, not a `changed: false` no-op.
The `set`/`cleared` arrays are the requested per-field flags;
`set_count`/`cleared_count` are the aggregate.

### 7.3 Success text

Text uses only fixed vocabulary, the supplied run ID, field names, counts and
resolved paths:

```text
Wrote /resolved/experiment/runevidence.json for run baseline-run
(set: evaluation_split, source_revision; cleared: environment).
Backup: /resolved/experiment/runevidence.json.bak
```

```text
/resolved/experiment/runevidence.json already matches the requested state for
run baseline-run. Nothing was written.
```

```text
/resolved/experiment/runevidence.json already matches the requested state for
run baseline-run. Dry run: nothing was written.
```

```text
Would update /resolved/experiment/runevidence.json for run baseline-run
(set: evaluation_split; cleared: none). Dry run: nothing was written.
```

The second no-op form is the dry-run no-op and must say it was a dry run. The
backup component is the actual reused or newly created generation (for
example `.bak` or `.bak.2`). A real changed run with zero requested set fields
prints `set: none`; the same for clears. A committed replacement with a
post-replace cleanup error prints the normal `Wrote ...` text and never says
nothing was replaced.

### 7.4 Error JSON

```json
{
  "format": "runevidence-conditions-error-v1",
  "ok": false,
  "exit_code": 2,
  "error": {"code": "PATCH_INVALID"}
}
```

Closed keys only. No run ID, field name, path, side, message, hint, location,
selector, submitted token or exception text.

### 7.5 Error text

One fixed line per code. No path, run ID, field name or value:

```text
ERROR (INVALID_ARGUMENTS): the conditions command line is invalid. Nothing was written.
ERROR (UNSUPPORTED_VERSION): the manifest declares or omits schema 0.1; use runevidence migrate PATH --to 0.2 first. Nothing was written.
ERROR (RESERVED_PATH): the target resolves to a backup name; choose the manifest, not a backup. Nothing was written.
ERROR (MANIFEST_LOAD_FAILED): the manifest could not be loaded and validated. Nothing was written.
ERROR (RUN_SELECTION_INVALID): --run must match exactly one existing run ID. Nothing was written.
ERROR (PATCH_INVALID): the patch is not a valid conditions patch. Nothing was written.
ERROR (SELECTOR_INVALID): a declared run condition selector is invalid. Nothing was written.
ERROR (SERIALIZATION_FAILED): the updated manifest could not be serialized safely. Nothing was written.
ERROR (READ_FAILED): a required local input could not be read. Nothing was written.
ERROR (STALE_MANIFEST): the bytes observed at the stale check differ from the loaded snapshot; nothing was replaced.
ERROR (WRITE_FAILED): the updated manifest could not be written; the manifest was not replaced.
ERROR (INTERNAL_ERROR): the conditions update failed internally; the manifest was not replaced.
```

`PATH` in the `UNSUPPORTED_VERSION` line is a literal placeholder, not the
actual operand. The line deliberately names the remedy without printing a
path.

### 7.6 Closed code catalogue

Exactly this set is closed for the command:

| Code | Trigger | Side effects |
|---|---|---|
| `INVALID_ARGUMENTS` | grammar; missing `--run`; duplicate `--patch`; no `--patch` and no `--clear`; unknown/duplicate `--clear` field; extra operand; unknown option | none |
| `UNSUPPORTED_VERSION` | loaded manifest declares or omits schema 0.1 (only 0.1/0.2 load) | no patch read, no write, no backup |
| `RESERVED_PATH` | resolved target basename is a backup generation | no load, no write |
| `MANIFEST_LOAD_FAILED` | resolution/stat/read/decode/schema failure of the target | no write |
| `RUN_SELECTION_INVALID` | zero or multiple exact `runs[i].id` matches | no patch read, no write |
| `PATCH_INVALID` | patch content after a successful read: malformed JSON, parse-time `RecursionError`, duplicate/unknown key, wrong shape/type, blank string, invalid null, non-UTF-8-encodable string or lone surrogate, integer parse-limit violation, protocol `{}`, protocol nested null, environment `{}`, non-finite number, integer/float magnitude, container width, node count, size/depth/string bound, patch/clear conflict, candidate schema failure | no write |
| `SELECTOR_INVALID` | a patch-supplied non-null selector fails existence, non-directory, expected kind or `input_to` attachment; duplicate artifact IDs resolve by first occurrence; validation runs before `changed`, so a same-value invalid selector also fails | no write |
| `SERIALIZATION_FAILED` | `UnicodeEncodeError` or `RecursionError` during dump/encode/decode of the candidate | no backup, no write |
| `READ_FAILED` | patch file/stdin read failure, or pre-backup manifest re-read failure | no write; pre-backup: no backup |
| `STALE_MANIFEST` | pre-backup bytes differ, or late non-committed diagnostic observes different bytes | pre-backup: no backup; late: backup may remain |
| `WRITE_FAILED` | backup create/verify/fsync, stage, replace, mode or readback failure **before commit**; late diagnostic re-read fails or observes unchanged bytes | manifest unchanged; backup may remain; temp removed when cleanup succeeds |
| `INTERNAL_ERROR` | unexpected defect, including a pre-serialization preservation-proof mismatch or a post-serialization round-trip mismatch (`strict_equal(candidate, emitted)`) | no partial document |

No other code is produced. A no-op is `ok: true`, `changed: false`, exit 0.
A committed replacement whose only later error is post-replace cleanup is
`ok: true`, `changed: true`, exit 0.

### 7.7 Envelope serialization

- Build the complete document before printing; no partial output.
- Envelopes use `json.dumps(..., indent=2, ensure_ascii=True,
  allow_nan=False)`.
- Manifest serialization does **not** use `allow_nan=False`; `dump_raw` keeps
  the legacy non-finite tokens accepted at `results[*].value`.
- `ok`, `changed`, `dry_run` are real JSON booleans; counts are integers;
  `exit_code` is the integer 2 on error.
- Tests assert exact key sets and no extra keys.

## 8. CLI, pins and documentation

### 8.1 26 routes and order

Adding `conditions` makes 26 unique routes:

```text
validate, validate-batch, inspect, compare, comparability, leaderboard, matrix, run,
claim, lineage, impact, coverage, coverage-gate, fingerprint, fingerprint-compare,
coverage-diff, review-diff, impact-diff, review-gate, init, migrate, conditions,
rehash, add, schema, adapters
```

`conditions` is registered directly after `migrate`. Removing it recovers the
exact accepted 25-route sequence.

### 8.2 Pin refresh packet

`tests/test_experimental_cli_parser_contract.py`:

- Preserve the current reviewed 25-route CLI pair as
  `HISTORICAL_25_ROUTE_CLI_HASH`:

  ```text
  749a6a7270216417d278524453a3e582767e4c48e4111c2debd0ce5261abcffd  # LF
  08361a545733bf1ea8dbf31efcb69229defe23039af2491bf5e1b109f46e105f  # CRLF
  ```

- Preserve the current tuple as `HISTORICAL_25_ROUTES`.
- Refresh `CLI_HASH` to exactly two new digests (LF and CRLF) computed from the
  reviewed `cli.py`.
- Add `HISTORICAL_25_ROUTE_CLI_HASH` to the historical-disjointness set in
  `source()` and to the constant-test disjointness assertion.
- Extend `ROUTES` and the count assertion to 26.
- Keep the existing 23- and 22-route derivations from `HISTORICAL_24_ROUTES`
  unchanged, and add the historical 25 -> 24 derivation:
  `[r for r in HISTORICAL_25_ROUTES if r != "migrate"] ==
  list(HISTORICAL_24_ROUTES)`.
- **Explicitly replace the now-false current line**
  `[r for r in ROUTES if r != "migrate"] == list(HISTORICAL_24_ROUTES)` with
  `[r for r in ROUTES if r not in ("migrate", "conditions")] ==
  list(HISTORICAL_24_ROUTES)`.
- Keep `[r for r in ROUTES if r != "conditions"] ==
  list(HISTORICAL_25_ROUTES)`.
- **Also explicitly replace parser-test line 127**
  `[r for r in ROUTES if r not in ("validate-batch", "comparability",
  "migrate")] == list(HISTORICAL_22_ROUTES)` with
  `[r for r in HISTORICAL_25_ROUTES if r not in ("validate-batch",
  "comparability", "migrate")] == list(HISTORICAL_22_ROUTES)`. The old
  current-`ROUTES`-based 22-route line is absent; the historical 22-route
  comparison must not be polluted by the new route.
- Assert `ROUTES[ROUTES.index("migrate") + 1] == "conditions"`.
- Append the current reader digest
  `aa7eeedadb7495b80a8c551683d759c3195a62d39a3a22025450d2d763bee360`
  to `HISTORICAL_READER_HASHES` before replacing `READER_HASH`.
- Assert the new `READER_HASH` is exactly one LF digest, is not in the
  historical reader history, and matches the edited staged-reader test.
- Add a grammar-only `conditions` parse test using the comparability
  handler-replacement pattern; the historical seven-handler
  `parse_without_dispatch` helper does not admit this route.

`tests/test_experimental_staged_reader.py`:

- Add `conditions` to the explicit `current_public` set, not only the count.
- Update 25 -> 26 and the dated comment.
- Recompute the single LF `READER_HASH`.
- Keep the argv tripwire cases unchanged (`validate`, `validate --json`,
  `validate --strict`, `inspect`, `inspect --json`).

`tests/test_experimental_versioned_manifest.py`:

- Add `conditions` to its explicit `current_public` set.
- Update 25 -> 26.

Historical hashes, dated notes, earlier private pin records and the accepted
25-route packet stay untouched. No private/runtime route may be added;
`PRIVATE_ROUTE_MARKERS` still applies (`conditions` contains none of them).

### 8.3 Parser and boundary

- `conditions` uses `Path` for PATH, a required `--run` string, `--patch`
  as a raw string (`type` omitted or `type=str`) with `action="append"`
  (then exactly zero or one), `--clear` with `action="append"`, `--dry-run`
  and `--json`.
- The raw `--patch` value is compared exactly to `-`; `Path` is constructed
  only for a non-`-` value. `--patch ./-` is a file, not stdin.
- Do not add a custom argparse `type=` callable or helper for `--patch`; the
  pinned parser-structure test enumerates the allowed call names in
  `build_parser`, and a plain string option keeps that pin intact.
- The parser accepts an invocation with neither `--patch` nor `--clear`; the
  command layer refuses it with `INVALID_ARGUMENTS`. This keeps the
  "at least one operation" rule in one place and still guarantees one
  sanitized document on failure.
- `--patch` appears more than once -> `INVALID_ARGUMENTS`; do not merge or
  last-win.
- `--clear` duplicates or unknown fields -> `INVALID_ARGUMENTS`.
- The sanitized boundary is `_conditions_main(raw)`, mirroring
  `_migrate_main`; `main()` dispatches to it before the normal parse.
- No `--redact-manifest-path`, `--strict`, `--summary-only`, `--allow-*` or
  private route.
- The reader seam, staged reader, SQLite, directory scanner and adapters are
  not connected to this command.

### 8.4 Help, README and the 0.2 guide

- CLI module docstring: add `conditions` to the writer list and the backup
  sentence.
- Root parser description: add `conditions` to the writer list.
- Subparser help: section 2.5 text.
- README:
  - one command-table row with the full synopsis;
  - writer inventory and backup sentence (`init`, `migrate`, `conditions`,
    `rehash`, `add`; `init --force`, `migrate` and `conditions` keep
    backups);
  - exit-code 2 guidance for a refused/failed conditions update;
  - single-writer guidance (`conditions` joins `rehash`, `add` and `migrate`);
  - a new "Authoring recorded conditions" section covering the patch format,
    null versus clear, selectors, bounds, 0.1 refusal, dry run, backup,
    output redaction and examples;
  - document the `conditions: {}` + clear-absent normalization: the empty
    parent is removed and the outcome is `changed: true`, not a no-op;
  - revise the migrated-0.2 "hand-authored from that point" sentence to say
    `run.conditions` can use the `conditions` command while `dataset_id` and
    `metric_definition` remain hand-authored;
  - update the mandatory authoring order so `run.conditions` can use the
    command while `dataset_id` and `metric_definition` remain hand-authored.
- `docs/comparability-schema-0.2.md`:
  - add an "Authoring conditions with the CLI" subsection;
  - explicitly revise the existing sentence that treats 0.2 manifests as
    "hand-authored for the first-class fields": `run.conditions` can be
    authored with the `conditions` command or by hand, while `dataset_id` and
    `metric_definition` remain hand-authored in this gate;
  - explicitly revise the mandatory order's step 3: the 0.2 guide currently
    says "Add the first-class fields (`run.conditions`,
    `artifact.dataset_id`, `result.metric_definition`)"; the README uses the
    separate "Hand-add" wording. Revise both: guide step 3 becomes "Author
    `run.conditions` with `conditions` or by hand, then add
    `artifact.dataset_id` and `result.metric_definition`"; the README uses
    "hand-add" only for `dataset_id` and `metric_definition`;
  - state that hand-editing remains supported;
  - document the `conditions: {}` + clear-absent normalization: the empty
    parent is removed and the outcome is `changed: true`, not a no-op;
  - state the 0.1 refusal and the explicit `migrate` step;
  - state the null/clear matrix, selector `input_to` requirement, environment
    and comparison bounds and the absence of legacy-conflict preflight;
  - state that the command does not run `validate`, `inspect` or
    `comparability` and does not prove readiness.
- README must include `conditions` in the command table, writer inventory,
  exit-code 2 guidance, single-writer guidance and the authoring section; the
  existing writer/backup sentences must name it alongside `init`, `migrate`,
  `rehash` and `add`.
- No README or guide claim that the command verifies artifact identity,
  reproducibility or scientific validity.
- Preserve dated/private pin records and the accepted Stage 2/3 contract text.
  This RFC gains a status line only when implemented; its contract text is not
  silently rewritten.
- No private route.

## 9. Test plan

New tests: `tests/test_conditions_authoring.py` (core) and
`tests/test_cli_conditions.py` (CLI, envelopes, privacy, no-I/O). Parser/route
tests extend the three pin/structure files named in section 8.2. All tests use
`tmp_path` copies; no committed example or manifest is rewritten in place.

### 9.1 Patch parsing

- Top-level object accepted; array, string, number, boolean, `null`, empty
  file and whitespace-only file -> `PATCH_INVALID`.
- Valid UTF-8 accepted; invalid UTF-8, a UTF-8 BOM and trailing data ->
  `PATCH_INVALID`; a JSON `\ud800`-style lone surrogate escape is
  `PATCH_INVALID` at patch validation. Separately, a **source manifest**
  containing a lone surrogate still loads, but a changed edit that leaves a
  lone surrogate in the candidate fails `SERIALIZATION_FAILED`; an edit that
  removes the only one can succeed.
- Duplicate keys at the top level and nested inside protocol/environment ->
  `PATCH_INVALID`; no last-wins.
- Unknown top-level keys, including `conditions`, `run`, `schema_version` and
  `dataset_id` -> `PATCH_INVALID`.
- `NaN`, `Infinity`, `-Infinity`, `1e400` and non-finite floats ->
  `PATCH_INVALID`.
- Exactly 65,536 bytes accepted; 65,537 -> `PATCH_INVALID` for both a patch
  file and stdin. The command never reads more than 65,537 bytes; a larger
  file is rejected after 65,537.
- A 64 KiB deeply nested patch that makes `json.loads` raise
  `RecursionError` -> `PATCH_INVALID`, not a crash or `INTERNAL_ERROR`.
- A 5,000-digit integer literal -> `PATCH_INVALID`: on the CPython
  3.10.7+/3.12 default it exceeds the 4,300-digit parse limit, and even if a
  runtime accepts it, it exceeds `MAX_SAFE_INT` at the magnitude check.
- Exactly depth 9 accepted; depth 10 -> `PATCH_INVALID`; a scalar member of a
  depth-9 container is accepted because scalars add no level.
- String/key length exactly 1,024 accepted; 1,025 -> `PATCH_INVALID`, for
  each scalar and for environment keys/values.
- `--patch` file, `--patch -` stdin, `--patch ./-` file named `-`; a
  `--patch` missing file, directory, unreadable file or stdin read error ->
  `READ_FAILED`; `--patch` repeated; no `--patch` and no `--clear`; `--clear`
  unknown/duplicate; patch key plus `--clear` same field.
- The patch path never appears in any output; a patch file containing secret
  text produces only a fixed error.

### 9.2 Set, clear and null matrix

For every field, test absent, present value, present `null` (where valid),
clear, clear-absent, and set-equals-current. Specifically:

- split/source revision: nonblank string; `""` and whitespace -> invalid;
  `null` -> explicit UNKNOWN; clear -> absent.
- protocol: `null` -> UNKNOWN; `{}` -> invalid; missing `protocol_id` ->
  invalid; `{"version": null}` -> invalid; `version` bool/float -> invalid;
  `protocol_id` blank -> invalid; unknown member -> invalid; clear -> absent.
- selectors: valid ID; `null` -> invalid; wrong type -> invalid; pattern
  violation -> invalid; clear -> absent; no fallback and no edge synthesis.
- environment: `null` -> UNKNOWN; `{}` -> refused; array/string/number ->
  invalid; nested null member accepted as a value; clear -> absent.
- `conditions` absent + set -> created; absent + clear-only -> unchanged;
  `conditions: {}` + set -> child added; `conditions: {}` + clear absent ->
  parent removed and `changed: true`; last child cleared -> parent removed;
  clear of `null` -> key removed and fallback restored; `null` set where
  absent -> changed; set to same value -> no-op; clear absent when the parent
  is absent or still has another child -> no change.
- Empty patch `{}` with no clears -> no-op and an existing `conditions: {}`
  is left untouched.
- Empty patch `{}` / clear-absent no-op (parent absent or still has another
  child) on a manifest containing an unrelated invalid selector -> exit 0,
  `changed: false`, no write, and no `SELECTOR_INVALID`: untouched selectors
  are not validated even though selector validation runs before the no-op
  check.
- A patch that re-supplies an existing invalid selector with its current value
  -> `SELECTOR_INVALID`, not `changed: false`: patch-supplied selector
  validation runs before the no-op check. A patch that re-supplies an
  existing valid selector with its current value is a `changed: false` no-op.
- Non-fixed-order conditions fixture (for example keys ordered `environment`,
  `evaluation_split`, `source_revision`) plus a same-value set of one child ->
  `changed: false`, no write, and the exact ordered key list is unchanged.
  Also combine a same-value set with a different changed child so the
  preservation proof runs and asserts the same-value child kept its position.
  A new child appends after existing children; multiple new children append
  in fixed field order.
- An existing `conditions` parent keeps its index in the target run's ordered
  key list; a parent removed by the edit is absent. A negative control that
  moves an existing parent is rejected by the preservation proof.
- `conditions` is never written as `null`; an empty object is never left after
  an operation.
- Requested `set`/`cleared` arrays preserve fixed field order regardless of
  patch key order; counts match; duplicate/unknown clears refuse.
- Assert `source_a` and the candidate share no mutable container by identity;
  a one-sided nested-mutation negative control against an independent baseline
  fails the preservation proof.

### 9.3 Selector graph

- A patch-supplied selector naming an existing artifact of the right kind
  with an `input_to` edge -> accepted.
- Missing artifact -> `SELECTOR_INVALID`.
- Wrong kind -> `SELECTOR_INVALID`.
- Directory artifact -> `SELECTOR_INVALID`.
- No `input_to`, produced-by only, or an edge to a different run ->
  `SELECTOR_INVALID`.
- Repeated/multiple `input_to` edges with at least one correct edge ->
  accepted.
- An invalid selector in another run, or an untouched invalid selector in the
  target run, does **not** block an unrelated edit or a no-op and is not
  validated; it remains a `validate`/`comparability` finding.
- A cleared selector is not validated; removing an invalid selector is a
  valid repair.
- A patch-supplied valid selector with its current value is a `changed: false`
  no-op, but selector validation still runs first. A patch-supplied invalid
  selector with its current value is `SELECTOR_INVALID`.
- Duplicate artifact IDs resolve by **first occurrence**
  (`artifacts.setdefault(artifact.id, artifact)`); the helper is not
  last-wins. Equivalence fixtures: (a) first artifact wrong kind, second same
  ID right kind -> `SELECTOR_INVALID`; (b) first artifact right kind, second
  wrong -> accepted.
- Selector `null` -> `PATCH_INVALID`, never `SELECTOR_INVALID`.
- No relationship is added, removed or rewritten by a successful command;
  compare relationships before and after.
- An invalid selector never falls back to a single attached dataset or
  checkpoint.
- The authoring selector helper's verdict for each of the four rules and both
  duplicate-ID directions matches `check_conditions` on a manifest whose only
  declared selector is the same selector.
- `check_conditions` findings remain sanitized (no IDs/paths), and the
  command never calls `run_checks`, `validate` or `inspect`; it does not call
  `check_conditions` over the complete candidate.
- A duplicate artifact ID outside selector resolution is not claimed as
  validated; `comparability`/`validate` remain the review path.

### 9.4 Environment and comparison bounds

- Depth 8 accepted; depth 9 -> `PATCH_INVALID`, counting the environment root
  as 1.
- Compact JSON size exactly 16,384 accepted; 16,385 -> `PATCH_INVALID`;
  multi-byte and escaped strings measured with `ensure_ascii=True`.
- Key/string 1,024 accepted; 1,025 -> `PATCH_INVALID`.
- `NaN`/`Infinity`/overflow -> `PATCH_INVALID`.
- `{}` refused; non-object refused; nested arrays/objects/null/bool/int/finite
  float accepted.
- Integer `9007199254740991` accepted; `9007199254740992` and
  `-9007199254740992` -> `PATCH_INVALID` (also for
  `evaluation_protocol.version`).
- Float magnitude `1e100` accepted; `1e300` and `-1e300` -> `PATCH_INVALID`.
- A 128-key environment object accepted; a 129-key object ->
  `PATCH_INVALID`; the same width check applies to supplied lists.
- A supplied value with exactly 4096 nodes accepted if all other bounds fit;
  4097 nodes -> `PATCH_INVALID`; dict keys do not count as nodes.
- A 5,000-digit integer -> `PATCH_INVALID` (parse limit or
  `MAX_SAFE_INT` magnitude).
- `evaluation_protocol.version` `1` and `"1"` are distinct valid values; a
  float `1.0`, bool or null is invalid.
- The authoring bounds and measurement agree with the accepted comparison
  behavior for the same fixtures, without importing the comparison core or the
  bridge in production code.
- Untouched out-of-bounds environment is not re-bounded; a patch to another
  field still succeeds. Re-supplying that same out-of-bounds value in the
  patch is `PATCH_INVALID`.
- A patch with several individually valid values, or a single 4096-node value
  plus wrapper nodes, whose total across all dimensions would exceed 4096
  nodes is not claimed to fail at authoring; the total-input node count
  remains a comparison-time concern.

### 9.5 0.1 refusal and reserved paths

- A valid 0.1 manifest with a valid patch, invalid patch, `--dry-run`, and
  `--patch -` -> `UNSUPPORTED_VERSION`; no patch read (stdin untouched), no
  write, no backup, bytes and `st_mtime_ns` unchanged.
- A manifest with no `schema_version` (decoded as 0.1) ->
  `UNSUPPORTED_VERSION`, not a successful no-op.
- A manifest with explicit `schema_version: "0.3"` -> `MANIFEST_LOAD_FAILED`,
  not `UNSUPPORTED_VERSION`.
- A 0.1 manifest with a 0.2-only key -> `MANIFEST_LOAD_FAILED`, not
  `UNSUPPORTED_VERSION`.
- A 0.2 manifest -> proceeds.
- No automatic migration: a second invocation still sees 0.1 until the user
  runs `migrate`.
- Regular `runevidence.json.bak` and `.bak.2` -> `RESERVED_PATH`, no load, no
  write.
- `runevidence.json` symlinked to `runevidence.json.bak` -> `RESERVED_PATH`.
- `runevidence.json.bak` symlinked to `runevidence.json` -> allowed; the
  resolved manifest is written; the symlink is not reused as a backup; a new
  `.bak.2` generation is created; original bytes are recoverable.
- Folder operand, missing manifest, directory without manifest, symlink loop,
  unreadable file -> `MANIFEST_LOAD_FAILED`.
- Run not found, duplicate selected run ID, near-miss (case/space/prefix) ->
  `RUN_SELECTION_INVALID`; no patch read, no write.
- A unique selected run among other duplicate IDs is editable; the other
  duplicates are not claimed as validated.

### 9.6 Dry run, no-op and write path

- Dry run with changes: exit 0, `changed: true`, `dry_run: true`,
  `backup: "none"`, `st_mtime_ns` unchanged, no backup/temp, no backup
  enumeration, writer helpers not called.
- Dry-run no-op: exit 0, `changed: false`, `dry_run: true`, no writes; text
  says the manifest already matches the requested state **and** that it was a
  dry run. Patch-supplied selectors are validated first; a same-value invalid
  selector is `SELECTOR_INVALID`, not a dry-run no-op.
- Real no-op: no write, no backup, no stale read, `save_raw`/backup helpers
  not called; text says the manifest already matches the requested state and
  omits the dry-run clause. Patch-supplied selectors are validated first; a
  same-value invalid selector is `SELECTOR_INVALID`.
- Real changed run: manifest replaced, one backup created or reused, mode
  preserved, success envelope and text.
- Backup reuse matrix is inherited from migration: regular distinct
  byte-identical permission-subset file reused; symlink, alias, different
  bytes, more permissive mode or non-regular type -> next generation.
- Concurrent backup-name collision -> `WRITE_FAILED`, other writer's file
  intact.
- Backup readback/partial-write failure -> `WRITE_FAILED`, only the
  just-created incomplete file removed.
- Mode preservation: source `0600`/`0640`/`0444` with various umasks; no
  upward `chmod`; ACLs/ownership not asserted preserved.

### 9.7 Stale, read and write failures

- Pre-backup stale injection -> `STALE_MANIFEST`; no backup, no temp, manifest
  unchanged.
- Pre-backup re-read failure -> `READ_FAILED`; no backup, no temp.
- Late stale after backup before `save_raw` final check -> `STALE_MANIFEST`;
  backup may remain; manifest unchanged.
- `save_raw` `OSError` classification without exception-text matching:
  bytes differ -> `STALE_MANIFEST`; bytes unchanged -> `WRITE_FAILED`;
  diagnostic re-read fails -> `WRITE_FAILED`.
- Late-classifier counterexamples (stale then restored -> `WRITE_FAILED`;
  unrelated failure then concurrent change -> `STALE_MANIFEST`) assert the
  observation and the write-safety conclusion.
- Post-replace cleanup injection: `save_raw` suppresses the error; exit 0,
  new manifest, intact backup, normal `Wrote ...` text, no diagnostic read,
  no false "nothing was replaced".
- Pre-replace cleanup/staging failure: `save_raw` re-raises; exit 2, old
  manifest intact, no false success.
- Injected backup/fsync/replace/mode/readback failure: manifest unchanged;
  backup state matches the stage table; temp cleanup as specified.
- Simulated crash/partial write: incomplete backup or temp may remain; the
  manifest is never truncated; no parent-directory durability claim.
- A **source manifest** containing a lone surrogate escape loads, but a
  changed edit that leaves a lone surrogate in the candidate ->
  `SERIALIZATION_FAILED`; no write, no backup, no value echo. An edit that
  removes the only one can succeed. A no-op returns before serialization and
  writes nothing. Patch-supplied lone surrogates are a separate
  `PATCH_INVALID` case and must not be conflated with this source-side
  residual.
- No lock is claimed; the detectable half of the check-then-replace race has
  a named test.
- Reused-helper error mapping: monkeypatch each reused helper to raise
  `MigrationError` with every mapped code
  (`MANIFEST_LOAD_FAILED`, `READ_FAILED`, `STALE_MANIFEST`, `WRITE_FAILED`,
  `RESERVED_PATH`, `SERIALIZATION_FAILED`, `INTERNAL_ERROR`) and assert the
  `ConditionsError.code` is unchanged; assert no exception text, path or value
  appears. An unknown `MigrationError` code maps to `INTERNAL_ERROR`.

### 9.8 Privacy canaries for every outcome

Canaries in: run ID (other than the supplied one), project/run names, split,
protocol ID/version, selectors, artifact IDs, artifact paths, locators, URLs,
hashes, environment keys/values, metadata keys/values, relationship notes,
patch values and the patch path.

Run the canary suite independently for:

- success `created`, success `reused`, cleanup-only post-commit success;
- no-op and dry run;
- every error code: `INVALID_ARGUMENTS`, `UNSUPPORTED_VERSION`,
  `RESERVED_PATH`, `MANIFEST_LOAD_FAILED`, `RUN_SELECTION_INVALID`,
  `PATCH_INVALID`, `SELECTOR_INVALID`, `SERIALIZATION_FAILED`, `READ_FAILED`,
  `STALE_MANIFEST`, `WRITE_FAILED`, `INTERNAL_ERROR`.

Assert:

- stdout, stderr, JSON and text contain no canary except the supplied run ID,
  field names, counts and (text success only) resolved manifest/backup paths;
- the patch path never appears anywhere;
- JSON never contains any path;
- error text never contains a path, run ID or field name;
- bad JSON/UTF-8/parser input containing secret text produces the fixed
  document only;
- Unicode, quotes, newlines and terminal control characters in canaries do
  not reach output;
- a deliberately echoing renderer fails the named assertions.

### 9.9 JSON, text and exit codes

- Exact key sets for success and error; no extra keys; one document per
  invocation; no partial output.
- Real booleans and integer counts; `exit_code` is 2 on error; `allow_nan=False`
  on envelopes.
- `--json` before/after operands and alongside invalid tokens still emits one
  JSON document; `--help` prints help and exits 0; `conditions --version` is
  an unknown option -> `INVALID_ARGUMENTS`, exit 2.
- Text vocabulary is fixed; success text shows resolved paths only; error text
  shows no path; no-op text says the manifest already matches the requested
  state, and the dry-run no-op form says it was a dry run.
- Exit 0 for success/no-op/dry run/cleanup-only success; exit 2 for every
  refusal; exit 1 is never returned.
- A no-op is not an error; a committed cleanup-only success is not
  `STALE_MANIFEST`/`WRITE_FAILED`.

### 9.10 Pins, parser and documentation

- Exactly 26 unique routes in the specified order.
- Removing `conditions` recovers `HISTORICAL_25_ROUTES` exactly.
- The current 25-route CLI pair is preserved as history and included in both
  historical-disjointness checks.
- `CLI_HASH` is exactly two strings (LF, CRLF) and disjoint from all historical
  pins.
- The pin test asserts the corrected current-to-24 line
  `[r for r in ROUTES if r not in ("migrate", "conditions")] ==
  list(HISTORICAL_24_ROUTES)`, the historical 25-to-24 line
  `[r for r in HISTORICAL_25_ROUTES if r != "migrate"] ==
  list(HISTORICAL_24_ROUTES)`, the 24-to-23 line, the 24-to-22 line and the
  25-to-22 line; the old `[r for r in ROUTES if r != "migrate"]` line is
  absent, and the old current-`ROUTES`-based 22-route line
  `[r for r in ROUTES if r not in ("validate-batch", "comparability",
  "migrate")]` is absent.
- The current reader digest is appended to history; the new `READER_HASH` is
  exactly one LF digest, not in history, and matches the edited staged-reader
  test.
- Both explicit `current_public` route sets include `conditions`, not only the
  count; the argv tripwire cases are unchanged.
- `conditions` grammar-only test dispatches nothing, asserts the parsed
  `path`, `run`, `patch` list (for example `["./-"]` keeps the raw `./-`
  token), `clear` list, `dry_run`, `json`, and the absence of
  `strict`/`redact_manifest_path`/`summary_only`; missing `--run` exits 2.
- Root `--help` and module docstring list `conditions` among the writers.
- README command/writer/exit/single-writer/authoring sections and the 0.2
  guide authoring section are updated; no claim of comparability readiness.
- No private route marker, SQLite import or adapter import in the new module.

### 9.11 Python versions and installed wheel

- Run the focused and full suites on Python 3.10.7+ and 3.12.
- Build a wheel and sdist from a clean copy; install in a fresh venv with
  Pydantic 2.6.4 and a current 2.x version.
- Invoke the installed entry point outside the checkout (for example from
  `/tmp`), not `python -m` against the source tree.
- Run set, null, clear, clear-only, dry-run, no-op, 0.1 refusal, reserved
  path, invalid patch and invalid selector cases on copied 0.2 manifests.
- Installed JSON/text shapes and exit codes match source-tree behavior.
- The wheel/sdist contain no `schema/`, `tests/`, `docs/` or `examples/`
  content; `pip check` is clean; no network is used.

### 9.12 No-I/O and filesystem allowlist

Install traps after CLI startup/imports or count named operations, so
argparse/locale/terminal reads and Python startup are not treated as
application environment discovery.

Allowed operations:

- path resolution/stat/lstat for the target, resolved path and backup
  candidates;
- one validated manifest load read;
- one bounded patch read from the supplied file or stdin (only when
  `--patch` is present);
- one pre-backup source-byte read (only for a changed run, including dry run);
- one folder enumeration for backup generations (only a real changed run);
- backup read/verification/reuse checks and exclusive create/write/fsync/
  readback, including mode `stat`/`fstat` and `chmod` on the staged manifest
  and backup;
- `save_raw` stage write, final source-byte read, `os.replace`, mode
  `stat`/`chmod` and temp cleanup;
- one conditional diagnostic target read after a non-committed `save_raw`
  raise;
- post-serialization decode in memory.

Forbidden and trapped: artifact open/stat/scan, directory scans outside the
backup namespace, adapters, checks/hashes, subprocesses, network/DNS/URL,
repository inspection and application environment discovery.

Exercised paths: success (`created` and `reused`), cleanup-only success,
no-op, dry run, 0.1 refusal, reserved path, invalid patch, invalid selector,
serialization failure, pre-backup stale/read failure, late stale/write failure,
backup failure and internal error. Assert:

- grammar/reserved/load/version/run-selection failures perform no patch read;
- clear-only invocations perform no patch read;
- a patch file and stdin are both read with the `MAX_PATCH_BYTES + 1` bound;
- a no-op performs patch-supplied selector validation but no preservation,
  serialization, pre-backup read, backup enumeration or write;
- a dry-run no-op performs patch-supplied selector validation but no backup
  enumeration or write; a dry-run change performs the pre-backup read but no
  backup/write;
- a successful real run performs exactly one folder enumeration and one
  backup operation;
- a non-committed late failure performs at most the one diagnostic read;
- no forbidden operation is attempted on any path;
- traps record before raising.

### 9.13 Negative controls

Each must fail a named assertion: patch-value echo, nested environment echo,
selector-ID echo, artifact-path/hash echo, metadata echo, patch-path echo,
manifest-path echo in JSON, backup-path echo in error text, raw parser
diagnostic, raw loader message, traceback, Unicode/control-character echo,
unexpected artifact read, directory scan outside the backup namespace,
network/DNS attempt, application environment read, adapter call, second
manifest load, missing patch read, missing pre-backup stale read, missing
backup, a backup more permissive than its source, a stale check that matches
exception text instead of re-reading bytes, a committed cleanup-only success
reported as failure, a no-op that writes, a `Path`-typed `--patch` that sends
`./-` to stdin, a whole-candidate selector validation that blocks a no-op or
an unrelated edit, a selector validation skipped for a same-value supplied
invalid selector, and a parent-position proof that accepts a moved existing
`conditions` parent.

## 10. Implementation slices and gates

This RFC is 3d3-0. No implementation starts until it is accepted.

### 3d3-0 — this RFC

- Deliverable: this document only.
- Acceptance: design decisions reviewable; no production/test/schema/example/
  README change; no Git operation; `progress/` untouched; the working tree is
  otherwise unchanged.
- **HOLD:** no code until this RFC is reviewed and accepted.

### 3d3-1 — core transformation

- New `src/runevidence/conditions_authoring.py`:
  - closed `ConditionsError` code set and `ConditionsResult`;
  - patch acquisition (raw string source, file/stdin, `MAX_PATCH_BYTES + 1`
    bound) and strict JSON parsing, including parse-time `RecursionError`,
    UTF-8/lone-surrogate rejection and the integer parse limit;
  - allowed-key/type/duplicate/depth/string bounds and patch/clear conflict;
  - candidate construction from independent strict decodes, mutating the
    target run's `conditions` dict in place;
  - set/clear/null matrix, exact key order, fixed new-child order and
    empty-parent removal;
  - complete `Manifest` revalidation;
  - patch-supplied comparison bounds (string, depth, size, number magnitude,
    container width, per-value node count) and patch-only selector validation
    with first-occurrence duplicate-artifact resolution;
  - patch-supplied selector validation before the changed/no-op computation;
    no-op return before preservation, serialization and stale work;
  - preservation proof (masked projection, exact ordered key list, existing
    parent key index or absence) and serialization eligibility;
  - reuse of `save_raw` and the reviewed migration backup/stale helpers, with
    `MigrationError.code` translation by code only;
  - no CLI, no route, no parser/pin change.
- Tests: `tests/test_conditions_authoring.py` covering sections 9.1-9.7 and
  the core half of 9.8-9.12, including the number/width/node limits,
  5,000-digit integer, `./-` core source handling, no-op ordering with an
  unrelated invalid selector, the same-value invalid selector counterexample,
  the first-occurrence duplicate-ID fixtures, the parent-position negative
  control and the key-order/same-value fixture.
- Acceptance: all core tests pass on Python 3.10.7+ and 3.12; 0.1 refusal,
  reserved paths, no-op/dry-run, backup/reuse, stale/read/write failure,
  preservation, bounds and selector tests pass; no forbidden I/O; no adapter,
  artifact, SQLite or network connection.
- **HOLD:** focused review of the patch format, null/clear matrix, patch-only
  selector validation and first-occurrence resolver, comparison limits,
  selector-before-no-op ordering, key-order/parent-position proof, write path,
  error mapping and failure table before the CLI slice.

### 3d3-2 — CLI and pins

- `src/runevidence/cli.py`: 26th route after `migrate`, `cmd_conditions`,
  `_conditions_main`, fixed failure text table, result document, module
  docstring/root help, `main()` dispatch.
- `--patch` stays a raw string; exact `-` comparison; `./-` is a file; no
  custom argparse type/helper.
- Pin refresh exactly as section 8.2: parser-contract test, staged-reader
  route set and `READER_HASH`, versioned-manifest route set, CLI LF/CRLF
  `CLI_HASH`, historical 25-route pair preserved, explicit replacement of
  both the current-to-24 and current-to-22 `ROUTES`-based assertions.
- Tests: `tests/test_cli_conditions.py` covering sections 9.1-9.9 and the CLI
  half of 9.10-9.13; grammar-only route test; sanitized parse failures;
  `--patch ./-` vs `--patch -`; `conditions --version`; omitted-version and
  explicit-0.3 load fixtures.
- Acceptance: 26 routes in order; removing `conditions` recovers the 25-route
  tuple; the 25 -> 24 historical derivation and the corrected current -> 24
  line pass; the corrected current -> 22 line passes and the old
  current-`ROUTES`-based 24- and 22-route lines are absent; exactly two CLI
  digests; one new reader digest in history; both explicit route sets include
  `conditions`; privacy canaries pass for every outcome; parser diagnostics
  never leak; Python 3.10.7+/3.12 pass.
- **HOLD:** focused review of the pin-refresh packet, corrected 25/24/23/22
  derivations and both stale-line replacements, raw `--patch` token handling,
  sanitized boundary, route order and CLI privacy before merge.

### 3d3-3 — documentation and packaging

- `docs/comparability-schema-0.2.md` authoring section and order update,
  explicitly revising the "hand-authored for the first-class fields" sentence,
  the guide's step-3 "Add the first-class fields" wording and the README's
  separate "Hand-add" wording as section 8.4 requires.
- README command table, writer inventory, exit-code 2, single-writer guidance
  and authoring section, with `conditions` named in every list.
- This RFC's status line only (contract text unchanged) when implemented.
- Installed wheel/sdist checks from section 9.11.
- Acceptance: docs agree that `init` is 0.1, `migrate` is the only 0.2 opt-in,
  `conditions` requires 0.2, hand-editing remains supported, `run.conditions`
  can be authored by the command or by hand, `dataset_id` and
  `metric_definition` remain hand-authored, selectors require `input_to`,
  environment/comparison bounds apply, legacy conflicts still fail
  comparison, and no command claims readiness; the guide step-3 and README
  hand-add citations are corrected; installed package works and matches source
  behavior; no build artifacts committed.
- **HOLD:** docs/packaging review; no release, version bump or publication.

### 3d3 close

- Verify only the files named by accepted slices changed.
- Re-run focused and full suites, Python 3.10.7+/3.12, installed-wheel checks,
  privacy and no-I/O tests.
- Report exact pins, test counts and residual limitations.
- No Git operation, release or version bump.

## 11. Alternatives, rejections and residual risks

### 11.1 Alternatives

| Alternative | Why rejected |
|---|---|
| Put condition values on argv (`--split test --revision abc`) | Exposes sensitive values in shell history, process lists, CI echoes and terminal capture; quoting/Unicode problems; no usable privacy boundary. |
| One `--set FIELD=VALUE` flag per field | Same argv exposure; also makes `null` versus `clear` ambiguous. |
| Require `--patch` even for clears | A clear-only operation is safe and natural; requiring an empty patch file/stdin is artificial and can block on interactive stdin. |
| Allow multiple `--patch` files merged in order | Ambiguous precedence/conflicts, multiple reads and a larger privacy surface. Reject; one patch. |
| Full JSON-manifest editor or generic `edit` command | Unbounded write surface, arbitrary unrelated changes, harder preservation proof, overlaps `rehash`/`add`, and needs its own gate. |
| Auto-migrate a 0.1 target | Violates the explicit-consent and no-migration-side-effect rules; changes a version and creates a backup the user did not ask for. |
| Infer values from legacy metadata, artifact names or environment | Explicit non-goal; would create unsupported assertions. |
| Synthesize `input_to` edges for selectors | Alters relationships and hides a missing graph fact; selectors must be independently attached. |
| Validate every selector in the candidate | Blocks unrelated edits and even no-ops on pre-existing invalid selectors; pre-existing selector findings belong to `validate`/`comparability`, not to a writer. |
| Validate only the target run's selectors | Still blocks an unrelated field on the same run; the command validates only the selectors it supplies. |
| Validate no selectors at all | Would let the patch create a dangling, directory, wrong-kind or unattached selector; the four supplied-selector rules are mandatory. |
| Resolve duplicate artifact IDs last-wins | Diverges from `checks/conditions.py`'s `setdefault` first-occurrence rule and makes validation order-dependent; the first manifest-order artifact decides. |
| Validate supplied selectors only after the no-op check | A re-supplied invalid selector would silently pass as an unchanged value; supplied values are validated before the no-op, matching the bounds rule. |
| `type=Path` for `--patch` | `Path("-") == Path("./-")`, erasing the exact stdin token; use a raw string and convert only a non-`-` value. |
| Bound the whole candidate's node count at authoring | Requires reconstructing the comparison input and scanning untouched dimensions; the command bounds each supplied value and leaves the total to comparison. |
| Re-bound every existing value, not only patch-supplied values | Blocks unrelated edits and duplicates comparison readiness; `comparability` already reports existing bounds. |
| Add legacy-conflict detection in this gate | Expands scope into canonical equality and comparison precedence; `comparability` remains the readiness check. |
| Use `dump_manifest`/a Pydantic round-trip to write | Could drop unknown-but-valid raw values, add defaults, reorder keys or change presence; a raw candidate plus an independent preservation proof is narrower. |
| New backup suffix (`.conditions.bak`) | Not recognized by `is_own_file()`; would be scanned as ordinary content. The `.bak` generation namespace is required. |
| Reuse `_backup_existing` wholesale | Reloads the manifest, prints paths and can print artifact IDs during regeneration analysis; the migration-specific helper has the reviewed output/read contract. |
| Fold `RUN_SELECTION_INVALID` into `INVALID_ARGUMENTS` | Selection happens after load and depends on manifest data; conflating it with grammar hides that no patch was read and that the manifest loaded. |
| Fold `PATCH_INVALID` into `INVALID_ARGUMENTS` | Patch content is a distinct input with a different remedy; a separate closed code is useful to CI and keeps grammar failures read-free. |
| Echo the supplied run ID in error output | Errors need no identifier; success already names the run. Keeping errors code-only is stricter. |
| Print the patch path in text output | Adds sensitivity and recovery confusion; only the manifest/backup paths are needed. |
| Make `conditions` a parent-level patch (`{"conditions": {...}}`) | The parent cannot be null and the six-child surface is the contract; a nested wrapper adds ambiguity and unknown-key handling. |
| YAML/TOML patch | JSON remains the authority; extra parser surface and dependency. |
| Read the patch from an environment variable | Environment discovery and secret exposure; explicitly out of contract. |
| Compare-and-swap or locking | Not available portably under the current writer design; documenting the check-then-replace race is honest. |
| Shared neutral module for bounds/backup helpers | A larger refactor of accepted code; reuse the accepted helpers and mirror the bounds constants with tests. Revisit only if review rejects private-helper reuse. |

### 11.2 Residual risks and limitations

1. The patch is sensitive at rest. Output redaction does not protect the patch
   file, shell history (patch path/run ID/field names), process memory,
   debuggers, terminal capture, backups or the resulting manifest.
2. Success output names the supplied run and the fields changed. That reveals
   which run was authored and which dimensions changed; it is not anonymous
   and not safe for unrestricted publication.
3. The command does not detect unequal first-class/legacy duplicates, duplicate
   artifact IDs outside selector resolution, unrelated dangling references,
   provenance cycles or other graph findings. `comparability` can still fail
   with `INPUT_SHAPE_UNSUPPORTED`; `validate`/`inspect` remain the review path.
4. Selector validation is patch-only and runs before the changed/no-op check.
   A pre-existing invalid selector in an untouched or cleared field does not
   block an edit; it remains a `validate`/`comparability` finding and is not
   repaired. The command guarantees only that the selectors it supplies are
   valid, not that every selector in the manifest is valid. A supplied invalid
   selector with its current value is `SELECTOR_INVALID`, not a no-op.
   Duplicate artifact IDs resolve by first occurrence, matching
   `checks/conditions.py`; a later duplicate never overrides the first.
5. Bounds are enforced on patch-supplied values only. An untouched
   out-of-bounds environment can remain and comparison will reject it;
   re-supplying that value is `PATCH_INVALID`. Each supplied value is bounded
   independently (integer/float magnitude, container width, per-value node
   count), but the comparison core's total node count across all dimensions
   and both sides remains a comparison-time concern. Several individually
   valid values, or one value plus wrapper nodes, can still make the total
   exceed 4096 nodes.
6. Selector validation proves only manifest graph facts (syntax, existence,
   first-occurrence duplicate resolution, recorded kind, directory type,
   `input_to` attachment). It does not verify artifact identity, bytes,
   freshness or scientific meaning.
7. The command reads no artifact bytes, so it cannot prove that a selected
   dataset or checkpoint is the intended file.
8. `conditions: {}` plus a clear of an absent child is normalized to an absent
   `conditions` key and reported as changed. This is the only structural
   normalization; it is documented rather than hidden.
9. A no-op returns after patch-supplied selector validation but before
   preservation, serialization and the pre-backup stale read. A concurrent
   writer is not detected, but nothing is written; the reported verdict
   describes the loaded snapshot.
10. The stale checks are check-then-replace, not a lock. Concurrent writers can
    still race and one edit can be lost.
11. Backup durability is not guaranteed: no parent-directory `fsync`; a crash
    may leave an incomplete backup or a temp file. A completed backup may
    remain after a later non-committed failure.
12. Replacement and backup create new inodes. POSIX mode bits are preserved or
    reduced under the umask; owner, group, ACLs, security descriptors and
    extended attributes are not preserved. Backup ownership is the invoking
    process.
13. A suppressed post-replace cleanup failure may leave a staged temp file.
    The replacement is still committed and successful.
14. Patch size/depth/number/width/node limits bound memory for the patch path
    but are not a general DoS-resistance claim; parser startup, OS caches,
    atime/bytecode effects, debuggers and process memory remain outside the
    no-I/O and privacy claims. CPython's integer string conversion limit is
    interpreter-dependent, but a 5,000-digit integer is `PATCH_INVALID` either
    at parse or at the `MAX_SAFE_INT` magnitude check.
15. A source manifest containing a lone surrogate escape loads, but a changed
    edit that leaves a lone surrogate in the candidate fails
    `SERIALIZATION_FAILED`; an edit that removes the only one can succeed.
    Patch-supplied lone surrogates are a distinct `PATCH_INVALID` case.
16. A 0.3 or future version is `MANIFEST_LOAD_FAILED`, not
    `UNSUPPORTED_VERSION`; the command only recognizes the accepted 0.1/0.2
    loader boundary.
17. The command does not author `dataset_id` or `metric_definition`; users
    still hand-edit those fields. A future general authoring gate would need
    its own patch, privacy and validation contract.
18. Success text may show resolved manifest/backup paths, a deliberate
    deviation from the 3d sketch; JSON never carries a path. Path components
    can reveal folder or project names even when no identifier value is
    printed.
19. The closed code set is a contract. Adding a code later is a reviewed
    change, not an incidental implementation detail.

## Document verification

This v0.3 RFC was accepted and implemented as 3d3 on 2026-11-11. The command,
patch format, bounds, selector rules, write path, envelopes and tests described
above are the implemented contract; this status update does not rewrite the
design sections. This document is the decision record for that implementation.
