# TensorBoard scalar locators — slice D design RFC (v0.2)

Status: **DESIGN PROPOSAL. NOTHING IMPLEMENTED. NOTHING AUTHORIZED BY THIS DOCUMENT.**
A design gate and an owner schema-admission decision (D2) are required before any code.

## 0. Changes from v0.1 (round-1 findings)

- **Per-kind file cap**: 128 MiB only for an artifact targeted by a matching `tensorboard`
  locator; 50 MiB everywhere else. The snapshot is read once into a preallocated buffer (no
  `bytearray` → `bytes` copy); 0.3/0.4 over-cap text and behavior are unchanged.
- **Streaming selection, no index**: one pass keeps at most one decoded value per requested
  result plus occurrence counters; a dense 128 MiB file cannot allocate a per-event index.
- **Scalar defined by decoded shape**, not payload length: rank-0, or rank-1 with one dim of
  size 1; dtype↔encoding table; content-vs-`*_val` conflicts are unsupported scalars.
- **Protobuf semantics pinned**: oneof last-wins (including `Event.what` and `Summary.Value`),
  duplicate singular fields last-wins, packed and unpacked numerics, int64 sign/10th byte,
  wire-type mismatch refusal, uint64 overflow, depth 8.
- **Selection classification** for supported scalar / unsupported scalar / malformed /
  non-scalar, including occurrence counting and the single finding per result.
- **Capped path separated from the normal path** and placed right after kind, before any read;
  hashing is not parsing.
- **Number comparison** keeps the delivered `compare_values` at `1e-9` for everything; a separate
  TensorBoard comparator applies `1e-6` only to float32-origin values. NaN/Inf follow the
  comparator (finite vs NaN is `result-value-mismatch`).
- **Kind overlap**: a `.tfevents`/`.csv` overlap matches both kinds; each verifier applies its own
  profile and cap, and no kind mismatch is manufactured.
- **Adapter**: no new CLI flag; all scalar tags, per-manifest budget, deterministic refusal,
  tag-free result ids, run id from the relative path.
- **Complete 0.5 consumer inventory**, exact texts, parent v0.9 errata, fixture moves, schema
  artifact, departures list, oracle with real fixtures and benchmarks.

## 1. Problem and evidence

- TensorBoard `tfevents` files are the default training log for many frameworks. The `generic`
  adapter records the file, but no result can point at a scalar, so `loss/val` at step 40 is
  unverifiable.
- The adapter memo ranks TensorBoard last: TFRecord framing, CRC, protobuf, duplicates from
  restarts, TF2 `tensor` versus legacy `simple_value`, and a dependency/security gate. It also
  notes that locator checking lives in core.
- 0.3 (JSONL) and 0.4 (CSV cells) established the pattern this slice follows.

## 2. Scope and non-goals

In scope: one event file per artifact; scalar summaries; occurrence-aware locator; strict event
profile with CRC; caps; outcomes; display/privacy; migration; optional adapter; verification.

Out: histograms/images/audio/text/graphs as results, TensorBoard UI/DB, multi-file or directory
runs, tag filtering in v1, remote/streamed logs, other adapters, YAML, parity, release, and any
change to 0.1-0.4 behavior.

## 3. Architecture (D1)

**Core owns verification** with a dependency-free reader. `validate` must produce the same result
on every install; an optional plugin cannot decide whether a manifest is valid. The strict JSON,
JSONL and CSV profiles are already in core.

Alternatives:

- **Plugin verification** — rejected: availability-dependent results, and
  `locator-verifier-unavailable` is defensive/unreachable by design.
- **Pinned `protobuf` runtime with checked-in generated code** — deterministic, but adds a native
  dependency and upstream CVE surface to core. Rejected for this slice; the memo's dependency
  gate can revisit it only with a separate decision.

The dependency-free parser is itself security-relevant: its gate is strict bounds, caps, CRC and
fuzzing (§12), not the absence of a dependency.

## 4. Schema 0.5 (D2)

**Admit `0.5`; 0.4 stays byte-frozen.** `0.5` adds `tensorboard` and keeps string, `jsonl` and
`csv-cell`.

