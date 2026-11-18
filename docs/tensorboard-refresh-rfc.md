# TensorBoard scalar byte refresh — RFC v0.2

Status: design draft for review. New opt-in rehash behavior; no schema change.

## 0. Changes in this version

v0.2 narrows the slice to **schema 0.5 scalar TensorBoard locators** and
**explicit artifact targets**. The command verifies every recorded locator
against the new bytes and updates only the artifact digest and size. It never
rewrites a result value. A changed value must be recorded first with the
existing `result edit` path, then the byte refresh can verify and accept the
new file.

This removes schema-dispatch differences, `--all`, mixed plain files, automatic
value replacement, and the second-confirmation question. Default `rehash`
behavior and text remain unchanged.

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
mixed locator styles keep the existing refusal. This is not a generic refresh
protocol and has no `--all` mode.

## 2. Behavior

The flag is opt-in and requires one or more explicit artifact targets. Without
it, `rehash` behaves exactly as delivered, including the current refusal text.

For the selected set, preflight **before reading any event bytes**:

1. `--refresh-locators` with `--all`, `--volatile`, `--no-volatile` or
   `--directory` is refused with a fixed usage message.
2. Every selected artifact must:
   - exist in the manifest;
   - have `artifact_type == "file"`;
   - satisfy the shared TensorBoard event predicate;
   - be non-volatile, have a recorded `sha256`, and the manifest must
     declare schema 0.5;
   - have at least one result with a `TensorboardLocator`;
   - have **no** other locator style on that artifact (no string pointer, no
     other structured kind, no locator-less summary).
3. The manifest must pass the existing TensorBoard cap
   (`_tensorboard_cap`: 10,000 entries / 16 MiB tag metadata). A capped
   manifest is refused with the existing cap text.
4. Any failure refuses the whole edit before a read or mutation; nothing is
   written.

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
- collect claims that rest on the changed artifact for the existing rehash
  warning;
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
  `run:`, `glob:`, `folder:` and path selectors are refused. `--all` has a
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

All refresh-specific failures use `EditError`. `_edit_error` prints:

```text
ERROR: <message>
Nothing was written.
```

The exception message never contains `Nothing was written.`. The table below
lists the `<message>` only. `_locator_refresh_unsupported` is used only by the
default (no-flag) refusal and is not used by the opt-in path.

### 5.1 Flag and target preflight

| Condition | Message |
| --- | --- |
| no targets | `--refresh-locators needs at least one artifact target` |
| `--all` | `--refresh-locators requires explicit artifact targets; --all is not accepted` |
| `--volatile`, `--no-volatile` or `--directory` | `--refresh-locators cannot be combined with --volatile, --no-volatile or --directory` |
| `run:`, `glob:`, `folder:` or path selector | `--refresh-locators accepts only exact artifact IDs` |
| volatile target | `rehash cannot refresh 'ID': it is volatile` |
| directory target | `rehash cannot refresh 'ID': it is a directory artifact` |
| no `sha256` | `rehash cannot refresh 'ID': it has no recorded digest` |
| schema is not 0.5 | `rehash cannot refresh 'ID': the manifest is not schema 0.5` |
| path fails the event predicate | `rehash cannot refresh 'ID': the path is not a TensorBoard event file` |
| mixed locator styles | `rehash cannot refresh 'ID': the artifact mixes locator styles` |
| no scalar locator results | `rehash cannot refresh 'ID': the artifact has no TensorBoard scalar locators` |
| manifest over cap | `the manifest exceeds the tensorboard verification limit (10,000 entries or 16 MiB of tag metadata); no TensorBoard scalar was read` |

`ID` is the artifact ID in single quotes, matching the existing rehash text.
Preflight runs before any event read or `raw` mutation.

### 5.2 Snapshot states

The protected reader uses exactly:
`ok`, `over-cap`, `missing`, `unreadable`, `changed`, `symlinked-parent`,
`refused-path`, `refused-alias`, `escapes-root`.

Build the parts with `_targeted_parts(root, art.path)`. Map non-`ok` states to:

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
`Cannot refresh event bytes for 'ID': the event file does not satisfy the strict
TensorBoard profile`.

### 5.3 Scalar outcomes

`ScalarOutcome` has exactly `value`, `mismatch`, `unsupported-scalar` and
`unsupported-value`. Map a non-value outcome to:

| Outcome | Word |
| --- | --- |
| `mismatch` | `not found` (an absent `(tag, step)` and an out-of-range occurrence are indistinguishable) |
| `unsupported-scalar` | `unsupported scalar encoding` |
| `unsupported-value` | `unsupported value` |

Result refusal:

```text
Cannot refresh result 'ID': locator <display> is <word> in <path>.
```

A changed value:

```text
Cannot refresh result 'ID': recorded value does not match the current locator. Record the intended value with 'runevidence result edit' first.
```

