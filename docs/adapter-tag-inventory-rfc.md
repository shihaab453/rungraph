# Adapter tag inventory — RFC v0.2

Status: design draft for review. Read-only; no manifest, schema or writer change.

## 0. Changes in this version

v0.2 narrows the first slice to **TensorBoard scalar tags only**, matching the
default `init` profile. It removes the multi-kind/TF2 alternatives that made v0.1
ambiguous. It also adds a bounded inventory read, path admission, exact skip and
budget accounting, escaped text, copyable literal patterns, a JSON error
envelope, preflight ordering, and the missing route/pin work.

## 1. Problem and scope

TensorBoard adapters can import a board only after the user supplies `--tags`
patterns. A default `init` selects scalars; non-scalar kinds require explicit
`--include-*` flags. Real scalar boards commonly hold tens of tags and tens of
thousands of entries per run. Without a way to list those tags, a user must
inspect binary event files externally or guess patterns until `init` succeeds.
The 10,000-result and 16 MiB tag limits are deliberate; the missing piece is
discovery, not a larger import budget.

This slice adds one read-only route:

```text
runevidence adapter-tags PATH --adapter NAME [--match GLOB]... [--json]
```

It inventories the tags that the adapter's **default scalar profile** can
select. `PATH` is required. It does not create a manifest, hash into a
manifest, change a budget, add a schema, or list non-scalar kinds. A later slice
may add profiles for other kinds.

`TensorboardAdapter` is the first implementation. An adapter without the
private capability is refused explicitly. The capability is not added to the
public `Adapter` base class in this slice.

## 2. Output model

One row per scalar tag:

| Field | Meaning |
| --- | --- |
| `tag` | Exact tag string. |
| `kind` | `scalar`; the only value in this slice. |
| `count` | Number of supported scalar entries for the tag, including repeated steps and duplicate event files. For this profile one entry is one prospective `init` result. |
| `first_step` | `min(step)` across counted entries; steps are signed and may be non-monotonic. |
| `last_step` | `max(step)` across counted entries. |
| `pattern` | A literal `--tags` pattern with `\`, `*` and `?` escaped, when the tag is at most 1,024 code points; otherwise `null`. |

Rows are sorted by `tag`; scalar-only output has no kind-order ambiguity.
`count` is an entry count, not a distinct-step count.

Tag fields are exact in JSON. Text output encodes each tag with
`json.dumps(tag, ensure_ascii=True)` so newlines, tabs, carriage returns,
escapes and non-ASCII cannot break the line-oriented table or forge a header.
Text renders `pattern` with control characters escaped but leaves the glob
escapes `\`, `*` and `?` intact, so a newline cannot break the row and a
copyable literal pattern keeps its meaning. Paths are shown as the resolved
absolute path; JSON is exact.

Successful JSON:

```json
{
  "format": "runevidence-adapter-tags-v1",
  "version": "0.1",
  "adapter": "tensorboard",
  "path": "/logs/study",
  "profile": "scalar",
  "match": ["val-*"],
  "tags": [
    {
      "tag": "val-core/openai/gsm8k/acc/mean@1",
      "kind": "scalar",
      "count": 4,
      "first_step": 820,
      "last_step": 928,
      "pattern": "val-core/openai/gsm8k/acc/mean@1"
    }
  ],
  "totals": {
    "tags": 1,
    "entries": 4,
    "selected_results": 4,
    "selected_tag_bytes": 128,
    "retained_tag_bytes": 32
  },
  "skipped_unrepresentable_tags": 0,
  "skipped_entries": 0,
  "would_refuse_unfiltered_init": false,
  "would_exceed_result_budget": false,
  "would_exceed_tag_budget": false,
  "unmatched_patterns": [],
  "warnings": [],
  "exit_code": 0
}
```

Definitions:

- `totals.entries`: sum of `count`.
- `totals.selected_results`: for this scalar profile, the same as `entries`; it
  is what `init --tags` would attempt to record.
- `totals.selected_tag_bytes`: sum over entries of the tag's UTF-8 byte length,
  matching `init`'s per-result tag charge.
- `totals.retained_tag_bytes`: sum of distinct retained tag byte lengths.
- `would_exceed_result_budget`: `selected_results > 10,000`.
- `would_exceed_tag_budget`: `selected_tag_bytes > 16 MiB`.
- `skipped_unrepresentable_tags`: distinct raw tag byte strings that are empty,
  invalid UTF-8, or longer than 4,096 bytes. They cannot be selected by
  `--tags`.
- `skipped_entries`: supported scalar entries under those skipped tags. They are
  charged to scan limits.
- `would_refuse_unfiltered_init`: true when any scalar tag was skipped, because
  unfiltered `init` refuses such a tag. The route still skips it so discovery
  can continue, and the text output warns.
- `unmatched_patterns`: patterns that matched no representable scalar tag.
- `warnings`: fixed, human-readable strings derived from the booleans above.

Text output:

```text
Adapter: tensorboard
Path: /logs/study
Profile: scalar
Match: "val-*" (1 pattern)
Scalar tags: 1 (4 entries, 1 row)
WARN: selected tags exceed init's 10,000-result budget
TAG                          COUNT  FIRST  LAST  PATTERN
"val-core/openai/gsm8k/acc/mean@1"  4    820   928  val-core/openai/gsm8k/acc/mean@1
Recorded data only. No manifest was written; claim relevance is not assessed.
```

`Scalar tags` counts rows (one per tag in this profile). A tag with `pattern:
null` prints `-` and a warning that it cannot be selected exactly by `--tags`
because of the 1,024-code-point pattern cap.

## 3. Reader profile and limits

The command is read-only and never writes a manifest, backup, lock or cache.

**Path admission.** `discover` supplies candidate paths. The inventory selects
event files with the shared `_tensorboard_path_matches` predicate, then calls
the adapter's existing `_refuse_paths(paths, event_paths)` **before any read**.
This refuses surrogate paths and, on POSIX, literal backslash event paths that
the descriptor walk would otherwise normalize to a different file.

**Bounded inventory read.** The existing protected snapshot hashes oversized
files to EOF; its 128 MiB cap bounds retained memory, not bytes read. The
inventory therefore uses a separate bounded read that:

- opens through the protected descriptor walk (or the documented path fallback
  where descriptor support is unavailable, with the same weaker parent-swap
  caveat as `init`, and no stronger claim);
- takes the file size from the opened descriptor and refuses a file above
  128 MiB before allocating or parsing;
- reads at most 128 MiB plus one byte; if the file grows past the cap, refuses
  `OVER_CAP` after verifying identity;
- does not hash and does not retain values beyond the bytes needed for parsing;
- charges aggregate read bytes and refuses above 512 MiB or 1,024 event files.

**Scan and retained limits.**

| Limit | Stage | Value |
| --- | --- | --- |
| Event files | pre-scan | 1,024 |
| Bytes per file | pre-read | 128 MiB |
| Bytes total | pre-read | 512 MiB |
| Entries per file | scan | 1,000,000 |
| Entries total | scan | 2,000,000 |
| Decoded tag bytes scanned | scan work | 64 MiB |
| Rows after `--match` | retained | 10,000 |
| Distinct tag bytes after `--match` | retained | 16 MiB |

The 1,000,000/2,000,000 entry caps are scan caps, not the 10,000-result import
budget. The 10,000-row and 16 MiB retained caps bound output and memory after
filtering. A `--match` that selects a small subset may therefore inspect a board
with more than 10,000 total tags. The selected-result and selected-tag-byte
totals still warn when the user's `init --tags` would exceed its own budget.

Decoded tag bytes scanned means the sum over scanned supported scalar entries of
the tag's UTF-8 length; a skipped unrepresentable tag charges its raw byte
length. Retained tag bytes are charged once per distinct row after `--match`.

A malformed or structurally refused event file refuses the whole listing with
exit `2`; no partial rows or success header are printed. A later corrupt file
must not be masked by an earlier successful file. The adapter's existing
failure precedence is preserved: profile/over-cap, then invalid/expensive
pattern, then scan bound.

A folder with no event files is exit `2`, even if it contains other files. An
empty folder uses the existing "not a folder"/"no event files" distinction:
`PATH` must resolve to a directory.

## 4. Identity and ordering

A row identity is the exact decoded tag. Scalar-only output has one row per
tag. Duplicate event files and repeated steps increase `count`; the inventory
does not merge runs, choose a winning occurrence or deduplicate steps. Step
endpoints are numeric `min`/`max`, not encounter order.

`pattern` is a literal pattern for `init --tags`:

- escape `\`, `*` and `?` with a backslash;
- return `null` when the tag has more than 1,024 code points, because the
  pattern grammar cannot express it exactly;
- leave all other characters unchanged; JSON holds the exact pattern string.

Text displays a JSON-escaped tag and a control-escaped pattern; JSON holds the
exact tag and pattern strings. Users should quote patterns in the shell. A
pattern is a union element: multiple `--match` values are ORed, matching
`init --tags`.

## 5. Adapter interface

No change to `Adapter` or `runevidence.adapters` exports in this slice.
`TensorboardAdapter` adds a private, duck-typed method:

```python
def inventory_scalar_tags(
    self,
    root: Path,
    discovery: Discovery,
    *,
    match: TagFilter | None = None,
) -> dict[str, Any]:
    ...