### 4.1 Models and dispatch

- `SCHEMA_VERSION_0_5 = "0.5"`; base `Manifest` stays `Literal["0.1","0.2","0.3"]`.
- `TensorboardLocator(_Strict)`: `kind: Literal["tensorboard"]`, `tag: StrictStr`
  (`min_length=1`, `max_length=4096`, 4096 UTF-8 bytes at runtime), `step: StrictInt`
  (`-2**63 .. 2**63-1`), `occurrence: StrictInt` (`0 .. 2**31-1`, required; see §4.3).
- `Result05` with `locator: Annotated[Optional[Union[StrictStr, JsonlLocator, CsvCellLocator,
  TensorboardLocator]], BeforeValidator(_dispatch_locator_05)] = None`; the dispatcher mirrors
  0.4 (str pointer grammar, model instance, dict through a module-level discriminated adapter,
  fixed `value_error` otherwise), and the field is defaulted so `result-unverified` stays
  reachable.
- `Manifest05`: `schema_version: Literal["0.5"]`, `results: list[Result05] =
  Field(default_factory=list)`, cross-field rule: any non-null locator requires `artifact_id`.
- `_version_specific_shape` gains a 0.5 branch (artifact_id; string/jsonl/csv-cell/tensorboard
  admitted). The inherited `else` must never see a 0.5 locator.
- `validate_manifest` dispatches only on an explicit `"0.5"`; `is_manifest` accepts all three
  manifest classes; `model_dump` treats 0.5 like 0.2-0.4 (`conditions`, `dataset_id`,
  `metric_definition` preserved); `artifact_to_dict(..., "0.5")` accepts.
- `json_schema_0_5()`: draft 2020-12, `$id urn:runevidence:manifest:0.5`, root `Manifest05`,
  `$defs` `Result05`/`JsonlLocator`/`CsvCellLocator`/`TensorboardLocator`, a hand-built `oneOf`
  for the locator (null/string/three objects), integer ranges, `.tfevents` description overrides
  (no 0.3/0.4-only text), `if`/`then` artifact_id; written to
  `schema/runevidence-0.5.schema.json` and pinned by `test_schema_freeze.py`.

### 4.2 Locator shape

```json
{"kind": "tensorboard", "tag": "loss/val", "step": 40, "occurrence": 0}
```

- `tag` is the exact event tag (no normalization, no case folding, no glob).
- `step` is the signed int64 event step; an absent `Event.step` means protobuf default `0`.
- `occurrence` is the zero-based position among the **supported scalar** entries with that exact
  `(tag, step)` in file order (record order, then `Summary.Value` order). It is required: an
  omitted field is a load error, never a silent "first".
- A `tensorboard` locator requires a `.tfevents` basename (§4.4).
- Tags longer than 4,096 UTF-8 bytes are model-rejected at load (`string_unicode` is separate);
  the field cap is a schema cap, not a runtime read outcome.

### 4.3 Serialization and diagnostics

- `locator_to_json`/`dump_manifest` write all four fields; `occurrence` is a normal required field
  (no `_zero_two_only_fields` exception).
- Unknown/missing kind: `schema-invalid`; `union_tag_*` locations append `/kind`.
- Pinned vectors (type, location, category):

| Input | Type | Location | Category |
|---|---|---|---|
| missing `tag` | `missing` | `/results/0/locator/tag` | `missing_required` |
| missing `step` | `missing` | `/results/0/locator/step` | `missing_required` |
| missing `occurrence` | `missing` | `/results/0/locator/occurrence` | `missing_required` |
| `tag` with 4,096 code points but >4,096 UTF-8 bytes | `value_error` | `/results/0/locator/tag` | `invalid_value` |
| `step: 1.0` | `int_type` | `/results/0/locator/step` | `invalid_type` |
| `step: 2**63` | `less_than_equal` | `/results/0/locator/step` | `invalid_value` |
| `step: -2**63-1` | `greater_than_equal` | `/results/0/locator/step` | `invalid_value` |
| `occurrence: -1` | `greater_than_equal` | `/results/0/locator/occurrence` | `invalid_value` |
| `occurrence: 2**31` | `less_than_equal` | `/results/0/locator/occurrence` | `invalid_value` |
| empty `tag` | `string_too_short` | `/results/0/locator/tag` | `invalid_value` |
| unknown kind | `union_tag_invalid` | `/results/0/locator/kind` | `invalid_value` |
| missing kind | `union_tag_not_found` | `/results/0/locator/kind` | `missing_required` |
| extra key | `extra_forbidden` | `None` | `invalid_value` |
| non-str/non-dict locator | `value_error` | `/results/0/locator` | `invalid_value` |
| lone surrogate tag | `string_unicode` | `/results/0/locator/tag` | `other_validation` |

