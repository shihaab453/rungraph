# TensorBoard scalar locators — slice D design RFC (v0.11)

Status: **DESIGN PROPOSAL. NOTHING IMPLEMENTED. NOTHING AUTHORIZED BY THIS DOCUMENT.**
A design gate and an owner schema-admission decision (D2) are required before any code.

> **Current amendment.** `docs/tensorboard-multifile-tags-rfc.md` amends §2, §9 and §13 of
> this document and supersedes §16 for two features only: one run per log directory (instead of
> one per event file) and `init --tags`. §14's "one-file-one-run and no tag filtering" risk and
> §15 D7's "all scalars in v1" are stale after that amendment. Everything else here stands as
> accepted.

## 0. Changes in v0.11 (post-acceptance editorial)

- **§6 cap unit**: names the winning `summary` occurrence payload bytes after the last other
  `what` member, matching §5.1/§5.2.
- The duplicated tag/Dim.size/tensor_content vector was already removed; the 8.4M/1.6M budget
  vector stays cross-referenced by design.

## 0.1 Changes in v0.10 (round-9 findings)

- **Erratum 24**: the inserted text ends “…cross-field rule)” without a period, so the parent's
  own period closes the sentence; no `..`.
- **Wording**: §5.1 counts only the winning summary occurrences after the last other `what`
  member; §5.2 says a losing member is never checked against the 16 MiB cap.
- **Vectors**: removed the duplicated 15 MiB / `file_version` / 15 MiB and 15+15 entries.

## 0.2 Changes in v0.9 (round-8 findings)

- **Race vectors**: fstat 140/EOF 140 is over-cap again; the EOF == cap boundary is fstat 140/EOF
  128 → artifact-changed; fstat 140/EOF 141 is over-cap.
- **Fuzz**: the directory seed pins `enable_directory_artifacts=False` and adds `not-a-file`; an
  absent-(tag, step) / occurrence-beyond seed makes `locator-mismatch` deterministic.
- **Errata**: item 24 quotes `not-a-file` without the final period (no stray “.;”), item 17 keeps
  case-insensitivity for all kinds, item 27 says “whose read ends past its cap”.

## 0.3 Changes in v0.8 (round-7 findings)

- **Fuzz harness**: entry point is `check_files` with the targeted callback; `missing-file`,
  `hash-mismatch` and `size-mismatch` are in the exact reachable subset; corpus (i) recomputes
  size as well as digest; `no-digest` uses a digestless + wrong-kind-only seed; "missing artifact"
  means a missing file, not a dangling id.
- **Errata**: item 26 uses the semicolon-anchored gate-6 clause so it is unique after item 22.
- **Vectors**: boundary races (fstat ≤ cap/EOF > cap → over-cap; fstat > cap/EOF == cap →
  artifact-changed) are pinned.
- **Changelog headings** renumbered.

## 0.4 Changes in v0.7 (round-6 findings)

- **Fixed-id reservation**: `tb-*` ids are reserved in `used` before generic allocation; generic
  slugs suffix on any collision; only fixed-vs-fixed collisions are `AdapterError`.
- **Errata**: item 5 now contains the per-artifact-per-kind cardinality (item 6 no longer claims
  it); item 7 reads “16 MiB merged summary payload cap per Event”; items 26/27 add gate 6 and the
  parent §4.2 race sentence.
- **Fuzz seeds**: byte mutations recompute the digest; explicit wrong digest/size and manifest
  variants define an exact reachable subset including hash/size; graph/dangling/claim/schema are
  upper-bound only.
- **Race boundary**: fstat > cap with EOF == cap is `artifact-changed`; only EOF > cap is
  over-cap. One short-read wording; `checks/files.py` scoped to TensorBoard-raised snapshots.
- **Hints/fence**: the `claim-at-risk` cap hint is listed; the INIT note is outside the fence.

## 0.5 Changes in v0.6 (round-5 findings)

- **Comparator scope**: parent errata cover 0.5 string/jsonl/csv-cell at 1e-9/1e-12; TensorBoard
  floats use 1e-6/1e-9 with abs 1e-12.
- **Snapshot race scoped**: fstat/growth/shrink rules apply only to TensorBoard-raised snapshots;
  0.3/0.4 keep read-to-EOF. Full race table (including fstat over cap with EOF under) and a
  truncate-in-place rule.
- **Max-summary**: removed the “value-count or memory cap” phrase; 8.4M empty and 1.6M tagged
  vectors are asserted in §6; the 67M record-only wall/memory vector is outside (a)-(c).
- **Adapter ids**: UTF-8 + 0x00 for every result-id component; generic slugs suffix in discovery
  order, fixed tb-* ids never suffix, a fixed-id collision is `AdapterError` (no `duplicate-id`);
  path-qualified run names; own manifest/backups excluded; one read for hash and parse.
- **Volatile**: “event artifact” is defined as tensorboard-targeted in Manifest05; generic init
  unchanged.
- **Errata**: item 6 rows quoted exactly with cardinality anchored to item 5; item 7 merged
  summary payload; items 21-25 cover §2 in-scope, §8 readers, §6 fingerprint, gate-1 0.5 matrix,
  and the v0.9 header/status; gate 13 is gate 8.
- **Table/step**: artifact `no-digest` row; step 8 includes unsupported selection; per-key flags.

## 0.6 Changes in v0.5 (round-4 findings)

- **O(1) Event scanning**: the reader tracks the last non-`summary` member offset and the sum of
  summary payload bytes after it; no slice list. The 16 MiB cap is the sum of merged summary
  payload bytes, and a 67M-empty-summary record cannot allocate.
- **Falsifiable max-summary vector**: no value cap; 8.4M empty Values and 1.6M tagged scalars must
  verify inside the asserted ≤300 MB budget.
- **Snapshot growth**: growth past the fstat size below the cap is `artifact-changed`; past the
  cap is over-cap.
- **Parent errata**: items 12/17 merged into one gate-4 replacement; item 16 keeps backticks;
  items 6/11/13 have anchors; comparator erratum scopes TensorBoard and 0.3/0.4 separately; added
  §3.1 kind/absent-locator and gate 1/2 errata (20 items).
- **Adapter**: one shared id namespace and collision text; step-qualified result names
  (`tag @ step`, `#occurrence`); path hashed as UTF-8; generic folder runs not emitted; volatile
  event check in `cmd_init`; 0.5 init text includes `migrate --to 0.5`.
- **Fuzz**: entry point named (event reader + targeted results); set is an upper bound including
  `no-digest` and directory codes; graph/conditions passes excluded.
- **Checklist**: correct modules (`manifest.py`, `locator_profiles.py`, `run_fingerprint.py`) and
  the `checks/files.py` row.

## 0.7 Changes from v0.3 (round-3 findings)

- **Scalar classification is exclusive** and has an ordered selection table; a no-member or
  no-encoding value is classified; non-scalar uses `locator-unsupported-value` (parent §4.3).
- **Per-request retention**: one counter per `(tag, step)`, one retained value per requested
  `(key, occurrence)`; multiple occurrences on one key retain multiple values, never an index.
- **Protobuf merge semantics**: message-typed fields merge, scalar singular fields last-wins,
  different oneof members replace; field number 0 and invalid field keys are refused.
- **Per-kind caps and structural status**: string/jsonl/csv-cell always enforce 50 MiB and never
  enter their profiles above it, even when a TensorBoard locator raised the snapshot cap to
  128 MiB. A structural refusal in one kind never suppresses another kind on the same artifact.
