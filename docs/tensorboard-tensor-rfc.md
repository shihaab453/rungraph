# TensorBoard numeric tensor digests — design RFC (v0.2)

Status: **DESIGN PROPOSAL. NOTHING IMPLEMENTED. NOTHING AUTHORIZED BY THIS DOCUMENT.**
A new design gate and an owner schema-0.9 admission decision are required before any code.
Schema 0.1–0.8, their readers/checkers and every delivered behavior stay frozen, except the
additive dispatch, model, migration and text edits this document names explicitly.

## 0. Changes in v0.2

All three round-1 seats rejected v0.1. v0.2 fixes every blocker:

- **Frozen-strictness boundary.** The 0.9 scanner reproduces every whole-file refusal the frozen
  tensor grammar already performs. Entry-local failures are limited to checks that are new in
  0.9 (unsupported dtype, caps, count/size mismatch, typed-list decoding), which the frozen
  readers never inspected, so a version-only migration cannot change a scalar/string outcome.
  A differential test pins that property.
- **Integer wire rules.** `int_val`/`int64_val` are bounded varint sequences, not fixed-width
  elements; only `float_val`/`double_val` use the `% 4`/`% 8` packed checks. `int32` takes the
  low 32 bits as two's complement and encodes 4 bytes; `int64` takes the full 64-bit two's
  complement and encodes 8. Non-minimal encodings and negative 10-byte forms are accepted; a
  truncated varint fails the entry.
- **Canonical float hashing.** The typed float/double path hashes the raw wire bytes, never a
  Python float, so NaN payloads and `-0.0` survive and equal a matching `tensor_content`.
- **O(1) accumulator.** Four streaming hashers plus running counts, and the last
  `tensor_content` range; no value, dimension list or offset list is retained. The ninth
  dimension fails as it is read. `_TensorState` and every 0.5–0.8 reader stay byte-identical.
- **Payload selection.** The matching-dtype list is the only payload; any non-empty list for
  another dtype makes the entry fail. Non-empty `tensor_content` wins over typed values.
- **Ladder row.** An entry the 0.5 scalar or 0.6 string reader records, or one excluded by
  dtype/rank, is `selects an unsupported value` and consumes no tensor occurrence. "Skipped"
  is replaced by one word, "failure".
- **Admission.** `_csv_cell_cap`, `_tensorboard_cap`, `_tensorboard_snapshot_ids`, explicit 0.9
  scanner routing and the full model/manifest dispatch list are named. The element-count rule is
  model-only; the schema bounds each dimension and the array length.
- **Texts.** `TAG_MESSAGE_09`, the `_decoded_tag` signature and call site, the capability check
  and error order, the no-match kind, the `--include-tensors` help, the updated histogram help,
  the migrate/`--to` literals and `INIT_0_9_REFUSAL` are pinned; the budget text is defined per
  flag family and tensors never change it.

## 1. Problem and scope

`Summary.Value.tensor` (field 8) carries a `TensorProto`. The delivered reader classifies only
rank-0 and rank-1-with-one-element float/double tensors as scalars and scalar-shaped
`DT_STRING` tensors as strings. Everything else — embedding tables, attention maps, confusion
matrices, logits, per-class vectors — is recorded as nothing, and a manifest cannot state that
the array existed or changed.

Inlining arrays is not an option: a 1,000 × 256 float32 matrix is ~1 MB of JSON, and manifests
are human-readable documents. The gate records a **canonical digest**: `TensorValue` stores
dtype, shape and element count, plus the SHA-256 of a canonical byte encoding. The bytes stay in
the event file; verification re-reads and re-canonicalizes them.

In scope: numeric tensors the scalar/string readers do not record, digest-only recording, exact
verification, opt-in extraction, schema 0.9, migration and compact display. Non-goals in §11.

## 2. Schema 0.9 and the value model