`_LOCATOR_TAGS` gains `tensorboard`; `_issue_location` maps it before branch labels and forwards
the root model.

### 4.4 Basename and kind matching

`PurePosixPath(artifact.path.replace("\\", "/")).name.lower()` is the only filename source.

- `tensorboard`: basename contains `.tfevents`.
- `csv-cell`: basename ends `.csv`.
- `jsonl`: basename ends `.jsonl`.
- string: basename ends `.json`.

A basename may match more than one kind (`metrics.tfevents.csv`). Each locator matches by its own
kind; no kind mismatch is invented. A TensorBoard locator on a CSV-content file is a structural
refusal of that verifier, not a kind mismatch.

## 5. Event-file profile (D3)

The file is read once through the slice-B protected snapshot; the same bytes are hashed and
parsed. The snapshot is allocated once at the stat size (`readinto`/memoryview), never
`bytearray` + `bytes`.

### 5.1 TFRecord framing

- `uint64 length` little-endian, `uint32 masked_crc32c(length bytes)`, `byte[length] data`,
  `uint32 masked_crc32c(data)`; mask `((crc >> 15) | (crc << 17)) + 0xa282ead8 (mod 2**32)`.
- Zero-length records are valid (an empty `Event`) and skipped.
- A truncated length/data field, a CRC mismatch, or trailing bytes after the last record is a
  structural refusal.
- The 50/128 MiB file cap is checked before parsing. A record length that exceeds the remaining
  bytes is a structural refusal. There is no per-record byte cap; the cap that matters is the
  decoded `summary` size (§5.2).
- CRC32C is a table-driven core implementation; the benchmark records its cost.

### 5.2 Protobuf subset and oneof semantics

Only wire types 0 (varint), 1 (64-bit), 2 (length-delimited) and 5 (32-bit) are admitted. Groups
are refused. Known fields with the wrong wire type are a structural refusal. Unknown fields are
skipped by wire type, bounded by the record. Varints are at most 10 bytes; the 10th byte must be
`<= 0x01` for 64-bit values; non-minimal varints are accepted if they fit. Length delimiters must
fit the enclosing message. Nested depth is at most 8 (Event, Summary, Value, TensorProto,
TensorShape, Dim plus two).

- `Event`: `wall_time` (1) ignored; `step` (2, int64, absent = 0; duplicate = last wins);
  `what` is a **oneof** (`file_version` 3, `graph_def` 4, `summary` 5, `log_message` 6,
  `session_log` 7, `tagged_run_metadata` 8, `meta_graph_def` 9). A later member replaces the
  earlier one. Non-summary `what` members are CRC-checked and skipped without decoding their
  payload.
- `Summary`: `value` (1) repeated `Value`; duplicate `value` fields append.
- `Summary.Value` is a **oneof** (`tag` is a separate field; `simple_value` 2, `obsolete_old_style
  _histogram` 3, `image` 4, `histo` 5, `audio` 6, `tensor` 8; `metadata` 9 is separate). The last
  oneof member wins; `tag` is not part of the oneof.
- `TensorProto`: `dtype` (1, varint enum; duplicate last wins), `tensor_shape` (2),
  `tensor_content` (4, bytes), `float_val` (5, float; packed wire 2 or unpacked wire 5),
  `double_val` (6, double; packed wire 2 or unpacked wire 1). Packed length must be a multiple of
  4/8.