- **Delivered 0.3/0.4 predicates unchanged**: csv-cell eligibility keeps the delivered full-path
  `endswith(".csv")`; only TensorBoard uses the basename `.tfevents` rule.
- **Tags**: the 4 KiB cap is locator-only. Event tags are matched as bytes with no runtime cap or
  UTF-8 refusal; a long or invalid tag is simply unmatchable.
- **Parent v0.9 errata are verbatim quotes**, kind-scoped, including the comparator and the
  per-kind cap sentences.
- **Adapter**: fixed texts for over-cap/truncated/unrepresentable tags, per-manifest budget,
  tag-free digest ids, `Run.name` rule, and a truthful 0.5 init refusal.
- **Benchmark (c)** uses minimal scalar records and the wall bound is 30 s (CRC plus decode).
- **Round-3 fixes**: every non-repeated, non-message field is last-wins (including
  `tensor_content`, `tag`, `Dim.size`), last-present for merged messages, with oneof interleaving
  after the last different member; supported shapes require `unknown_rank=false` and shape is
  classified before encoding; the 16 MiB cap applies to the merged summary; 0.5 `model_dump`
  preserves 0.2+ fields; parent errata are exact and complete (snapshot scope, gate 4, table row,
  mismatch row); adapter runs are per event file with path-based ids and one name rule; adapter
  failure strings and volatile-event refusal are quoted.

## 1. Problem and evidence

TensorBoard `tfevents` files are the default training log for many frameworks. The `generic`
adapter records the file, but no result can point at a scalar, so `loss/val` at step 40 is
unverifiable. The adapter memo ranks TensorBoard last (framing, CRC, protobuf, restart duplicates,
TF2 `tensor` versus legacy `simple_value`, dependency/security), and notes that locator checking
lives in core. 0.3 (JSONL) and 0.4 (CSV cells) established the pattern this slice follows.

## 2. Scope and non-goals

In scope: one event file per artifact; scalar summaries; occurrence-aware locator; strict event
profile with CRC; caps; outcomes; display/privacy; migration; optional adapter; verification.

Out: histograms/images/audio/text/graphs as results, TensorBoard UI/DB, multi-file or directory
runs, tag filtering in v1, remote/streamed logs, other adapters, YAML, parity, release, and any
change to 0.1-0.4 behavior.

## 3. Architecture (D1)

**Core owns verification** with a dependency-free reader. `validate` must produce the same result
on every install; an optional plugin cannot decide whether a manifest is valid.

- **Plugin verification** — rejected: availability-dependent results; `locator-verifier-unavailable`
  is defensive/unreachable by design.
- **Pinned `protobuf` runtime with checked-in generated code** — deterministic, but adds a native
  dependency and upstream CVE surface to core. Rejected for this slice; a later dependency gate can
  revisit it.

The dependency-free parser is security-relevant: its gate is bounds, caps, CRC and fuzzing (§12).

## 4. Schema 0.5 (D2)

### 4.1 Models and dispatch

- `SCHEMA_VERSION_0_5 = "0.5"`; base `Manifest` stays `Literal["0.1","0.2","0.3"]`.
- `TensorboardLocator(_Strict)`: `kind: Literal["tensorboard"]`, `tag: StrictStr`
  (`min_length=1`, `max_length=4096` code points, and at most 4,096 UTF-8 bytes via a field
  validator), `step: StrictInt` (`-2**63 .. 2**63-1`), `occurrence: StrictInt`
  (`0 .. 2**31-1`, **required**).
- `Result05` with `locator: Annotated[Optional[Union[StrictStr, JsonlLocator, CsvCellLocator,
  TensorboardLocator]], BeforeValidator(_dispatch_locator_05)] = None`; the dispatcher mirrors
  0.4 (str pointer grammar, model instance, dict through a module-level discriminated adapter,
  fixed `value_error` otherwise). `Result05` **overrides `_locator_scalar_shape`** to accept a
  constructed `TensorboardLocator` (as `Result04` does for `CsvCellLocator`).
- `Manifest05`: `schema_version: Literal["0.5"]`, `results: list[Result05] =
  Field(default_factory=list)`, cross-field rule: any non-null locator requires `artifact_id`.
- `_version_specific_shape` gains a 0.5 branch; the inherited `else` must never see a 0.5 locator.
- `validate_manifest`, `is_manifest`, `model_dump` (0.5 joins the 0.2+ preservation branch so `conditions`, `dataset_id` and
`metric_definition` survive), `artifact_to_dict(..., "0.5")`,
  `json_schema_0_5()` (`$id urn:runevidence:manifest:0.5`, root `Manifest05`, `$defs`
  `Result05`/`JsonlLocator`/`CsvCellLocator`/`TensorboardLocator`, hand-built `oneOf`,
  description overrides with no 0.3/0.4-only text, `if`/`then` artifact_id), written to
  `schema/runevidence-0.5.schema.json` and pinned by `test_schema_freeze.py`.

### 4.2 Locator shape

```json
{"kind": "tensorboard", "tag": "loss/val", "step": 40, "occurrence": 0}
```

`tag` is the exact event tag, no normalization/glob. `step` is signed int64; an absent
`Event.step` means protobuf default `0`. `occurrence` is the zero-based position among
**supported scalar** entries with that exact `(tag, step)` in file order (record order, then
`Summary.Value` order), and is required. A locator requires a `.tfevents` basename (§4.4).

### 4.3 Serialization and diagnostics

`locator_to_json`/`dump_manifest` write all four fields; `occurrence` is a normal required field.
Unknown/missing kind is `schema-invalid`; `union_tag_*` locations append `/kind`.

| Input | Type | Location | Category |
|---|---|---|---|
| missing `tag` / `step` / `occurrence` | `missing` | `/results/0/locator/<field>` | `missing_required` |
| `tag` 4,096 code points but >4,096 UTF-8 bytes | `value_error` | `/results/0/locator/tag` | `invalid_value` |
| `step: 1.0` | `int_type` | `/results/0/locator/step` | `invalid_type` |
| `step: 2**63` / `-2**63-1` | `less_than_equal` / `greater_than_equal` | `/results/0/locator/step` | `invalid_value` |
| `occurrence: -1` / `2**31` | `greater_than_equal` / `less_than_equal` | `/results/0/locator/occurrence` | `invalid_value` |
| empty `tag` | `string_too_short` | `/results/0/locator/tag` | `invalid_value` |
| `tag` >4,096 code points | `string_too_long` | `/results/0/locator/tag` | `invalid_value` |
| unknown kind | `union_tag_invalid` | `/results/0/locator/kind` | `invalid_value` |
| missing kind | `union_tag_not_found` | `/results/0/locator/kind` | `missing_required` |
| extra key | `extra_forbidden` | `None` | `invalid_value` |
| non-str/non-dict locator | `value_error` | `/results/0/locator` | `invalid_value` |
| lone surrogate tag | `string_unicode` | `/results/0/locator/tag` | `other_validation` |

`_LOCATOR_TAGS` gains `tensorboard`; `_issue_location` maps it before branch labels and forwards
the root model.

### 4.4 Predicates (delivered behavior preserved)

- **csv-cell** (0.4 and 0.5): the delivered full-path rule
  `artifact.path.replace("\\", "/").lower().endswith(".csv")`. `other.csv/` stays ineligible, so
  0.4 cap accounting is unchanged.
