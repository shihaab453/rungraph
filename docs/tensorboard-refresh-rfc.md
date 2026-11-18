# TensorBoard scalar locator refresh — RFC v0.1

Status: design draft for review. New opt-in rehash behavior; no schema change.

## 0. Changes in this version

Initial draft. Adds an explicit `--refresh-locators` mode to `rehash` for
recorded TensorBoard scalar locators. The default `rehash` refusal for every
structured locator is unchanged.

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
runevidence rehash PATH --all --refresh-locators [--dry-run]
```

It re-reads **every recorded scalar locator on each selected artifact** from
the current event bytes, updates the artifact digest/size, and updates recorded
values that changed. It does not add or remove results, runs, artifacts, tags or
steps. It does not change conditions, claims, relationships, metadata or IDs.
An intentional edit or append is accepted only when every recorded locator still
resolves to a supported scalar.

Other structured locator kinds (string, histogram, image, audio, tensor, TF2
media/histogram) and other adapters keep the existing refusal. This is not a
generic refresh protocol.

## 2. Behavior

The flag is opt-in. Without it, `rehash` behaves exactly as delivered, including
the current refusal text.

For each selected artifact:

1. If the artifact has no structured-locator results, use the existing re-record
   path unchanged.
2. If every structured locator on it is a `TensorboardLocator` (`kind:
   "tensorboard"`), use the refresh path below.
3. If any structured locator is another kind, refuse the whole edit with
   `LOCATOR_REFRESH_UNSUPPORTED`; nothing is written.

Mixed selections are all-or-nothing. If one selected artifact is unsupported,
no artifact in the edit is changed.

The refresh path:

- reads the event bytes once through the existing protected snapshot
  (`TENSORBOARD_MAX_FILE_BYTES`, 128 MiB) and uses that read for both the digest
  and parsing;
- refuses an over-cap, unreadable, changed-during-read or non-event file;
- builds one `scan_scalars` request per recorded result from its exact tag
  UTF-8 bytes, signed step and occurrence;
- requires every request to return `ScalarOutcome.value`; a missing, mismatched,
  unsupported-scalar or unsupported-value outcome refuses the whole edit with a
  result-specific message and nothing written;
- updates the artifact's `sha256` and `size_bytes` from the same snapshot;
- if the digest changed and a returned value differs from the recorded value,
  updates the raw result `value` and reports the change; if the digest is
  unchanged, values are not rewritten (a mismatch is left for `validate`, as
  today);
- preserves result IDs, names, tags, steps, occurrences, run IDs, conditions,
  claims, relationships and metadata byte-for-byte except for the value fields
  and artifact digest/size;
- reports claims that rest on a changed value, using the existing rehash claim
  warning;
- writes nothing on any refusal, including a later artifact failure.

New tags/steps in the file are not added to the manifest. Deleted tags/steps are
not removed. A file that grows by appending a record can refresh because the old
locators still resolve; a file that removes a recorded locator refuses.

`occurrence` is positional among supported scalar entries with the same exact
`(tag, step)`. If a changed file inserts or removes a duplicate entry before a
recorded occurrence, the same occurrence can name a different entry. The
refresh contract is "the current value at this exact locator", not "the
original semantic entry." The changed-value warning and the claim-review line
make that visible; the command does not guess a new occurrence.

## 3. CLI

Add `--refresh-locators` to the existing `rehash` parser.

- Applies only to `rehash`, not `add` or directory rehash.
- `--refresh-locators` with `--volatile` or `--no-volatile` is refused: a
  volatile artifact has no digest and values are existence-only.
- `--refresh-locators` with `--directory` is refused; directory artifacts have
  no scalar locators.
- `--all` is allowed, subject to all-or-nothing support.
- `--dry-run` prints the same digest and value plan and writes nothing.
- The writer lock, stale-byte check, backup behavior and atomic save are the
  existing rehash paths.

Exit codes:

- `0` refresh succeeded, or dry run printed the plan;
- `2` unsupported target, missing/mismatched locator, read/profile refusal,
  stale manifest, writer busy/lock unavailable, or bad flag combination.

## 4. Reader and identity

Only `TensorboardLocator` objects are accepted. The artifact path must satisfy
the shared TensorBoard event predicate. The tag must already satisfy the model's
UTF-8 and 4,096-byte bounds; it is encoded back to exact bytes for the request.

The request key is `(tag_bytes, step, occurrence)`, exactly the locator. The
`scan_scalars` reader is the same parser used by verification; no second parser
is introduced. A scalar from a `float32` source updates the JSON value to the
same finite number the reader returns; `validate` remains the authority on the
final representation.

The snapshot digest and size are the artifact's new recorded identity. No
artifact path, ID, run or result identity changes.

## 5. Refusals

| Condition | Exit | Text shape |
| --- | --- | --- |
| No `--refresh-locators` on a structured locator | 2 | existing `LOCATOR_REFRESH_UNSUPPORTED` text, unchanged |
| Non-scalar or non-TensorBoard structured locator | 2 | `ERROR (LOCATOR_REFRESH_UNSUPPORTED): rehash cannot refresh <artifact>: <reason>. Nothing was written.` |
| Event file missing/unreadable/over-cap/profile | 2 | existing reader message; nothing written |
| Recorded locator missing or mismatched | 2 | `ERROR: Cannot refresh result <id>: locator <display> is <missing|mismatched|unsupported> in <path>. Nothing was written.` |
| Changed value with claims | 0 | writes and prints the existing rehash claim warning |
| `--volatile`/`--no-volatile`/`--directory` combination | 2 | fixed usage text; nothing written |

The existing read-only versus writer behavior, stale-byte refusal and lock
semantics are unchanged.

## 6. Module checklist

- `src/runevidence/edit.py`: plan and apply the scalar refresh in
  `plan_rehash`; add a TensorBoard result refresh helper that uses
  `scan_scalars`; keep the default blocked branch. Import the protected read
  and `TensorboardLocator` only where needed.
- `src/runevidence/cli.py`: add the flag, validation combinations, help text,
  read-only route docstring if needed, and the existing rehash output paths.
  Recompute the CLI LF/CRLF pin.
- `README.md`, `docs/tensorboard-guide.md`, `docs/authoring-files.md`: flag,
  scope, append/edit example, occurrence caveat, no new tag/step addition.
- `tests/test_edit.py`, `tests/test_cli_locator_0_3.py` or a new focused test:
  default refusal unchanged; scalar refresh cases; mixed refusals; dry-run;
  stale bytes; no write.
- `tests/test_experimental_cli_parser_contract.py`: flag/help hash and route
  pin if the parser source changes; no route addition.
- `docs/qualification.md`: test count after the suite lands.

## 7. Verification plan

1. Write a scalar event file, init, author a claim and conditions, append one
   valid scalar record; `rehash --refresh-locators` exits 0, updates digest and
   size, preserves the claim/conditions, and `validate --strict` passes.
2. Modify an existing scalar value; refresh updates the result value, prints the
   claim warning, and `validate` passes. Restore the old value and refresh
   again.
3. Delete/rename a recorded tag or step; refresh exits 2 with the result-specific
   message and leaves the manifest byte-identical.
4. A schema 0.6+ manifest with a `tensorboard-string` locator on the target is
   refused even when its scalar locators would resolve.
5. Mixed targets: one scalar-refreshable artifact and one unsupported artifact
   refuse the whole edit.
6. `--all` with any unsupported artifact refuses.
7. `--dry-run` prints the plan and writes nothing; `--volatile`,
   `--no-volatile`, `--directory` combinations refuse.
8. Stale manifest bytes, writer busy/lock unavailable, missing file, over-cap
   and malformed event file refuse with the existing texts.
9. Default `rehash` on a scalar locator still emits the unchanged
   `LOCATOR_REFRESH_UNSUPPORTED` refusal.
10. MLflow/locator-less rehash and all existing positive-cost vectors remain
    unchanged.
11. Full suite on 3.12 and 3.10/pydantic 2.6; docs contract and parser pin.
12. Real four-run TensorBoard board: append a valid record to one run, refresh
    the artifact, validate; then restore the original file and refresh again.

## 8. Decisions requested

1. Is `--refresh-locators` the right opt-in name and scope, or should scalar
   TensorBoard refresh become the default `rehash` behavior?
2. Is all-or-nothing across selected targets the right refusal policy?
3. Should changed values be updated automatically (with a claims warning), or
   require a second explicit confirmation flag?
4. Is the occurrence-position limitation acceptable with a warning, or should
   occurrence > 0 refuse after a digest change?
5. Should `--all` be allowed with this flag, or require explicit artifacts?

## 9. Non-goals

- No new schema, locator kind, adapter, route or writer.
- No adding or removing results, tags, steps, runs or artifacts.
- No non-scalar TensorBoard refresh.
- No automatic claim rewriting.
- No change to default rehash behavior or refusal text.
- No SQLite or authority work.
