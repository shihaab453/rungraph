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
   - be non-volatile, and the manifest must declare schema 0.5;
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
updates only the size after the same verification. If an artifact was
non-volatile with `sha256: null`, that counts as a changed digest and is
recorded from the snapshot.

New tags/steps in the file are not added. Deleted tags/steps that a recorded
locator needs cause a refusal. A file that grows by appending records can
refresh because old locators still resolve. A changed value cannot be accepted
by this command; the user must run `result edit` first.

## 3. CLI

Add `--refresh-locators` to the existing `rehash` parser, with help text that
limits it to schema 0.5 TensorBoard scalar locators and says values must
already match.

Flag rules:

- Requires explicit artifact targets; `--all` is refused.
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

## 5. Refusals

| Condition | Exit | Text |
| --- | --- | --- |
| No `--refresh-locators` on a structured locator | 2 | existing `LOCATOR_REFRESH_UNSUPPORTED` text, unchanged |
| `--refresh-locators` with `--all` | 2 | `ERROR: --refresh-locators requires explicit artifact targets; --all is not accepted. Nothing was written.` |
| `--refresh-locators` with `--volatile`/`--no-volatile`/`--directory` | 2 | `ERROR: --refresh-locators cannot be combined with --volatile, --no-volatile or --directory. Nothing was written.` |
| Selected artifact is volatile, a directory, not a file or not schema 0.5 | 2 | `ERROR (LOCATOR_REFRESH_UNSUPPORTED): rehash cannot refresh <artifact>: <reason>. Nothing was written.` |
| Event path fails the TensorBoard predicate | 2 | same shape, reason `the path is not a TensorBoard event file` |
| Artifact mixes scalar locators with a string/other locator or a locator-less summary | 2 | same shape, reason `the artifact mixes locator styles` |
| No scalar locator results on the selected artifact | 2 | same shape, reason `the artifact has no TensorBoard scalar locators` |
| Manifest exceeds the TensorBoard cap | 2 | existing `TENSORBOARD_CAP_MESSAGE` text |
| Event file missing/unreadable/not-file/outside-root | 2 | `ERROR: Cannot refresh event bytes for <artifact>: <state>. Nothing was written.` |
| Event file changed during read | 2 | `ERROR: Cannot refresh event bytes for <artifact>: the event file changed during the read. Nothing was written.` |
| Event file over 128 MiB | 2 | existing `OVER_CAP_MESSAGE` |
| Event file fails the strict profile | 2 | existing `PROFILE_MESSAGE` |
| Locator resolves to a non-value outcome | 2 | `ERROR: Cannot refresh result <id>: locator <display> is <not found|unsupported> in <path>. Nothing was written.` |
| Recorded value changed | 2 | `ERROR: Cannot refresh result <id>: recorded value does not match the current locator. Record the intended value with 'runevidence result edit' first. Nothing was written.` |
| Recorded value is bool/string/null | 2 | `ERROR: Cannot refresh result <id>: the recorded value is not a TensorBoard scalar. Fix it with 'runevidence result edit' first. Nothing was written.` |

`<not found|unsupported>` maps directly to the `ScalarOutcome` words; the
reader does not distinguish an absent `(tag, step)` from an out-of-range
occurrence, so the text uses the reader's `not found`/`unsupported` vocabulary
rather than inventing a distinction. `Nothing was written.` is appended exactly once by
the CLI; exception messages do not include it.

The default refusal text for any structured locator without the flag is
unchanged, including its existing wording.

## 6. Module checklist

- `src/runevidence/edit.py`: add a preflight helper and a schema-0.5
  TensorBoard byte-refresh path. For refresh targets, bypass
  `sha256_file` + `stat`; create `FileChange` from the protected snapshot.
  Refuse mixed locator styles and exclude `TensorboardLocator` results from the
  legacy `_reread_values` path. Do not mutate `raw` until every selected
  artifact has passed the reads and locator checks.
- `src/runevidence/cli.py`: add the flag, preflight flag combinations **before**
  the `--directory` branch, help text and fixed refusals. Recompute the LF/CRLF
  `CLI_HASH`; keep `ROUTES` unchanged.
- `src/runevidence/checks/files.py`: no new reader; reuse
  `_read_snapshot_parts`. If a small helper is added, import it lazily.
- `README.md`: flag, scope, explicit-target and value-first rules.
- `docs/schema-0.5-tensorboard.md`, `docs/tensorboard-guide.md`,
  `docs/authoring-files.md`: replace unconditional refusal wording with the
  opt-in scalar byte-refresh rule; keep historical RFC documents frozen.
- `tests/test_edit.py` or a new focused test: single-snapshot proof, changed
  value refusal, missing locator, mixed locator, volatile/directory/`--all`,
  schema 0.6 refusal, cap, dry-run, stale manifest, no partial write.
- `tests/test_cli_locator_0_3.py` / parser contract: flag grammar, exact
  output, LF/CRLF rotation, no route change.
- `docs/qualification.md`: test count and A7 evidence after the suite lands.

## 7. Verification plan

1. Write a schema 0.5 scalar event file, init, author a claim and conditions,
   append one valid scalar record; refresh exits 0, updates only
   `sha256`/`size_bytes`, preserves the claim/conditions, and `validate
   --strict` passes.
2. Assert the main main diff is limited to those two artifact fields; no result
   value, name, tag, step, occurrence or result count changes.
3. Modify an existing scalar value without editing the result: refresh exits 2
   with the value-first message and leaves the manifest byte-identical. Run
   `result edit` to the new value, refresh again, and validate.
4. Delete/rename a recorded tag or step: refresh exits 2, manifest
   byte-identical.
5. A schema 0.6+ scalar locator target and a schema 0.5 target with a
   `tensorboard-string` result are refused.
6. Mixed locator styles, locator-less summaries, volatile targets, directory
   targets, `--all`, `--volatile`, `--no-volatile` and `--directory` are
   refused before any read.
7. Manifest cap at 10,001 entries and tag-byte boundary refuses with the
   existing text.
8. Snapshot states `missing`, `not-file`, `outside-root`, `unreadable`,
   `changed`, `over-cap` and a malformed profile refuse with exact texts.
9. A read spy proves one snapshot per artifact and no `sha256_file`/second
   content read for refresh targets.
10. A first artifact succeeds in memory, a later artifact fails; the manifest
    is byte-identical and no temporary file remains.
11. Bool, string/null, NaN, infinities, float32 `0.1` and a difference inside
    validation tolerance follow the comparator rule; no non-finite value is
    written.
12. Occurrence shift with an equal value still prints the positional warning.
13. `--dry-run` is byte-identical and prints the same plan.
14. Default rehash on a scalar locator still emits the unchanged
    `LOCATOR_REFRESH_UNSUPPORTED` text.
15. Docs contract, parser hash rotation, full 3.12 and 3.10/pydantic 2.6
    suites.

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