- **jsonl**: full-path `endswith(".jsonl")`; **string**: full-path `endswith(".json")`.
- **tensorboard** (0.5): `PurePosixPath(path.replace("\\", "/")).name.lower()` contains
  `.tfevents`. Basename only; a directory named `.tfevents` never makes a child eligible.

A path can match more than one kind (`metrics.tfevents.csv`). Each locator uses its own kind
predicate; no kind mismatch is invented.

### 4.5 Consumer checklist (0.5)

| Area | Required change |
|---|---|
| `models.py` | version const, `TensorboardLocator`, `Result05` + validator override, `Manifest05`, `_version_specific_shape` 0.5, `json_schema_0_5`, `is_manifest`, `model_dump` (0.5 in the 0.2+ preservation branch) |
| `manifest.py` | `validate_manifest`, `_root_model_for`, `_LOCATOR_TAGS`, `_issue_location` (tag map, root forwarding), 0.5 `artifact_to_dict` |
| `locator_profiles.py` / `run_fingerprint.py` | `display_locator` tensorboard branch; fingerprint 0.5 refusal |
| `checks/files.py` | per-target snapshot cap chosen from kind eligibility, single-copy allocation from `os.fstat` on the protected descriptor (TensorBoard-raised snapshots only; 0.3/0.4 keep read-to-EOF), cap visible to the per-kind 50 MiB check |
| `checks/__init__.py` | `_LOCATOR_SCHEMAS` includes 0.5; four targeted gates; `_csv_cell_cap` accepts 0.5; tensorboard cap pre-pass; per-artifact snapshot cap; per-kind callback/early-return discipline |
| `checks/results.py` | `has_locators`, `_extension_matches`/`_expected_extension` (tensorboard basename), tensorboard route before the JSONL pre-scan, `_check_one_tensorboard`, per-kind structural/cap status, `display_locator`, 0.5 directory hint |
| `checks/graph.py` | 0.5 `claim-unverified` text and the cap-specific `claim-at-risk` hint |
| `cli.py` / `migrate.py` | `TARGET_VERSIONS` +0.5, `UNSUPPORTED_TARGET`, conditions 0.5 text, `INIT_0_5_REFUSAL`, help/docstrings |
| `edit.py` / `conditions_authoring.py` / `run_comparability.py` | dispatcher use, 0.5 refusal, comparability guard |
| `run_claim.py` / `run_lineage.py` / `run_details.py` | `_display_details` includes tensorboard; display copies; 0.3/0.4 bytes unchanged |
| tests | wrong 0.5 value is exit 1 (not `result-unverifiable`), 0.5 diagnostics, independent caps, kind overlap, adapter ids, fixtures 0.6, schema freeze |

## 5. Event-file profile (D3)

The file is read once through the protected snapshot; the same bytes are hashed and parsed. The
snapshot size is checked with `os.fstat` on the **opened protected descriptor** before
allocation (a pre-open path stat could misclassify a shrink race); an over-cap file is
stream-hashed without a snapshot. A short read is truncated in place; growth during the read is
detected by the running total. **This fstat sizing and growth/shrink rule applies only when a
kind-eligible TensorBoard locator raises the cap to 128 MiB; 0.3/0.4, and 0.5 string/jsonl/csv-cell
locators, keep the delivered read-to-EOF behavior.** Size races:

| fstat | EOF | Outcome |
|---|---|---|
| ≤ cap | == fstat | normal |
| ≤ cap | < fstat but ≤ cap | `artifact-changed` |
| ≤ cap | > fstat but ≤ cap | `artifact-changed` |
| ≤ cap | > cap | over-cap |
| > cap | < cap | `artifact-changed` |
| > cap | > cap | over-cap |
| > cap | == cap | `artifact-changed` |

Profiles receive `bytes` or `bytearray`, never a `memoryview`; a short read is truncated in place
(`del buf[n:]`), not copied.

### 5.1 TFRecord framing

- `uint64 length` LE, `uint32 masked_crc32c(length)`, `byte[length]`, `uint32 masked_crc32c(data)`;
  mask `((crc >> 15) | (crc << 17)) + 0xa282ead8 (mod 2**32)`.
- Zero-length records are valid (empty `Event`) and skipped.
- Truncated length/data, CRC mismatch, or trailing bytes after the last record are structural
  refusals. A record length over the remaining bytes is a structural refusal.
- The file cap is checked before parsing; there is no per-record byte cap. The 16 MiB cap applies
  to the **merged** `summary` payload of one Event (the winning `summary` occurrences after the
  last other `what` member); it is what bounds decoding.
- CRC32C is a table-driven core implementation; the benchmark records its cost.

### 5.2 Protobuf subset

Wire types 0/1/2/5 only; groups refused; **field number 0 is invalid** and refused; field numbers
must fit 29 bits. Known fields with the wrong wire type are refused; the key/wire-type check
applies to every field, including a losing oneof member (only its payload is not validated).
Unknown fields are skipped by
wire type, bounded by the record. Varints are at most 10 bytes; the 10th byte must be `<= 0x01`
for 64-bit values; non-minimal varints are accepted if they fit. Length delimiters must fit.
Depth is bounded by the fixed schema (Event→Summary→Value→TensorProto→TensorShape→Dim = 6).

- `Event`: `wall_time` (1) ignored; `step` (2, int64; absent = 0); `what` is a oneof
  (`file_version` 3, `graph_def` 4, `summary` 5, `log_message` 6, `session_log` 7,
  `tagged_run_metadata` 8, `meta_graph_def` 9). `file_version` is not validated. Different
  members replace; the losing member is skipped, never decoded and never checked against the
  16 MiB cap. If a
  `summary` member is later replaced by another `what` field, its bytes are not decoded and the
  16 MiB summary cap applies only to the winning summary. In O(1) state the reader tracks the
  offset of the last non-`summary` `what` member and the sum of `summary` lengths after it, then
  stream-decodes the winning summaries; no list of slices is retained.
- `Summary`: `value` (1) repeated; repeated `summary` fields merge.
- `Summary.Value`: `tag` (1) separate; the value member is a oneof (`simple_value` 2, obsolete
  histogram 3, `image` 4, `histo` 5, `audio` 6, `tensor` 8); `metadata` (9) ignored. Different
  members replace; a repeated winning member is merged (tensor fields merge).
- `TensorProto`: `dtype` (1, enum `DT_FLOAT=1`, `DT_DOUBLE=2`; last-wins), `tensor_shape` (2,
  merges), `tensor_content` (4, singular bytes: last-wins across repeated fields and merged
  tensors; LE IEEE-754), `float_val` (5, packed
  wire 2 or unpacked wire 5), `double_val` (6, packed wire 2 or unpacked wire 1). Packed length
  must be a multiple of 4/8.
- `TensorShapeProto`: `dim` (2) repeated `Dim{size=1 int64, name=2}`; `unknown_rank` (3, bool).
  `Dim.size` absent means 0; `unknown_rank` is true for any nonzero varint. Repeated shapes merge
  their dim lists.
- Every non-repeated, non-message field (numeric, enum, string or bytes — including `step`,
  `dtype`, `tag`, `Dim.size` and `tensor_content`) is last-wins. For a merged message that means
  the last value encoded on the wire (an explicit default counts as present; absent means the
  default). Only repeated fields append; only message fields merge; different
  oneof members replace, and only occurrences after the last different member merge.

