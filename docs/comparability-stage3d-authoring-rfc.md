# Stage 3d authoring, migration and preflight RFC (v0.4)

Status: **REVISION v0.4 — IMPLEMENTED (3d-2, 2026-11-11).** The 3d-1/3d-2
contract in this document is implemented. 3d-3 (conditions command) was
deferred by this RFC and is now implemented under the separate [Stage 3d-3
conditions authoring RFC
v0.3](https://github.com/shihaab453/rungraph/blob/main/docs/comparability-stage3d3-conditions-rfc.md);
3d-4 (preflight) remains deferred. The contract text below is unchanged.

Revision: v0.4 (2026-11-11). Changelog: closes the Sol A blocker by specifying the
`save_raw` commit/cleanup invariant (Option A), making a committed replacement whose only
later error is cleanup a success, and aligning the failure contract, stage table,
envelope/exit text, 3d-2 file scope and regression tests. All v0.3 corrections remain.
Date: 2026-11-11.
Authority: `docs/comparability-stage3-schema-rfc.md` v0.3, sections 7, 8, 9 and 11;
`docs/comparability-stage2-privacy-cli-rfc.md` v0.3; `progress/stage3-gate-checklist.md`
(gate closed 2026-11-11).
Baseline: schema 0.2 exists; 0.1 remains the default; writers are version-aware; the
bridge resolves first-class conditions/selectors with bounds-before-conflict and
baseline-first precedence; `validate`/`inspect` report sanitized selector-reference
findings; public 0.2 documentation exists.

## Decision summary

| # | Question | Decision |
|---|---|---|
| 2 | `init` default | **Keep 0.1. Add no flag in 3d.** `migrate` is the only 0.2 opt-in. |
| 3 | Explicit migration | **Implement `migrate PATH --to 0.2`** in 3d-2. Explicit, value-preserving, atomic, backed up, never inferential. |
| 4 | Conditions authoring command | **Defer.** Hand-editing 0.2 JSON remains supported and documented; a future command needs its own gate. |
| 5 | Preflight/discovery helper | **Defer.** ID-listing is rejected; a presence-only preflight is sketched for a separate privacy gate, not implemented in 3d. |
| 6 | CLI impact | **One new route (`migrate`, 25 total)**, one reviewed pin refresh, one sanitized parse boundary for that route. `init` code is unchanged except for one fixed `--force` warning bundled into 3d-2. |

The smallest safe change is: keep generating 0.1, add exactly one explicit write command
that changes exactly one key, and add no read or discovery surface. Backup safety,
serialization eligibility and decoded-value preservation are part of that one command,
not a broader authoring surface.

## 1. Scope, non-goals and threat/authorization model

### 1.1 Scope

3d decides and, in later implementation slices, implements:

1. whether `init` starts writing 0.2;
2. an explicit command that declares an existing valid manifest as 0.2;
3. whether first-class condition fields get an authoring command;
4. whether a preflight/discovery helper exists at all;
5. the CLI/privacy/test/gate consequences of those decisions.

### 1.2 Non-goals

- No automatic migration, no migration on load, no migration as a side effect of
  `validate`, `inspect`, `rehash`, `add`, `init`, comparison or fingerprint.
- No inference of first-class fields from legacy metadata, file names, folder layout,
  adapters, environment variables, URLs, artifact bytes or run/result names.
- No rewriting of existing 0.1 evidence. A migration is explicit, user-initiated, and
  keeps the original bytes in a backup.
- No change to 0.1 interpretation, 0.1 fingerprints, schema 0.1 publication, or the
  0.2 comparison semantics accepted in 3b+3c.
- No SQLite activation, no directory scanner, no adapter changes, no network, no
  publication/release/package bump, no Git operation.
- No claim that `validate`, `init`, migration or any authoring command proves
  comparability readiness, reproducibility or scientific validity.
- No ID-listing or value-listing discovery output under the 3d gate.
- No preservation of lexical JSON spelling, formatting, artifact bytes or external
  metadata; the promise is the loader's decoded manifest values (section 3.2).

### 1.3 Trust and authorization

Authoring is authorized only as a **trusted-local, deliberate write**:

- The caller owns or is authorized to modify the chosen manifest and its folder.
- The local process, interpreter, installed package and filesystem are trusted. Shared
  hosts need account/file/process isolation; Unix permissions alone are not
  organizational consent.
- Running `migrate` (without `--dry-run`) is sufficient opt-in. A separate
  `--allow-write` or `--yes` flag is unnecessary and would not establish ownership.
- The command is not a service, not remotely callable, and not safe for
  attacker-controlled adaptive invocation.
- Hostile Python objects, monkeypatched modules, `model_construct` substitutes,
  concurrent mutation and manually forged snapshots remain out of contract, exactly as
  in Stage 2.

### 1.4 Local-only and deterministic

Every 3d authoring operation:

- reads only the manifest, the manifest folder's backup namespace, and (for a future
  conditions command) an explicitly supplied local patch file/stdin;
- does not scan artifact directories, read artifact bytes or locators, inspect repository
  state, resolve URLs, call adapters or use the network;
- does not run checks, hashes or subprocesses;
- does not discover application configuration from the environment;
- produces deterministic output for identical inputs, except for paths and filesystem
  errors;
- never uses random IDs, timestamps or ordering that depends on hash randomization.

Normal argparse/locale/terminal reads (`LANGUAGE`, `COLUMNS`, `LINES`, startup imports)
are not "environment discovery" and are outside the application-data restriction. Tests
must distinguish the two (section 7.9).

### 1.5 Failure and atomicity model

All writes reuse the reviewed `manifest.py` discipline:

- stage a complete new file beside the target, `flush` + `fsync`, then `os.replace`;
- preserve the target file **mode bits** on POSIX;
- verify the source bytes are still the loaded snapshot before replacing;
- remove a staged file on a handled pre-commit failure; after a committed replace,
  cleanup is best-effort and a cleanup `OSError` is suppressed (a temp file may remain);
- make the backup before replacing the manifest;
- state honestly that the byte check is **not** an atomic compare-and-swap and that no
  lock is taken. A concurrent writer between the check and `os.replace` can be lost.
  This is the same limitation as `rehash` and `add`, not a new guarantee.

Replacement creates a new inode. POSIX permission bits are preserved; **owner, group,
ACLs, security descriptors and extended attributes are not preserved** by `save_raw` or
by the backup helper. A backup is a new file owned by the invoking process with POSIX
permission bits no more permissive than the source (platform ACLs are a residual risk).
This limitation is explicit and is not silently claimed otherwise.

Refusals before the backup stage create no backup, no temp file and no manifest change.
After a successful backup, a later **non-committed** failure or late stale check leaves
the manifest unreplaced but may leave the completed backup. A committed replacement whose
only later error is post-replace cleanup is success, not a failure; a temp file may
remain. A crash may leave a temp file or an incomplete backup; parent-directory `fsync` is
absent, so backup durability is not guaranteed. Effects by stage are in section 3.7.
Secure erasure is not claimed.

### 1.6 Preservation contract

Migration preserves the loader's **decoded Python values, types, presence and decoded
object-key order**, not lexical JSON spelling. The accepted loader already normalizes
some numbers and spellings; migration adds no further change and promises no mathematical
precision beyond the loader. The strict comparison rules, independent decodes and
negative controls are in section 3.2 and section 7.3.

## 2. Decision: `init` default

### 2.1 Current behavior

- `SCHEMA_VERSION = "0.1"`; `Manifest.schema_version` defaults to `"0.1"`.
- Every bundled adapter returns a `Manifest` without an explicit version, so it is 0.1.
- `dump_manifest` writes an explicit `"schema_version": "0.1"` for that default and
  strips every 0.2-only key.
- `init --force` regenerates from disk and preserves the old bytes only in a `.bak`
  generation.

### 2.2 Options considered

| Option | Result | Verdict |
|---|---|---|
| A. Keep 0.1 default; no flag; `migrate` opts in | Smallest surface, one explicit step, no adapter/init change | **Chosen** |
| B. Keep 0.1 default; add `init --schema-version 0.2` | One-step 0.2 creation, but new flag and pin churn for a relabel with no fields | Rejected for 3d |
| C. Make 0.2 the `init` default | Implies readiness; changes every new project; worsens `--force` loss | Rejected |
| D. Infer 0.2 when the folder "looks comparable" | Forbidden by the RFC non-goal | Rejected |

### 2.3 Decision

**`init` remains 0.1 by default in 3d. No `--schema-version` flag is added.**

Rationale:

1. `init` invents no first-class fields. A 0.2 default would only relabel an empty
   manifest and would not make it more comparable.
2. The explicit `migrate --to 0.2` command already produces an empty 0.2 manifest one
   deterministic step after `init`; two auditable steps are preferable to one implicit
   version change.
3. `init --force` cannot inventory `conditions`, `dataset_id` or `metric_definition`.
   Making 0.2 the default would preserve the version while silently dropping the very
   content that makes 0.2 meaningful, amplifying a known loss.
4. It avoids an adapter-contract change and a second authorization surface in the same
   gate.

### 2.4 What a generated empty 0.2 manifest means

If a user runs `init` then `migrate --to 0.2`, the result is a valid 0.2 manifest with:

- `schema_version: "0.2"`;
- no `run.conditions`, no `artifact.dataset_id`, no `result.metric_definition`;
- every comparison dimension falling back independently to legacy metadata exactly as
  under 0.1.

It is **not** a comparability claim. It is value-equivalent to the 0.1 manifest except
for the declared version, with one important consequence: 0.1-only readers reject it.
Migration is therefore a deliberate compatibility decision, not a free upgrade.

### 2.5 Adapter behavior

- Adapters keep their current signature and continue to produce 0.1 manifests.
- `init` never post-labels adapter output as 0.2.
- If a future decision adds a version option, it must set the version **after** adapter
  extraction and validation, must not let an adapter emit 0.2-only keys under 0.1, and
  must revalidate the complete manifest. That is not authorized here.

