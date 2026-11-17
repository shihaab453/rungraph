# Schema 0.5 TensorBoard scalar locators (slice D)

Status: **slice D implementation gate (2026-11-14).** Schema 0.5 admits the
`tensorboard` locator and verifies one numeric scalar from a local TensorBoard
event file per result, using a dependency-free strict reader. Schemas 0.1, 0.2,
0.3 and 0.4 are unchanged; 0.4 remains byte-frozen. The 0.5 schema is
documentation/file-only: `runevidence schema` still prints the 0.1 schema, and
no CLI route was added.

The design contract is
[docs/tensorboard-locator-rfc.md](tensorboard-locator-rfc.md) (v0.11). This
page summarizes the delivered behavior; the RFC remains normative for edge
cases.

## Manifest shape

Schema 0.5 keeps the 0.3/0.4 top-level shape: `schema_version: "0.5"`,
`project`, `runs`, `artifacts`, `results`, `evidence` and `relationships`. The
version-specific change is the result locator union; `results` is a list of
`Result05`. A minimal manifest:

```json
{
  "schema_version": "0.5",
  "project": {"id": "demo", "name": "Demo"},
  "runs": [{"id": "train", "name": "training run"}],
  "artifacts": [
    {"id": "events", "path": "runs/exp/events.tfevents.1700000000",
     "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
     "size_bytes": 4096}
  ],
  "results": [
    {
      "id": "val_loss",
      "run_id": "train",
      "name": "validation loss",
      "value": 0.25,
      "artifact_id": "events",
      "locator": {"kind": "tensorboard", "tag": "loss/val", "step": 40, "occurrence": 0}
    }
  ],
  "evidence": []
}
```

All 0.2/0.3/0.4 fields remain admitted, including `run.conditions`,
`artifact.dataset_id` and `result.metric_definition`; 0.5 adds no other field.

## Locator union

Schema 0.5 is an explicit opt-in (`"schema_version": "0.5"`). A result accepts
the same forms as 0.4 plus `tensorboard`:

- a **string** JSON Pointer (empty, or `/`-prefixed with only `~0`/`~1`
  escapes);
- a **`jsonl`** object: `{"kind": "jsonl", "line": 41, "pointer": "/val_rmse"}`;
- a **`csv-cell`** object:
  `{"kind": "csv-cell", "row": 41, "column": 4, "header": "val_rmse"}`;
- a **`tensorboard`** object:

  ```json
  {"kind": "tensorboard", "tag": "loss/val", "step": 40, "occurrence": 0}
  ```

`tag` is the exact event tag: no normalization, glob or case folding. It is a
strict non-empty string of 1 to 4,096 code points, additionally capped at
4,096 UTF-8 bytes at runtime; a lone surrogate is rejected at model load
(`string_unicode`). `step` is a strict signed 64-bit integer; an absent
`Event.step` on disk means `0`. `occurrence` is required and is a strict
integer from 0 to `2**31 - 1`: the zero-based position among **supported
scalar** entries with that exact `(tag, step)` in file order (record order,
then `Summary.Value` order). The object is closed
(`additionalProperties: false`).

Any non-null locator in 0.5 requires `artifact_id` (`if`/`then` in the schema
and a model check). A `tensorboard` locator requires a file artifact whose
basename contains `.tfevents`, case-insensitively; the parent folder name is
never considered, so a folder named `.tfevents` makes no child eligible. A path
can match more than one kind (`metrics.tfevents.csv`): each locator uses its
own predicate and no kind mismatch is invented. A mismatch is
`locator-kind-mismatch`.

Unknown or missing `kind`, a non-string/non-object locator, a bad
`tag`/`step`/`occurrence` range, or a missing `artifact_id` are load refusals
(`schema-invalid`, exit 2), never a crash and never a silent downgrade to a
string locator.

The 0.5 JSON Schema is `schema/runevidence-0.5.schema.json` (draft 2020-12,
`$id` `urn:runevidence:manifest:0.5`, root title `Manifest05`, `$defs`
`Result05`, `JsonlLocator`, `CsvCellLocator`, `TensorboardLocator`). Its
`locator` union is a hand-built `oneOf` (null / string / the three objects).
Draft 2020-12 accepts `1.0` for an integer and counts `maxLength` in code
points; the model uses `StrictInt` and additionally enforces 4,096 UTF-8 bytes
for `tag`, as documented on the properties.

## Strict event profile

The reader is core-owned and dependency-free; the `protobuf` runtime and
TensorFlow are never imported. The file is read once through the protected
snapshot shared with the file check; the same bytes are hashed and parsed.