### 5.3 Classification and selection

Tags are matched as **bytes**: a `Summary.Value.tag` is compared to the UTF-8 encoding of the
locator tag. Event tags are not decoded, UTF-8-validated, or capped at runtime; an invalid or
long tag simply never matches. Locator tags are strict UTF-8, 1..4,096 code points and ≤4,096
UTF-8 bytes at load.

For one `Summary.Value`:

Shape is classified first. A tensor is **scalar-shaped** only when `unknown_rank == false` and
its shape is rank-0 (`dim` empty) or rank-1 with exactly one dim of size 1; every other shape is
non-scalar regardless of dtype or encoding.

1. **Supported scalar**: the winning oneof member is `simple_value`; or it is `tensor`, the
   tensor is scalar-shaped, has
   `DT_FLOAT`/`DT_DOUBLE`, and has exactly one encoding: `DT_FLOAT` = 4 content bytes XOR one
   `float_val`, with no `double_val`; `DT_DOUBLE` = 8 content bytes XOR one `double_val`, with no
   `float_val`. Packed and unpacked both admitted; content is LE.
2. **Unsupported scalar**: scalar-shaped tensor with another dtype (half, bfloat16, int, string,
   out-of-int32 enum) or an ambiguous/absent encoding (non-empty content plus a `*_val`, wrong
   count, `float_val` on double or vice versa, empty content and no `*_val`).
3. **Non-scalar**: winning member is histogram/image/audio, or the tensor is not scalar-shaped
   (`unknown_rank == true`, rank ≥2, rank-1 dim ≠1), or there is no winning value member.
4. **Malformed**: structural refusal.

Selection:

| State for the requested `(tag, step)` | Outcome |
|---|---|
| supported count > `occurrence` | value |
| supported count ≥ 1, occurrence beyond | `locator-mismatch` (`was not found`) |
| no supported, ≥1 unsupported scalar | `locator-unsupported-value` (`selects an unsupported scalar encoding`) |
| no supported, no unsupported scalar, ≥1 non-scalar | `locator-unsupported-value` (`selects an unsupported value`) |
| never seen | `locator-mismatch` (`was not found`) |

Mixed unsupported/non-scalar uses the unsupported-scalar reason. Only supported scalars increment
occurrence. Retained state: one counter per requested `(tag, step)`, a value per requested
`(key, occurrence)`, and per-key `unsupported_seen`/`non_scalar_seen` flags; no event index.

### 5.4 Numbers

`compare_values` stays at `1e-9` for 0.3/0.4, and for 0.5 string/jsonl/csv-cell locators; `compare_tensorboard(recorded, actual, *,
float32)` uses `1e-6`/`1e-12` for float32-origin (`simple_value`, `DT_FLOAT`) and `1e-9`/`1e-12`
for `DT_DOUBLE`; an int recorded value uses the same tolerance (decoded values are floats) and a
too-large int falls back to exact comparison on `OverflowError`. NaN equals NaN; infinities equal
themselves; finite vs NaN/inf is `result-value-mismatch`. Recorded `str`/`bool`/`None` is
`locator-unsupported-value`.

## 6. Caps (D4)

**Per-kind file cap on the shared snapshot**: the 128 MiB cap applies only when a TensorBoard
locator targets an artifact whose §4.4 predicate matches (basename contains `.tfevents`); a
kind-mismatched locator never raises the cap; string/jsonl/csv-cell results always enforce 50 MiB
and never enter their profiles above it (`was not read because the artifact exceeds 50 MiB`).
TensorBoard results enforce 128 MiB and never enter the event parser above it. An artifact
targeted by both reads a snapshot up to 128 MiB, but each verifier applies its own cap. 0.3/0.4
bytes are unchanged.

Per-artifact structural caps (per-result `locator-unreadable`):

| Cap | Unit | Value |
|---|---|---|
| file (json/jsonl/csv) | raw bytes | 50 MiB |
| file (tensorboard) | raw bytes | 128 MiB |
| merged `summary` payload per Event | winning `summary` occurrence payload bytes after the last other `what` member | 16 MiB (larger refuses the file structurally) |

The locator `tag` cap is load-time only; event tags are byte-compared with no runtime cap.

Per-manifest eligibility caps, independent per kind (one cap finding per eligible artifact and
kind):

| Kind | Cap | Value |
|---|---|---|
| tensorboard results | eligible entries | 10,000 |
| tensorboard tag metadata | sum of locator tag UTF-8 bytes | 16 MiB |
| csv-cell results | eligible entries (0.4/0.5) | 10,000 |
| csv-cell header metadata | sum of header UTF-8 bytes (0.4/0.5) | 16 MiB |

Eligibility uses the §4.4 predicates plus non-directory, sha256 non-null, volatile false. A 0.5
manifest can cap both kinds independently; one artifact can receive one finding per kind.

Memory: snapshot ≤128 MiB, retained state O(requested results), no index. Budget ≤300 MB of
additional working set above the post-load baseline: subprocess load, `clear_refs`, baseline
`VmRSS`, run, `VmHWM − baseline`; (a)-(c) plus the 8.4M-empty and 1.6M-tagged summary vectors assert it;
  manifest-proportional and 67M-empty vectors are record-only.

## 7. Outcomes and precedence (D5)

### 7.1 Normal path

1. Directory: ordinary check (zero or more findings), then one `directory-locator-unsupported` per
   result; return for that kind.
2. File state: `missing`/`reference-failed`/`unreadable`/`changed`/path refusals → one artifact
   finding, zero per-result for all kinds.
3. Kind: per result `locator-kind-mismatch`; restore the artifact `no-digest` warning only when no
   usable locator remains and the state is `no-digest`.
4. Volatile → per-result `locator-unverified`; return for that kind.
5. No digest → per-result `locator-no-digest`; return for that kind.
6. Per-kind over-cap → per-result `locator-unreadable`; return for that kind.
7. Structural pass **per kind**: a TensorBoard refusal affects only tensorboard results; a CSV
   refusal affects only csv-cell results; other kinds continue.
8. Selection → `locator-mismatch` (`was not found`) or `locator-unsupported-value` for an
   unsupported scalar / non-scalar.
9. Recorded type / decode → `locator-unsupported-value`.
10. Compare → `result-value-mismatch`.

Artifact `hash-mismatch`/`size-mismatch` are emitted by the file check; on state `ok` values are
still compared unless the kind is capped/refused/volatile/no-digest.

### 7.2 Capped path

For an artifact with eligible entries of a capped kind:

1. No eligible entries of that kind → normal path for that kind.
2. File state/digest as step 2. Missing/path states → artifact finding, no cap.
3. Kind mismatches for non-matching locators, as step 3.
4. **In state `ok` or `over-cap`**: one artifact-level `locator-unreadable` for that kind,
   artifact broken, no scalar parse for that kind. Hashing and digest/size still happen; a capped
   kind may release the snapshot without parsing.
5. Other kinds on the same artifact continue their own normal/capped path; a cap or structural
   refusal in one kind never short-circuits another.

### 7.3 Table and texts