- `TensorShapeProto`: `dim` (2) repeated `Dim{size=1 int64}`; `unknown_rank` (3, bool).
- Duplicate singular fields (step, dtype, shape) follow last-wins; duplicate repeated fields
  append.

### 5.3 What is a scalar (classification)

For a `Summary.Value` with tag `t` and event step `s`:

1. **Supported scalar**:
   - `simple_value` is the winning oneof member; value = float32 widened to float; no shape.
   - or `tensor` is the winner, dtype `DT_FLOAT` or `DT_DOUBLE`, shape is rank-0 (`dim` empty,
     `unknown_rank == false`) or rank-1 with exactly one dim of size 1; exactly one encoding:
     `tensor_content` of 4 (float) / 8 (double) bytes XOR exactly one `float_val`/`double_val`
     element (packed or unpacked). `tensor_content` is little-endian IEEE-754.
2. **Unsupported scalar**: the winner is a tensor with a supported shape but another dtype
   (half, bfloat16, int, string, …), or an ambiguous encoding (both `tensor_content` and
   `*_val`, wrong element count for the dtype, rank-1 with dim size ≠ 1, rank ≥ 2), or
   `simple_value` plus a non-scalar winner (the winner decides).
3. **Non-scalar**: the winning member is histogram/image/audio or a tensor whose shape is not
   rank-0/[1].
4. **Malformed**: framing/protobuf errors; a whole-file structural refusal.

Occurrence counting:

- Only **supported scalars** with the requested `(tag, step)` increment occurrence. `occurrence`
  selects among them; unsupported/non-scalars never shift it.
- If no supported scalar matches:
  - the `(tag, step)` exists only as unsupported scalar(s) → `locator-unsupported-value`;
  - it exists only as non-scalar(s) → `locator-mismatch` (`was not selectable`);
  - it does not exist → `locator-mismatch` (`was not found`).
- If a locator occurrence is beyond the supported-scalar count but unsupported entries exist →
  `locator-mismatch` (`was not found`).

The pass retains only: requested `(tag, step)` keys, a per-key supported-scalar count and the one
selected value, and a per-key unsupported/non-scalar flag. It never holds an event index.

### 5.4 Numbers

- `simple_value` and `DT_FLOAT` are float32-origin; `DT_DOUBLE` is float64-origin.
- The delivered `compare_values` stays at `1e-9` for 0.3/0.4. A new
  `compare_tensorboard(recorded, actual, *, float32)` applies `rel_tol=1e-6, abs_tol=1e-12` for
  float32-origin values and `rel_tol=1e-9, abs_tol=1e-12` for float64-origin values; int/int is
  unreachable (decoded values are floats), so an int recorded value uses the same tolerance.
- NaN equals NaN; infinities equal themselves; finite vs NaN/inf is `result-value-mismatch`.
- Recorded `str`/`bool`/`None` is `locator-unsupported-value`.
- A decoded non-finite value is a value, not an unsupported one.

## 6. Caps (D4)

**Per-kind file cap**: the snapshot cap for an artifact is 128 MiB when at least one `tensorboard`
locator targets a basename containing `.tfevents`; otherwise 50 MiB. The over-cap message renders
the actual cap (`exceeds 50 MiB` or `exceeds 128 MiB`); 0.3/0.4 bytes are unchanged.

Per-artifact structural caps (a violation is a per-result `locator-unreadable`):

| Cap | Unit | Value |
|---|---|---|
| file | raw on-disk bytes | 50 MiB, or 128 MiB for a tensorboard target |
| `summary` message | decoded length | 16 MiB (a larger summary refuses the file structurally) |
| tag | UTF-8 bytes | 4 KiB |
| protobuf depth | nested messages | 8 |

Per-manifest eligibility caps, **independent per kind** (counted without reads; one cap finding per
eligible artifact and kind):

| Kind | Cap | Unit | Value |
|---|---|---|---|
| tensorboard | results | eligible entries | 10,000 |
| tensorboard | tag metadata | sum of tag UTF-8 bytes | 16 MiB |
| csv-cell | results | eligible entries (0.4 and 0.5) | 10,000 |
| csv-cell | header metadata | sum of header UTF-8 bytes (0.4 and 0.5) | 16 MiB |