Schema 0.9 = 0.8 plus tensors. `Result09.value` is
`Scalar | HistogramValue | ImageValue | AudioValue | TensorValue`.

```json
{
  "kind": "tensor",
  "dtype": "float32",
  "shape": [2, 3],
  "element_count": 6,
  "sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
}
```

Rules:

- strict (`extra="forbid"`), all five keys required, `kind` const;
- `dtype` is const: `float32`, `float64`, `int32` or `int64`. Every other dtype (`bfloat16`,
  `float16`, `uint32`, `complex64`, `string`, `bool`, variants, resources, `_REF` codes) is
  excluded by the tensor profile and handled by §3.1;
- `shape` is 1–8 `StrictInt` values, each `1..2**31-1`; `element_count` is a `StrictInt` in
  `1..1_000_000` and must equal the shape product;
- `sha256` is 64 lowercase hexadecimal characters over the canonical encoding (§3.4). The model
  uses the media slice's lowercase-only pattern; the shared `SHA256_PATTERN` accepts uppercase
  and the artifact path case-folds digests, so reusing it would accept a re-spelled digest;
- the shape-product rule is **model-only**: JSON Schema cannot express it, so the published
  schema bounds each dimension and the array length, and the model enforces the product;
- "canonical bytes are at most `element_count * 8`" is a reader invariant, not a manifest
  constraint: a manifest stores no bytes and cannot violate it.

Object values remain pinned to their own locator kind or `null`; scalars keep every locator kind.
The 0.8 matrix gains `TensorValue` → `tensorboard-tensor`. The locator is a standalone model, not
a `TensorboardLocator` subclass, and `_locator_kind` checks it before the scalar branch.

## 3. Reader profile

### 3.1 Which entries, and what happens to the rest

A `Summary.Value` whose winning oneof member is `tensor` (8) is a tensor candidate. The legacy
classifier runs first with exactly the frozen semantics:

1. if the frozen scalar classifier records the entry (rank-0 or rank-1 `[1]` float/double) or
   the frozen string classifier records it (rank-0 or `[1]` `DT_STRING`), the entry is that
   scalar/string and is **not** a tensor; a tensor locator for it receives
   `selects an unsupported value` and the entry consumes no tensor occurrence;
2. otherwise, if the dtype is float32/float64/int32/int64, the shape has 1–8 dimensions with
   `unknown_rank` false and each dimension `1..2**31-1`, the element count is `1..1_000_000`
   and the payload resolves per §3.3, the entry is a tensor;
3. every other candidate — unsupported dtype, rank 0 that the scalar classifier did not record
   (for example `int32`), unknown rank, bad dimension, cap overflow, payload mismatch — is a
   **failure** and receives `selects an unsupported tensor encoding`. It consumes no occurrence
   and does not affect siblings.

"Failure" is the only word used for case 3; "skipped" is not used anywhere.

### 3.2 Frozen strictness boundary

The 0.9 scanner walks tensor payloads with the frozen grammar and reproduces every whole-file
refusal that 0.5–0.8 perform: bad keys, field number 0 or above `2**29-1`, wire types 3/4/6/7,
truncated lengths, wrong wire type for `dtype` (0), `tensor_shape` (2), `tensor_content` (4),
`float_val` (2/5) or `double_val` (2/1), a packed `float_val` length not divisible by 4, a
packed `double_val` length not divisible by 8, malformed `Dim.size`/`Dim.name`, and the outer
Event/Summary rules including the 16 MiB merged-summary cap.

Everything else is new in 0.9 and fails entry-locally: unsupported dtype, rank/dimension caps,
element-count and canonical-length mismatches, and decoding of `int_val`/`int64_val`, which the
frozen reader never inspects. A version-only migration therefore cannot change a frozen
scalar/string verification outcome; §9 pins this with a differential test.

### 3.3 Merge and payload selection

`TensorProto` is a message; repeated field-8 occurrences after the last different oneof member
merge:

- `dtype` (1, varint): last wins;
- `tensor_shape` (2): message merge; `Dim.size` (1, varint) is last-wins signed int64, default
  0; `Dim.name` (2) is length-delimited and ignored; dimensions append in wire order;
  `unknown_rank` (3) is last-wins and its **final** value decides;
- `tensor_content` (4): singular bytes, last wins; only the last range is used;
- `float_val` (5, fixed32), `double_val` (6, fixed64), `int_val` (7, varint), `int64_val`
  (10, varint): repeated, appended, in packed (wire 2) or unpacked form;
- `version_number` (3, varint), `string_val` (8), `scomplex_val` (9), `bool_val` (11),
  `dcomplex_val` (12), `half_val` (13), `resource_handle_val` (14), `variant_val` (15),
  `uint32_val` (16), `uint64_val` (17) and field 18 are wire-checked and ignored. The wire table:
  `string_val`/`resource_handle_val`/`variant_val`/field 18 are length-delimited;
  `scomplex_val`/`half_val`/`bool_val`/`uint32_val`/`uint64_val` are varint or packed;
  `dcomplex_val` is fixed64 or packed; `version_number` is varint.

The payload is chosen once the merge ends:

- a non-empty `tensor_content` is the payload and must be exactly `element_count * width` bytes;
  any typed list is ignored (still wire-checked and decoded for validity);
- otherwise the list matching `dtype` is the payload and its element count must equal
  `element_count`;
- a non-empty typed list for a dtype other than the winning one makes the entry fail, as the
  frozen scalar and string readers do. This includes `float_val` together with `double_val`;
- an empty `tensor_content` counts as absent.

TF's broadcast form (fewer typed values than elements) is not supported and fails the entry.

### 3.4 Canonical encoding

The digest is over a canonical encoding so different writer forms of the same array agree:

- row-major element order;
- `float32`/`int32`: 4 bytes little-endian per element; `float64`/`int64`: 8 bytes;
- `tensor_content` bytes are hashed as-is (TensorProto writes them in host byte order; big-endian
  writers are out of scope and produce a different digest);
- typed `float_val`/`double_val` hash the **raw wire bytes** of each element in wire order, never
  a Python float, so a signalling NaN is not quieted and `-0.0` keeps its sign;
- typed `int_val`: take the low 32 bits of the decoded varint as two's complement and write 4
  little-endian bytes, so `-1` (a 10-byte varint) and `0xFFFFFFFF` agree;
- typed `int64_val`: the full 64-bit two's complement value, 8 little-endian bytes;
- varints are bounded to 10 bytes and the frozen `_read_varint` semantics; non-minimal
  encodings are accepted, a truncated varint fails the entry;
- if both a non-empty `tensor_content` and typed values are present, `tensor_content` wins and
  the typed values are ignored.

No element passes through a Python float or int conversion that could change its bytes.

### 3.5 Accumulator and bounds

The 0.9 state is separate from `_TensorState`, whose "never allocates a value or dimension
list" contract stays frozen. It holds:

- the last `tensor_content` range (two integers);
- at most eight dimension integers; a ninth `dim` fails the entry as it is read;
- four streaming SHA-256 hashers (one per supported dtype) and four running counts with
  overflow flags;
- the last `unknown_rank` value and the final `dtype`.

Typed values are fed to their dtype's hasher as they are read and are never retained. A count
above 1,000,000 marks overflow and fails the entry; packing cannot exceed the 16 MiB Summary
cap. Packed float/double payloads are hashed as contiguous byte spans; varint integers are
decoded and re-encoded one element at a time with O(1) state.

The frozen 16 MiB merged-`Summary` cap still outranks everything: an oversized Summary refuses
the whole file.

## 4. Locator, identity, ordering

```json
{"kind": "tensorboard-tensor", "tag": "logits", "step": 3, "occurrence": 0}
```