| Condition | Code | Severity | Cardinality |
|---|---|---|---|
| Invalid 0.5 shape/kind/range/tag/pointer/artifact_id | `schema-invalid` | exit 2 | per load issue |
| Dangling artifact | `dangling-reference` | error | per result |
| Directory artifact ordinary finding(s) | existing | error/warning | zero or more per artifact |
| Directory artifact with a locator | `directory-locator-unsupported` | error | per result |
| Kind mismatch | `locator-kind-mismatch` | error | per result |
| Missing file | `missing-file` | error | one per artifact |
| Unreadable/changed/path refusals | existing slice-B codes | error | one per artifact |
| Volatile | `locator-unverified` | error | per result |
| No digest | `locator-no-digest` | error | per result; size compared |
| Digestless artifact with no usable locator remains | `no-digest` | warning | one per artifact |
| Per-kind over-cap | `locator-unreadable` | error | per result for that kind |
| TensorBoard cap | `locator-unreadable` (cap message) | error | one per eligible artifact and kind in `ok`/`over-cap` |
| Structural refusal (per kind) | `locator-unreadable` | error | per result for that kind |
| `hash-mismatch` / `size-mismatch` | existing | error | one per artifact; compared unless capped/over-cap/refused/volatile/no-digest |
| Not found | `locator-mismatch` (`was not found`) | error | per result |
| Recorded non-number / unsupported scalar / non-scalar | `locator-unsupported-value` | error | per result |
| Value differs | `result-value-mismatch` | error | per result |
| Absent locator | `result-unverified` | warning | per result |
| Claim only from unverified | `claim-unverified` | warning | per claim |
| Claim on a capped/broken artifact | `claim-at-risk` | error | per claim |

Exact texts:

- cap: `the manifest exceeds the tensorboard verification limit (10,000 entries or 16 MiB of tag
  metadata); no TensorBoard scalar was read`
- structural: `result {id!r} locator {display} could not be read under the strict TensorBoard
  profile in {path!r}`; hint `the TensorBoard event file does not satisfy the strict profile`
- unsupported scalar: `... selects an unsupported scalar encoding in {path!r}`; non-scalar:
  `... selects an unsupported value in {path!r}`
- recorded type: `... selects an unsupported value in {path!r}`
- not found: `... was not found in {path!r}`
- kind: `... needs a TensorBoard event artifact, not {path!r}`
- over-cap: `was not read because the artifact exceeds {50|128} MiB`
- value: `result {id!r} locator {display} does not match the recorded value in {path!r}`
- directory hint 0.5: `Point the result at a single JSON, JSONL, CSV or TensorBoard event file
  artifact, or drop the locator.`
- `result-unverified` 0.5: `result {id!r} has no locator in schema 0.5; its recorded value was not
  checked`; `claim-unverified` 0.5: `Claim {id!r} cites only results with no locator in schema
  0.5; their recorded values were not checked`.

Hints are `None` except structural, directory, the reused no-digest/volatile hints and the
`claim-at-risk` cap hint `a resource cap is not a change; narrow the manifest or raise the limit`.

## 8. Display and privacy (D6)

- Human display: `tensorboard:step=<step>,occurrence=<n>`; no tag. It is not unique across tags;
  the result id/name and artifact path disambiguate.
- `display_locator` returns this string directly for a dict or model before payload escaping;
  `format_run_details`, `format_claim_details`, `format_claim_lineage` humanize nested `csv-cell`
  and `tensorboard` locators from display copies; `--json` keeps `tag`.
- Findings never echo the tag, scalar, protobuf/CRC text or OS text. The promise covers locator
  rendering and source-derived diagnostics, **not** a recorded `Result.name` (which may be the tag
  and is printed as recorded data).
- Canary design: use a tag token that differs from every result name; assert it is absent from
  run/claim/lineage human output and from all findings, present in `--json`. Test recorded names
  separately, including terminal controls and spreadsheet prefixes. 0.3/0.4 human bytes unchanged.

## 9. Adapter (D7)

Optional package `packages/runevidence-tensorboard`; core verification is unchanged if absent.