Eligibility is the no-read basename predicate plus non-directory, `sha256` non-null, `volatile`
false. Case-insensitive. A 0.5 manifest can have both kinds; capping one does not cap the other.
An artifact can receive one cap finding per kind.

Memory: one snapshot (≤128 MiB), selected state O(requested results) (≤10,000 small tuples), no
event index. The budget is ≤300 MB of additional working set above the post-load baseline,
measured in a subprocess: load, `clear_refs`, baseline `VmRSS`, run, `VmHWM − baseline`; benchmarks
(a)-(c) assert it, manifest-proportional vectors are record-only.

## 7. Outcomes and precedence (D5)

### 7.1 Normal path (not capped)

1. Directory artifact: ordinary check (zero or more findings), then one
   `directory-locator-unsupported` per result; return.
2. File state: `missing`, `reference-failed`, `unreadable`, `changed`, `symlinked-parent`,
   `refused-path`, `refused-alias`, `escapes-root` → one artifact finding, zero per-result.
3. Kind/extension: per result `locator-kind-mismatch`; restore the artifact `no-digest` warning
   only when no usable locator remains and the state is `no-digest`.
4. Volatile → per-result `locator-unverified`; return.
5. No digest → per-result `locator-no-digest` (size already compared); return.
6. Over-cap file → per-result `locator-unreadable`; return.
7. Structural pass → per-result `locator-unreadable`; selection/compare suppressed.
8. Selection → `locator-mismatch` (`was not found` / `was not selectable`).
9. Recorded type → `locator-unsupported-value`.
10. Decode classification → `locator-unsupported-value`.
11. Compare → `result-value-mismatch`.

Artifact `hash-mismatch`/`size-mismatch` are emitted by the file check before per-result findings;
on state `ok` values are still compared, and a difference adds `result-value-mismatch`.

### 7.2 Capped path

For an artifact with eligible entries of a capped kind:

1. No eligible entries of that kind → the normal path for that kind.
2. File state/digest as step 2. Missing/path states → artifact finding, no cap.
3. Kind mismatches for non-matching locators, as step 3.
4. **Cap finding first**: one artifact-level `locator-unreadable` for that kind, artifact broken,
   and no scalar parse for that kind. Hashing and the digest/size check still happen (the file
   check already did them); “no read” means no scalar parse.
5. Other kinds on the same artifact follow their own normal/capped path independently.

### 7.3 Table and texts

| Condition | Code | Severity | Cardinality |
|---|---|---|---|
| Invalid 0.5 shape, kind, range, tag, pointer, artifact_id | `schema-invalid` | exit 2 | per load issue |
| Dangling artifact | `dangling-reference` | error | per result |
| Directory artifact (ordinary finding(s)) | existing codes | error/warning | zero or more per artifact |
| Directory artifact with a locator | `directory-locator-unsupported` | error | per result |
| Kind/extension mismatch | `locator-kind-mismatch` | error | per result |
| Missing file | `missing-file` | error | one per artifact |
| Unreadable/changed/symlinked-parent/refused-path/refused-alias/escapes-root/not-a-file | existing slice-B codes | error | one per artifact |
| Volatile | `locator-unverified` | error | per result |
| No digest | `locator-no-digest` | error | per result; size compared |
| Over-cap file | `locator-unreadable` | error | per result |
| TensorBoard cap | `locator-unreadable` (cap message) | error | one per eligible artifact per kind |
| Structural refusal | `locator-unreadable` (structural message) | error | per result |
| `hash-mismatch` / `size-mismatch` | existing codes | error | one per artifact; compared unless capped/over-cap/refused/volatile/no-digest |
| Tag/step not found or not selectable | `locator-mismatch` | error | per result |
| Recorded non-number | `locator-unsupported-value` | error | per result |
| Unsupported scalar encoding | `locator-unsupported-value` | error | per result |
| Value differs | `result-value-mismatch` | error | per result |
| Absent locator | `result-unverified` | warning | per result |
| Claim only from unverified | `claim-unverified` | warning | per claim |
| Claim on a capped/broken artifact | `claim-at-risk` | error | per claim |