Inherited: exact tag (1..4,096 code points and UTF-8 bytes), signed 64-bit `step`, zero-based
`occurrence` among conforming tensors with that `(tag, step)`, bounded by
`MAX_TENSORBOARD_OCCURRENCE = 2**31-1`. Six kinds count independently. Result ids append
`NUL + "tensor"`, names use `tag @ step (tensor)`, and multifile groups append the basename and a
16-hex digest.

## 5. Checker and consumers

### 5.1 Admission

`checks/__init__.py`:

- `_LOCATOR_SCHEMAS` gains 0.9;
- `_tensorboard_cap` gains 0.9 in its version tuple and `TensorboardTensorLocator` in its class
  tuple, so the six kinds share the 10,000-result and 16 MiB tag limits;
- `_csv_cell_cap` gains 0.9, or the inherited CSV entry/header caps silently disappear;
- `_tensorboard_snapshot_ids` gains both, keeping the 128 MiB event-file snapshot.

`checks/results.py`: `_extension_matches`/`_expected_extension`, `_locator_kind`, `capped_kinds`,
the per-kind 128 MiB branch, the directory hint, the cap-message gate (`TENSORBOARD_CAP_MESSAGE_06`
for 0.9), `check_results`' own `has_locators`, and the `tensorboard_results` tuple all learn the
kind. The scan has an **explicit** `schema_version == SCHEMA_VERSION_0_9` branch calling
`scan_tensorboard_09`; without it tensor requests fall through to the 0.6 scanner and vanish.

`models.py` admission: `TensorValue`, `TensorboardTensorLocator`, `Result09` with
`_dispatch_value_09` and `_media_value_locator`, `Manifest09._version_specific_shape` (seven
locator kinds, pointer grammar, `artifact_id` rule), `_LOCATOR_ADAPTER_09`, `_dispatch_locator_09`,
`model_dump` 0.2 fields, and five `allOf` entries (the `artifact_id` rule plus four per-kind
branches). `manifest.py` gains 0.9 in `validate_manifest`, `_root_model_for`, `artifact_to_dict`,
`_LOCATOR_TAGS` and the value-union tuple. `models.is_manifest` needs no change: `Manifest09`
subclasses `Manifest`, so the existing `isinstance` already accepts it.

### 5.2 Ladder

The delivered two-stage ladder, first matching row wins:

| Stage 1 condition | Outcome |
| --- | --- |
| conforming tensor at that occurrence | `value` (stage 2) |
| conforming tensors exist but the occurrence is exhausted | `missing` |
| the entry is a `tensor` candidate excluded by dtype, rank or payload (§3.1 case 3) | `unsupported_encoding` |
| the entry is recorded as a 0.5 scalar or 0.6 string, or carries another member, or has no winning member | `unsupported_value` |
| no entry at the tag/step | `missing` |

| Stage 2 condition | Finding |
| --- | --- |
| recorded value is not a `TensorValue` | `locator-unsupported-value`, `selects an unsupported value` |
| recorded value differs | `result-value-mismatch`, `does not match the recorded value` |
| recorded value matches | none |

Findings: `missing` → `was not found`; `unsupported_encoding` → `selects an unsupported tensor
encoding`; `unsupported_value` → `selects an unsupported value`. Comparison is exact over
`dtype`, `shape`, `element_count` and `sha256`.

### 5.3 Display

`inspect`, `claim`, `lineage`, `run`, `compare` and `matrix` render
`tensor(float32[2x3], 6 elements)` for results only, in model and dict form. `--json` emits the
object exactly. Compare rows gain `baseline_tensor`/`target_tensor`, and **both**
`_structured_flag` and `_side_flag` must learn `"tensor"`, or the human row prints raw JSON.
The leaderboard excludes the kind as `non_numeric_or_nonfinite`. `display_locator` renders
`tensorboard-tensor:step=<n>,occurrence=<k>`, never the tag; `run_claim`'s whitelist and value
display gain the kind; the graph conflicting-evidence text uses the display.

