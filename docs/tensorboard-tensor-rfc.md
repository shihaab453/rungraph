# TensorBoard numeric tensor digests — design RFC (v0.1)

Status: **DESIGN PROPOSAL. NOTHING IMPLEMENTED. NOTHING AUTHORIZED BY THIS DOCUMENT.**
A new design gate and an owner schema-0.9 admission decision are required before any code.
Schema 0.1–0.8, their readers/checkers and every delivered behavior stay frozen, except the
additive dispatch, model, migration and text edits this document names explicitly.

## 0. Changes in v0.1

- First draft. Proposes one `tensorboard-tensor` locator and a digest-only `TensorValue` for
  numeric tensors the 0.5 scalar and 0.6 string readers reject.
- Pins: float32/float64/int32/int64 only, a canonical little-endian row-major encoding shared by
  every writer form, rank and element caps, merge rules, per-kind occurrence, exact
  verification, and compact display.

## 1. Problem and scope

`Summary.Value.tensor` (field 8) carries a `TensorProto`. The delivered reader classifies only
rank-0 and rank-1-with-one-element float/double tensors as scalars and scalar-shaped
`DT_STRING` tensors as strings. Everything else — embedding tables, attention maps, confusion
matrices, logits, per-class vectors — is recorded as nothing, and a manifest cannot state that
the array existed or changed.

Inlining arrays is not an option: a single 1,000 × 256 float32 matrix is ~1 MB of JSON, and
manifests are human-readable documents. The gate therefore records a **canonical digest**:
`TensorValue` stores dtype, shape and element count, and the SHA-256 of a canonical byte
encoding. The bytes stay in the event file; verification re-reads and re-canonicalizes them.

In scope: numeric tensors of rank ≥ 1 that the scalar/string readers do not already record,
digest-only recording, exact verification, opt-in extraction, schema 0.9, migration and compact
display. Non-goals in §11.

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
- `dtype` is a const string: `float32`, `float64`, `int32` or `int64`. Every other dtype
  (`bfloat16`, `float16`, `uint32`, `complex64`, `string`, `bool`, variants, resources) is
  unsupported and is skipped entry-locally;
- `shape` is a list of 1–8 integers, each `1..2**31-1`; the product of the dimensions is the
  element count;
- `element_count` is an integer `1..1_000_000` and must equal the shape product;
- `sha256` is 64 lowercase hexadecimal characters over the canonical encoding (§3);
- the canonical bytes are at most `element_count * 8` bytes, so the largest value hashes 8 MB.

Object values remain pinned to their own locator kind or `null`; scalars keep every locator kind,
as delivered. The 0.8 rule is extended with `TensorValue` → `tensorboard-tensor`.

## 3. Reader profile

### 3.1 Which entries

A `Summary.Value` whose winning oneof member is `tensor` (8) is a tensor candidate. It is
recorded only when:

- the parsed dtype is float32/float64/int32/int64;
- `TensorShapeProto` has 1–8 dimensions, `unknown_rank` is false, and every dimension is
  `1..2**31-1`;
- the element count is `1..1_000_000`;
- the payload form resolves per §3.3 and its length matches the element count exactly.

Anything else (rank 0, unknown rank, negative or zero dimension, unsupported dtype, mismatch) is
an entry-local failure, never a file refusal. Existing scalar and string classification runs
first: a tensor the 0.5 reader records as a scalar is not a tensor result, so no manifest can
carry the same entry twice.

### 3.2 Merge rule

`TensorProto` is a message; repeated field-8 occurrences after the last different oneof member
merge:

- `dtype` (1, varint): last wins;
- `tensor_shape` (2): message merge, dimension lists append in wire order; `unknown_rank` (3)
  last wins;
- `tensor_content` (4): singular bytes, last wins;
- `float_val` (5), `double_val` (6), `int_val` (7), `int64_val` (10): repeated, appended, in
  either packed or unpacked wire form for the matching dtype;
- `version_number` (3), `half_val` (13), `string_val` (8), `scomplex_val` (9), `bool_val` (11),
  `dcomplex_val` (12), `resource_handle_val` (14), `variant_val` (15), `uint32_val` (16),
  `uint64_val` (17) and unknown fields are wire-checked and skipped.

A different oneof member resets tensor state; a failure is sticky until a different member
appears, mirroring the delivered histogram and media parsers.

### 3.3 Canonical encoding

The digest is over a **canonical** encoding so writers that use different `TensorProto` forms for
the same array produce the same digest:

- element order is row-major over `shape`;
- each element is its little-endian fixed-width encoding: 4 bytes for `float32`/`int32`, 8 for
  `float64`/`int64`;
- a non-empty `tensor_content` is used when present and must be exactly
  `element_count * width` bytes; its bytes are hashed as-is (TensorProto content is already
  little-endian and unpadded);