- **Discovery**: every regular file other than RunEvidence's own manifest and backups is recorded
  as an artifact (generic behavior, so untracked checks behave); the TensorBoard predicate is exactly §4.4 (`name.lower()` contains `.tfevents`); non-event artifacts keep the generic adapter's ids and have no `run_id`, event
  artifacts use `tb-art-`. The adapter does **not** create `runs/<name>/` folder runs (the generic
  adapter's folder-run convention is not inherited). **All fixed `tb-art`/`tb-run`/`tb-res` ids
  are reserved in `used` before any generic allocation.** Generic file slugs are then allocated
  in discovery order and suffixed on any collision (including a collision with a fixed id). Only
  a fixed-vs-fixed collision (two event files hashing to the same id) raises `AdapterError` with
  `a generated id collides with an existing id; nothing was written`; `duplicate-id` is not used.
  Event basenames (`.tfevents`) additionally produce scalar results. Zero event files is not an
  error: artifacts only.
- **Selection v1**: all supported scalars, record order, then `Summary.Value` order. No `--tags`
  flag and no parser change. Tags that are absent/empty, invalid UTF-8, or longer than 4,096 UTF-8
  bytes cannot be represented; if any selected supported scalar has such a tag, the adapter raises
  `AdapterError` (nothing written). Plugin pseudo-scalars (e.g. a float placeholder from another
  plugin) are selected in v1; plugin filtering is later work.
- **One read**: hash and parse the same bytes (one protected read/snapshot); a change detected
  between them refuses the file.
- **Strict profile during extract**: a truncated/over-cap (129 MiB)/malformed event file raises
  `AdapterError` with a fixed message and writes nothing. Exact texts:
  - over-cap: `the event file exceeds the 128 MiB TensorBoard limit; nothing was written`
  - profile: `the event file does not satisfy the strict TensorBoard profile; nothing was written`
  - unrepresentable tag: `a scalar tag cannot be represented in schema 0.5; nothing was written`
  - budget: `the selected TensorBoard scalars exceed the 10,000-result or 16 MiB tag limit;
    nothing was written`
- **Budget per manifest**: at most 10,000 tensorboard results and 16 MiB of tag bytes across all
  event files in one `init`; otherwise `AdapterError`, nothing written.
- **Identity** (one run per event file, never per folder):
  - artifact id `tb-art-<16 hex of SHA-256(UTF-8 of the recorded Artifact.path)>`.
  - run id `tb-run-<16 hex of SHA-256(UTF-8 of the recorded Artifact.path)>`; run name is the
    recorded relative path (`PurePosixPath(path).as_posix()`), so `a/events...` and `b/events...`
    differ and root files keep their name. Two event files in one folder are
    two runs; disjoint tags never merge into a multi-file run.
  - result id `tb-res-<16 hex of SHA-256(UTF-8(run_id) + 0x00 + UTF-8(tag) + 0x00 +
    UTF-8(str(step)) + 0x00 + UTF-8(str(occurrence)))>`; decimal step/occurrence, literal NUL
    separators; valid under `ID_PATTERN`, tag-free. Identical tags in different files get
    different ids through `run_id`. A collision raises `AdapterError`.
  - result name is step-qualified: `tag @ step`, with ` #occurrence` when occurrence > 0. A bare
    tag would trigger `conflicting-evidence` for a multi-step series under one run; this name is
    unique per result and is recorded data (see §8).
- **Interface**: `Adapter.discover/extract` signatures stay unchanged; the adapter returns
  `Manifest05`. No warning channel is added.
- **Volatile event targets**: an “event artifact” is an artifact targeted by a `tensorboard`
  locator in the extracted `Manifest05`. Only in that case, and before `_mark_volatile` (the
  frozen `Adapter.extract` signature has no volatile channel), a matching `--volatile` glob stops
  with `an event artifact cannot be volatile; nothing was written`. Generic `init` behavior is
  unchanged.
- **init --force**: a 0.5-capable binary refuses an existing 0.5 manifest with
  `INIT_0_5_REFUSAL` (below); a pre-0.5 binary backs up and replaces it. The frozen 0.4 refusal
  text still says “regenerate it as schema 0.1”; with the TensorBoard adapter the regeneration
  target would be 0.5, so the text is adapter-dependent and documented here; 0.4 bytes stay
  frozen.

## 10. Compatibility, migration and CLI (D8)

- 0.1-0.4 unchanged; 0.4 byte-frozen.
- 0.3/0.4 → 0.5: version-only bump; csv-cell/jsonl/string locators keep working, and csv-cell
  keeps its independent cap.
- 0.1/0.2 → 0.5: manifest-only precheck; refuse before writes.
- 0.5 → 0.4/0.3/0.2: `DOWNGRADE_UNSUPPORTED`.
- `TARGET_VERSIONS` +0.5; `UNSUPPORTED_TARGET` becomes `only --to 0.2, --to 0.3, --to 0.4 or
  --to 0.5 is supported; downgrade and other versions are not available. Nothing was written.`
  `--to` help and migrate docstring move.
- Conditions 0.5 text: `ERROR (UNSUPPORTED_VERSION): the conditions command requires an
  explicit schema 0.2 manifest. A schema 0.1 manifest can be migrated with runevidence migrate
  PATH --to 0.2; schema 0.3, 0.4 and 0.5 have no downgrade path. Nothing was written.`
- `INIT_0_5_REFUSAL`:

  ```
  ERROR: the existing manifest declares schema 0.5. init --force would regenerate the manifest
  and cannot carry over first-class values or locators. Nothing was written. Move the manifest
  aside, run init, then run runevidence migrate PATH --to 0.5 (unless the adapter writes 0.5
  itself) and reapply the first-class values and locators.
  ```

  (0.4 keeps its frozen text.)
- `rehash`: string-only succeeds; any object locator (jsonl, csv-cell, tensorboard) refuses
  `LOCATOR_REFRESH_UNSUPPORTED`.
- Comparability/fingerprint/conditions refuse 0.5; `schema` stays file/docs-only.
- Schema 0.5 vector notes: `maxLength` counts code points and `type: integer` accepts `1.0` at the
  schema level; the model enforces 4,096 UTF-8 bytes and `StrictInt`, as documented on the fields.
- Fixtures: `reject-unknown-version.json` and 0.5-as-unknown tests move to 0.6; 0.5 schema freeze;
  dispatcher/CLI tests; `cli.py` hash pins.

## 11. Parent contract errata → v0.9 (D9) — exact quotes

Reissue `docs/verified-value-locators-rfc.md` as **v0.9**. Each item quotes the v0.8 text and its
replacement exactly (whitespace-normalized; items apply in order). Docs tests pin these strings.

1. §4.1 memory sentence: replace “Aggregate memory is one artifact snapshot at a time (released
   after its locators are checked), capped at 50 MiB.” with “Aggregate memory is one artifact
   snapshot at a time (released after its locators are checked), capped at 50 MiB, or at 128 MiB
   when the artifact is targeted by a kind-eligible TensorBoard locator.”
2. §4.1 snapshot scope: replace “Only artifacts targeted by a 0.3 or 0.4 locator (string, `jsonl`
   or `csv-cell`) get a retained in-memory snapshot.” with “Only artifacts targeted by a 0.3, 0.4
   or 0.5 locator (string, `jsonl`, `csv-cell` or `tensorboard`) get a retained in-memory
   snapshot.”
3. §4.1 over-cap sentence: replace “while each locator on the normal path emits
   `locator-unreadable`; a csv-cell-capped artifact (eligible csv-cell entries in a capped
   manifest) emits one cap finding instead, one per eligible artifact (including an over-cap
   artifact).” with “while each locator on the normal path emits `locator-unreadable`; a capped
   artifact emits one cap finding per capped kind, one per eligible artifact and kind (including
   over-cap artifacts).”
4. §4.2 step 4: replace “except on a csv-cell-capped artifact, where no value is compared” with
   “except for a locator kind whose artifact is capped for that kind, where no value is compared”.
5. §4.3 cap sentence: replace “A csv-cell-capped artifact emits at most one cap finding, alongside
   any hash/size or kind finding; no value is compared.” with “A capped artifact emits at most one
   cap finding per capped kind, alongside any hash/size or kind finding; no value is compared for
   that kind, and other kinds on the same artifact still verify. There is one cap finding per
   eligible artifact and kind in state `ok` or `over-cap`.”
6. §4.3 table: after the row

   ```
   | Directory artifact locator | `directory-locator-unsupported` (existing) | error | 1 |
   ```

   insert

   ```
   | TensorBoard scalar cap | `locator-unreadable` (new) | error | 1 |
   ```

   and replace

   ```
   | Unsupported target type / non-scalar | `locator-unsupported-value` (new) | error | 1 |
   ```

   with

   ```
   | Recorded type / unsupported scalar / non-scalar | `locator-unsupported-value` (new) | error | 1 |
   ```
7. §5.2 caps bullet: replace the exact v0.8 bullet

   > Caps: file 50 MiB per artifact; record 1 MiB, field 64 KiB, rows 1,000,000, columns 1,024 and
   > total cells 10,000,000 per artifact, never summed across artifacts; csv-cell locator results
   > 10,000 and csv-cell header metadata 16 MiB per manifest, counting only manifest-eligible
   > entries (non-directory `.csv` artifact with a recorded sha256, not volatile), without reading
   > a file. Lengths are bytes of decoded UTF-8; the file cap uses raw on-disk bytes (a leading
   > BOM counts) and the record cap uses raw record bytes after BOM strip, excluding the
   > terminator.

   with the same text plus “A TensorBoard target uses a 128 MiB file cap and a 16 MiB merged
   summary payload cap per Event; TensorBoard results and tag metadata are capped at 10,000 entries and
   16 MiB per manifest, counting only manifest-eligible entries (non-directory file whose
   basename contains `.tfevents`, a recorded sha256, and not volatile), without reading a file.”
8. §5.4 comparator: replace “**Comparator:** 0.3 and 0.4 locators (string, `jsonl` and
   `csv-cell`) use a new comparator:” with “**Comparator:** string, `jsonl` and `csv-cell`
   locators (0.3, 0.4 and 0.5) use a new comparator:”. After the paragraph ending “even though
   it approximates adjacent large integers.”, insert: “TensorBoard locators use
   `compare_tensorboard`: `rel_tol=1e-6, abs_tol=1e-12` for float32-origin values;
   `rel_tol=1e-9, abs_tol=1e-12` for float64; a TensorBoard int recorded value uses the actual
   value's origin tolerance; a recorded string, boolean or null is `locator-unsupported-value`.
   The mixed int/float `1e-9` rule remains scoped to string, `jsonl` and `csv-cell` locators
   (including 0.5).”
9. §6 display: replace the exact substring “`csv-cell:41,4`). Pointer components escape” with
   “`csv-cell:41,4`; `tensorboard:step=40,occurrence=0`). Pointer components escape”.