## 6. Adapter and CLI

- `init --include-tensors` is opt-in and selects schema 0.9. Adapter capability:
  `supports_include_tensors`, with `include_tensors` as the extraction keyword.
- Schema precedence: tensors → 0.9; media → 0.8; histograms → 0.7; strings → 0.6; else 0.5.
- `iter_supported_summaries_09` is the tag-agnostic generator; `_read_event_file` filters every
  kind by its flag and `_decoded_tag` receives the 0.9 flag last in the boolean chain (0.9 is
  tested before 0.8). The `text=` argument includes tensor records so an unrepresentable tensor
  tag is skipped.
- `--tags` filters tensor tags; the no-match warning treats tensors as a non-scalar kind
  ("summary tag").
- Capability errors print in the delivered order, with tensors appended last: strings,
  histograms, images, audio, tensors.
- Budgets: tensors add no limit and never change the text. The text is chosen by the media /
  histogram / string flags exactly as in 0.8, so tensors-only and tensors+media runs use
  `BUDGET_MESSAGE_08`, tensors+strings uses `BUDGET_MESSAGE_06`, tensors+histograms uses
  `BUDGET_MESSAGE_07_HIST`, and all three use `BUDGET_MESSAGE_07`. A digest value is at most
  167 bytes of compact JSON (dimensions are capped by the element product), far under the tag
  budget.
- Unusable tags: a scalar tag refuses `init` without `--tags` and is skipped with it; tensor
  tags are skipped in both cases.

## 7. Migration, refusals, fixed texts

- `migrate --to 0.9` admits 0.1/0.2 through the locator precheck and 0.3–0.8 as version-only
  rewrites.
- `--to 0.8` or lower from 0.9 is `DOWNGRADE_UNSUPPORTED`; every frozen target branch gains 0.9.
- Comparability refuses 0.9 by version; `init --force` refuses a declared 0.9 manifest before
  discovery; `_event_artifact_is_volatile` admits `Manifest09`.
- Fixed texts, all literal:

```
ERROR: the existing manifest declares schema 0.9. init --force would regenerate the manifest and cannot carry over first-class values or locators. Nothing was written. Move the manifest aside, run init, then run runevidence migrate PATH --to 0.9 (unless the adapter writes 0.9 itself) and reapply the first-class values and locators.
ERROR (UNSUPPORTED_TARGET): only --to 0.2, --to 0.3, --to 0.4, --to 0.5, --to 0.6, --to 0.7, --to 0.8 or --to 0.9 is supported; downgrade and other versions are not available. Nothing was written.
ERROR (UNSUPPORTED_VERSION): the conditions command requires an explicit schema 0.2 manifest. A schema 0.1 manifest can be migrated with runevidence migrate PATH --to 0.2; schema 0.3, 0.4, 0.5, 0.6, 0.7, 0.8 and 0.9 have no downgrade path. Nothing was written.
ERROR: adapter {name!r} does not support --include-tensors. Nothing was written.
ERROR: adapter {name!r} cannot apply --include-tensors. Nothing was written.
a scalar tag cannot be represented in schema 0.9; nothing was written
```

`--include-tensors` help: `also record numeric TensorBoard tensors as schema 0.9 results
(opt-in; implies schema 0.9)`. The histogram help becomes `… (opt-in; implies schema 0.7 unless
a media or tensor flag selects a later schema)`. `--to` help lists 0.9 and the migrate parser
help lists 0.9. The 0.9 cap text is the delivered `TENSORBOARD_CAP_MESSAGE_06`.

## 8. Admission checklist (per module)