- **Framing:** TFRecord `uint64` LE length, masked CRC32C of the eight length
  bytes, payload, masked CRC32C of the payload. Zero-length records are valid
  and skipped. A truncated length or payload, a CRC mismatch, trailing bytes
  after the last record, or a length over the remaining bytes is a structural
  refusal. There is no per-record byte cap.
- **Protobuf:** wire types 0/1/2/5 only; groups refused; field number 0
  refused; field numbers fit 29 bits; varints at most 10 bytes with the tenth
  `<= 0x01` (non-minimal encodings are accepted when they fit); length
  delimiters must fit the record; unknown fields are skipped by wire type.
  Known fields with the wrong wire type are refused, including a losing oneof
  member (only its payload is not decoded).
- **Merge semantics:** every non-repeated, non-message field is last-wins (an
  explicit default counts as present); only repeated fields append; only
  message fields merge; different oneof members replace, and only occurrences
  after the last different member merge. `Event.step` is last-wins;
  `file_version` is not validated.
- **Merged summary cap:** 16 MiB over the winning `summary` occurrences after
  the last other `what` member of one Event. A losing summary is skipped,
  never decoded and never counted against the cap. The cap bounds decoding and
  is enforced in O(1) state.
- **Scalar classification:** shape is checked first. A tensor is
  scalar-shaped only when `unknown_rank` is false and the shape is rank 0
  (no `dim`) or rank 1 with exactly one `dim` of size 1. A supported scalar is
  either `simple_value`, or a scalar-shaped `DT_FLOAT`/`DT_DOUBLE` tensor with
  exactly one encoding: `DT_FLOAT` has 4 little-endian `tensor_content` bytes
  XOR exactly one `float_val`, with no `double_val`; `DT_DOUBLE` has 8 content
  bytes XOR exactly one `double_val`, with no `float_val`. Packed and unpacked
  values are admitted.
- **Unsupported scalar:** a scalar-shaped tensor with another dtype (half,
  bfloat16, int, string, out-of-int32 enum) or an ambiguous/absent encoding.
  **Non-scalar:** histogram/image/audio members, a non-scalar shape, or no
  winning value member.
- **Selection:** a key with more supported scalars than the requested
  occurrence yields the value; a key with at least one supported scalar but a
  later occurrence is `locator-mismatch` (`was not found`); with no supported
  scalar, an unsupported scalar wins over a non-scalar
  (`selects an unsupported scalar encoding` vs `selects an unsupported
  value`); a key never seen is `was not found`. Only supported scalars
  increment occurrence.
- **Numbers:** `simple_value` and `DT_FLOAT` use relative `1e-6`, absolute
  `1e-12`; `DT_DOUBLE` uses relative `1e-9`, absolute `1e-12`. A recorded
  integer uses the actual value's origin tolerance and falls back to exact
  comparison when it cannot be converted (`OverflowError`). NaN equals NaN;
  infinities equal themselves; finite versus NaN/infinity is
  `result-value-mismatch`. A recorded string, boolean or null is
  `locator-unsupported-value`.

Event tags are compared as bytes against the UTF-8 encoding of the locator
tag. They are never decoded, UTF-8-validated or runtime-capped; an invalid or
long tag simply never matches.

### Caps

Per-artifact structural caps (a violation is a per-result
`locator-unreadable`):

| Cap | Unit | Value |
| --- | --- | --- |
| file (`json`/`jsonl`/`csv`) | raw bytes | 50 MiB |
| file (`tensorboard`) | raw bytes | 128 MiB |
| merged `summary` payload per Event | winning `summary` payload bytes after the last other `what` member | 16 MiB |

The 128 MiB snapshot cap applies only when a kind-matching `tensorboard`
locator targets the artifact. An artifact targeted by both a TensorBoard
locator and a string/JSONL/CSV locator reads one snapshot up to 128 MiB, but
each verifier applies its own cap: the other kinds still refuse above 50 MiB
(`was not read because the artifact exceeds 50 MiB`). A kind-mismatched
`tensorboard` locator never raises the cap. 0.1-0.4 artifacts keep the
delivered read-to-EOF behavior and the 50 MiB cap.

The TensorBoard snapshot is sized from `os.fstat` on the already-open
protected descriptor, then read once. A read that differs from the fstat size
is `artifact-changed` (shrink or growth inside the cap), and a read past the
cap is over-cap; the hash and size checks still run on over-cap files.

Per-manifest eligibility caps, counted without reading a file and independent
per kind:

| Kind | Cap | Value |
| --- | --- | --- |
| tensorboard results | manifest-eligible `tensorboard` entries | 10,000 |
| tensorboard tag metadata | sum of locator tag UTF-8 bytes | 16 MiB |
| csv-cell results | manifest-eligible `csv-cell` entries | 10,000 |
| csv-cell header metadata | sum of header UTF-8 bytes | 16 MiB |

A manifest-eligible entry has a non-directory artifact matching the kind
predicate, a recorded `sha256`, and `volatile: false`; runtime file states do
not reduce the count. When either threshold is exceeded, each eligible
artifact gets **one** cap finding per capped kind (`locator-unreadable`), the
artifact is marked broken, and no value is read or compared for that kind.
Other kinds on the same artifact still verify. A 0.5 manifest can cap both
kinds independently.

- TensorBoard cap text: `the manifest exceeds the tensorboard verification
  limit (10,000 entries or 16 MiB of tag metadata); no TensorBoard scalar was
  read`.
- CSV cap text: `the manifest exceeds the csv-cell verification limit (10,000
  entries or 16 MiB of header metadata); no CSV cell was read`.

## Outcomes

Reference and file states run first, then kind mismatches, then per-kind caps,
then per-kind structural checks. A TensorBoard refusal affects only
`tensorboard` results; a CSV refusal affects only `csv-cell` results; other
kinds continue.

| Condition | Code | Severity | Cardinality |
| --- | --- | --- | --- |
| Invalid 0.5 shape/kind/range/tag/pointer/`artifact_id` | `schema-invalid` | exit 2 | per load issue |
| Dangling `artifact_id` | `dangling-reference` | error | per result |
| Directory artifact with a locator | `directory-locator-unsupported` | error | per result |
| Kind mismatch | `locator-kind-mismatch` | error | per result |
| Missing file | `missing-file` | error | one per artifact |
| Unreadable/changed/path refusals | existing slice-B codes | error | one per artifact |
| Volatile artifact with a locator | `locator-unverified` | error | per result |
| Digestless artifact with a locator | `locator-no-digest` | error | per result |
| Digestless artifact with no usable locator remains | `no-digest` | warning | one per artifact |
| Over-cap file for the kind | `locator-unreadable` | error | per result for that kind |
| Manifest exceeds a per-kind cap | `locator-unreadable` (cap message) | error | one per eligible artifact and kind |
| Strict-profile refusal | `locator-unreadable` (structural message) | error | per result for that kind |
| `hash-mismatch` / `size-mismatch` | existing codes | error | one per artifact |
| Not found | `locator-mismatch` (`was not found`) | error | per result |
| Recorded non-number / unsupported scalar / non-scalar | `locator-unsupported-value` | error | per result |
| Value differs | `result-value-mismatch` | error | per result |
| Absent locator | `result-unverified` | warning | per result |
| Claim cites only results with no locator | `claim-unverified` | warning | per claim |
| Claim rests on a capped or broken artifact | `claim-at-risk` | error | per claim |

The structural text is `result {id!r} locator {display} could not be read
under the strict TensorBoard profile in {path!r}`, with hint
`the TensorBoard event file does not satisfy the strict profile`. The cap hint
for a claim resting on a capped artifact is `a resource cap is not a change;
narrow the manifest or raise the limit`.

## Display and privacy

A finding never contains the event tag, a scalar value, protobuf/CRC text or
OS text. The artifact path, the fixed message and the escaped locator display
are permitted; the locator display never includes the tag.

Human output renders a `tensorboard` locator as
**`tensorboard:step=<step>,occurrence=<n>`**. `run`, `claim` and `lineage`
replace each nested tensorboard locator (`results[i].locator`, each
`details.locator`) with that display form in a **copy**; the recorded data is
never mutated. `--json` keeps the structured locator, including `tag`.

## Adapter

The optional package `packages/runevidence-tensorboard` registers the
`tensorboard` adapter. Core verification is unchanged if it is absent.

- **Discovery:** every regular file except RunEvidence's own manifest and
  backups becomes an artifact. Event files (basename contains `.tfevents`,
  case-insensitively) additionally produce runs, artifacts and scalar results.
- **Runs:** one run per directory that directly contains event files; nested
  directories are separate and the experiment root is `.`. Run id
  `tb-run-<16 hex SHA-256(os.fsencode(dir_rel))>`; run name is the directory's
  relative path.
- **Identity:** event artifact id `tb-art-<16 hex SHA-256(os.fsencode(path))>`
  and `run_id` set to its directory's run. Result id
  `tb-res-<16 hex SHA-256(run_id + NUL + artifact_id + NUL + tag + NUL +
  str(step) + NUL + str(occurrence))>`; occurrence is per file. Non-event
  artifacts keep the generic adapter's ids and have no `run_id`. All fixed
  `tb-` ids are reserved before generic allocation.