10. §7 privacy: replace “a `csv-cell` finding never names the header.” with “a `csv-cell` finding
    never names the header, and a `tensorboard` finding never names the tag.”
11. §8 migration: after “old readers reject 0.3.” add “A 0.3/0.4 manifest migrates to 0.5 as a
    version-only bump; 0.5 has no downgrade.”
12. §9 gate 4: replace the whole item

    > 4. Snapshot: protected open for the initial hash, descriptor identity, path/FIFO swap
    >    before and during the read, containment, one snapshot per targeted artifact, streaming
    >    for others, aggregate memory, an explicit 60 MiB over-cap fixture (hash still checked,
    >    locators `locator-unreadable` on the normal path; a csv-cell-capped artifact (eligible
    >    csv-cell entries in a capped manifest) emits one cap finding, one per eligible artifact
    >    (including an over-cap artifact)), no cross-run cache.

    with

    > 4. Snapshot: protected open for the initial hash, descriptor identity, path/FIFO swap before
    >    and during the read, containment, one snapshot per targeted artifact, streaming for
    >    others, aggregate memory, an explicit 60 MiB over-cap fixture for 50 MiB kinds and a
    >    129 MiB fixture for TensorBoard targets (hash still checked; normal-path locators
    >    `locator-unreadable`; a capped artifact emits one cap finding per capped kind, one per
    >    eligible artifact and kind, including over-cap artifacts), no cross-run cache.
13. §9 gates: append a new gate 8 “TensorBoard adds framing/CRC/protobuf vectors:
    duplicate singular fields, message merge, oneof interleaving, packed/unpacked values, the
    merged summary cap, and the 50/128 MiB overlap.”
14. Status block (v0.8 lines 3-7): replace “TensorBoard and other adapters remain later slices.” with “TensorBoard
    scalar locators are specified by the slice D design RFC; other adapters remain later
    slices.”
15. §2 scope: replace “Out of scope: TensorBoard (separate RFC), computed/aggregate values, YAML
    manifests,” with “Out of scope: computed/aggregate values, YAML manifests,”.
16. §4.3 mismatch row: replace

    ```
    | Line/row/column out of range | `locator-mismatch` (new) | error | 1 |
    ```

    with

    ```
    | Line/row/column/occurrence out of range | `locator-mismatch` (new) | error | 1 |
    ```
17. §3.1 kind rule: replace “`kind` must match the artifact extension, case-insensitively: `jsonl`
    on `.jsonl`, `csv-cell` on `.csv`.” with “`kind` must match the artifact, case-insensitively:
    `jsonl` on `.jsonl`, `csv-cell` on `.csv`, and `tensorboard` on a basename containing
    `.tfevents`.”
18. §3.1 absent locator: replace “in 0.3 and 0.4.” with “in 0.3, 0.4 and 0.5.”
19. §9 gate 1 extension: replace “extension matching (`.json`/`.jsonl`/`.csv`; `.ndjson`/`.tsv`
    refused)” with “extension matching (`.json`/`.jsonl`/`.csv`, or a TensorBoard basename
    containing `.tfevents`; `.ndjson`/`.tsv` refused)”.
20. §9 gate 2 absent locator: replace “absent locator (0.3 and 0.4)” with “absent locator (0.3,
    0.4 and 0.5)”.
21. §2 in-scope: replace “In scope: a structured locator union in an opt-in schema; the `jsonl`
    kind implemented first; the `csv-cell` kind specified but admitted only with its verifier;
    verification semantics, outcomes, privacy, limits, compatibility and gates.” with “In scope:
    a structured locator union in an opt-in schema; the `jsonl` kind implemented first; the
    `csv-cell` kind specified but admitted only with its verifier; the `tensorboard` kind
    specified for slice D; verification semantics, outcomes, privacy, limits, compatibility and
    gates.”
22. §8 readers: replace “New readers read 0.1/0.2 without rewriting them; old readers reject 0.3.”
    with “New readers read 0.1-0.5 without rewriting them; old readers reject 0.3, 0.4 and 0.5.”
23. §6 fingerprint: replace “Fingerprint v1 is frozen: it accepts schema **0.1 only** and refuses
    both 0.2 and 0.3.” with “Fingerprint v1 is frozen: it accepts schema **0.1 only** and refuses
    0.2, 0.3, 0.4 and 0.5.”
24. §9 gate 1 0.5 matrix: replace “`artifact_id` required for any non-null 0.3 locator; extension
    matching (`.json`/`.jsonl`/`.csv`, or a TensorBoard basename containing `.tfevents`;
    `.ndjson`/`.tsv` refused); directory artifact behavior with the flag on and off;
    `not-a-file`” (without its final period) with the same text plus “; a 0.5 matrix
    (Manifest05/Result05 round-trip, tensorboard tag/step/occurrence and range errors, 0.5
    string/jsonl/csv-cell carry-over, and the 0.5 `artifact_id` cross-field rule)”
25. Header/status: replace “# Verified value locators: design RFC (v0.8)” with “# Verified value
    locators: design RFC (v0.9)” and “Status: **v0.8 ERRATA FOR SLICE C.**” with “Status:
    **v0.9 ERRATA FOR SLICE C AND SLICE D.**”.
26. §9 gate 6: replace “v1 refuses 0.2 and 0.3” with “v1 refuses 0.2, 0.3, 0.4 and 0.5”, and
    “old readers reject 0.3;” (with its semicolon) with “old readers reject 0.3, 0.4 and 0.5;”.
27. §4.2 race sentence: after “`artifact-changed` is the only race signal.” add “The
    TensorBoard-only size-race rows also classify a file whose read ends past its cap as
    over-cap.”.

## 12. Verification plan

- **Framing**: CRC32C vectors; masked CRC; zero-length records; truncated length/data/trailing
  bytes; CRC mismatch; length over remaining; header-only and empty files; zero scalar entries.
- **Protobuf**: field 0/invalid keys; varint bounds/sign; non-minimal; packed/unpacked
  `float_val`/`double_val`; LE content; duplicate singular last-wins; repeated summary/tensor/
  shape merge; oneof interleaving (merge only after the last different member); tensor-last after
  simple_value; duplicate tag/Dim.size/tensor_content last-wins; wrong wire type; depth by schema;
  `Dim.size` default 0; out-of-int32 dtype.
- **Classification**: rank-0/[1]; rank-2 `[[0.5]]`; no winning member; no encoding; both content
  and `*_val`; dtype/field mismatch; half/bfloat16/int/string; histogram/image/audio; mixed
  unsupported+non-scalar; occurrence counting.
- **Selection**: absent step = 0; negative step; duplicate `(tag, step)` with occurrences 0 and 1
  both requested; occurrence beyond; long/invalid-UTF-8 event tags unmatchable; exact tag bytes.
- **Numbers**: 1e-6/1e-9 boundaries; int recorded; `10**400` overflow fallback; NaN/inf; 0.4
  regression at 1e-9.
- **Precedence**: directory, missing/path, kind, volatile/no-digest, per-kind over-cap, per-kind
  structural, capped, hash/size, value; mixed `.tfevents.csv` with valid CSV and invalid TFRecord;
  both kinds capped → two findings; cap + non-matching kind.
