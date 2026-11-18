# TensorBoard scalar byte refresh — RFC v0.6

Status: design draft for review. New opt-in rehash behavior; no schema change.

## 0. Changes in this version

v0.6 is the closure pass. It adds the real-write trailer, pins the absent old
size and size-only claim behavior, corrects the `_read_snapshot_parts`
provenance, scopes the suffix rule to the new refusals, and fixes the remaining
selector/order wording.

The design remains schema 0.5 scalar locators only, explicit artifact IDs, one
protected snapshot, verify-never-write values, artifact digest/size only, and
all-or-nothing.

## 1. Problem and scope

A TensorBoard event file normally grows as training continues and can be
replaced after a resumed run. The manifest records exact scalar locators
`(tag, step, occurrence)`. Today `rehash` refuses any artifact read through a
structured locator with `LOCATOR_REFRESH_UNSUPPORTED`, and `init --force`
refuses a schema 0.2+ manifest because a fresh scan cannot carry authored
values. The user is caught between restoring the old bytes and rebuilding the
manifest by hand.

This slice makes one bounded case refreshable:

```text
runevidence rehash PATH ARTIFACT --refresh-locators [--dry-run]
```

It re-reads every recorded scalar locator on each selected schema 0.5 artifact
from the current event bytes, requires every recorded value to still match its
locator, and updates the artifact digest/size. It does not write a result value,
add or remove a result, tag, step, run or artifact, or change conditions,
claims, relationships, metadata or IDs. An intentional value change is recorded
with `result edit` first; the byte refresh then verifies it.

Only schema 0.5 scalar `TensorboardLocator` results are supported. Other
schemas, structured locator kinds, directory artifacts, volatile artifacts and
mixed locator styles keep the delivered default refusal; with the flag they get
the fixed target refusals in section 5. This is not a generic refresh protocol
and has no `--all` mode.

## 2. Behavior

The flag is opt-in and requires one or more explicit artifact targets. Without
it, `rehash` behaves exactly as delivered, including the current refusal text.

For the selected set, preflight **before reading any event bytes**:

1. `--refresh-locators` with `--all`, `--volatile`, `--no-volatile` or
   `--directory` is refused with a fixed usage message. Without the flag, every
   structured locator keeps the delivered default refusal.
2. Every selected artifact must:
   - exist in the manifest;
   - have `artifact_type == "file"`;
   - satisfy the shared TensorBoard event predicate;
   - be non-volatile, have a recorded `sha256`, and the manifest must
     declare schema 0.5;
   - have **no** other locator style on that artifact (no string pointer, no
     other structured kind, no locator-less result);
   - have at least one result with a `TensorboardLocator`.
3. The manifest must pass the existing TensorBoard cap
   (`_tensorboard_cap`: 10,000 entries / 16 MiB tag metadata). A capped
   manifest is refused with the existing cap text.
4. Any failure refuses the whole edit before a read or mutation; nothing is
   written. The exact precedence and first-failure rule is section 5.1.

Then, for each selected artifact:

- read the event bytes **once** through
  `_read_snapshot_parts(..., cap=TENSORBOARD_MAX_FILE_BYTES,
  tensorboard_raised=True)`; accept only `state == "ok"` and use the same
  snapshot's `digest`, `size` and `data` for every check and the recorded
  identity;
- build one `scan_scalars` request per result from exact tag UTF-8 bytes, signed
  step and occurrence;
- require every request to return `ScalarOutcome.value`;
- require `compare_tensorboard(recorded_value, actual_value,
  float32=selection.float32)` to return `True` for every result:
  - `None` (recorded `bool`, `str`, `null` or other non-number) refuses as
    `unsupported`;
  - `False` refuses with a fixed "value changed; record it with result edit"
    message; the command never writes a new value;
- require the selected artifact's result set to be non-empty and every result
  on it to be a supported scalar locator on the same path;
- update `sha256` and `size_bytes` from the snapshot. No result value changes;
- release the snapshot before reading the next artifact (no accumulation);
- collect claims that rest on a digest-changed artifact for the existing
  rehash warning; a size-only repair does not produce claim warnings;
- add one positional warning whenever the digest changed: the exact
  `(tag, step, occurrence)` contract is positional, so duplicate insertion or
  deletion can retarget an occurrence even when its value is equal.

If the digest and size are unchanged, the command verifies and reports a no-op
without writing. If the digest is unchanged but `size_bytes` is wrong, it
updates only the size after the same verification. A non-volatile target with
`sha256: null` is refused before reading; it has no recorded identity to
refresh.