Exact texts:

- cap: `the manifest exceeds the tensorboard verification limit (10,000 entries or 16 MiB of tag
  metadata); no TensorBoard scalar was read`
- structural: message `result {id!r} locator {display} could not be read under the strict
  TensorBoard profile in {path!r}`; hint `the TensorBoard event file does not satisfy the strict
  profile`
- not found: `... was not found in {path!r}`; not selectable: `... was not selectable in
  {path!r}`
- unsupported scalar: `... selects an unsupported scalar encoding in {path!r}`
- recorded type: `... selects an unsupported value in {path!r}`
- kind: `... needs a TensorBoard event artifact, not {path!r}`
- directory hint 0.5: `Point the result at a single JSON, JSONL, CSV or TensorBoard event file
  artifact, or drop the locator.`
- `result-unverified` 0.5: `result {id!r} has no locator in schema 0.5; its recorded value was not
  checked`; `claim-unverified` 0.5: `Claim {id!r} cites only results with no locator in schema
  0.5; their recorded values were not checked`.
- over-cap: `was not read because the artifact exceeds {50|128} MiB`.

Hints are `None` except the structural, directory and reused no-digest/volatile hints.

## 8. Display and privacy (D6)

- Human display: `tensorboard:step=<step>,occurrence=<n>`; no tag. It is not unique across tags;
  the result id/name and artifact path disambiguate.
- `display_locator` returns this string directly for a dict or model (before payload escaping);
  `format_run_details`, `format_claim_details` and `format_claim_lineage` humanize nested
  `csv-cell` and `tensorboard` locators from display copies; `--json` keeps the structured
  locator including `tag`.
- Findings never echo the tag, scalar value, protobuf/CRC text or OS text. The tag-secrecy promise
  covers locator rendering and source-derived diagnostics, not a recorded `Result.name` (which may
  legitimately be the tag).
- Canaries: a distinct tag token absent from run/claim/lineage human output and from cap,
  structural, kind, mismatch, no-digest/volatile, directory and value findings; present in
  `--json`; 0.3/0.4 human bytes unchanged; terminal-control and spreadsheet-prefix names tested
  separately.

## 9. Adapter (D7)

Optional package `packages/runevidence-tensorboard`; core verification is unchanged if it is
absent.

- **Discovery**: basenames containing `.tfevents`, deterministically sorted; each is a file
  artifact with digest and size.
- **Selection (v1)**: all supported scalar summaries, in record order then `Summary.Value` order.
  No `--tags` flag and no parser change; filtering is a later, separately authorized feature.
- **Budget per manifest**: at most 10,000 emitted tensorboard results and 16 MiB of tag bytes
  across **all** event files in one `init`. If the selection would exceed either limit, the
  adapter raises `AdapterError` with a fixed message and writes nothing (no silent truncation).
- **Identity**:
  - run id: `tb-` + first 16 hex of SHA-256 of the artifact's manifest-relative path; run name is
    the relative parent folder or file stem (recorded name, not an id).
  - result id: `tb-` + first 16 hex of SHA-256 of `run_id + "\0" + tag + "\0" + step + "\0" +
    occurrence`; valid under `ID_PATTERN`, tag-free, collision-checked against the manifest.
  - result name: the tag; `value` is the decoded scalar; the locator is exact.
- **Interface**: `Adapter.discover/extract` signatures stay unchanged. The adapter returns
  `Manifest05`; core's dispatcher accepts it. No warning channel is added: budget refusal is an
  `AdapterError` (exit 2, nothing written), documented.
- `init --force` on an existing 0.5 manifest refuses with `INIT_0_5_REFUSAL` (§10).

## 10. Compatibility, migration and CLI (D8)

- 0.1-0.4 unchanged; 0.4 byte-frozen and refuses `tensorboard`.
- 0.3/0.4 → 0.5: version-only bump; values/locators/backups preserved; a 0.4 csv-cell stays
  verified under 0.5 and keeps its independent cap.
- 0.1/0.2 → 0.5: manifest-only precheck (defensive object locator, missing artifact_id, invalid
  pointer, directory, volatile, no digest, non-`.json` string target); refuse before writes.