| Module | Change |
| --- | --- |
| `models.py` | `TensorValue`, standalone `TensorboardTensorLocator`, `Result09` matrix, `Manifest09` version shape and dispatch, `model_dump` 0.2 fields, five `allOf` entries, schema 0.9 file and pin |
| `manifest.py` | `validate_manifest`, `_root_model_for`, `artifact_to_dict`, `_LOCATOR_TAGS`, value-union tuple admit 0.9; `is_manifest` needs no change |
| `tensorboard_events.py` | `_parse_value_09` with the separate streaming state, canonical digest, `scan_tensorboard_09`, `iter_supported_summaries_09`; `_TensorState` and 0.5–0.8 readers byte-identical |
| `checks/__init__.py` | locator admission, six-kind caps, csv cap, snapshot eligibility |
| `checks/results.py` | explicit 0.9 routing, extension/kind maps, caps, 128 MiB branch, ladder |
| `checks/graph.py` | version gates, capped hint, compact display |
| `histogram_display.py` | `value_to_json` and `tensor_display` |
| `locator_profiles.py`, `run_claim.py` | display, whitelist, value branch |
| `run_compare.py` | `_structured_flag` and `_side_flag`; `run_matrix.py`, `run_details.py`, `run_lineage.py`, `run_leaderboard.py`, `report.py` | display and JSON |
| `migrate.py`, `run_comparability.py`, `cli.py`, `adapter.py` | targets, downgrade tuples, flags, texts, precedence, generator |

## 9. Verification plan

- Reader: canonical equality across `tensor_content` and typed forms for all four dtypes; packed
  and unpacked values; int32 `-1` vs `0xFFFFFFFF`; non-minimal varints; truncated varints;
  conflicting typed lists; content plus typed; empty content; shape merge; dimension 0, negative
  and ninth dimension; unknown rank last-wins; unsupported dtypes and `_REF`; NaN payload and
  `-0.0` distinctness; element-count and canonical-length mismatch; 1,000,000/1,000,001
  elements; entry-local failures with sibling scalars intact; Summary-cap refusal.
- Differential: for scalar, string, histogram and media requests, `scan_tensorboard_09`
  returns the same selections and outcomes as 0.5–0.8 on the same bytes, including files where
  the frozen grammar refuses.
- Identity: per-kind occurrence, a scalar entry not consuming a tensor occurrence, tag filter,
  multifile names, occurrence 2,000,000,000.
- Checker: every ladder row and byte-exact texts; six-kind caps; csv cap on 0.9; 128 MiB
  eligibility; digest, shape, dtype and count mismatches.
- Adapter/CLI: flag matrix with 0.9 precedence; budget texts; tag texts; capability errors and
  their order; `init --force` and `--volatile` refusals.
- Migration: 0.1–0.8 → 0.9; 0.9 → 0.8 downgrade refusal; comparability refusal; help texts.
- Consumers: every surface in model and dict form; compare flags; claim hides the tag.
- Mutation probes: drop the canonical-length check, drop the int32 mask, drop the digest
  comparison, remove 0.9 from `_LOCATOR_SCHEMAS`, remove the 0.9 scan branch, remove the
  `artifact_to_dict` branch, admit 0.9 in comparability, drop the force guard.

## 10. Decisions requested

1. Digest-only canonical encoding (recommended) versus inline arrays.
2. float32/float64/int32/int64 only (recommended) versus also float16/bfloat16/uint types.
3. One `tensorboard-tensor` kind with a `dtype` field (recommended) versus per-dtype kinds.
4. 1,000,000-element cap (recommended) versus a larger or smaller bound.
5. Bit-exact digests (recommended) versus numeric normalization.

## 11. Non-goals

- Inline arrays or sidecar extraction; the adapter stays read-only.
- String, bool, complex, half, variant, resource and sparse tensors.
- Numeric tolerance or statistical comparison; the digest is exact.
- TF2 `[k,3]` histogram tensors are recorded as raw tensors here; a future TF2-histogram gate
  must decide whether it supersedes them.
- Big-endian writers.
- TensorBoard plugin rendering and graph/run metadata.