New tags/steps in the file are not added. Deleted tags/steps that a recorded
locator needs cause a refusal. A file that grows by appending records can
refresh because old locators still resolve. A changed value cannot be accepted
by this command; the user must run `result edit` first.

## 3. CLI

Add `--refresh-locators` to the existing `rehash` parser, with help text that
limits it to schema 0.5 TensorBoard scalar locators and says values must
already match.

Flag rules:

- Requires explicit artifact targets: a bare ID or `id:ID` only. `--all`,
  `run:`, `path:`, `glob:`, `folder:` and path-like selectors are refused.
  `--all` has a
  separate fixed message; the other selectors use the same target-shape
  refusal.
- `--volatile`, `--no-volatile` and `--directory` are refused.
- `--dry-run` performs the same reads, checks and plan output, then writes
  nothing; it takes no writer lock, as today.
- The writer lock, manifest stale-byte check and atomic save remain the
  existing rehash paths. Dry runs do not lock and nonparticipating writers are
  uncontrolled.
- A real refresh takes the existing lock through the save. It creates no backup,
  as ordinary rehash does.
- The manifest stale-byte check covers the manifest only; artifact bytes are
  not rechecked after the snapshot. The snapshot's identity/size checks are
  advisory against concurrent rewrites, not a content seal.

Exit codes:

- `0` verification succeeded, including a no-op or a digest/size update;
- `2` unsupported target or flag combination, changed/missing locator, cap,
  read/profile refusal, stale manifest, writer busy/lock unavailable, or usage.

## 4. Reader and identity

Only `TensorboardLocator` results on a schema 0.5 manifest are accepted. The
request key is exactly `(tag_bytes, step, occurrence)`; the tag is encoded back
to exact UTF-8 bytes. `scan_scalars` is the delivered scalar reader for schema
0.5 and is used without a second parser.

The snapshot digest and size are the new artifact identity. No artifact path,
ID, run, result, tag, step, occurrence or result set changes. The only possible
manifest edit is `artifacts[*].sha256` and `artifacts[*].size_bytes`.

The value comparator is `compare_tensorboard`, not `==` or `values_match`:
float32-origin values use the delivered 1e-6 tolerance, float64 uses 1e-9,
NaN equals NaN, infinities equal only themselves, and a recorded bool/string/
null returns `None`. A changed numeric value is refused, so the command never
writes a non-finite or type-changing value.

## 5. Exact refusals

The flag is opt-in. Without it, the delivered `LOCATOR_REFRESH_UNSUPPORTED`
refusal is unchanged.

Two printers are involved:

- Flag, selector-shape and no-target usage failures print `ERROR: <message>`
  only, like the existing `cmd_rehash` usage lines. They do not print
  `Nothing was written.`.
- Post-load refusals (unknown ID, target shape, schema, cap, read, locator,
  value) use `EditError`; `_edit_error` prints:

```text
ERROR: <message>
Nothing was written.
```

A new refresh-refusal message never contains `Nothing was written.`. (Some
legacy summary `EditError`s in the delivered tree already do; this slice does
not copy that pattern.) The table below lists the `<message>` only and names the
printer class.
`_locator_refresh_unsupported` is used only by the default (no-flag) refusal and
is not used by the opt-in path.

### 5.1 Flag and target preflight

Usage failures print `ERROR: <message>` only, in this order; the first failure
wins:

| Condition | Message |
| --- | --- |
| `--all` | `--refresh-locators requires explicit artifact targets; --all is not accepted` |
| `--volatile`, `--no-volatile` or `--directory` | `--refresh-locators cannot be combined with --volatile, --no-volatile or --directory` |
| no targets | `--refresh-locators needs at least one artifact target` |
| `run:`, `path:`, `folder:`, `glob:` or empty `id:` | `--refresh-locators accepts only exact artifact IDs` |

Post-load refusals use `EditError`; the first failure in this order wins:

| Condition | Message |
| --- | --- |
| schema is not 0.5 | `rehash cannot refresh: the manifest is not schema 0.5` |
| unknown exact ID | `--refresh-locators accepts only exact artifact IDs; no artifact has ID <V!r>` |
| volatile target | `rehash cannot refresh 'ID': it is volatile` |
| directory target | `rehash cannot refresh 'ID': it is a directory artifact` |
| no `sha256` | `rehash cannot refresh 'ID': it has no recorded digest` |
| path fails the event predicate | `rehash cannot refresh 'ID': the path is not a TensorBoard event file` |
| mixed locator styles | `rehash cannot refresh 'ID': the artifact mixes locator styles` |
| no scalar locator results | `rehash cannot refresh 'ID': the artifact has no TensorBoard scalar locators` |
| manifest over cap | `the manifest exceeds the tensorboard verification limit (10,000 entries or 16 MiB of tag metadata); no TensorBoard scalar was read` |

`ID` is the artifact ID in single quotes; `V` is the requested token with an
`id:` prefix stripped, and `<V!r>` is its Python repr. The schema check is
manifest-level and runs before ID lookup. The per-artifact rows
run in target argument order and stop at the first failing artifact. The cap is
checked after the per-artifact target shape and before any read. Event reads run
in selection order; results are checked in manifest order and stop at the first
failure. Exactly one refusal is reported.

With the flag, the CLI does not call `select_artifacts`. Only `run:`, `path:`,
`folder:`, `glob:` and an empty `id:` are refused as selector shapes. `id:V`
strips the prefix; every other token, including `foo:bar` or `runs/a`, is the
bare ID `V`. An empty `id:` is a usage refusal; a non-empty absent ID is the
post-load unknown-ID refusal. Duplicates are removed after stripping.

### 5.2 Snapshot states

The protected reader uses exactly:
`ok`, `over-cap`, `missing`, `unreadable`, `changed`, `symlinked-parent`,
`refused-path`, `refused-alias`, `escapes-root`.

Build the parts with `_targeted_parts(root, art.path)`. If it returns a
non-`ok` state (`refused-path` or `escapes-root`), use that state directly and
do not call `_read_snapshot_parts`; only read when it returns `ok`. The other
states (`missing`, `unreadable`, `changed`, `symlinked-parent`, `refused-alias`,
`escapes-root` from a leaf alias, `over-cap`) come from
`_read_snapshot_parts`; pass those through too. Map non-`ok` states to:

| State | Message |
| --- | --- |
| `missing` | `the event file is missing` |
| `unreadable` | `the event file is unreadable` |
| `changed` | `the event file changed during the read or is not a regular file` |
| `symlinked-parent` | `a parent of the event file is a symlink` |
| `refused-path` | `the event path has a refused shape` |
| `refused-alias` | `the event path resolves through a refused alias` |
| `escapes-root` | `the event path escapes the experiment folder` |
| `over-cap` | `the event file exceeds the 128 MiB TensorBoard limit` |

The message is `Cannot refresh event bytes for 'ID': <state message>`.
A strict-profile `EventFileError` from `scan_scalars` maps to
`Cannot refresh event bytes for 'ID': the TensorBoard event file does not
satisfy the strict profile`, reusing `TENSORBOARD_PROFILE_HINT` verbatim.

### 5.3 Scalar outcomes

`ScalarOutcome` has exactly `value`, `mismatch`, `unsupported-scalar` and
`unsupported-value`. Map a non-value outcome to the delivered phrases:

| Outcome | Phrase |
| --- | --- |
| `mismatch` | `was not found` (an absent `(tag, step)` and an out-of-range occurrence are indistinguishable) |
| `unsupported-scalar` | `selects an unsupported scalar encoding` |
| `unsupported-value` | `selects an unsupported value` |

Result refusals use the result ID, `<display> = display_locator(locator)` and
`<path> = repr(art.path)`:

```text
Cannot refresh result 'RESULT_ID': locator <display> <phrase> in <path>.
```

The loaded model already rejects a lone-surrogate locator tag, so no separate
UTF-8 refusal is needed here.

A changed value:

```text
Cannot refresh result 'RESULT_ID': recorded value does not match the current locator. Record the intended value with 'runevidence result edit' first.
```

A recorded bool, string or null:

```text
Cannot refresh result 'RESULT_ID': the recorded value is not a TensorBoard scalar. Fix it with 'runevidence result edit' first.
```

### 5.4 Output and warnings

The positional warning is one `plan.notes` entry for each artifact whose digest
changed (not a size-only repair):

```text
<artifact-id>: accepted new event bytes; recorded scalar locators are positional, so a duplicate insertion or deletion can retarget an occurrence even when its value is unchanged
```

`_rehash_under_lock` renders notes as:

```text
  NOTE <artifact-id>: accepted new event bytes; recorded scalar locators are positional, so a duplicate insertion or deletion can retarget an occurrence even when its value is unchanged
```

No `NOTE` is embedded in `plan.notes`.

Refresh output is emitted by a new branch in `_rehash_under_lock`, in this
exact order:

1. changed artifact lines, in target argument order;
2. verified artifact lines, in target argument order;
3. `plan.notes` lines (`  NOTE ...`), in target argument order;
4. the existing claims line, only when at least one digest changed;
5. summary;
6. real changed run: `Wrote <path>` and `Next: runevidence validate <path>`,
   through `_finish_edit`; dry changed run: `Dry run: nothing was written.`.
   A no-op prints no step 6 line.

Lines:

- digest update: `  <id>: sha256 <_short(old)> -> <_short(new)>` (plus
  `, <old> -> <new> bytes` when size changes; `none` when the old size is
  absent);
- size-only repair: `  <id>: size <none|old> -> <new> bytes; sha256 unchanged`;
- no-op: `  <id>: verified; bytes and locators match`.

Summaries use `_count(len(...), 'file')` so one file prints as `1 file`:

- changed only: existing `Would re-record`/`Re-recorded
  <_count(len(changed), 'file')>`;
- changed and verified: `Would re-record <_count(len(changed), 'file')> and
  verify <_count(len(verified), 'file')>` / `Re-recorded
  <_count(len(changed), 'file')> and verified <_count(len(verified), 'file')>`;
- no-op only real: `Verified <_count(len(verified), 'file')>; no changes.`;
- no-op only dry: `Would verify <_count(len(verified), 'file')>; no changes.`.

Claims are collected only from digest-changed artifacts, not size-only repairs.
A dry run cannot detect a stale manifest because the check is inside
`save_raw`; only a real refresh that writes can refuse stale bytes. A no-op
never reaches `save_raw`.

## 6. Module checklist

- `src/runevidence/edit.py`: add a preflight helper and a schema-0.5
  TensorBoard byte-refresh path. Import `_targeted_parts`,
  `_read_snapshot_parts`, `TENSORBOARD_MAX_FILE_BYTES`, `_tensorboard_cap`,
  `TENSORBOARD_CAP_MESSAGE`, `TENSORBOARD_PROFILE_HINT`,
  `_tensorboard_path_matches`, `display_locator`, `TensorboardLocator`,
  `SCHEMA_VERSION_0_5`, `scan_scalars`, `compare_tensorboard`, `ScalarOutcome`
  and `EventFileError`. Resolve refresh targets by exact
  artifact ID without `select_artifacts`; `id:V` and bare `V` are the only
  accepted shapes. Skip the whole legacy block for refresh targets, create
  `FileChange` from the protected snapshot, release `snapshot.data` before the
  next artifact, exclude `TensorboardLocator` results from `_reread_values`,
  and stage only `sha256`/`size_bytes`. An explicit `volatile: false` must
  survive; do not reuse the legacy `entry.pop("volatile", None)` branch. End
  with `validate_manifest(raw)`.
- `src/runevidence/cli.py`: add the flag, flag/selector preflight **before** the
  `--directory` branch, help text, the usage lines, and the new refresh output
  branch (changed lines, verified lines, no-op summary) in
  `_rehash_under_lock`.
- `tests/test_experimental_cli_parser_contract.py`: replace the current LF/CRLF
  `CLI_HASH` pair, unconditionally archive the superseded pair in
  `HISTORICAL_26_ROUTE_CLI_HASH` with a dated comment, keep `ROUTES` unchanged,
  add `refresh_locators` to the grammar assertion tuple and assert it defaults
  to false in `test_edit_route_grammar_only`.
- `src/runevidence/checks/files.py`: no new reader; reuse
  `_read_snapshot_parts`. If a helper is added, import it lazily.
- `README.md`: flag, exact-ID selector, schema 0.5 scope, value-first rule;
  update the TensorBoard paragraph that currently states the unconditional
  structured-locator refusal.
- `docs/schema-0.5-tensorboard.md`: replace the unconditional rehash refusal
  paragraph with the opt-in rule. `docs/tensorboard-guide.md`: add the rule in
  its "Migration and refusals" section. `docs/authoring-files.md`: add it where
  artifact identity mentions rehash. Keep historical RFC documents frozen; a
  dated note may state that the parent refusal is superseded for schema 0.5
  only.