- 0.5 → 0.4/0.3/0.2: `DOWNGRADE_UNSUPPORTED`.
- `TARGET_VERSIONS` gains `0.5`; `UNSUPPORTED_TARGET` becomes `only --to 0.2, --to 0.3, --to 0.4
  or --to 0.5 is supported; downgrade and other versions are not available. Nothing was written.`;
  the `--to` help and migrate docstring move.
- Conditions 0.5 text: `... schema 0.3, 0.4 and 0.5 have no downgrade path. Nothing was written.`
  (0.1-0.4 bytes unchanged).
- `INIT_0_5_REFUSAL`: `ERROR: the existing manifest declares schema 0.5. init --force would
  regenerate it as schema 0.1 and cannot carry over first-class values or locators. Nothing was
  written. Move the manifest aside, run init, then runevidence migrate --to 0.5 and reapply the
  first-class values and locators.`
- `rehash` with any tensorboard object locator refuses `LOCATOR_REFRESH_UNSUPPORTED`; a 0.5
  string/JSONL/csv-cell rehash follows the 0.4 rules.
- Comparability/fingerprint/conditions refuse 0.5 as they do 0.4; the `schema` route stays
  file/docs-only for 0.5.
- Fixtures: `tests/data/schema-vectors/reject-unknown-version.json` and the 0.5-as-unknown
  assertions move to 0.6; add the 0.5 schema freeze, dispatcher tests and CLI hash re-pins.
- A pre-0.5 binary backs up and replaces a 0.5 manifest (documented limitation).

## 11. Parent contract errata → v0.9 (D9)

Reissue `docs/verified-value-locators-rfc.md` as **v0.9** with exact replacement text:

- §3.1 kind/extension: `.tfevents` for `tensorboard`; unknown kind is `schema-invalid`.
- §3.1/§4.3 absent locator: “0.3, 0.4 and 0.5”; directory locator row: not version-scoped.
- §4.1 snapshot scope: “0.3, 0.4 or 0.5 locator (string, `jsonl`, `csv-cell` or `tensorboard`)”.
- §4.1 over-cap: per-artifact 50 MiB, or 128 MiB for a TensorBoard target; one artifact-level cap
  finding per capped kind.
- §4.2 step 4: comparison exception applies to csv-cell-capped and tensorboard-capped artifacts.
- §4.3: insert tensorboard cap row and `locator-unsupported-value` for unsupported scalars; add
  both cap findings to the broken-set list.
- §5.2: split caps per kind; add the TensorBoard structural and eligibility caps.
- §6 display: add `tensorboard:step=<step>,occurrence=<n>`.
- §7 privacy: a TensorBoard finding never names the tag.
- §8 migration: 0.5 paths and no downgrade.
- §9 gates: TensorBoard framing/protobuf/CRC vectors.

The exact sentences are checked by docs tests; 0.3/0.4 bytes stay frozen.

## 12. Verification plan

- **Framing**: standard CRC32C vectors; masked CRC; zero-length record; truncated length/data;
  trailing bytes; CRC mismatch; record length over remaining; a file with only the `file_version`
  header; an empty file; a file with zero scalars.
- **Protobuf**: varint 10-byte bound and 10th byte; negative int64 step; packed and unpacked
  `float_val`/`double_val`; `tensor_content` little-endian; duplicate singular fields (last wins);
  `Event.what` oneof; `Summary.Value` oneof (simple_value+histo, simple_value+tensor); wrong wire
  type; unknown fields; depth; malformed length.
- **Classification**: rank-0; rank-1 `[1]`; rank-2 `[[0.5]]` (non-scalar); `DT_FLOAT`/`DT_DOUBLE`;
  half/bfloat16/int/string (unsupported); both content and `*_val`; wrong element count;
  histogram-only tag; image-only; bfloat16+float same `(tag, step)`; occurrence counting.
- **Selection**: absent step = 0; negative step; duplicate `(tag, step)`; occurrence bounds;
  exact tag (no normalization); kind overlap (`.tfevents.csv`).