### 2.6 `init --force` and the inventory limitation

`init --force` remains a destructive regeneration protected by a backup generation. It
does not preserve or inventory first-class 0.2 fields. In 3d-2, bundle one fixed,
value-free warning. It is emitted **only after the backup succeeds and only after the old
manifest has been successfully loaded and identified as 0.2**:

```text
WARNING: the existing manifest declares schema 0.2. Regeneration writes schema 0.1
and does not carry over first-class conditions, dataset_id or metric_definition.
The backup keeps the previous manifest.
```

Rules:

- the new warning contains no run/result/artifact IDs, values, paths, hashes or metadata;
- if the backup fails, or the old manifest cannot be loaded/identified, the warning is
  not emitted;
- this value-free guarantee applies to the **new** warning only. Existing regeneration
  diagnostics may still print renamed artifact IDs or other best-effort analysis, and
  that existing behavior is not broadened;
- no inventory, preservation or copying of first-class fields is added in 3d.

### 2.7 Why not a new flag

A flag would add parser surface, help text, a pin refresh and a misleading "0.2-ready"
signal for zero authoring capability. It would also need a defined `--force` interaction.
The explicit migration command is smaller, easier to audit, and already required.

### 2.8 Authoring workflow and validation

The documented order is mandatory:

1. `runevidence init PATH` writes schema 0.1.
2. `runevidence migrate PATH --to 0.2` declares 0.2 and keeps a `.bak` generation.
3. Hand-add first-class fields (`run.conditions`, `artifact.dataset_id`,
   `result.metric_definition`).
4. Run `runevidence validate PATH` and `runevidence inspect PATH`.
5. Use `runevidence comparability ...` to check recorded-condition readiness.

Adding any 0.2-only key while `schema_version` is still 0.1 makes the manifest invalid,
including a key whose value is `null`. `migrate` refuses such a manifest; it does not
repair it. The safe order is: migrate first, then add the fields. If they are already
present under 0.1, remove them, migrate, then add them back.

When hand-authoring:

- first-class and matching legacy values must be canonically equal; unequal or malformed
  duplicates produce `INPUT_SHAPE_UNSUPPORTED`, not a first-class win;
- a first-class selector requires an artifact ID of the right kind and an independent
  `input_to` edge; selectors never synthesize edges;
- an invalid explicit selector fails with no fallback to legacy metadata;
- `validate`/`inspect` report sanitized selector findings; `comparability` is the
  readiness check, not `validate`.

## 3. Decision: explicit migration

### 3.1 Command surface

```text
runevidence migrate PATH --to 0.2 [--dry-run] [--json]
```

- `PATH` is an experiment folder containing `runevidence.json` or a manifest file.
- `--to` is required. `0.2` is the only supported value.
- `--to 0.1` is a recognized **downgrade refusal**, not a silent success.
- Any other `--to` value is an unsupported-target refusal.
- No `--force`, no `--no-backup`, no `--yes`, no selector, no run/result/artifact
  operand.
- `--json` is detected from raw argv so a grammar failure still produces one JSON
  document. `--help` and `--version` keep normal behavior.

The command is registered directly after `init` so removing `migrate` recovers the exact
current 24-route sequence.

Check order is fixed:

1. argument grammar;
2. target version (`--to`);
3. path resolution/stat and the reserved-backup-name check;
4. manifest load and schema validation;
5. already-0.2 no-op;
6. candidate construction and decoded-value preservation proof;
7. serialization eligibility (`dump_raw` + UTF-8 encode + decode);
8. pre-backup stale check;
9. backup creation/reuse;
10. `save_raw` commit invariant and, only for a non-committed raise, late-stale
    classification.

### 3.2 Semantics: exactly one semantic change

Migration may change only the top-level `schema_version`:

| Input | Output |
|---|---|
| key omitted | `"schema_version": "0.2"` inserted as the first key |
| `"schema_version": "0.1"` | `"schema_version": "0.2"` |
| `"schema_version": "0.2"` | no write; idempotent success |
| `"schema_version": null` / `""` / `"0.3"` | input already fails `load_manifest`; refused |

It may not:

- add `conditions`, `dataset_id`, `metric_definition` or any other 0.2 key;
- remove, add, normalize, reorder, re-type or rewrite any other decoded value;
- trim strings, sort arrays, drop nulls, collapse empty objects, convert numbers or
  reinterpret legacy metadata;
- infer a split, protocol, dataset, checkpoint, revision, environment or metric;
- change `project`, runs, artifacts, results, evidence or relationships;
- run checks, read artifact bytes, scan directories, call adapters or touch the network.

#### 3.2.1 Decoded-value promise

The promise is to preserve the **loader's decoded Python values, types, presence and
decoded object-key order**, not lexical JSON spelling, byte layout or mathematical
precision beyond what the loader retained. The accepted loader retains decoded `dict`
insertion order, and migration preserves it; the strict walker checks it. The accepted
loader already performs these normalizations, and migration adds no further change:

- large float literals round to the nearest binary64 value;
- literals that underflow become `0.0` (a float `-0.0` remains a signed zero);
- a JSON integer literal `-0` decodes to `0`; a float `-0.0` remains a signed zero;
- integer/float spelling, exponent form and `1` versus `1.0` spelling may change when
  re-serialized, but the decoded type/value is preserved; an int `1` never becomes a
  float `1.0` (and the strict walker rejects that substitution);
- the three legacy non-finite tokens (`NaN`, `Infinity`, `-Infinity`) are accepted only
  at `results[*].value` and decode to the corresponding float values.

Migration does not promise original bytes, original indentation, CRLF preservation or
mathematical exactness beyond the loader. The backup preserves the original bytes
exactly.

#### 3.2.2 Independent decodes and strict walker

The candidate must be built from a **second, independent decode** of the loaded source
bytes; never share nested objects between compared structures.

Implementation shape:

1. `load_manifest(PATH)` gives the validated model and `source_bytes`.
2. `source_a = decode_snapshot(loaded.source_bytes)`.
3. `source_b = decode_snapshot(loaded.source_bytes)` independently.
4. Build the candidate from `source_b`: copy the top-level object, remove any existing
   `schema_version`, insert `"schema_version": "0.2"` first. All other values come from
   `source_b`; `source_a` is never mutated.
5. Prove `strict_equal(without_schema_version(source_a), without_schema_version(candidate))`.
   Separately assert that the only top-level difference is `schema_version`: the key set
   is otherwise identical, the remaining keys have the same order, and the version is
   0.1/omitted -> 0.2.
6. Validate the candidate as a `Manifest`; confirm `schema_version == "0.2"`.
7. Serialize, encode, decode and prove
   `strict_equal(without_schema_version(source_a), without_schema_version(decoded_emitted))`;
   confirm the emitted decode still declares `"0.2"`.
8. If writing, perform the stale check, backup and `save_raw`.

`decode_snapshot` uses the same JSON hooks and legacy-token resolution as
`load_manifest` (`object_pairs_hook` duplicate-key rejection, `parse_constant`,
`parse_float` finiteness, and nonfinite tokens only at `results[*].value`). It returns a
fresh object graph for every call. `load_raw` alone is not sufficient because it shares
one decode and uses plain `json.loads`.

`strict_equal(a, b)` is a custom walker, not Python `==`:

- `dict`: both are exactly `dict`; **key lists are equal in order**; then compare values
  in that order;
- `list`: both are exactly `list`; same length; elementwise;
- `bool`: exact `bool` type and equal value; `True` never equals `1`;
- `int`: exact `int` type (booleans excluded by type) and equal value; `1` never equals
  `1.0`;
- `float`: exact `float` type; both NaN is equal; exactly one NaN is not; infinities
  compare by sign; zeros compare with `math.copysign` so `-0.0` and `0.0` differ;
  otherwise exact value equality;
- `str`: exact string;
- `None`: both `None`;
- any other type: not equal.

`without_schema_version(obj)` returns a new top-level dict with only the
`schema_version` member removed, preserving the order of every remaining key. It is
applied to both sides of every preservation comparison because inserting the version key
may legitimately change the top-level key order; nested key order and array order remain
strictly compared. The version change itself is checked separately, so this exception
cannot hide any other difference.

This makes object-key order and array order part of the preservation proof, and it keeps
the legacy non-finite values distinguishable. NaN/Infinity acceptance is a manifest-value
rule only; `allow_nan=False` is never used for manifest serialization (section 6.4).

#### 3.2.3 Serialization eligibility

A schema-valid source can still be unwritable or unserializable:

- a lone surrogate can be loaded from a JSON escape and pass validation, but UTF-8
  encoding of the emitted text raises `UnicodeEncodeError`;
- deeply nested metadata can make `dump_raw` or the post-serialization decode raise
  `RecursionError`.

Before any backup, and during `--dry-run`, the command must:

1. `text = dump_raw(candidate)`;
2. `data = text.encode("utf-8")`;
3. `emitted = decode_snapshot(data)`;
4. `strict_equal(without_schema_version(source_a), without_schema_version(emitted))`.

`UnicodeEncodeError` and `RecursionError` at any of these steps produce
`SERIALIZATION_FAILED` (exit 2) with no exception text, character, key or path echoed.
A strict-equality mismatch after serialization is an internal defect and produces
`INTERNAL_ERROR`; it is not presented as a user input problem. No universal nesting
threshold is promised across Python versions.

### 3.3 Omitted vs null vs empty

- Omitted `schema_version` means 0.1 and becomes explicit `"0.2"`.
- Explicit `"0.1"` becomes `"0.2"`.
- Explicit `null` or `""` is already invalid under both versions; load refuses.
- Every other omitted member stays omitted; every explicit `null` stays null; every
  empty object/array/string, `false` and `0` stays exactly as it was.
- Migration adds no first-class field, so the 0.2 presence matrix after migration is:
  `conditions` absent, `dataset_id` absent, `metric_definition` absent.

### 3.4 Refusal matrix and reserved target paths

All refusals exit 2 and do not replace the manifest. Refusals before the backup stage
create nothing; a late stale or write failure may leave a completed backup (section 3.7).
Grammar and target-version failures do not load the manifest.