A recorded bool, string or null:

```text
Cannot refresh result 'ID': the recorded value is not a TensorBoard scalar. Fix it with 'runevidence result edit' first.
```

### 5.4 Output and warnings

The positional warning is one `plan.notes` line per changed artifact:

```text
NOTE <artifact-id>: accepted new event bytes; recorded scalar locators are positional, so a duplicate insertion or deletion can retarget an occurrence even when its value is unchanged
```

`_rehash_under_lock` prints notes as `  NOTE <text>`.

Success output reuses the existing rehash lines and summary:

- digest update: `  <id>: sha256 <old> -> <new>` (plus `, N -> M bytes` when
  size changes);
- size-only repair: `  <id>: size <old> -> <new> bytes; sha256 unchanged`;
- no-op: `  <id>: verified; bytes and locators match`, with the existing
  `Re-recorded 0 files (N already up to date)` summary;
- dry run: existing `Dry run: nothing was written.`.

A dry run cannot detect a stale manifest (the check is inside `save_raw`, which
is not reached); only a real refresh can refuse stale bytes.

## 6. Module checklist

- `src/runevidence/edit.py`: add a preflight helper and a schema-0.5
  TensorBoard byte-refresh path. For refresh targets, skip the whole legacy
  `target.is_file()` / `_within` / `sha256_file` / `stat` block; create
  `FileChange` from the protected snapshot. Build parts with
  `_targeted_parts`, accept only `state == "ok"`, release `snapshot.data` before
  the next artifact, exclude `TensorboardLocator` results from
  `_reread_values`, and stage changes so `raw` is untouched until every
  selected artifact passes. End with `validate_manifest(raw)`.
- `src/runevidence/cli.py`: add the flag, flag/selector preflight **before** the
  `--directory` branch, help text, the two fixed usage messages and the
  refresh-specific no-target message.
- `tests/test_experimental_cli_parser_contract.py`: replace the current LF/CRLF
  `CLI_HASH` pair (archive the superseded pair in
  `HISTORICAL_26_ROUTE_CLI_HASH` if the historical policy requires it), keep
  `ROUTES` unchanged, and assert `refresh_locators` defaults to false in
  `test_edit_route_grammar_only`.
- `src/runevidence/checks/files.py`: no new reader; reuse
  `_read_snapshot_parts`. If a helper is added, import it lazily.
- `README.md`: flag, exact-ID selector, schema 0.5 scope, value-first rule.
- `docs/schema-0.5-tensorboard.md`: replace the unconditional rehash refusal
  paragraph with the opt-in rule. `docs/tensorboard-guide.md` and
  `docs/authoring-files.md`: add the rule beside rehash. Keep historical RFC
  documents frozen; a dated note may state that the parent refusal is
  superseded for schema 0.5 only.
- `tests/test_edit.py`, `tests/test_rehash_selectors.py` and
  `tests/test_cli_locator_0_3.py`: the vectors in section 7, exact selector
  grammar, one `Nothing was written.` per refusal.
- `docs/qualification.md`: test count and A7 evidence after the suite lands.

## 7. Verification plan

1. Write a schema 0.5 scalar event file, init, author a claim and conditions,
   append one valid scalar record; refresh exits 0, updates only
   `sha256`/`size_bytes`, preserves the claim/conditions, and `validate
   --strict` passes.
2. Assert the manifest diff is limited to those two artifact fields; no result
   value, name, tag, step, occurrence or result count changes.
3. Modify an existing scalar value without editing the result: refresh exits 2
   with the value-first message and leaves the manifest byte-identical. Run
   `result edit` to the new value, refresh again, and validate.
4. Delete/rename a recorded tag or step: refresh exits 2, manifest
   byte-identical.
5. A schema 0.6 scalar target and a 0.5 target with a `jsonl` or `csv-cell`
   mixed result are refused; a 0.6 `tensorboard-string` target is refused.
6. Mixed locator styles, locator-less results, volatile targets, directory
   targets, null-digest targets, `run:`/`glob:`/`folder:`/path selectors,
   `--all`, `--volatile`, `--no-volatile` and `--directory` are refused before
   any read, with the exact messages.
7. Manifest cap at 10,001 entries and the tag-byte boundary refuses with the
   exact text; a selected null-digest target is refused rather than counted.
8. Snapshot states `missing`, `unreadable`, `changed`, `symlinked-parent`,
   `refused-path`, `refused-alias`, `escapes-root`, `over-cap` and a malformed
   profile refuse with the exact texts. A non-regular file maps to `changed`.
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
14. `--dry-run` is byte-identical and prints the same plan.
15. Default rehash on a scalar locator still emits the unchanged
    `LOCATOR_REFRESH_UNSUPPORTED` text with one suffix.
16. Claims on a changed artifact print the existing claim warning; exact-output
    assertions confirm one `Nothing was written.` for every refusal.
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