- **Numbers**: float32 1e-6 boundary; float64 1e-9 boundary; a 0.4 csv-cell regression at 1e-9;
  int recorded; NaN/inf recorded; finite vs NaN → mismatch.
- **Precedence/cardinality**: directory, missing, path, kind, volatile/no-digest, over-cap,
  structural, capped, hash/size, value; mixed 0.5 manifest with both kinds and independent caps;
  cap + hash/size; cap + non-matching kind.
- **Caps/memory**: 50/128 MiB boundary; 16 MiB summary; 4 KiB tag; 10,000/10,001 entries and
  16 MiB tag bytes; 128 MiB minimal-record file; dense 1M-event file with 10 results; no index.
- **Privacy**: tag canary in all human outputs and findings; `--json` retention; 0.3/0.4 bytes.
- **Migration/CLI**: 0.3/0.4→0.5 version-only; 0.1/0.2 precheck; downgrade; texts; 0.5 init
  refusal; fixtures moved to 0.6.
- **Oracle/fuzz**: checked-in real TensorBoard fixtures (legacy `simple_value`, `tensor_content`,
  packed `float_val`/`double_val`, a graph record) with provenance/hash, plus a test-only writer;
  seeded mutation fuzz over framing/protobuf, closed refusal set, no parser/OS text, no crash.
- **Benchmarks**: reference `ubuntu-latest` 4 vCPU/16 GiB, Python 3.12: (a) 10,000 scalar events,
  10 results; (b) 1,000,000 events near 100 MiB, 10 results; (c) 127 MiB minimal records.
  Wall ≤20 s, `VmHWM − baseline` ≤300 MB, Linux-only with counted skips.

## 13. Departures (closed list)

- Zero-length records are valid and skipped; empty files and files with zero scalars are
  structurally valid (locator-mismatch, not structural refusal).
- A truncated trailing record, trailing bytes after the last record, and a summary message over
  16 MiB are structural refusals.
- Groups and unknown wire types are refused; known fields with the wrong wire type are refused.
- Only float/double scalar tensors are indexed; other dtypes are unsupported scalars.
- Rank-0 and rank-1 `[1]` shapes are scalars; every other shape is non-scalar.
- One file is one artifact; multi-file runs are out of scope.
- The adapter selects all scalars in v1; tag filtering is later.

## 14. Risks and open points

- The dependency-free parser is security-relevant; fuzzing and bounds are mandatory.
- Pure-Python CRC32C costs several seconds per 128 MiB; the benchmark pins the ceiling.
- TensorBoard is not a versioned contract; fixtures and a departure list limit drift.
- float32 tolerance differs from JSON/CSV; documented.
- Tags may contain secrets; display hides them, recorded names may not.
- One-file-one-run and no tag filtering exclude common multi-file workflows.
- The 128 MiB cap and one-snapshot design exclude very large event files.
- A summary over 16 MiB (large graphs/images in the same file) makes the file unverifiable for
  scalars; documented.

## 15. Decisions requested

- **D1:** core-owned dependency-free reader; optional adapter; no plugin verification.
- **D2 (owner):** admit schema 0.5 with `Manifest05`/`Result05`/`TensorboardLocator`.
- **D3:** strict TFRecord/protobuf profile, numeric rank-0/[1] scalars, oneof last-wins,
  packed/unpacked, depth 8.
- **D4:** per-kind file cap (50/128 MiB), summary 16 MiB, tag 4 KiB, 10,000/16 MiB eligibility
  caps independent per kind, single-copy snapshot and O(requested) selection.
- **D5:** normal/capped paths and the outcome table.
- **D6:** `tensorboard:step=<step>,occurrence=<n>` display; tag only in JSON; exact canaries.
- **D7:** optional adapter, all scalars in v1, per-manifest budget refusal, tag-free digest ids.
- **D8:** migration/texts and 0.5 init refusal.
- **D9:** parent v0.9 errata and exact quotes.

## 16. What acceptance authorizes

A separate implementation gate for schema 0.5 with the core reader and adapter under these rules,
plus the parent v0.9 errata. It does not authorize release, version bump, publication, multi-file
logs, other summary kinds, tag filtering, or parity claims.