| Case | Result |
|---|---|
| `--to 0.1` (downgrade) | `UNSUPPORTED_TARGET`; no load, no write |
| `--to` other than `0.2` | `UNSUPPORTED_TARGET`; no load, no write |
| missing `--to`, unknown option, extra operand, missing PATH | `INVALID_ARGUMENTS`; no load |
| resolved target basename is a backup generation | `RESERVED_PATH`; no load, no write |
| missing manifest, directory without manifest, unreadable file, symlink loop | `MANIFEST_LOAD_FAILED` |
| bad UTF-8, bad JSON, duplicate keys, nonfinite token outside a result value, excessive nesting | `MANIFEST_LOAD_FAILED` |
| schema-invalid 0.1, including any 0.2-only key under 0.1 even when null | `MANIFEST_LOAD_FAILED` |
| already 0.2 | **not a refusal**: exit 0, `changed: false`, no write, no backup |
| UTF-8 encoding failure or dump/decode recursion | `SERIALIZATION_FAILED`; no backup, no write |
| pre-backup source re-read fails | `READ_FAILED`; no backup, no write |
| late diagnostic re-read observes different bytes | `STALE_MANIFEST`; this is an observation of current bytes, not reliable identification of the original failure; nothing replaced; a completed backup may remain |
| backup or manifest write/fsync/replace/readback/mode failure before commit | `WRITE_FAILED`; original intact; a completed backup may remain |
| unexpected internal error, including a post-serialization preservation mismatch | `INTERNAL_ERROR`; no partial document |

A committed replacement whose only later error is post-replace cleanup (`staged.unlink`)
is **not a refusal**: it is a success (exit 0), even though a temp file may remain. It is
never reported as `STALE_MANIFEST` or `WRITE_FAILED`. The `STALE_MANIFEST` and
`WRITE_FAILED` rows above apply only to a path that did not commit.

#### 3.4.1 Reserved backup namespace

The migration target must not itself be a backup generation. After resolving exactly as
`load_manifest` does (including folder-to-`runevidence.json` resolution), inspect the
resolved basename:

```python
resolved = resolve_manifest_path(Path(PATH)).resolve()
if backup_generation(resolved.name) is not None:
    return RESERVED_PATH
```

This check runs before `load_manifest`, so it performs no manifest read and no write. It
covers:

- a regular valid manifest named `runevidence.json.bak`, `.bak.2`, and so on — it must
  not migrate using itself as its own backup;
- a symlink operand that resolves into the backup namespace, for example
  `runevidence.json -> runevidence.json.bak`;
- any resolved basename whose `backup_generation(name)` is not `None`.

A symlink named `runevidence.json.bak` that resolves to `runevidence.json` resolves to a
non-reserved basename. It is allowed to address the resolved manifest, but the symlink
itself is never eligible for backup reuse (section 3.6); a new generation is created.
The same migration output rules apply. `RESERVED_PATH` is not folded into
`MANIFEST_LOAD_FAILED`: it is detected without a load, has a different remedy (choose the
manifest, not its backup alias), and must not imply the file is invalid.

No `0.1 -> 0.2` migration is attempted on an invalid manifest. No partial repair is
offered: fix the manifest with the normal tools first.

### 3.5 Dry run

`--dry-run` performs the same load, reserved-path check, candidate/preservation proof,
serialization eligibility and pre-backup stale read, then prints the would-be outcome and
writes nothing: no manifest change, no backup, no temp file.

- 0.1: exit 0, `changed: true`, `dry_run: true`.
- 0.2: exit 0, `changed: false`, `dry_run: true`.
- invalid, reserved, unserializable or stale: the same refusal and exit 2 as a real run.

### 3.6 Backup namespace, eligibility and permissions

- Backups use the existing `runevidence.json.bak`, `.bak.2`, `.bak.3`, ... generation
  namespace. This is mandatory: `backup_generation()`/`is_own_file()` already exclude
  those names from artifact tracking. A new suffix such as `.pre-0.2.bak` would be
  scanned as ordinary content by adapters and `inspect`, so it is rejected.
- **Do not reuse `_backup_existing` wholesale.** It reloads the manifest, prints the
  manifest/backup paths, and can print artifact IDs during regeneration analysis, which
  violates the migration output and no-extra-read contract. Reuse only its generation
  naming/eligibility convention through a new migration-specific helper,
  `_backup_for_migration(target, source_bytes, source_mode)`, with the migration output
  contract.
- The helper enumerates the target's folder once for backup generations. That directory
  enumeration is the only directory access allowed (section 3.9).
- **Reuse eligibility.** The newest generation may be reused only when all of these hold
  (`source_mode` is the resolved target's POSIX mode,
  `stat.S_IMODE(os.stat(target).st_mode)`):
  1. it is a regular file (`lstat` says regular, not a directory or other type);
  2. it is not a symlink;
  3. it is not the migration target or a hardlink/same-file alias of it: compare with
     `os.path.samefile` where available, otherwise `stat` `st_dev`/`st_ino`; if neither
     identity check is available, treat the candidate as ineligible and create the next
     generation;
  4. its bytes equal `loaded.source_bytes`;
  5. stat the candidate and require its POSIX permission bits to be a subset of
     `source_mode`: `(stat.S_IMODE(candidate_stat.st_mode) & ~source_mode) == 0`.
  Any failed check falls through to creating the next generation. A symlink or alias is
  never used as a backup, even when its bytes match. A byte-identical candidate that is
  more permissive than the source is left untouched and is **not** reused; the next
  generation is created with the restricted creation policy. Reuse never repairs or
  chmods an existing generation.
- Otherwise create `runevidence.json.bak` (generation 1) or the next `.N` generation with
  `O_CREAT | O_EXCL | O_RDWR` (an equivalent exclusive create that allows readback).
  Never overwrite or delete an existing completed generation, including one created
  concurrently. A name collision is `WRITE_FAILED`, not a retry that deletes the other
  writer's file.
- **Permissions.** Create the backup with `os.open(..., mode=source_mode)` so the process
  umask can only reduce permissions. Never `chmod` the backup upward. Verify after
  creation that no permission bit outside `source_mode` is set; a violation is
  `WRITE_FAILED` and the just-created incomplete file is removed. Read back the bytes
  through the creation descriptor (or a permitted reopen) and compare with
  `source_bytes`; a mismatch or partial write is `WRITE_FAILED`, and only the file
  created by this attempt is removed. Prior generations are untouched. The "not more
  permissive" guarantee is scoped to POSIX permission bits under the process umask;
  platform ACLs/security descriptors are outside it and are listed as a residual risk.
- **Ownership.** A backup is a new file owned by the invoking process. Owner, group,
  ACLs and extended attributes are not copied from the source. The manifest replacement
  likewise creates a new inode and preserves mode bits only.
- Only one backup file is created per successful migration. A reused generation counts
  as the backup and is reported as `reused`.

### 3.7 Atomicity, stale checkpoints and failure side effects

There are two stale checkpoints:

1. **Before backup.** Immediately before creating/reusing a backup, re-read the target
   and compare it with `loaded.source_bytes`. A difference is `STALE_MANIFEST` with no
   backup and no write. A failed re-read is `READ_FAILED` with no backup and no write.
2. **During `save_raw`.** `save_raw` performs its own final source-byte check immediately
   before `os.replace`. Under the commit invariant below, a cleanup-only error after a
   committed replace is suppressed and never reaches this classifier. If `save_raw`
   raises, no commit was recorded; classify without matching exception text: wrap the
   `OSError`, re-read the target, and compare with `loaded.source_bytes`. Call this a
   **diagnostic observation of the bytes present at that re-read**, not reliable
   identification of the original failure:
   - bytes differ -> `STALE_MANIFEST`;
   - re-read fails -> `WRITE_FAILED`;
   - bytes are unchanged -> `WRITE_FAILED`.

   The observation can be wrong in both directions. If a stale rejection occurred but
   the original bytes are restored before the diagnostic re-read, the observed bytes are
   unchanged and the command reports `WRITE_FAILED`. If an unrelated replacement failure
   occurs and a concurrent writer changes the file before the diagnostic re-read, the
   observed bytes differ and the command reports `STALE_MANIFEST`. In every raised,
   non-committed case the write-safety conclusion is that this command did not replace
   the manifest. A committed replacement is never reported as `STALE_MANIFEST` or
   `WRITE_FAILED`; see the invariant below.

#### 3.7.1 `save_raw` commit invariant (Option A)

`src/runevidence/manifest.py::save_raw` currently calls `staged.unlink(missing_ok=True)`
in a `finally` block after a successful `os.replace`. `missing_ok=True` suppresses only
`FileNotFoundError`; an `EIO` or other `OSError` from cleanup propagates **after** the
manifest has already been replaced. A migration wrapper would then diagnostic-read the
migrated bytes, report `STALE_MANIFEST`, and falsely say nothing was replaced.

The chosen invariant is the smallest correct one:

- `save_raw` tracks `replaced = False` and sets `replaced = True` only after
  `os.replace(staged, path)` returns.
- In the `finally` cleanup, attempt `staged.unlink(missing_ok=True)`.
- If cleanup raises `OSError`:
  - `replaced is True`: **suppress the cleanup error and return success.** A temporary
    file may remain; the committed replacement wins. Do not convert this to
    `WRITE_FAILED`, `STALE_MANIFEST` or any warning that says nothing was replaced.
  - `replaced is False`: re-raise as today. The pre-replace stale check, staging, mode
    preservation, final source-byte check and replace failures keep their existing
    behavior.
- `os.replace` returning is the only proof of commit. An `OSError` raised by
  `os.replace` itself is not treated as committed.

Option B (a migration-specific commit helper duplicating staging/stale/replace) is
rejected: it would create a second write path whose stale, mode and cleanup behavior must
be proven not to diverge from `save_raw`, for no benefit over a small invariant fix in the
shared writer. Option A also fixes the existing `rehash`/`add` callers, which can hit the
same post-replace cleanup error.