- otherwise the typed value list supplies the elements, which are packed little-endian; its
  length must equal `element_count`;
- if both a non-empty `tensor_content` and typed values are present, `tensor_content` wins and
  the typed values are ignored (they are still wire-checked);
- bit patterns are preserved exactly: `-0.0`, NaN payloads and infinities hash as written. The
  digest proves byte-level identity, not numeric equivalence; two NaNs with different payloads
  differ, as do `0.0` and `-0.0`.

No element is converted through Python floats, so no value is normalized or lost.

### 3.4 Inner failure set (entry-local)

Inside a winning tensor member, key decoding is lenient exactly like the histogram and media
decoders:

- wrong wire type for a known field, field number 0, field number greater than `2**29-1`, wire
  types 3/4/6/7, a truncated length, a malformed varint or a bad packed length (`% 4` for
  float32/int32, `% 8` for float64/int64) make **that entry** fail;
- the outer Event/Summary walk keeps its frozen behavior and still refuses the file on a
  structural error outside the tensor payload;
- a failed entry consumes no occurrence number and does not affect sibling entries;
- unknown fields are skipped; `unknown_rank` true is a failure, not "ignore the shape";
- a merge that ends with no canonical payload is a failing entry.

### 3.5 Bounds

The frozen 16 MiB merged-`Summary` cap still outranks everything: an oversized Summary refuses
the whole file. Below it, the 1,000,000-element cap bounds hashing at 8 MB per entry, and the
reader retains only offsets, dtype, up to eight dimension integers and counters — never the
elements. The canonical bytes are hashed streaming from the buffer.

## 4. Locator, identity, ordering

One locator joins the delivered set:

```json
{"kind": "tensorboard-tensor", "tag": "logits", "step": 3, "occurrence": 0}
```

Rules are inherited: exact tag (1..4,096 code points and UTF-8 bytes), signed 64-bit `step`,
zero-based `occurrence` among conforming tensors with that `(tag, step)`, bounded by
`MAX_TENSORBOARD_OCCURRENCE = 2**31-1`, six kinds counting independently. Result ids append
`NUL + "tensor"`, names use `tag @ step (tensor)`, and multifile groups append the basename and a
16-hex digest.

## 5. Checker and consumers

Admission follows the 0.8 slice exactly: `_LOCATOR_SCHEMAS`, the five-kind cap becoming six,
`_tensorboard_snapshot_ids`, the locator-class tuples, `_extension_matches`/`_expected_extension`,
`_locator_kind`, `capped_kinds`, the per-kind 128 MiB branch, the directory hint, the cap-message
gate, `check_results`' own `has_locators`, and the `tensorboard_results` request tuple all learn
`tensorboard-tensor`.

The comparison ladder is the delivered two-stage ladder:

- stage 1: conforming entry → `value`; exhausted → `was not found`; failed tensor → `selects an
  unsupported tensor encoding`; other member or tag-only value → `selects an unsupported value`;
  absent → `was not found`;
- stage 2: recorded value is not a `TensorValue` → `selects an unsupported value`; differs →
  `result-value-mismatch` / `does not match the recorded value`; matches → none.

Comparison is exact over `dtype`, `shape`, `element_count` and `sha256`. Human surfaces render
`tensor(float32[2x3], 6 elements)`; `--json` emits the object; compare rows gain
`baseline_tensor`/`target_tensor`; the leaderboard excludes the kind as
`non_numeric_or_nonfinite`.

## 6. Adapter and CLI

- `init --include-tensors` is opt-in and selects schema 0.9.
- Schema precedence: tensors → 0.9; media → 0.8; histograms → 0.7; strings → 0.6; else 0.5.
  The adapter needs a tag-agnostic `iter_supported_summaries_09`; `_read_event_file` filters
  every kind by its flag, and `_decoded_tag` selects the 0.9 tag text whenever tensors are on.
- `--tags` filters tensor tags like every other kind.
- Budgets: the tensor slice adds no budget; the 10,000-result and 16 MiB tag limits bound it, and
  a digest value is at most 202 bytes of compact JSON. The budget text for a tensors-only run is
  the delivered 10,000-result / 16 MiB tag line; mixed runs reuse the delivered string,
  histogram and media texts.
- Unrepresentable tags: a scalar tag refuses `init` without `--tags` and is skipped with it;
  tensor tags are skipped in both cases.

## 7. Migration, refusals, fixed texts

- `migrate --to 0.9` admits 0.1/0.2 through the locator precheck and 0.3–0.8 as version-only
  rewrites, since 0.9 admits every delivered locator and value.
- `--to 0.8` or lower from 0.9 is `DOWNGRADE_UNSUPPORTED`; every frozen target branch gains 0.9.
- Comparability refuses 0.9 by version; `init --force` refuses a declared 0.9 manifest before
  discovery; `_event_artifact_is_volatile` admits `Manifest09`.
- Fixed texts:

```
ERROR: the existing manifest declares schema 0.9. init --force would regenerate the manifest and cannot carry over first-class values or locators. Nothing was written. Move the manifest aside, run init, then run runevidence migrate PATH --to 0.9 (unless the adapter writes 0.9 itself) and reapply the first-class values and locators.
ERROR (UNSUPPORTED_TARGET): only --to 0.2, --to 0.3, --to 0.4, --to 0.5, --to 0.6, --to 0.7, --to 0.8 or --to 0.9 is supported; downgrade and other versions are not available. Nothing was written.
ERROR (UNSUPPORTED_VERSION): the conditions command requires an explicit schema 0.2 manifest. A schema 0.1 manifest can be migrated with runevidence migrate PATH --to 0.2; schema 0.3, 0.4, 0.5, 0.6, 0.7, 0.8 and 0.9 have no downgrade path. Nothing was written.
ERROR: adapter {name!r} does not support --include-tensors. Nothing was written.
ERROR: adapter {name!r} cannot apply --include-tensors. Nothing was written.
a scalar tag cannot be represented in schema 0.9; nothing was written
```

`--to` help lists 0.9; the 0.9 cap text is the delivered `TENSORBOARD_CAP_MESSAGE_06`.

## 8. Admission checklist (per module)

| Module | Change |
| --- | --- |
| `models.py` | `TensorValue`, `TensorboardTensorLocator`, `Result09` matrix, `Manifest09` version shape and locator dispatch, `model_dump` 0.2 fields, four `allOf` branches (histogram/image/audio/tensor), schema 0.9 file and pin |
| `manifest.py` | dispatch, `artifact_to_dict`, `_LOCATOR_TAGS`, value-union tuple admit 0.9 |
| `tensorboard_events.py` | `_parse_value_09` with tensor state, canonical encoding and digest, `scan_tensorboard_09`, `iter_supported_summaries_09`; 0.5–0.8 readers untouched |
| `checks/__init__.py` | locator admission, six-kind caps, snapshot eligibility |
| `checks/results.py` | scanner routing, kind maps, caps, ladder, media-style comparison for tensors |
| `checks/graph.py` | version gates and compact conflicting-evidence display |
| `histogram_display.py` | `value_to_json` and `tensor_display` |
| `locator_profiles.py`, `run_claim.py` | display and whitelist |
| `run_compare.py`, `run_matrix.py`, `run_details.py`, `run_lineage.py`, `run_leaderboard.py`, `report.py` | display and per-kind JSON flags |
| `migrate.py`, `run_comparability.py`, `cli.py`, `packages/runevidence-tensorboard/adapter.py` | targets, downgrade tuples, flags, texts, precedence, generator |
| `docs/`, `schema/`, `README.md` | 0.9 page, schema file, flag documentation |

## 9. Verification plan

- Reader: tensor_content and typed-value forms for all four dtypes produce identical digests;
  packed and unpacked typed values; shape merge across occurrences; last-wins content; reset by
  a different member; unsupported dtypes; unknown rank; zero/negative dimensions; rank 9;
  1,000,001 elements; element-count mismatch; canonical-size mismatch; NaN payload and `-0.0`
  distinctness; entry-local failures with sibling scalars intact; Summary-cap refusal.
- Identity: per-kind occurrence, tag filter, multifile names, occurrence 2,000,000,000.
- Checker: every ladder row and byte-exact texts; six-kind caps; 128 MiB eligibility; digest,
  shape, dtype and count mismatches.
- Adapter/CLI: flag matrix with 0.9 precedence; budget texts; tag texts; capability errors;
  `init --force` and `--volatile` refusals.
- Migration: 0.1–0.8 → 0.9; 0.9 → 0.8 downgrade refusal; comparability refusal; help texts.
- Consumers: every surface in model and dict form; compare flags; claim hides the tag.
- Mutation probes: drop the canonical-size check, drop the digest comparison, remove 0.9 from
  `_LOCATOR_SCHEMAS`, remove the `artifact_to_dict` branch, admit 0.9 in comparability, drop the
  force guard.

## 10. Decisions requested

1. Digest-only canonical encoding (recommended) versus inline arrays.
2. float32/float64/int32/int64 only (recommended) versus also float16/bfloat16/uint types.
3. A single `tensorboard-tensor` kind with a `dtype` field (recommended) versus per-dtype kinds.
4. 1,000,000-element cap (recommended) versus a larger or smaller bound.
5. Bit-exact digests, including NaN payloads and `-0.0` (recommended), versus numeric
   normalization.

## 11. Non-goals

- Inline arrays or sidecar extraction; the adapter stays read-only.
- String, bool, complex, half, variant, resource and sparse tensors.
- Numeric tolerance or statistical comparison; the digest is exact.
- TensorBoard plugin rendering (PR curves, embeddings projections).
- Graph/run metadata and other non-result event payloads.
