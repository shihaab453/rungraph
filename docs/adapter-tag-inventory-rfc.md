# Adapter tag inventory — RFC v0.1

Status: design draft for review. Read-only; no manifest, schema or writer change.

## 0. Changes in this version

Initial draft. Defines a read-only command that lists the tags an adapter can
select, with counts and step ranges, before `init` writes a manifest.

## 1. Problem and scope

TensorBoard adapters can import a board only after the user supplies `--tags`
patterns. Real training boards commonly hold tens of tags and tens of thousands
of scalar entries per run. Without a way to list the tags, a user must inspect
binary event files with an external tool or guess patterns until `init`
succeeds. The adapter's 10,000-result and 16 MiB tag limits are deliberate and
remain unchanged; the missing piece is discovery, not a larger budget.

This slice adds one read-only route and an optional adapter capability:

```text
runevidence adapter-tags PATH --adapter NAME [--match GLOB]... [--json]
```

It answers: which selectable tags exist, what kind is each, how many entries
would be selected, and the first/last recorded step. It does not create a
manifest, hash artifacts into a manifest, change a budget, or add a schema.

`TensorboardAdapter` is the first implementation. Adapters that do not opt in
are refused explicitly rather than returning an empty list.

## 2. Output model

One row per `(tag, kind)` pair:

| Field | Meaning |
| --- | --- |
| `tag` | Exact tag name, decoded as UTF-8. |
| `kind` | `scalar`, `string`, `histogram`, `image`, `audio`, `tensor`, `tf2-image`, `tf2-audio` or `tf2-histogram`. |
| `count` | Number of selectable summary entries for the pair. For TF2 media this is entries, not elements. |
| `first_step` | Lowest step among those entries. |
| `last_step` | Highest step among those entries. |

Rows are sorted by `(tag, kind)`. A tag with two kinds yields two rows. An
entry is counted with the same reader predicate that `init` uses for that kind;
unsupported or unrepresentable values are not counted.

Text output:

```text
Adapter: tensorboard
Path: /logs/study
Tags: 63 (68461 entries)
val-core/openai/gsm8k/acc/mean@1  scalar  4  820  928
...
```

JSON:

```json
{
  "format": "runevidence-adapter-tags-v1",
  "version": "0.1",
  "adapter": "tensorboard",
  "path": "/logs/study",
  "match": ["val-*"],
  "tags": [
    {
      "tag": "val-core/openai/gsm8k/acc/mean@1",
      "kind": "scalar",
      "count": 4,
      "first_step": 820,
      "last_step": 928
    }
  ],
  "skipped_unrepresentable": 0,
  "exit_code": 0
}
```

`match` is `null` when no filter was supplied. `skipped_unrepresentable` counts
tags that the strict reader could not represent as UTF-8 within the existing
4,096-byte tag limit; they cannot be passed to `--tags` and are not rows.

## 3. Reader profile and limits

- The command is read-only and never writes a manifest, backup or cache.
- Each event file is opened through the existing protected snapshot used by
  `init` (`_read_snapshot_parts`, 128 MiB per file cap).
- The inventory itself is bounded: at most 10,000 rows, 1,000,000 entries per
  file, 16 MiB of tag bytes per file and 2,000,000 entries total across the
  selected folder. A refusal names the exceeded bound and writes nothing.
- The same event-file predicate as verification and `init`
  (`_tensorboard_path_matches`) selects files. A folder with no event file is a
  refusal with exit `2`.
- A malformed or structurally refused event file refuses the whole listing
  with exit `2`; no partial rows are printed. This matches the adapter's
  all-or-nothing extraction.
- `--match` uses the existing `TagFilter` glob grammar. A malformed or too
  expensive pattern refuses with exit `2`. Patterns apply to the same tag
  bytes that `init --tags` would apply.
- Text output is line-oriented and may be long; `--json` is the stable machine
  form.

## 4. Identity and ordering

Tags are not manifest IDs. The row identity is `(exact tag bytes, kind)`.
Ordering is by decoded tag, then a fixed kind order matching the adapter's
capability order: scalar, string, histogram, image, audio, tensor, tf2-image,
tf2-audio, tf2-histogram.

Duplicate event files for the same tag are counted independently, matching
`init`'s occurrence model. The inventory does not choose a winning occurrence
or merge runs; it reports what the folder contains.

## 5. Adapter capability

Add to `src/runevidence/adapters/base.py`:

```python
@dataclass(frozen=True)
class TagCount:
    tag: str
    kind: str
    count: int
    first_step: int
    last_step: int

class Adapter:
    supports_tag_inventory: ClassVar[bool] = False

    def inventory_tags(
        self,
        root: Path,
        discovery: Discovery,
        *,
        match: TagFilter | None = None,
    ) -> list[TagCount]:
        raise AdapterError(
            f"adapter {self.name!r} does not support tag inventory; nothing was written"
        )
```