Scope note: `save_manifest` has a similar staged-cleanup block and is used by `init`, but
migration does not call it. Changing it is outside this blocker; if the implementation
chooses to apply the same guard there, it is a separate consistency change with its own
`init` regression coverage.

Consequences:

- A committed replacement whose only later error is cleanup is a **success**: exit 0,
  `changed: true`, `backup: "created"` or `"reused"`, normal success text. A temp file may
  remain.
- A raised `save_raw` path that did not commit keeps the v0.3 classification (stale
  observation vs write failure).
- `save_raw`'s signature and successful return value are unchanged; only the
  post-replace cleanup exception behavior changes.
- `manifest.py`'s hash changes in 3d-2. Existing `rehash` and `add` tests must stay green,
  with new regression coverage for the cleanup-after-commit case.

Effects by stage:

| Stage | Manifest | Backup | Temp |
|---|---|---|---|
| grammar, target, reserved, load, candidate, preservation, Manifest validation | unchanged | none | none |
| serialization eligibility (`dump`/encode/decode) | unchanged | none | none |
| pre-backup stale or `READ_FAILED` | unchanged | none | none |
| backup create/verify/fsync failure | unchanged | only prior generations; incomplete new file removed | none |
| backup success, then `save_raw` stage/fsync/final-check/replace failure (no commit) | unchanged | completed backup may remain | temp removed when cleanup succeeds; may remain if cleanup fails or crashes |
| replace committed, then post-replace cleanup `unlink` raises `OSError` | **new manifest** | completed backup remains | temp may remain; **SUCCESS, exit 0** |
| late stale at `save_raw` final check (no commit) | unchanged | completed backup may remain | temp removed when cleanup succeeds; may remain if cleanup fails |
| crash during backup | unchanged | incomplete backup may remain | none |
| crash during staged write | unchanged | completed backup may remain | temp may remain |
| crash after `os.replace` | new manifest | completed backup exists | none (renamed away) |

Refusals before the backup stage create nothing. A later **non-committed** failure does
not replace the manifest but may leave the backup; this is intentional recoverable state,
not a rollback that deletes evidence. A committed replacement whose only later error is
post-replace cleanup is success, not a refusal or write failure. Parent-directory `fsync`
is absent, so directory-entry and backup durability are not guaranteed across a crash. No
lock is taken; concurrent writers must be avoided.

### 3.8 Output, exit codes and path disclosure

Exit codes:

- `0` — migrated, already 0.2, dry run completed, or a committed replacement whose only
  later error was post-replace cleanup (a temp file may remain);
- `2` — invalid arguments, unsupported target, reserved path, load failure,
  serialization failure, pre-backup read failure, stale observation, non-committed write
  failure or internal error.

Exit `1` is not used: migration has no "evidence problems found" verdict.

**Path disclosure decision:** text output may print the **resolved** manifest path and
the **resolved** backup path, because they identify the only file written and the only
recoverable copy. If the operand was a symlink, the resolved target is printed, not the
supplied alias. JSON output never contains a path. No ID, value, artifact path, locator,
hash or metadata key appears in either form.

Success JSON (closed key set):

```json
{
  "format": "runevidence-migrate-result-v1",
  "ok": true,
  "from": "0.1",
  "to": "0.2",
  "changed": true,
  "dry_run": false,
  "backup": "created"
}
```

`from` is `"0.1"` for an omitted version. `backup` is `"created"`, `"reused"` or
`"none"`. The document contains no manifest path, ID, name, value, hash or metadata.

Success-envelope matrix (the same fields for `--json`; text carries no JSON fields but
agrees on the outcome):

| Input | Mode | `from` | `to` | `changed` | `backup` | Writes |
|---|---|---|---|---|---|---|
| valid 0.1 | real | `"0.1"` | `"0.2"` | `true` | `"created"` or `"reused"` | manifest replaced; backup created/reused |
| valid 0.1 | real, post-replace cleanup `OSError` | `"0.1"` | `"0.2"` | `true` | `"created"` or `"reused"` | manifest replaced; temp may remain; exit 0 |
| valid 0.1 | `--dry-run` | `"0.1"` | `"0.2"` | `true` | `"none"` | nothing |
| already 0.2 | real | `"0.2"` | `"0.2"` | `false` | `"none"` | nothing |
| already 0.2 | `--dry-run` | `"0.2"` | `"0.2"` | `false` | `"none"` | nothing |

The text examples below are the same outcome families (`Wrote ...` for a real 0.1
migration, including the cleanup-only success, `already declares` for either 0.2 mode,
`Would declare ...` for a 0.1 dry run). A cleanup-only error after commit must print the
normal `Wrote ...` success text and must never say nothing was replaced. Error JSON always
has `ok: false`, `exit_code: 2`, and a closed code; it never carries `from`, `to`,
`changed` or `backup`.

Error JSON:

```json
{
  "format": "runevidence-migrate-error-v1",
  "ok": false,
  "exit_code": 2,
  "error": {"code": "MANIFEST_LOAD_FAILED"}
}
```

Closed codes: `INVALID_ARGUMENTS`, `UNSUPPORTED_TARGET`, `RESERVED_PATH`,
`MANIFEST_LOAD_FAILED`, `SERIALIZATION_FAILED`, `READ_FAILED`, `STALE_MANIFEST`,
`WRITE_FAILED`, `INTERNAL_ERROR`. No message, hint, location, path, selector, ID or
value is added.

Text output uses only fixed vocabulary plus the resolved manifest/backup paths and the
two schema-version tokens:

```text
Wrote /resolved/runevidence.json (schema 0.1 -> 0.2). Backup: /resolved/runevidence.json.bak
/resolved/runevidence.json already declares schema 0.2. Nothing was written.
Would declare schema 0.2 for /resolved/runevidence.json (0.1 -> 0.2). Dry run: nothing was written.
ERROR (RESERVED_PATH): the target resolves to a backup name; choose the manifest, not a backup. Nothing was written.
ERROR (SERIALIZATION_FAILED): the migrated manifest could not be serialized safely. Nothing was written.
ERROR (READ_FAILED): the manifest could not be re-read before backup. Nothing was written.
ERROR (STALE_MANIFEST): the bytes observed at the stale check differ from the loaded snapshot; nothing was replaced.
ERROR (UNSUPPORTED_TARGET): only --to 0.2 is supported; downgrade and other versions are not available. Nothing was written.
```

Error text prints no path. Success/no-op/dry-run text prints resolved paths only; the
backup component is the actual reused or newly created generation (for example `.bak` or
`.bak.2`).

### 3.9 Filesystem-access inventory

This section replaces any earlier "one read, no scan" shorthand with this narrow
allowlist. A migration invocation may perform only:

- path resolution and `stat`/`lstat` for the target, its resolved path and backup
  candidates;
- one validated manifest load read (`load_manifest`, including its own resolve/stat/read);
- one pre-backup source-byte read for the first stale check;
- one directory enumeration of the target's own folder for backup generations;
- backup read/verification/reuse checks (including `lstat`, resolve and byte compare);
- exclusive backup create, write, flush, `fsync`, mode preservation and readback;
- `save_raw`'s stage write, flush, `fsync`, mode preservation, final source-byte read,
  `os.replace`, and post-replace temp cleanup (best-effort; a cleanup-only `OSError` is
  suppressed and does not trigger the diagnostic read);
- the conditional diagnostic target read after a non-committed `save_raw` raises, whether
  that read succeeds or fails, solely to classify the outcome (section 3.7);
- the post-serialization decode of the emitted text in memory (no filesystem access).

It must **not** perform: artifact reads, stats or locators; artifact-directory scans;
adapter discovery; repository inspection; URL/DNS/network access; subprocesses; checks or
hashes; application configuration/environment discovery. Normal argparse/locale/terminal
reads and Python startup are not part of this restriction; I/O tests trap after CLI
startup/imports or count the named operations rather than forbidding parser startup.
Python bytecode/atime effects remain outside the application write claim.

## 4. Decision: optional conditions authoring command

### 4.1 Decision

**Defer the command. Do not implement it in 3d.**

3d still supports hand-authoring first-class fields in JSON, with the migration command
making the version declaration explicit. `validate`/`inspect` remain the review path.

### 4.2 Why defer

1. **Value privacy.** Splits, source revisions, protocol IDs and especially environment
   objects are sensitive. Putting them on argv exposes shell history, process lists, CI
   echoes and terminal capture; the no-raw-value output contract then conflicts with
   normal CLI usability.
2. **Presence complexity.** Six dimensions each have omitted/null/empty meanings, and
   selectors forbid null. A small flag surface easily creates ambiguous
   "set to null" versus "clear" operations.
3. **Reference validation.** Selectors must reuse the M15 syntax/existence/kind/
   attachment checks with no fallback, and the resulting manifest must remain valid 0.2.
4. **Atomicity surface.** It is a multi-field writer with backups, stale checks and
   partial-failure rules; that deserves its own reviewed gate rather than riding on
   migration.
5. **No proven need yet.** Migration unblocks the 0.2 path; hand-editing the six fields
   is already possible and documented. A command should follow demonstrated demand.

### 4.3 Candidate surface if a later gate approves it (non-normative)