- `tests/test_edit.py`, `tests/test_rehash_selectors.py` and
  `tests/test_cli_locator_0_3.py`: the vectors in section 7, exact selector
  grammar, an explicit `volatile: false` fixture, and one
  `Nothing was written.` for each post-load refusal (usage errors have none).
- `docs/qualification.md`: test count and A7 evidence after the suite lands.

## 7. Verification plan

1. Write a schema 0.5 scalar event file, init, author a claim and conditions,
   append one valid scalar record; refresh exits 0, updates only
   `sha256`/`size_bytes`, preserves the claim/conditions, and `validate
   --strict` passes.
2. Assert the manifest diff is limited to those two artifact fields; no result
   value, name, tag, step, occurrence, result count or `volatile: false` field
   changes.
3. Modify an existing scalar value without editing the result: refresh exits 2
   with the value-first message and leaves the manifest byte-identical. Run
   `result edit` to the new value, refresh again, and validate.
4. Delete/rename a recorded tag or step: refresh exits 2, manifest
   byte-identical.
5. A schema 0.6 scalar target and a 0.5 target with a `jsonl` or `csv-cell`
   mixed result are refused; a 0.6 `tensorboard-string` target is refused.
6. Mixed locator styles, locator-less results, volatile targets, directory
   targets, null-digest targets, `run:`/`path:`/`glob:`/`folder:` selectors,
   unknown exact IDs, `--all`, `--volatile`, `--no-volatile` and `--directory`
   are refused before any read, with the exact messages and the pinned
   precedence order. Usage refusals have no suffix. Bare tokens that are also
   paths/folders are not sent to `select_artifacts`.
7. Manifest cap at 10,001 entries and the tag-byte boundary refuses with the
   exact text; a selected null-digest target is refused rather than counted.
8. Snapshot states `missing`, `unreadable`, `changed`, `symlinked-parent`,
   `refused-path`, `refused-alias`, `escapes-root`, `over-cap` and a malformed
   `EventFileError` refuse with the exact texts; `TENSORBOARD_PROFILE_HINT` is
   reused verbatim. A non-regular file maps to `changed`. `_targeted_parts`
   non-`ok` states do not fall through to `_read_snapshot_parts`.
9. A read spy proves one snapshot per artifact and no `sha256_file`/second
   content read for refresh targets.
10. A first artifact succeeds in memory, a later artifact fails; the manifest
    is byte-identical and no temporary file remains. (A dry run cannot detect
    a stale manifest; test that only on a real refresh.)
11. Bool, string/null, NaN, infinities, float32 `0.1` and a difference inside
    validation tolerance follow the comparator rule; no non-finite value is
    written.
12. Occurrence shift with an equal value still prints the exact positional
    warning.
13. Unchanged digest and size: no write, exact no-op output. Digest unchanged
    with wrong size: size-only repair, exact line. Digest changed with same
    size: digest-only update.
14. `--dry-run` is byte-identical and prints the same plan; no-op real prints
    `Verified N files; no changes.`, no-op dry prints
    `Would verify N files; no changes.`, and changed dry prints the existing
    `Dry run: nothing was written.`
15. Default rehash on a scalar locator still emits the unchanged
    `LOCATOR_REFRESH_UNSUPPORTED` text with one suffix.
16. Claims on a changed artifact print the existing claim warning; exact-output
    assertions confirm one `Nothing was written.` for every post-load refusal
    and none for usage errors.
17. Docs contract, parser hash rotation, full 3.12 and 3.10/pydantic 2.6
    suites; package smoke with the installed wheel.
## 8. Decisions in this version

1. **Scope:** schema 0.5 scalar locators only; opt-in; explicit targets only.
2. **Value policy:** verify, never rewrite; changed values require `result
   edit` first. This is the safety boundary.
3. **Identity:** update only artifact digest/size from one protected snapshot;
   all authored JSON is otherwise preserved.
4. **All-or-nothing:** preflight and complete every selected artifact before a
   single save; any failure writes nothing.
5. **Occurrence:** accept with an explicit positional warning; do not guess a
   new occurrence.

Open questions for review: whether `--refresh-locators` is the right name for a
value-preserving byte refresh, and whether the schema-0.5-only first slice is
too narrow for the real four-run board.

## 9. Non-goals

- No schema 0.6+ or non-scalar TensorBoard refresh.
- No automatic result-value replacement.
- No `--all`, directory refresh or volatile refresh.
- No new schema, route, adapter or writer.
- No SQLite or authority work.