The default raises. `available_adapters()` and `get_adapter()` are unchanged.
`TensorboardAdapter` sets `supports_tag_inventory = True` and implements the
method with the existing event-file reader. No other adapter changes.

The CLI checks `supports_tag_inventory` before calling the method so an
installed third-party adapter that inherits the default cannot fail later with
an implementation-specific exception. The capability is additive; existing
adapters remain source-compatible.

## 6. CLI

New route, registered after `adapters`:

```text
runevidence adapter-tags PATH --adapter NAME [--match GLOB]... [--json]
```

- `PATH` is a folder, resolved the same way as `init`.
- `--adapter` is required; `runevidence adapters` lists valid names.
- `--match` is repeatable, as `init --tags` is.
- `--json` emits the format above; text is the default.
- Exit `0` when the listing succeeds (including zero rows after a filter),
  `2` on usage, unknown/unsupported adapter, load/read refusal, malformed
  filter or bound overflow.
- `--help` states that the route never writes and does not prove that a
  selected tag is relevant to a claim.

No `--force`, output file, dry-run or manifest argument exists on this route.

## 7. Refusals and fixed texts

| Condition | Exit | Text |
| --- | --- | --- |
| Unknown adapter | 2 | reuse `get_adapter`'s available-adapter message |
| Adapter lacks inventory | 2 | `adapter 'NAME' does not support tag inventory; nothing was written` |
| No event files | 2 | `no TensorBoard event files under PATH; nothing was written` |
| Malformed event file | 2 | the reader's existing structural message, plus `nothing was written` |
| Malformed/expensive match | 2 | the `TagFilter` reason, plus `nothing was written` |
| Row/entry/tag-byte bound | 2 | `tag inventory exceeds the N-row / N-entry / N-byte limit; nothing was written` |

A successful list with no rows after filtering is not a refusal: it prints the
header and `Tags: 0 (0 entries)` (or the JSON equivalent) with exit `0`.

## 8. Per-module admission checklist

- `src/runevidence/adapters/base.py`: `TagCount`, capability flag, default
  refusal; no change to `discover`/`extract` signatures.
- `packages/runevidence-tensorboard/.../adapter.py`: set the flag and implement
  `inventory_tags` using the protected snapshot, the event predicate and the
  existing summary iterators. Do not add a second event parser.
- `src/runevidence/cli.py`: parser route, text renderer, JSON document, fixed
  exit codes, no writer call. Recompute the CLI LF/CRLF pin.
- `src/runevidence/adapters/__init__.py`: no registry change required.
- `README.md`, `docs/tensorboard-guide.md`, `docs/adapters.md`: route table,
  example and the limits above.
- `tests/test_adapter_tags.py`: new focused suite.
- `tests/test_experimental_cli_parser_contract.py`: route addition and pin.
- `docs/qualification.md`: test count only after the suite lands.

## 9. Verification plan

1. Real event bytes with two runs and unequal steps: rows, counts, first/last
   steps, text and JSON.
2. Multiple kinds on one tag, sort order and duplicate-file counting.
3. `--match` selection, zero-row success, malformed/expensive filter refusal.
4. Empty folder, folder with non-event files, unknown adapter and an adapter
   without the capability.
5. Malformed/truncated/over-cap event file; assert no partial rows and no
   writes.
6. Protected-snapshot and path-containment tests mirroring the adapter's
   existing read tests.
7. CLI parser pin, docs-contract test, full suite on 3.12 and 3.10/pydantic
   2.6.
8. Packaging smoke: install core + TensorBoard wheel in a clean venv, run
   `adapter-tags` on a real generated logdir, then `init` with the returned
   tag patterns.

## 10. Decisions requested

1. Is `adapter-tags PATH --adapter NAME` the right public shape, or should this
   be a mode of `init` (for example `init --list-tags`)?
2. Is an optional adapter capability acceptable public plugin API, or should
   the first slice be TensorBoard-only and avoid changing `Adapter`?
3. Is `count = summary entries` the useful unit, or should count be distinct
   steps or runs?
4. Are the proposed row/entry/tag-byte bounds suitable, or should they reuse
   the existing 10,000-result and 16 MiB limits exactly?
5. Should unrepresentable tags be skipped with a count (as proposed), or should
   the listing refuse the whole folder?

## 11. Non-goals

- No larger import budget, sampling, latest-only ingestion or per-step
  selection.
- No manifest write, migration, schema version, locator kind or result value.
- No tag-to-result creation.
- No refresh of an existing manifest after logs change.
- No change to `init`, `validate`, `inspect` or artifact checking.