> **Status (2026-11-11):** the `conditions` command is implemented under the
> separate [Stage 3d-3 conditions authoring RFC
> v0.3](https://github.com/shihaab453/rungraph/blob/main/docs/comparability-stage3d3-conditions-rfc.md).
> This sketch remains the historical 3d design and is not normative.

```text
runevidence conditions PATH --run RUN_ID --patch FILE|- \
  [--clear FIELD]... [--dry-run] [--json]
```

- `--run` is an exact run ID; no name, prefix or fuzzy selection.
- The patch is one strict JSON object read from a local file or stdin. Values never
  appear on argv. Allowed keys are exactly:
  `evaluation_split`, `evaluation_protocol`, `evaluation_dataset_artifact_id`,
  `model_checkpoint_artifact_id`, `source_revision`, `environment`.
- Key absent: no change. Key present with a value: set it. Key present with `null`:
  - `evaluation_split`, `evaluation_protocol`, `source_revision`, `environment`:
    **whole-value explicit UNKNOWN**, valid, and no legacy fallback;
  - `evaluation_dataset_artifact_id`, `model_checkpoint_artifact_id`: **invalid**;
  - nested `evaluation_protocol.version: null`: **invalid**;
  - `evaluation_protocol: {}`: **invalid** (missing `protocol_id`);
  - `evaluation_protocol: {"version": null}`: **invalid** (nested null);
  - `environment: {}`: schema-valid but comparison-time
    `INPUT_SHAPE_UNSUPPORTED`; authoring must refuse it.
- Clearing a field means **removing the key** (restoring fallback), not writing null.
  `--clear FIELD` is repeatable and mutually exclusive with the same patch key. If all
  six children are absent after the operation, remove the `conditions` key entirely;
  never write `conditions: null`.
- **Schema validation versus comparison-time bounds.** Schema validation covers types,
  nonblank strings, selector pattern and nested non-null-when-present rules. Selector
  existence/kind/`input_to` attachment is manifest-graph validation. Environment depth
  8, 16 KiB serialized size, keys/strings <= 1024 and finite numbers are comparison-time
  bounds, not schema rules; the authoring command must apply the same bounded-JSON check
  before writing so it cannot create a schema-valid value that comparison rejects.
- The target must already be 0.2; 0.1 is refused with "migrate first". The command does
  not auto-migrate.
- Selectors are validated against the manifest graph: ID syntax, existence, required
  kind, and an `input_to` attachment; no fallback and no edge synthesis. No artifact
  bytes are read.
- The complete result is revalidated as `Manifest` before writing. One invalid field
  refuses the whole patch.
- Writes use `save_raw` with expected bytes, a `.bak` generation under the same reserved
  path, permission and stale-check rules as migration, mode preservation and the same
  ownership limitations.
- Output may name the user-supplied run ID, field names, set/cleared booleans and counts.
  It never echoes values, nested object content, selector IDs, manifest paths, hashes or
  metadata.
- Exit 0 on success/no-op/dry run; 2 on every refusal. JSON uses its own closed envelope.
- It does not run `validate`/`inspect`; the user composes those afterward.

This sketch is evidence that a safe design is possible, not authorization to build it.

## 5. Decision: preflight/discovery helper

### 5.1 Decision

**Defer both the ID-listing discovery helper and any preflight route. 3d adds no read
surface.**

The Stage 3 RFC is explicit: ID-listing conflicts with the no-ID output contract and
needs its own privacy decision. 3d does not make that decision.

### 5.2 Why no helper is needed for 3d

- `migrate --dry-run` already answers the only 3d question: is this manifest valid and
  eligible to become 0.2?
- `validate`/`inspect` already report sanitized selector-reference findings for 0.2.
- `comparability` already reports presence/state outcomes without IDs.
- A preflight would add a route, a privacy surface and a pin refresh for little gain.

### 5.3 Safe presence-only shape if a separate gate later approves it

If ever approved, the only acceptable helper reports **presence, not identity**:

```json
{
  "format": "runevidence-preflight-v1",
  "schema_version": "0.1",
  "version_declared": false,
  "migration_eligible": true,
  "readiness": "not-0.2",
  "first_class_present": {
    "conditions": false,
    "dataset_id": false,
    "metric_definition": false
  },
  "selectors_present": {"dataset": false, "checkpoint": false},
  "refusal_code": null
}
```

Rules for that future helper:

- `readiness` is structural only: `"not-0.2"` for schema 0.1 (never
  `"version-only"`), `"version-only"` for 0.2 with no first-class fields, or
  `"first-class-present"` for 0.2 with at least one; it is never a comparability
  verdict;
- no IDs, names, values, paths, hashes, artifact locators, metadata keys or per-run
  listing;
- aggregates only; no per-run presence table that reveals which run was authored;
- fixed refusal codes, no loader text;
- no artifact reads, discovery, network or writes;
- the output is sensitive and **not anonymous**: even presence can reveal experimental
  design, and it is not safe for unrestricted publication;
- it remains separate from 3d implementation and from the comparability no-ID contract.

Exact-selector discovery ("which dataset should I select?") is **rejected**, not
deferred: it requires listing IDs and therefore needs a different authorization and
privacy contract than this project currently has.

## 6. CLI and privacy impact

### 6.1 Routes and pins

Adding `migrate` makes **25 routes**:

```text
validate, validate-batch, inspect, compare, comparability, leaderboard, matrix, run,
claim, lineage, impact, coverage, coverage-gate, fingerprint, fingerprint-compare,
coverage-diff, review-diff, impact-diff, review-gate, init, migrate, rehash, add,
schema, adapters
```

Required pin-refresh package for 3d-2:

- `tests/test_experimental_cli_parser_contract.py`:
  - preserve the current 24-route pair as
    `HISTORICAL_24_ROUTE_CLI_HASH` (the current LF/CRLF
    `a4a4b921...`/`83c2dbfa...` pair) and `HISTORICAL_24_ROUTES` (the current 24-route
    tuple);
  - refresh `CLI_HASH` to exactly two digests (LF and CRLF) computed from the reviewed
    `cli.py`;
  - add `HISTORICAL_24_ROUTE_CLI_HASH` to the historical-disjointness set in `source()`
    and to the constant-test disjointness assertion; the current 24 pair must not be
    offered as a current pin;
  - extend `ROUTES` and the route-count assertion (24 -> 25);
  - **derive** the 23- and 22-route assertions from `HISTORICAL_24_ROUTES`, not from the
    new `ROUTES`:
    `[r for r in HISTORICAL_24_ROUTES if r != "comparability"] == list(HISTORICAL_23_ROUTES)`
    and
    `[r for r in HISTORICAL_24_ROUTES if r not in ("validate-batch", "comparability")] == list(HISTORICAL_22_ROUTES)`;
    assert `[r for r in ROUTES if r != "migrate"] == list(HISTORICAL_24_ROUTES)`;
  - assert `ROUTES[ROUTES.index("init") + 1] == "migrate"`;
  - append the current reader digest `582a27bca1dfe64e1d7aaf2c18376044511431b2c08cc270bbb6581cea604ecc`
    to `HISTORICAL_READER_HASHES` before replacing `READER_HASH`;
  - assert the new `READER_HASH` is not in the historical reader history;
  - add a grammar-only `migrate` parse test that never dispatches, using the
    comparability handler-replacement pattern rather than the historical seven-handler
    `parse_without_dispatch` helper (which does not admit `migrate`).
- `tests/test_experimental_staged_reader.py`: add `migrate` to the explicit
  `current_public` route set, not only the count; update 24 -> 25 and the comment;
  recompute the single LF `READER_HASH`. **Keep the argv tripwire cases unchanged**
  (`validate`, `validate --json`, `validate --strict`, `inspect`, `inspect --json`).
- `tests/test_experimental_versioned_manifest.py`: add `migrate` to the explicit
  `current_public` route set, not only the count; update 24 -> 25.
- Historical hashes, dated notes and earlier private pin records stay untouched.
- No private/runtime route may be added; `PRIVATE_ROUTE_MARKERS` still applies.

`init` gets no parser change. The only `init` code change is the fixed 0.2 `--force`
warning in 3d-2, which rides the same pin refresh.

### 6.2 Parser and READERS

- `migrate` uses `Path` for PATH, a required `--to` string (validated after parse, so
  downgrade gets `UNSUPPORTED_TARGET` rather than a generic argparse choice error),
  `--dry-run` and `--json`.
- The sanitized boundary is a new `_migrate_main(raw)` in `cli.py`, mirroring the
  reviewed `_comparability_main` pattern: raw `--json` detection, captured/discarded
  argparse stderr, fixed `INVALID_ARGUMENTS`, help/version pass-through. Do not
  refactor `_comparability_main`; leaving it byte-identical keeps its pins and tests
  stable.
- `main()` dispatches `raw[0] == "migrate"` to that boundary before the normal parse.
- No `--redact-manifest-path`, `--strict`, `--summary-only` or `--allow-*` surface.
- The reader seam, staged reader, SQLite and adapters are not connected to `migrate`.

### 6.3 Help, CLI description and documentation

- Add `migrate` to the **module docstring** writer list and backup sentence (currently
  only `init`, `rehash` and `add`; add `migrate` and its backup behavior).
- Add `migrate` to the **root parser description** writer list ("init, migrate, rehash
  and add write the manifest") and keep the exit-code epilog accurate.
- `migrate` help: "declare an existing manifest as schema 0.2 without changing any
  recorded value (explicit, no inference)".
- README:
  - add one `migrate` command-table row;
  - add the write-command inventory and backup sentence (`init`, `migrate`, `rehash`,
    `add`; `init --force` and `migrate` keep backups);
  - add `migrate` to the exit-code 2 guidance for refusal/failed write with nothing
    replaced;
  - add `migrate` to the single-writer/race guidance;
  - add a short "Migrating a manifest to 0.2" section covering backup, idempotence, dry
    run, refusal, empty-0.2 meaning and the mandatory authoring order from section 2.8.
- `docs/comparability-schema-0.2.md` authoring-limits section: update "no migration" to
  the explicit command, state that `init` is still 0.1, keep the `--force` warning and
  document the authoring order, legacy conflicts and selector `input_to` requirement.
- Preserve dated/private pin records; do not rewrite historical Stage 2b text.
- This RFC gains a status line only when implemented; the contract text is not silently
  rewritten.

### 6.4 JSON envelope, exit codes and numeric serialization

- One complete document per invocation, no partial output.
- `runevidence-migrate-result-v1` / `runevidence-migrate-error-v1`, closed key sets.
- `ok`, `changed`, `dry_run` are real JSON booleans; `from`/`to` are the strings
  `"0.1"`/`"0.2"`; `exit_code` is the integer 2 on error.
- Exit 0/2 only, as in section 3.8.
- Output envelopes use `json.dumps(..., allow_nan=False)`. **Manifest serialization does
  not**: `dump_raw` must keep the legacy non-finite tokens that the loader accepts at
  `results[*].value`. `allow_nan=False` is scoped to CLI result/error envelopes only.

### 6.5 Authorization and inference

- Trusted-local inference from the act of invoking a write command is the authorization
  model, identical to `init`/`rehash`/`add`.
- Migration is explicit user intent; it never infers from metadata, adapters,
  environment or history.
- The command does not verify ownership, signatures, provenance or artifact integrity.

### 6.6 Redaction and path rules

For authoring output:

| May appear | Must not appear |
|---|---|
| command name | any manifest value not supplied on argv |
| resolved manifest path (text only) | any run/result/artifact/claim ID |
| resolved backup path (text only) | artifact paths, locators, URLs, hashes |
| `"0.1"`/`"0.2"` | metadata keys or values |
| fixed error codes and fixed prose | adapter discovery, names, counts of sensitive objects |
| booleans and field **names** | raw parser diagnostics, `str(load_error)`, tracebacks |
| counts of changed top-level keys (at most 1) | whole-manifest echoes in errors |

Resolved paths are authorized in text only because migration writes through symlinks and
creates a backup beside the resolved target; showing the supplied alias would be
ambiguous. JSON never carries a path. The comparability no-ID/no-path contract remains
scoped to comparability; authoring emits no IDs and no machine-output paths, so it does
not weaken that contract.

### 6.7 Sanitized failure boundary

New code is needed only for `migrate`:

- parse failures produce the fixed `INVALID_ARGUMENTS` document, never an argparse
  diagnostic or a submitted token;
- target/version failures produce `UNSUPPORTED_TARGET`, never the submitted value;
- reserved paths produce `RESERVED_PATH`, never the resolved path in JSON;
- load failures produce `MANIFEST_LOAD_FAILED`, never the loader's message;
- serialization failures produce `SERIALIZATION_FAILED`, never the exception,
  character, key or path;
- a pre-backup difference, or a late diagnostic observation of different bytes, produces
  `STALE_MANIFEST` without matching exception text; the late code reports the diagnostic
  re-read, not a proven cause;
- a failed pre-backup re-read produces `READ_FAILED`;
- write failures on a non-committed path produce `WRITE_FAILED`, never an OS error string
  that may embed a path or filename; a cleanup-only error after a committed replace is
  suppressed and never enters the error boundary;
- unexpected exceptions produce `INTERNAL_ERROR`, never a traceback.

No change is needed to the comparability boundary, the manifest sanitizer, the report
renderer or the selector-finding sanitizer.

### 6.8 Closed error catalogue

| Code | Trigger | Side effects |
|---|---|---|
| `INVALID_ARGUMENTS` | grammar, missing `--to`, unknown option, extra operand | none |
| `UNSUPPORTED_TARGET` | `--to` is `0.1` or any value other than `0.2` | none |
| `RESERVED_PATH` | resolved target basename is a backup generation | none |
| `MANIFEST_LOAD_FAILED` | resolution/stat/read/decode/schema failure | none |
| `SERIALIZATION_FAILED` | `UnicodeEncodeError` during UTF-8 encoding, or `RecursionError` during dump or post-serialization decode | none |
| `READ_FAILED` | pre-backup source re-read fails | none |
| `STALE_MANIFEST` | pre-backup difference, or late diagnostic re-read observes different bytes after a **non-committed** `save_raw` raise (an observation of bytes at that read, not reliable identification of the original failure) | pre-backup difference: none; late: backup may remain |
| `WRITE_FAILED` | backup create/verify/fsync, stage, replace, mode or readback failure **before commit**; late diagnostic re-read fails or observes unchanged bytes | manifest unchanged; backup may remain; temp removed when cleanup succeeds, and may remain if cleanup fails |
| `INTERNAL_ERROR` | unexpected defect, including post-serialization preservation mismatch | no partial document |

Already-0.2 is a success (`changed: false`), not an error code. A committed replacement
whose only later error is post-replace cleanup is also a success (`changed: true`,
`exit 0`), not `STALE_MANIFEST` or `WRITE_FAILED`; a temp file may remain.

## 7. Test plan

### 7.1 `init` decision and warning

- `init` still writes `"schema_version": "0.1"` and no 0.2 key.
- No `--schema-version` option exists; passing it is an argument error.
- Adapters still produce 0.1; an adapter cannot smuggle `conditions`, `dataset_id` or
  `metric_definition`.
- `init --force` over a 0.2 manifest: backup is byte-identical to the old file and is
  created **before** the warning; new file is 0.1; the warning names `conditions`,
  `dataset_id`, `metric_definition`, says regeneration writes schema 0.1, and contains
  no IDs/values/paths.
- A failed backup emits no warning; an unreadable/unidentifiable old manifest emits no
  warning; existing lost/renamed diagnostics may still print IDs and are not covered by
  the new warning's value-free guarantee.
- `init --force` backup generation, reuse, numbering, failure and no-partial-file tests
  continue to pass.

### 7.2 Migration basics, no-op and dry run

- omitted version -> explicit 0.2 first key;
- explicit 0.1 -> 0.2;
- already 0.2 -> exit 0, bytes unchanged, `st_mtime_ns` unchanged, no backup, and
  `save_raw`, `save_manifest`, `_backup_for_migration` are **not called** (monkeypatch
  them to fail if called);
- dry run 0.1 -> exit 0, `changed: true`, `st_mtime_ns` unchanged, no manifest, backup or
  temp file, writer helpers not called;
- dry run 0.2 -> exit 0, `changed: false`, writer helpers not called;
- idempotence: second real migration is a no-op;
- assert the section 3.8 success-envelope matrix for all success rows, including
  `backup: "created"` versus `"reused"` for a real 0.1 migration and the cleanup-only
  success row;
- `--to 0.1`, `--to 0.3`, missing `--to`, unknown option, extra operand, missing PATH;
- missing manifest, directory without manifest, bad UTF-8, bad JSON, duplicate keys,
  nonfinite token outside a result value, deep nesting, symlink loop;
- 0.1 plus every 0.2-only key (including null) refused;
- use `st_mtime_ns`, never sleeps, for no-write assertions.

### 7.3 Preservation, numeric contract and negative controls

- Unit-test `strict_equal` for dict/list/bool/int/float/str/None, including ordered object
  keys, ordered arrays, `True` vs `1`, `1` vs `1.0`, NaN vs NaN, NaN vs Infinity,
  `-0.0` vs `0.0`, and infinities of opposite sign.
- Positive fixtures: nested objects/arrays with non-sorted key order, explicit
  null/empty/false/0, Unicode strings, large float literals, underflow, integer `-0`,
  float `-0.0`, and `NaN`/`Infinity`/`-Infinity` at `results[*].value`.
- Assert snapshot independence structurally: walk `source_a` and `candidate` and assert no
  mutable container (`dict`/`list`) is shared by identity. A defective shared-decode
  implementation that mutates a nested value through both aliased structures must fail
  this assertion; `strict_equal` cannot catch that case because equal aliased mutations
  are not a difference.
- Keep a nested-mutation negative control applied to one side only, against an
  independent baseline; it must fail `strict_equal`.
- After serialization, decode the emitted text and run the same walker on
  `without_schema_version` of both sides; assert equality and assert the only top-level
  difference is the version key/value.
- Negative controls that must fail `strict_equal`: one-sided nested mutation, key
  reordering, array reordering, `true -> 1`, `1 -> 1.0`, `-0.0 -> 0.0`,
  `NaN -> Infinity`, and a lost explicit null/empty.
- `allow_nan=False` is asserted only on output envelopes; a manifest with legacy
  `results[*].value` non-finite tokens still serializes and migrates.

### 7.4 Reserved paths, backup aliases and permissions

- A regular valid manifest named `runevidence.json.bak` (and `.bak.2`) -> `RESERVED_PATH`,
  no load, no write, original bytes unchanged.
- `runevidence.json` symlinked to `runevidence.json.bak` -> `RESERVED_PATH`, no load, no
  write.
- `runevidence.json.bak` symlinked to `runevidence.json` -> migration targets the
  resolved manifest; the symlink is not reused as a backup; a new `.bak.2` generation is
  created; the original 0.1 bytes are independently recoverable from `.bak.2`; the
  symlink is untouched.
- Backup reuse matrix:
  - newest generation is a regular file, not a symlink, a distinct file rather than a
    hardlink/same-file alias of the target, bytes equal, and its mode is a subset of
    `source_mode` -> reused, `backup: "reused"`;
  - symlink with equal bytes -> not reused; next generation created;
  - regular file with different bytes -> next generation created;
  - regular file with equal bytes but a more permissive mode (source `0600`, existing
    backup `0644`) -> not reused; the old generation is left untouched; the next
    generation is created and is no broader than `0600`;
  - candidate is the target itself, a hardlink/same-file alias, a directory or another
    non-regular type -> not reused; next generation created.
- Backup creation with source mode `0600` and umask `022` -> backup `0600`; source `0640`
  with restrictive umask `077` -> backup no more permissive than `0640` (observed
  `0600`); source `0444` -> backup `0444`; no upward `chmod`.
- Permission negative control: an existing byte-identical `0644` backup is not reused
  for a `0600` source, is not chmodded or deleted, and the replacement backup has no bits
  outside `0600`.
- Mode-bit assertions still run as root: root invalidates permission-denial assumptions,
  not the observed mode bits. Use injected `EACCES`/`EPERM` failures for deterministic
  denial tests. Skip cleanly only on Windows or filesystems where POSIX modes are
  ineffective, and record the skip reason.
- Backup readback mismatch and partial-write failure -> `WRITE_FAILED`; only the
  just-created incomplete file is removed; prior generations and the manifest are
  untouched.
- A concurrent file at the chosen generation name is never overwritten; the command
  fails `WRITE_FAILED` and leaves it intact.
- Ownership limitation is documented and not used as a success criterion: owner, group,
  ACLs and extended attributes are not asserted preserved.

### 7.5 Serialization eligibility and writer no-ops

- Lone-surrogate fixture (JSON escape in a valid 0.1 manifest) -> real run and dry run
  both `SERIALIZATION_FAILED`; no manifest, backup or temp file; writer helpers not
  called; no character/exception echo.
- Deeply nested schema-valid fixture: where the runtime raises `RecursionError`, assert
  `SERIALIZATION_FAILED`. Also monkeypatch `dump_raw` (and the post-serialization decode)
  to raise `RecursionError` for a deterministic test. Do not assert a universal nesting
  threshold.
- Monkeypatch `text.encode` or a lone surrogate to raise `UnicodeEncodeError`; assert
  `SERIALIZATION_FAILED` with no echo.
- Paired dry-run/real-run tests for both inputs; dry run writes no manifest, backup or
  temp file and does not call writer helpers.
- Already-0.2 and refusal paths assert `save_raw`/`save_manifest`/backup helpers are not
  called.

### 7.6 Stale checkpoints, atomicity and failure side effects

- Pre-backup stale injection: change the manifest after load but before backup ->
  `STALE_MANIFEST`; no backup, no temp, manifest unchanged.
- Pre-backup re-read failure injection -> `READ_FAILED`; no backup, no temp, manifest
  unchanged.
- Late stale injection: change the manifest after the backup but before `save_raw`'s
  final check -> `STALE_MANIFEST`; manifest unchanged; completed backup remains; no temp.
- Late-classifier counterexamples, asserting only the observation and write safety:
  - a stale rejection followed by restoration of the original bytes before the
    diagnostic re-read -> `WRITE_FAILED`;
  - an unrelated replacement failure followed by a concurrent change before the
    diagnostic re-read -> `STALE_MANIFEST`.
- `save_raw` `OSError` classification without exception-text matching:
  - bytes differ on re-read -> `STALE_MANIFEST`;
  - bytes unchanged -> `WRITE_FAILED`;
  - re-read fails -> `WRITE_FAILED`.
- Assert the classifier never claims a proven cause; tests assert the code, the observed
  bytes and, for a non-committed raise, that this command did not replace the manifest.
- **Post-replace cleanup regression (Option A).** Deterministically monkeypatch the
  `staged.unlink` cleanup (for the staged path only) to raise `OSError(EIO)` after
  `os.replace` has returned:
  - `save_raw` suppresses the cleanup error and returns success;
  - migration exits 0, the manifest declares 0.2, the backup is intact, the success
    envelope has `changed: true` and `backup: created|reused`, and the text is the normal
    `Wrote ...` output;
  - no `STALE_MANIFEST`, no `WRITE_FAILED`, no diagnostic re-read, and no false "nothing
    was replaced" text;
  - a temp file may remain and is not treated as failure.
- **Existing `rehash`/`add` callers.** Apply the same post-replace cleanup injection and
  prove those commands still succeed and report the committed edit; the existing
  `rehash`/`add` suites stay green.
- **Pre-replace counterpart.** Inject a cleanup/staging failure before `os.replace`
  (`replaced` is false): `save_raw` re-raises as today, migration exits 2, the old
  manifest is intact, and no false success text is printed. An injected `os.replace`
  failure remains a non-committed write/stale classification; it is never reported as a
  cleanup success.
- Injected backup failure, `fsync` failure, `os.replace` failure, mode/readback failure:
  manifest unchanged; backup state matches the stage table; no `.tmp` left when cleanup
  succeeds, while a cleanup failure may leave it behind.
- Simulated crash/partial-write: incomplete backup or temp may remain; the manifest is
  never truncated; no parent-directory durability claim.
- A backup name taken concurrently is never overwritten.
- No lock is claimed; the documented check-then-replace race has a named test for the
  detectable half only.

### 7.7 Round-trip, workflow and 0.1 preservation

- Migrated 0.2 loads, validates and inspects clean.
- Migrated 0.2 compares identically to its 0.1 backup when legacy metadata supplies the
  same dimensions (conditions absent, fallbacks independent).
- Old 0.1 reader rejects the migrated 0.2 file.
- 0.1 fingerprints are unchanged; `fingerprint` v1 still rejects 0.2.
- Workflow fixture: adding `conditions`, `dataset_id` or `metric_definition` while still
  0.1 fails schema validation and `migrate` refuses; after `migrate`, the same fields
  load; `validate`/`inspect` report sanitized selector findings.
- Legacy conflicts: equal first-class/legacy duplicates collapse; unequal or malformed
  duplicates produce `INPUT_SHAPE_UNSUPPORTED`.
- Selectors: correct ID/kind/`input_to` passes; unattached, wrong-kind, unknown or
  null selector fails; no edge is synthesized; no fallback occurs.
- Committed 0.1 examples remain byte-for-byte untouched by the suite; no test migration
  rewrites them in place.

### 7.8 Privacy, independently for every outcome

- Canaries in every manifest value class (names, IDs, splits, revisions, protocol IDs,
  environment objects, metadata keys, artifact paths, locators, hashes, relationship
  notes) must not appear in stdout, stderr, JSON, text or error documents.
- Run the canary suite independently for: success (`created`), success (`reused`),
  cleanup-only post-commit success, no-op, dry run, and every error code
  (`INVALID_ARGUMENTS`, `UNSUPPORTED_TARGET`, `RESERVED_PATH`, `MANIFEST_LOAD_FAILED`,
  `SERIALIZATION_FAILED`, `READ_FAILED`, `STALE_MANIFEST`, `WRITE_FAILED`,
  `INTERNAL_ERROR`).
- Unicode, quotes, newlines and terminal control characters in canaries.
- Bad JSON containing secret text: the fixed error document, no echo.
- Invalid `--to` value is not echoed; reserved path is not echoed in JSON.
- JSON success/no-op/dry-run/error output has no path, ID or value.
- Text output contains only resolved manifest/backup paths (success/no-op/dry run) and
  fixed tokens; error text contains no path.
- Symlink operand text shows the resolved target, not the supplied alias.
- Negative controls: a deliberately echoing renderer must fail the named assertions.

### 7.9 No-I/O and filesystem allowlist

- Install traps after CLI startup/imports or count named operations, so argparse/locale
  reads (`LANGUAGE`, `COLUMNS`, `LINES`) and Python startup are not treated as
  application environment discovery.
- Allowed operations are exactly the section 3.9 inventory: path resolution/stat/lstat,
  one validated manifest load read, one pre-backup source-byte read, one folder
  enumeration for backup generations, backup read/verification/reuse, exclusive backup
  write/fsync/readback, `save_raw` stage/final read/replace/temp cleanup, and the
  conditional diagnostic target read after a **non-committed** `save_raw` raises
  (successful or failed).
- Exercise the inventory on failure paths as well as success, no-op and dry run: at least
  `READ_FAILED`, late `STALE_MANIFEST`, late `WRITE_FAILED`, backup write/readback
  failure, and `SERIALIZATION_FAILED`, plus the cleanup-only post-commit success. On
  late-save failure paths, assert the only additional target access is the permitted
  diagnostic read and that it is attempted even when that read itself fails. For
  `READ_FAILED`, assert the failed pre-backup read is the only extra target access. For
  cleanup-only success, assert no diagnostic read occurs.
- Forbid and trap artifact open/stat/scan, directory scans outside the backup namespace,
  adapters, checks/hashes, subprocesses, network/DNS/URL, repository inspection and
  application environment discovery.
- Success migration, cleanup-only post-commit success, no-op, dry run and every exercised
  failure path assert the expected named operations and zero forbidden attempts;
  success/cleanup-only/no-op/dry run also assert that no post-`save_raw` diagnostic read
  occurs when `save_raw` does not raise. Traps record before raising.

### 7.10 Pins, parser and documentation surface

- Exactly 25 unique routes in the specified order.
- `migrate` is registered directly after `init`; removing it recovers the exact
  `HISTORICAL_24_ROUTES` sequence.
- The current 24-route CLI pair is preserved as history and included in both
  historical-disjointness checks (the `source()` set and the constant test).
- 23- and 22-route derivations are computed from `HISTORICAL_24_ROUTES`, not the new
  `ROUTES`.
- CLI `CLI_HASH` is exactly two strings (LF, CRLF), disjoint from all historical pins.
- The current reader digest is appended to `HISTORICAL_READER_HASHES`; the new
  `READER_HASH` is exactly one LF digest, matches the edited staged-reader test, and is
  not in history.
- Both explicit `current_public` route sets include `migrate`, not only the count; the
  staged-reader argv tripwire cases are unchanged.
- `migrate` grammar-only test dispatches nothing; use the comparability
  handler-replacement pattern (`cli.cmd_migrate = denied` around
  `build_parser().parse_args`), because the historical seven-handler
  `parse_without_dispatch` helper does not admit `migrate`; sanitized parse test asserts
  one JSON document, no diagnostic and exit 2; `--help`/`--version` keep normal behavior.
- Root `--help` and module docstring list `migrate` among the writing commands; README
  write inventory, exit-code and single-writer guidance are updated.
- Refresh the recorded `manifest.py` hash for the Option A change; `manifest.py` itself
  does not change the CLI or READER pins.
- No new private route marker, SQLite import or adapter import in the migration module.

### 7.11 Installed package

- Build a wheel and sdist from a clean copy; install in a fresh venv with Pydantic 2.6.4
  and a current 2.x version.
- Invoke the installed entry point **outside the checkout** (for example from `/tmp`),
  not `python -m` against the source tree; run migration, dry run, no-op and a refusal.
- Installed `migrate` works on copied 0.1 and 0.2 examples; JSON/text shapes and exit
  codes match source-tree behavior.
- Installed package contains no `schema/`, `tests/`, `docs/` or `examples/` content and
  needs no network. `pip check` clean.

### 7.12 Negative controls

Each must fail a named assertion: selected value echo, ignored metadata echo, ID echo,
path echo in JSON, resolved-path echo in error text, raw loader message, raw argparse
diagnostic, traceback, Unicode/surrogate echo, unexpected artifact read, directory scan
outside the backup namespace, network/DNS attempt, application environment read, adapter
call, second manifest load, a writer that skips the backup, a backup that is more
permissive than its source or is reused despite that, a stale check that matches
exception message text instead of re-reading bytes, a missing post-`save_raw` diagnostic
read, a shared-decode alias that the snapshot-independence assertion must catch, and a
post-replace cleanup-only success reported as `STALE_MANIFEST`, `WRITE_FAILED` or
"nothing was replaced".

## 8. Implementation sequencing and gates

### 3d-0 — this RFC

- Deliverable: this document only (v0.4 revision).
- Acceptance: no production/test/schema/example/README change; decisions reviewable;
  working tree otherwise unchanged.
- **HOLD:** no implementation until this RFC is reviewed and accepted.

### 3d-1 — documentation and `init` decision

- Update `docs/comparability-schema-0.2.md` authoring limits, README authoring text,
  write-command inventory/exit-code/single-writer guidance and the migration section. No
  code.
- Acceptance: docs agree that `init` is 0.1, no flag exists, migration is the only opt-in,
  an empty 0.2 manifest has no first-class fields, the order is init -> migrate -> add
  fields, adding 0.2-only fields under 0.1 is invalid, legacy conflicts are rejected and
  selectors require `input_to`.
- **HOLD:** docs review before migration code.

### 3d-2 — migration

- Add `src/runevidence/migrate.py` (or an equivalently small manifest-only module), the
  25th route, `_migrate_main`, the fixed `init --force` 0.2 warning, reserved-path
  refusal, migration-specific backup helper with exclusive create/mode/readback and
  permission-subset reuse, independent-decode strict preservation proof, serialization
  eligibility, two stale checkpoints with `READ_FAILED` and observation-qualified late
  classification, the success-envelope matrix, backups, JSON/text renderers and tests.
- Apply **Option A** to `src/runevidence/manifest.py::save_raw`: track the committed
  `os.replace`, suppress a post-replace cleanup `OSError` (a temp file may remain) and
  return success; re-raise pre-replace failures as today. `manifest.py`'s hash changes in
  3d-2; existing `rehash`/`add` tests must stay green, with new cleanup-after-commit
  regression coverage.
- Accepted files changed in 3d-2:
  - `src/runevidence/manifest.py` (commit/cleanup invariant; hash changes);
  - `src/runevidence/cli.py` (25th route, `_migrate_main`, fixed `init --force` warning);
  - new `src/runevidence/migrate.py`;
  - `tests/test_experimental_cli_parser_contract.py`,
    `tests/test_experimental_staged_reader.py`,
    `tests/test_experimental_versioned_manifest.py` (pins/route sets);
  - new migration and `save_raw` cleanup regression tests;
  - `README.md`, `docs/comparability-schema-0.2.md`, and this RFC's status line.
  No model or schema files change. Refresh any recorded `manifest.py` acceptance hash;
  the CLI/READER pins change only for `cli.py` and the staged-reader route set.
- Refresh the CLI/READER pins and route-set assertions exactly as section 6.1; update the
  CLI module docstring/root help and README as section 6.3.
- Acceptance: section 7 migration, preservation, backup/permission, serialization,
  round-trip, workflow, atomicity, privacy, no-I/O, pin and installed-package suites pass;
  the post-replace cleanup success and pre-replace counterpart tests pass; existing
  `rehash`/`add` suites stay green; 0.1 output and fingerprints unchanged; no adapter,
  artifact, SQLite or network connection.
- **HOLD:** focused review of the `save_raw` commit invariant, write path, backup
  namespace/permissions, preservation walker, stale classification, sanitized boundary
  and pin-refresh packet before merge.

### 3d-3 — conditions command

- **Deferred.** No code. Record the decision and the non-normative sketch in section 4.
- A future gate must approve the exact patch format, null/clear semantics, selector
  validation reuse, schema-versus-comparison bounds, output envelope, exit codes and its
  own pin refresh.
- **HOLD:** closed for 3d.

### 3d-4 — preflight decision

- **Deferred.** No route, no code. Record the presence-only contract, the `not-0.2`
  readiness state and the rejection of ID-listing discovery.
- A future privacy decision is required before any helper exists.
- **HOLD:** closed for 3d.

### 3d close

- Verify only the files named by accepted slices changed; no build artifacts, no Git
  operation, no release/version bump.
- Re-run the focused and full suites, installed-wheel checks and privacy/no-I/O tests.
- Report the exact pins, test counts and residual limitations.

## 9. Alternatives, rejections and residual risks

| Alternative | Why rejected |
|---|---|
| Make 0.2 the `init` default | Implies readiness, changes every new project, and worsens silent loss on `--force` without adding fields. |
| Add `init --schema-version 0.2` | Extra flag, help, pin and `--force` semantics for a relabel that `migrate` already provides explicitly. |
| Auto-migrate on load or on any write | Violates the RFC non-goal and rewrites evidence without explicit consent. |
| Migrate via `rehash`/`add` or a hidden flag | Conflates unrelated intent and makes migration hard to audit. |
| Rewrite through the Pydantic model | Risks dropping unknown-but-valid hand formatting and silently adding default keys; raw editing plus an independent decoded-value proof is narrower. |
| Support `--to 0.1` downgrade | 0.2 first-class fields cannot be represented under 0.1 without loss or rejection; downgrade is a separate, dangerous decision. |
| Infer fields from legacy metadata during migration | Explicit non-goal; would create unsupported assertions. |
| New backup suffix such as `.pre-0.2.bak` | Not recognized by `is_own_file()`; it would be scanned as ordinary content. Reusing the `.bak` generation namespace is required. |
| Reuse `_backup_existing` wholesale | Reloads the manifest, prints paths and can print artifact IDs during regeneration analysis; violates the migration output/read contract. |
| Fold `RESERVED_PATH` into `MANIFEST_LOAD_FAILED` | The reserved case is detected without a load, has a different remedy, and must not imply the manifest is invalid. |
| Reuse a symlink or alias backup | A symlink can point at the target or change under the writer; only a non-symlink regular file, distinct from the target, with equal bytes and a permission subset is safe. |
| Reuse a byte-identical but more permissive backup | Reuse would silently weaken the source's permission boundary; leave it untouched and create the next generation with `mode=source_mode`. |
| Fold `READ_FAILED` into `STALE_MANIFEST` or `WRITE_FAILED` | A failed pre-backup re-read is not evidence of a byte difference or a write failure; a dedicated code keeps the refusal table honest. |
| `chmod` the backup to the source mode after creation | Could widen permissions after a restrictive umask; create with `mode=source_mode` instead. |
| Promise lexical/byte preservation | The accepted loader already normalizes numbers and the writer normalizes formatting; the honest promise is decoded values plus a byte-identical backup. |
| Match stale errors by exception text | Fragile and message-dependent; re-read and compare bytes instead. |
| Use `allow_nan=False` for manifest serialization | Would reject the legacy non-finite result tokens the loader accepts; scope it to output envelopes. |
| No backup / overwrite backup | Destroys the only 0.1 copy and violates the backup-generation contract. |
| Add a conditions command now | Sensitive values on argv, six-way presence semantics and selector validation need their own reviewed gate. |
| ID-listing discovery/preflight | Directly conflicts with the no-ID contract; needs a separate authorization and privacy decision. |
| Presence-only preflight now | No 3d need; `migrate --dry-run` and `validate`/`inspect` already answer the actionable questions. |
| Locking or atomic compare-and-swap | Not available portably under the current writer design; documenting the race and re-reading bytes is honest. |

Residual risks and limitations:

1. Migration preserves decoded values, presence and key/array order, not lexical spelling,
   number precision beyond the loader, original formatting or original bytes. Large
   float literals round, underflow becomes `0.0`, integer `-0` becomes `0`, and
   integer/float spelling may change. The backup preserves the original bytes.
2. Replacement and backup create new inodes. POSIX permission bits are preserved or
   reduced under the process umask, but owner, group, ACLs, security descriptors and
   extended attributes are not preserved; backup ownership is the invoking process. The
   "not more permissive" guarantee is scoped to POSIX mode bits, not platform ACLs.
3. The stale check is check-then-replace, not a lock; concurrent writers can still race.
   Two checkpoints narrow but do not eliminate the window.
4. Backup durability is not guaranteed: no parent-directory `fsync`; a crash may leave an
   incomplete backup or a temp file. A completed backup may remain after a later failure.
5. The reserved-name check protects the exact `runevidence.json.bak[.N]` namespace, not
   arbitrary user files with backup-like names elsewhere.
6. `init --force` remains destructive for first-class fields; the backup and the fixed
   warning are the only mitigations, and existing regeneration diagnostics may print
   artifact IDs.
7. An empty 0.2 manifest is a compatibility declaration, not readiness; hand-authored
   legacy conflicts, incomplete selectors and comparison-time environment bounds can
   still make comparison fail.
8. Authoring output is sensitive even without values: schema version, eligibility and
   field presence can reveal experimental design. It is not anonymous or safe for
   unrestricted publication.
9. Selector authoring, if later approved, can only validate manifest graph facts, not
   artifact identity, bytes or scientific meaning.
10. Python bytecode/atime effects, OS caches, debuggers, process memory, shell history and
    terminal capture remain outside the privacy and no-I/O claims.
11. Late `STALE_MANIFEST` versus `WRITE_FAILED` reports the bytes observed at the
    diagnostic re-read, not the original cause; the two counterexamples are expected, and
    both preserve the write-safety conclusion that the command did not replace the
    manifest.
12. A suppressed post-replace cleanup failure may leave a staged temp file. The
    replacement is still committed and successful, but a later directory scan could see
    the leftover temp file as ordinary untracked content.

## Document verification

This RFC is documentation-only. It changes no production, test, schema, example or
README file, performs no Git operation, and does not modify `progress/`.