- **Framing/protobuf**: a 15 MiB summary, then `file_version`, then 15 MiB is accepted; 15+15
  with nothing between is refused; 17 MiB then `graph_def` is accepted (losing summary); T1
  `tensor{dtype=DT_FLOAT, tensor_content=4B}` then T2
  `tensor{tensor_shape=[1]}` (no dtype/content) keeps DT_FLOAT and the 4 bytes; duplicate
  `tag`/`Dim.size`/`tensor_content` last-wins. A 16 MiB summary of ~8.4M empty Values and one
  with ~1.6M tagged scalars
  must verify inside the asserted ≤300 MB budget (no value cap).
- **Snapshot races**: fstat 10,000,000 then EOF 10,000,300 → `artifact-changed`; EOF 9,999,700 →
  `artifact-changed`; fstat 100 MiB then EOF 129 MiB → over-cap; fstat 140 MiB then EOF 100 MiB →
  `artifact-changed`; fstat 140 MiB then EOF 128 MiB → `artifact-changed` (EOF == cap); fstat
  140 MiB then EOF 140 MiB → over-cap; fstat 140 MiB then EOF 141 MiB → over-cap; a 0.4 manifest
  keeps the delivered read-to-EOF behavior.
- **Caps/memory**: 50/128 MiB; 16 MiB merged summary; 10,000/10,001 and 16 MiB tag edges; 4.4M
  minimal scalar records in 127 MiB; a 16 MiB summary of ~8.4M empty Values and one of ~1.6M
  tagged scalars, both asserted inside the ≤300 MB budget; multiple occurrences; many unrequested
  tags. The 67M-empty-summary record is a record-only wall/memory vector, outside the (a)-(c)
  wall assertion.
- **Privacy**: tag token distinct from names; absent from human outputs/findings, present in JSON;
  0.3/0.4 bytes.
- **Migration/CLI**: 0.3/0.4→0.5 version-only; 0.1/0.2 precheck; downgrade; texts; init refusal;
  fixtures 0.6; a 0.4→0.5 round-trip preserves `conditions`, `dataset_id` and
  `metric_definition`; two event files in one folder and two root files produce distinct run and
  result ids.
- **Oracle/fuzz**: checked-in real TensorBoard fixtures (legacy, `tensor_content`, packed/unpacked
  `float_val`/`double_val`, graph record) with provenance and hashes, plus a test-only writer;
  seeded framing/protobuf mutation fuzz over **`check_files` with the targeted callback** (which
  invokes `check_targeted_results`); graph, conditions and claim passes are excluded and covered
  by their own tests. Because the file-check stage is included, `missing-file`, `hash-mismatch`
  and `size-mismatch` are reachable. **Seed corpora:** (i) byte mutations of a valid event file
  where the recorded sha256 *and* size are recomputed for every generated artifact, so no
  incidental hash/size finding; (ii) explicit seeds with a wrong recorded digest and a wrong size;
  (iii) manifest variants — a missing file on disk (not a dangling id), volatile, digestless,
  digestless with only a wrong-kind locator (for `no-digest`), a directory artifact with
  `enable_directory_artifacts=False` (so `not-a-file` is expected), csv-cell kind mismatch,
  10,001-entry cap, wrong value, unsupported scalar, non-scalar, and a valid file with an absent
  `(tag, step)` or occurrence beyond the supported count (for `locator-mismatch`). The closed set
  is the §7.3 codes and messages; the **exact reachable subset** is `missing-file`, `not-a-file`,
  `directory-locator-unsupported`, `locator-kind-mismatch`, the TensorBoard cap, `no-digest`,
  `locator-no-digest`, `locator-unverified`, `locator-unreadable`, `locator-mismatch`,
  `locator-unsupported-value`, `result-value-mismatch`, `hash-mismatch`, `size-mismatch`.
  Graph/dangling/claim/schema codes are outside this harness (upper-bound members only). Every
  emitted finding must be a member; no parser/OS text.
- **Benchmarks** (`ubuntu-latest`, 4 vCPU/16 GiB, Python 3.12): (a) 10,000 scalar events, 10
  results; (b) 1M events near 100 MiB, 10 results; (c) 127 MiB of minimal scalar records (~30 B).
  Wall ≤30 s (CRC ~7.5 s + decode ~8.6 s measured), `VmHWM − baseline` ≤300 MB, Linux-only with
  counted skips.

## 13. Departures (closed list)

- Zero-length records are valid and skipped; empty/zero-scalar files are valid and yield
  `locator-mismatch`.
- Truncated/trailing bytes, CRC mismatch, invalid field keys, wrong wire types, groups, and a
  winning/merged `summary` over 16 MiB are structural refusals.
- Message fields merge; scalar singular fields last-wins; oneof members replace; losing members
  are not decoded.
- Event tags are bytewise, not UTF-8-validated or runtime-capped; `file_version` is not validated;
  `wall_time`, `node_name`, `metadata` are ignored.
- `tensor_content` is little-endian; unknown fields are skipped; non-minimal varints accepted.
- Out-of-int32 dtype enums are unsupported scalars.
- The 10th varint byte must be ≤0x01 (C++ protobuf truncates instead).
- Losing oneof *payloads* are never validated; their keys and wire types are checked. Protobuf
  parses the payload and fails on malformed input, so the profile is not TensorBoard-equivalent
  for a malformed losing payload.
- A known field with the wrong wire type is refused (protobuf treats it as unknown).
- Only float/double rank-0/[1] scalars are indexed; other shapes/dtypes are unsupported.
- One file is one artifact; multi-file runs and tag filtering are out of scope.

## 14. Risks and open points

- Dependency-free parser is security-relevant; fuzzing and bounds are mandatory.
- Pure-Python CRC/decoding is near the 30 s ceiling at 128 MiB; the benchmark records the margin.
- TensorBoard is not a versioned contract; real fixtures limit drift.
- float32 tolerance differs from JSON/CSV; documented.
- Tags may contain secrets; display hides them, recorded names may not.
- One-file-one-run and no tag filtering exclude common workflows.
- 128 MiB and one-snapshot exclude very large logs; a winning/merged summary over 16 MiB
  refuses the file.
- Cap findings conservatively mark the artifact broken, so a verified result of another kind on the
  same artifact can raise claim risk; intended and tested. The 0.5 `claim-at-risk` finding gains
  the hint `a resource cap is not a change; narrow the manifest or raise the limit`; the reused
  “missing or changed” text is documented as imprecise.

## 15. Decisions requested

- **D1:** core-owned dependency-free reader; optional adapter; no plugin verification.
- **D2 (owner):** admit schema 0.5 with `Manifest05`/`Result05`/`TensorboardLocator`.
- **D3:** strict profile: framing, protobuf merge/oneof, numeric rank-0/[1] scalars, field-key
  validity.
- **D4:** per-kind file caps (50/128 MiB), summary 16 MiB, independent eligibility caps, no runtime
  tag cap, single-copy snapshot, O(requested) retention.
- **D5:** normal/capped per-kind paths and the outcome table.
- **D6:** `tensorboard:step=<step>,occurrence=<n>` display; tag only in JSON; names are recorded
  data and outside the locator-secrecy promise.
- **D7:** optional adapter, all scalars in v1, per-manifest refusal, tag-free digest ids, fixed
  failure texts.
- **D8:** migration/texts and the truthful 0.5 init refusal.
- **D9:** parent v0.9 verbatim errata.

## 16. What acceptance authorizes

A separate implementation gate for schema 0.5 with the core reader and adapter under these rules,
plus the parent v0.9 errata. It does not authorize release, version bump, publication, multi-file
logs, other summary kinds, tag filtering, or parity claims.