```

The method returns only built-in containers:

```python
{
    "tags": [
        {"tag": str, "kind": "scalar", "count": int,
         "first_step": int, "last_step": int, "pattern": str | None}
    ],
    "skipped_unrepresentable_tags": int,
    "skipped_entries": int,
}
```

The CLI:

1. resolves `PATH` and requires a directory;
2. gets the adapter by name;
3. checks that `inventory_scalar_tags` exists, is callable, and accepts the
   `match` keyword; otherwise refuses before discovery;
4. compiles the `TagFilter` before discovery;
5. calls `discover`, then `inventory_scalar_tags`;
6. validates the returned row shape, kind, counts, step order and pattern type,
   refusing a malformed adapter result as `INVALID_INVENTORY`.

No new core type is imported by the optional package, so an adapter wheel can
still load with an older core; an older core simply does not offer the route.
The method is not a base-class promise until a second adapter needs it. It is
documented in `docs/adapters.md` as a TensorBoard extension, not as a generic
plugin API.

## 6. CLI

New route, registered after `adapters`:

```text
runevidence adapter-tags PATH --adapter NAME [--match GLOB]... [--json]
```

- `PATH` is required and resolved like `init`; it must be a directory.
- `--adapter` is required; `runevidence adapters` lists valid names.
- `--match` is repeatable and unioned, using the same `TagFilter` grammar and
  caps as `init --tags`.
- `--json` emits the success document or the fixed error envelope.
- Exit `0` on a successful listing, including zero rows after a match; exit `2`
  on usage, unknown/unsupported adapter, invalid/expensive pattern, missing
  path, no event files, read/profile refusal, malformed adapter result, or
  bound overflow.
- No output file, force, dry-run or manifest argument.
- `--help` says the route is read-only, lists scalar tags only, never writes a
  manifest, and does not assess claim relevance or scientific validity.

### JSON error envelope

```json
{
  "format": "runevidence-adapter-tags-error-v1",
  "version": "0.1",
  "ok": false,
  "code": "NO_EVENT_FILES",
  "error": "no TensorBoard event files under /logs; nothing was written",
  "exit_code": 2
}
```

The closed codes are `USAGE`, `UNKNOWN_ADAPTER`, `UNSUPPORTED_ADAPTER`,
`NOT_A_DIRECTORY`, `NO_EVENT_FILES`, `INVALID_MATCH`, `EXPENSIVE_MATCH`,
`READ_FAILED`, `OVER_CAP`, `MALFORMED_EVENT`, `SCAN_LIMIT`, `ROW_LIMIT`,
`TAG_BYTE_LIMIT`, `INVALID_INVENTORY` and `INTERNAL_ERROR`. Text mode prints
`ERROR: <error>` once; it prints no partial rows or success header. Errors go to
stdout for consistency with `init`; JSON mode prints exactly one error object to
stdout and nothing else.

`adapter-tags` gets a dedicated `_adapter_tags_main` entry (like the existing
special-command handlers) so a usage error can still honor `--json`: the
wrapper scans for `--json` before parsing, converts argparse `SystemExit` into
the `USAGE` envelope when it is present, and otherwise leaves the ordinary text
usage. It calls `build_parser` only for the `adapter-tags` subparser and does not
enter `cmd_init` or its writer lock.

## 7. Refusals and fixed texts

| Condition | Exit | Text |
| --- | --- | --- |
| `PATH` not a directory | 2 | `ERROR: PATH is not a folder; nothing was written` |
| Unknown adapter | 2 | `ERROR: <get_adapter message>; nothing was written` |
| Adapter lacks capability | 2 | `ERROR: adapter 'NAME' does not support scalar tag inventory; nothing was written` |
| No event files | 2 | `ERROR: no TensorBoard event files under PATH; nothing was written` |
| Invalid `--match` | 2 | `ERROR: invalid --match pattern (<TagFilter reason>); nothing was written` |
| Expensive `--match` | 2 | `ERROR: --match pattern is too expensive; nothing was written` |
| Read/profile refusal | 2 | `ERROR: <reader message>; nothing was written` |
| Over-cap file | 2 | `ERROR: <OVER_CAP message>; nothing was written` |
| Malformed event file | 2 | `ERROR: <PROFILE message>; nothing was written` |
| Scan limit | 2 | `ERROR: scalar tag inventory exceeds the <limit>; nothing was written` |
| Row limit | 2 | `ERROR: scalar tag inventory exceeds the 10,000-row limit; nothing was written` |
| Retained tag bytes | 2 | `ERROR: scalar tag inventory exceeds the 16 MiB retained-tag-byte limit; nothing was written` |
| Invalid adapter result | 2 | `ERROR: adapter returned an invalid scalar tag inventory; nothing was written` |

A successful listing with zero rows after a filter is exit `0`. It prints the
header, `Scalar tags: 0 (0 entries, 0 rows)`, any unmatched-pattern warning and
the read-only note. It is not treated as an empty folder.

## 8. Per-module admission checklist

- `src/runevidence/checks/files.py`: add the inventory-only bounded read (or a
  reviewed helper) that refuses oversized files from descriptor metadata,
  caps bytes read, verifies identity and does not hash. Add focused tests.
- `packages/runevidence-tensorboard/.../adapter.py`: implement
  `inventory_scalar_tags` with the protected bounded read, `_refuse_paths`,
  `iter_supported_scalars`, min/max steps, distinct skipped tags and entries;
  no second event parser and no `_Budget` (which would stop at 10,000).
- `src/runevidence/cli.py`: parser route, `_adapter_tags_main` usage/JSON
  wrapper, preflight order, `TagFilter`, text renderer with JSON-escaped tags
  and control-escaped patterns, JSON success/error documents, exit codes; add
  `adapter-tags` to the read-only route docstring. Recompute the LF and CRLF
  `CLI_HASH` pin.
- `src/runevidence/adapters/__init__.py`: no registry change; do not export a
  new type.
- `README.md`: route table, `--match`, machine-readable and read-only lists.
- `docs/tensorboard-guide.md`, `docs/adapters.md`: scalar-only profile,
  pattern escaping, caps, skip/budget warnings, no scientific claim.
- `tests/test_adapter_tags.py`: new focused suite.
- `tests/test_experimental_cli_parser_contract.py`: add the route to `ROUTES`,
  historical-route projections, `len(ROUTES)`, and the LF/CRLF pin.
- `tests/test_experimental_staged_reader.py`: update the explicit route set and
  reader pin if its assertion includes the new route.
- `tests/test_docs_contract.py`: every route and `--match` must be named in
  `README.md`.
- `tests/test_cli_writer_admission.py`: update the subcommand count if it
  enumerates routes.
- `docs/qualification.md`: test count only after the suite lands.

## 9. Verification plan

1. **Exact scalar example.** One file with steps `[9, -3, 9]` and another with
   `[4, 2]` for one tag yields `count = 5`, `first_step = -3`, `last_step = 9`.
   Copying the first file yields `count = 8`.
2. **Repeated steps and duplicate files** increase count; distinct steps do not
   replace entry counts.
3. **Delta step** with another tag proves the totals are per tag, not global.
4. **Text escaping.** Tags containing `\n`, `\r`, `\t`, `\x1b`, a quote and
   non-ASCII render as one JSON-escaped line; JSON holds the exact tag.
5. **Pattern escaping.** Tags `a*b`, `a?b`, `a\b`, a leading-hyphen tag and a
   1,025-code-point tag: `pattern` is escaped or `null`; `init --tags` with a
   returned pattern selects the literal tag or is documented as not exact.
6. **Skip accounting.** Invalid UTF-8, empty and over-4,096-byte tags increment
   distinct skipped tags and skipped entries; `would_refuse_unfiltered_init`
   is true; text and JSON both warn.
7. **Filter behavior.** Multiple `--match` values are ORed; a typo produces
   `unmatched_patterns` and a warning but exit `0`; an invalid or expensive
   pattern exits `2` with the fixed text and JSON code.
8. **No partial output.** Valid rows followed by corruption in the same file and
   in a later file produce exit `2` and no header or rows. Corruption in a file
   excluded by `--match` still refuses the listing.
9. **Bounds.** Under/at/over each scan and retained cap; a 10,001-row board and
   a >10,000-entry tag; refusal writes nothing.
10. **Read path.** Oversized file, file growth past the cap, short reads,
    path/backslash/surrogate refusals, escaping links, and the no-descriptor
    fallback caveat.
11. **No writes.** Static AST test that the handler does not reference
    `_writer_lock`, `save_manifest`, `_backup_existing`, `_mark_volatile` or a
    write-mode open; a pre-existing manifest and backups remain byte-identical.
12. **Adapter preflight.** Unknown adapter, adapter without the method and an
    adapter whose `discover` counter must remain zero all exit before discovery.
13. **JSON errors.** One object with the closed code for every row in section 7;
    no text or partial success on stdout; text mode prints exactly one ERROR.
14. **Pins/contracts.** `ROUTES`, historical route projections, reader route
    set, CLI LF/CRLF pin, docs contract and the subcommand count.
15. **Packaging smoke.** Clean install of core + TensorBoard wheel, list tags on
    a real generated logdir, feed a returned pattern to `init --tags`, validate.
16. **Full suites** on 3.12 and 3.10/pydantic 2.6, plus focused tests with
    capped commands.

## 10. Decisions in this version

1. **Route:** separate `adapter-tags`, not an `init` mode; `PATH` required.
2. **Scope:** TensorBoard-only, scalar-only first slice; no `Adapter` base API.
3. **Count:** one row per tag, `count` = scalar entries = prospective results;
   `first_step`/`last_step` are min/max.
4. **Limits:** scan caps above plus a 10,000-row/16 MiB retained cap; 16 MiB
   charged per distinct retained row, not per entry; selected-result/tag-byte
   warnings describe what `init` would do.
5. **Skips:** skip unrepresentable tags with distinct-tag and entry counts;
   warn that unfiltered `init` would refuse; structural corruption still
   refuses the whole listing.

Open questions for review: whether 64 MiB scanned tag bytes and 512 MiB total
read are the right ceilings for the measured logs, and whether the text table
should print the escaped tag or its exact JSON string in a separate column.

## 11. Non-goals

- No non-scalar kinds, `--include-*` profiles, TF2 media or TF2 histograms.
- No larger import budget, sampling, latest-only ingestion or per-step
  selection.
- No manifest write, migration, schema version, locator kind or result value.
- No tag-to-result creation.
- No refresh of an existing manifest after logs change.
- No change to `init`, `validate`, `inspect` or artifact checking.