- **Names:** a single-file `(tag, step)` group is `tag @ step`, plus
  ` #occurrence` when occurrence is greater than 0. A group spanning files is
  `tag @ step [basename] #<16-hex digest of artifact_id + NUL + occurrence>`.
  A duplicate `(run_id, name)` refuses with
  `a generated result name collides within a run; nothing was written`.
- **Selection:** every supported scalar in discovery order, then record order,
  then `Summary.Value` order. `init --tags PATTERN` (repeatable) keeps only
  tags matching a full-string glob (`*`, `?`, `\` escapes; no character
  classes). A tag that is absent/empty, invalid UTF-8 or longer than 4,096
  bytes refuses the whole extraction without a filter and is skipped with one.
  Plugin pseudo-scalars remain unselected.
- **Budget:** at most 10,000 selected results and 16 MiB of selected tag UTF-8
  bytes per manifest; otherwise nothing is written. Pattern work shares a
  50,000,000-unit budget and is cached per `(tag, pattern)`.
- **One read:** the adapter hashes and parses the same protected snapshot; a
  path/identity change refuses the file. A malformed or truncated file refuses
  with `the event file does not satisfy the strict TensorBoard profile;
  nothing was written`; a file over 128 MiB refuses with `the event file
  exceeds the 128 MiB TensorBoard limit; nothing was written`.
- **Path refusals:** on POSIX an event path containing `\` refuses with
  `an event path contains a backslash; nothing was written`; a path that is
  not valid UTF-8 refuses with `a path is not valid UTF-8; nothing was
  written`. Both happen before any read.
- **Volatile:** a regular event artifact cannot be volatile. `init
  --volatile` that matches one stops with `an event artifact cannot be
  volatile; nothing was written`, before any backup or save; the check is by
  path, so an empty or fully filtered event file is still protected.

## Migration and compatibility

- `runevidence migrate PATH --to 0.5` opts a manifest into 0.5. The supported
  targets are `--to 0.2`, `--to 0.3`, `--to 0.4` and `--to 0.5`.
- 0.3 → 0.5 and 0.4 → 0.5 are version-only bumps: values, locators, key order,
  `conditions`, `dataset_id` and `metric_definition` are preserved.
- 0.1/0.2 → 0.5 runs the same manifest-only precheck as the earlier targets
  (defensive object locator; missing `artifact_id`; invalid/prefixed pointer;
  directory, volatile or digestless artifact; non-`.json` string target). It
  refuses before any backup or write with `LOCATOR_MIGRATION_UNSUPPORTED`.
- There is no downgrade: 0.5 → 0.4/0.3/0.2 is `DOWNGRADE_UNSUPPORTED`; 0.5 →
  0.5 is an idempotent no-op.
- `init --force` on a 0.5-capable build refuses an existing 0.5 manifest before
  adapter discovery, backup or save (`INIT_0_5_REFUSAL`); move the manifest
  aside, run `init`, then `migrate --to 0.5` and reapply the first-class values
  and locators. A pre-0.5 binary backs the manifest up and replaces it instead.
- `conditions` remains 0.2-only and names 0.3, 0.4 and 0.5 in its refusal;
  `comparability` refuses 0.5; `fingerprint` keeps v1's 0.1-only guard;
  `rehash` refuses an artifact read through a `tensorboard` locator
  (`LOCATOR_REFRESH_UNSUPPORTED`) but accepts unrelated artifacts in the same
  manifest; `add` accepts a 0.5 manifest.
- `validate`, `inspect`, `validate-batch` and the other manifest-reading routes
  load 0.5 through the one dispatcher. The `schema` CLI route is unchanged (it
  prints 0.1); the 0.5 schema is available as a file and through
  `json_schema_0_5()`.
- Regenerating a TensorBoard manifest now groups event files by directory. Run
  ids are derived from the directory (not the file), result ids include the
  artifact id, and a `(tag, step)` group spanning files gets qualified names.
  An existing manifest is recorded data, so it stays valid, but its ids and
  names differ from a fresh scan. Move it aside before re-running `init`;
  there is no id remapping tool.

## Not implemented

No histograms, images, audio, text, graphs or other TensorBoard value kinds as
results; no multi-file runs, tag filtering, TensorBoard UI/database access,
TensorFlow or protobuf dependency, YAML, or Windows/macOS parity for the
POSIX descriptor-walk race protection. No aggregate or computed values.
