# TensorBoard numeric tensor digests — design RFC (v0.9)

Status: **DESIGN PROPOSAL. NOTHING IMPLEMENTED. NOTHING AUTHORIZED BY THIS DOCUMENT.**
A new design gate and an owner schema-0.9 admission decision are required before any code.
Schema 0.1–0.8, their readers/checkers and every delivered behavior stay frozen, except the
additive dispatch, model, migration and text edits this document names explicitly.

## 0. Changes in v0.9

Round 8 accepted v0.8 with editorial limitations. v0.9 folds them; no normative rule changes:

- **§9 examples are exact.** Int `[1]` cases name their content length (4 bytes for int32, 8 for
  int64) and require the typed value to match the dtype with no other non-empty typed list. The
  rank-0 float32 case requires exactly 4 content bytes and no `float_val`/`double_val`, and "any
  other non-zero content length" replaces "any other length" because zero-length content is
  absent.
- **The changelog matches this round.** It names only the v0.9 edits, and the earlier-round
  summary covers the §3.2 typed-branch scope and the step-1 reminder added in v0.8.
- **`run_claim` needs one edit.** Only its kind whitelist changes: `_display_details` already
  calls `value_display` for every kind, so no value branch is added and the duplicate sentence
  is gone.
- **Formatting.** The two run-on lines are wrapped.

Earlier revisions folded rounds 1 to 6: the frozen-strictness boundary and encoding-based damage
split, varint and int32 rules, raw-wire float hashing, the separate O(1) accumulator,
post-selection overflow, payload precedence, the exact per-field wire table, walk-visited scope,
the stage-1 ladder row, the step-0 gate, six mappings, the lineage call path, and every
admission, text and display obligation.

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
- `dtype` is const: `float32` (`DT_FLOAT=1`), `float64` (`DT_DOUBLE=2`), `int32` (`DT_INT32=3`)
  or `int64` (`DT_INT64=9`), compared as raw varint codes. Every other dtype (`bfloat16`,
  `float16`, `half`, `uint32`, `uint64`, `complex64`, `string`, `bool`, variants, resources,
  `_REF` codes) is
  excluded by the tensor profile and handled by §3.1;
- `shape` is 1–8 `StrictInt` values, each `1..2**31-1`; `element_count` is a `StrictInt` in
  `1..1_000_000` and must equal the shape product;
- `sha256` is 64 lowercase hexadecimal characters over the canonical encoding (§3.4). The model
  uses the media slice's lowercase-only pattern; the shared `SHA256_PATTERN` accepts uppercase
  and the artifact loader lowercases its `sha256` field (`Artifact._lowercase_digest`), so
  reusing it would accept a re-spelled digest;
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

1. if the frozen scalar classifier records the entry (a valid scalar encoding at rank 0 or rank
   1 `[1]` float/double — note that content plus a `float_val`/`double_val` is *not* a valid
   scalar encoding, while an integer typed list is ignored and leaves the entry a recorded
   scalar) or the frozen string classifier records it (rank-0 or `[1]` `DT_STRING`), the entry is
   that scalar/string and is **not** a tensor; a tensor locator for it receives
   `selects an unsupported value` and the entry consumes no tensor occurrence;
2. otherwise, if the dtype is float32/float64/int32/int64, the shape has 1–8 dimensions with
   `unknown_rank` false and each dimension `1..2**31-1`, the element count is `1..1_000_000`
   and the payload resolves per §3.3a, the entry is a tensor;
3. every other candidate — unsupported dtype, rank 0 that the scalar classifier did not record
   (for example `int32`), unknown rank, bad dimension, cap overflow, payload mismatch — is a
   **failure** and receives `selects an unsupported tensor encoding`. It consumes no occurrence
   and does not affect siblings.

Case 3 is always called a **failure**; "skipped" is reserved for ignored fields and unusable
tags, never for a tensor candidate.

### 3.2 Frozen strictness boundary

The 0.9 scanner reuses the frozen Event, `Summary.Value`, shape and tensor walks, so **every
raise in those frozen parsers is a whole-file refusal in 0.9 too**. In particular:

- wire-0 bodies are decoded strictly in **every field the frozen walk visits**: a truncated or
  overlong varint refuses the file. Losing tensor occurrences, losing summaries and packed spans
  are not decoded by that walk, and the 0.7/0.8 media decoders are lenient, so none of them
  gains a strictness it never had;
- wire-1/5 bodies must be long enough in every field the walk visits: a short fixed32/fixed64
  body refuses the file;
- `float_val` (5) accepts wire 2 or 5 and `double_val` (6) accepts wire 2 or 1; any other wire on
  those two fields refuses the file;
- a packed `float_val` span not divisible by 4, or a packed `double_val` span not divisible by 8,
  refuses the file;
- the outer `Summary.Value` and Event wire checks and the 16 MiB merged-summary cap apply
  unchanged.

Only damage **inside a packed varint span** (`int_val` field 7, `int64_val` field 10) is
entry-local, because the frozen walk treats that span as an opaque length-delimited blob. A
packed span in an ignored field is never decoded at all.

Everything else that is new in 0.9 fails entry-locally and only for candidates the frozen
classifiers did not record: unsupported dtype, rank/dimension caps, element-count and
canonical-length mismatches, foreign typed lists and packed varint decoding **in the typed
branch**, and overflow of the selected list. A recorded scalar or string is never re-examined as
a tensor.

### 3.3 Merge rules

`TensorProto` is a message; repeated field-8 occurrences after the last different oneof member
merge:

- `dtype` (1, varint): last wins;
- `tensor_shape` (2): message merge; `Dim.size` (1, varint) is last-wins signed int64, default
  0; `Dim.name` (2) is length-delimited and ignored; dimensions append in wire order;
  `unknown_rank` (3) is wire-checked and last-wins, and its **final** value decides;
- `tensor_content` (4, wire 2): singular bytes, last wins; only the last range is retained;
- `float_val` (5, fixed32), `double_val` (6, fixed64), `int_val` (7, varint),
  `int64_val` (10, varint): repeated, appended, in packed (wire 2) or unpacked form;
- `version_number` (3, varint), `string_val` (8, bytes), `scomplex_val` (9, fixed32),
  `bool_val` (11, varint), `dcomplex_val` (12, fixed64), `half_val` (13, varint),
  `resource_handle_val` (14, message), `variant_val` (15, message), `uint32_val` (16, varint),
  `uint64_val` (17, varint) and `float8_val` (18, bytes) are ignored.
- Payload-list wires: `float_val` contributes on wire 2 (packed) or 5 (one fixed32) and
  `double_val` on wire 2 (packed) or 1 (one fixed64); any other wire on those fields refuses the
  file. `int_val`/`int64_val` contribute on wire 0 (one varint) or 2 (packed varints); a wire 1/5
  occurrence is skipped, contributes no element and does not make the list non-empty, though a
  short body still refuses the file.

Ignored fields are **wire-tolerated**, exactly as the frozen walk tolerates them: wire 0/1/2/5 is
skipped without regard to the field's nominal type, and any other wire type refuses the file. A
`string_val` sent as fixed32, or `scomplex_val` sent as varint, is skipped and never fails the
entry, provided its body is long enough for that wire; a short fixed32/fixed64 body refuses the
file first (§3.2), exactly as the frozen reader does.

### 3.3a Finalization order

Once the merge ends, these steps run in order. A failure at any step fails the entry (never the
file), and never stops the frozen walk early: every remaining byte is still walked and still
refuses the file where the frozen grammar would.

0. the dtype is one of `DT_FLOAT=1`, `DT_DOUBLE=2`, `DT_INT32=3`, `DT_INT64=9` (compared as raw
   varints; `_REF` codes 101 and above are rejected), `unknown_rank` is false, there are 1-8
   dimensions, each dimension decoded as a signed int64 is `1..2**31-1`, and the product is
   `1..1_000_000` (`width` follows the dtype);
1. the last `tensor_content` range is non-empty: its length must be exactly
   `element_count * width`, else fail; it is the payload and is hashed fresh. In this branch
   **every** typed list is ignored for value and validity: foreign dtype, overflow, zero-length
   packed span, packed-varint damage. Unpacked damage, packed float/double length damage, a
   wrong wire on `float_val`/`double_val` and a short fixed body have already refused the file in
   §3.2, so nothing in this branch can rescue them;
2. otherwise (content absent or empty) the payload is the list matching `dtype`:
   - it must be present and its element count must equal `element_count`, else fail; a selected
     count above 1,000,000 fails;
   - damage inside its packed varint span fails; a wire-1/5 `int_val`/`int64_val` occurrence is
     skipped, contributes no element and does not make the list non-empty (the same applies to a
     foreign integer list);
   - any **non-empty** list for another dtype fails the entry. Non-empty is a stored-span
     property: a packed span of at least one byte is non-empty even if it decodes to zero
     elements, an unpacked occurrence of that dtype is non-empty, and a zero-length packed span
     is not. For packed spans the frozen string path counts bytes the same way; its habit of
     counting wire-0/1/5 integer occurrences as other-typed values is not the 0.9 rule: wire 0
     contributes an element, and only wire 1/5 occurrences are skipped. The three other supported lists saturate their counters; a foreign list never
     reaches this test in the content branch.

An empty `tensor_content` range is defined as absent for step 1. TF's broadcast form (fewer typed
values than elements) is not supported and fails the entry.

### 3.4 Canonical encoding

The digest is over a canonical encoding so different writer forms of the same array agree:

- row-major element order;
- `float32`/`int32`: 4 bytes little-endian per element; `float64`/`int64`: 8 bytes;
- `tensor_content` bytes are hashed as-is (TensorProto writes them in host byte order; big-endian
  writers are out of scope and produce a different digest);
- typed `float_val`/`double_val` hash the **raw wire bytes** of each element in wire order, never
  a Python float, so a signalling NaN is not quieted and `-0.0` keeps its sign;
- the content digest is a **fresh** SHA-256 over the retained range, computed once at finalize;
  the four typed hashers hold typed bytes only and are discarded when content wins, so no
  implementation can hash `typed || content`;
- typed `int_val`: take the low 32 bits of the decoded varint as two's complement and write 4
  little-endian bytes, so `-1` (a 10-byte varint) and `0xFFFFFFFF` agree;
- typed `int64_val`: the full 64-bit two's complement value, 8 little-endian bytes;
- varints are bounded to 10 bytes and the frozen `_read_varint` semantics; non-minimal
  encodings are accepted. Damage follows §3.2: truncated or overlong **unpacked** varints and
  packed float/double length damage refuse the file; damage inside the selected packed varint
  span fails the entry; a packed varint list discarded **because `tensor_content` won** is still
  decoded eagerly, its damage is latched, and it is ignored; in the typed branch a foreign list
  fails the entry, so its damage is never "discarded";
- if both a non-empty `tensor_content` and typed values are present, `tensor_content` wins and
  the typed values are ignored.

No element passes through a Python float or int conversion that could change its bytes.

### 3.5 Accumulator and bounds

The 0.9 state is separate from `_TensorState`, whose "never allocates a value or dimension
list" contract stays frozen. It holds:

- the last `tensor_content` range (two integers);
- at most eight dimension integers; a ninth `dim` latches a failed entry without abandoning the
  walk;
- four streaming SHA-256 hashers that receive typed values only (one per supported dtype), four
  saturating counts (overflow is recorded, not acted on, until finalization decides whether that
  list is selected), and one latched damage flag per integer list (fields 7 and 10);
- the last `unknown_rank` value and the final `dtype`.

Typed values are fed to their dtype's hasher as they are read and are never retained. A count
above 1,000,000 marks overflow and fails the entry **only when that list is selected**. The
16 MiB Summary cap bounds a packed span to roughly 16.7 million one-byte varints, which is above
the 1,000,000-element profile limit; the saturating counter enforces that stricter limit. Packed float/double payloads are hashed as contiguous byte spans; varint integers are
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
for 0.9), `check_results`' own `has_locators`, the `tensorboard_results` list, a new
`tensor_requests` list, and the per-result dispatch branch all learn the kind. The scan has an
**explicit** `schema_version == SCHEMA_VERSION_0_9` branch calling
`scan_tensorboard_09(data, scalar_requests, string_requests, histogram_requests,
image_requests, audio_requests, tensor_requests)` and returning **six** mappings in that order;
without it tensor requests fall through to `scan_scalars` and vanish.

`models.py` admission: `TensorValue`, `TensorboardTensorLocator`, `Result09` with
`_dispatch_value_09` and `_media_value_locator`, `Manifest09._version_specific_shape` (**eight**
structured locator kinds — jsonl, csv-cell and the six TensorBoard kinds — plus the pointer
grammar and `artifact_id` rule), `_LOCATOR_ADAPTER_09`, `_dispatch_locator_09`, the 0.9 entry in
the `Manifest.model_dump` version tuple (or a saved manifest silently loses `conditions`,
`dataset_id` and `metric_definition`), and five `allOf` entries (the `artifact_id` rule plus four
per-kind branches). `manifest.py` gains 0.9 in `validate_manifest`, `_root_model_for`, `artifact_to_dict`,
`_LOCATOR_TAGS` and the value-union tuple. `models.is_manifest` needs no change: `Manifest09`
subclasses `Manifest`, so the existing `isinstance` already accepts it.

### 5.2 Ladder

The delivered two-stage ladder, first matching row wins:

| Stage 1 condition | Outcome |
| --- | --- |
| conforming tensor at that occurrence | `value` (stage 2) |
| conforming tensors exist but the occurrence is exhausted | `missing` |
| the entry is a `tensor` candidate excluded by §3.1 case 3 | `unsupported_encoding` |
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
`tensor(float32[2x3], 6 elements)` for results only, in model and dict form. The literal is
`tensor(<dtype>[<shape joined by "x">], <n> element[s])`: rank 1 is `tensor(float32[2], 2
elements)`, and the singular form is used for exactly one element. `--json` emits the
object exactly. Compare rows gain `baseline_tensor`/`target_tensor`, and **both**
`_structured_flag` and `_side_flag` must learn `"tensor"`, or the human row prints raw JSON.
`value_display` is the dispatcher for inspect, claim, compare, details, matrix and report; it
must route tensors to `tensor_display` or those surfaces print raw JSON. Lineage needs no branch
of its own: `format_claim_lineage` sends every root and node through `run_claim._display_row`,
which reaches `value_display`. Lineage still needs `value_to_json` to learn `TensorValue` for
`--json`, and `run_claim`'s locator whitelist to admit the kind. The leaderboard excludes the kind as
`non_numeric_or_nonfinite`. `display_locator` renders
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
- Budgets: tensors add no limit. `_budget_message` gains an explicit tensors branch in the first
  test (alongside media), which yields this table:

| Flags besides tensors | Text |
| --- | --- |
| none | `BUDGET_MESSAGE_08` |
| strings | `BUDGET_MESSAGE_06` |
| histograms | `BUDGET_MESSAGE_07_HIST` |
| strings + histograms | `BUDGET_MESSAGE_07` |
| media | `BUDGET_MESSAGE_08` |
| media + strings | `BUDGET_MESSAGE_06` |
| media + histograms | `BUDGET_MESSAGE_07_HIST` |
| media + strings + histograms | `BUDGET_MESSAGE_07` |

  This is a named edit site, not an inference from the 0.8 code. A digest value is at most
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
a media or tensor flag selects a later schema)`; the `--include-images` and `--include-audio`
help become `… (opt-in; implies schema 0.8 unless a tensor flag selects 0.9)`. `--to` help lists 0.9 and the migrate parser
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
| `histogram_display.py` | `value_to_json`, `tensor_display`, and `value_display` wiring |
| `locator_profiles.py`, `run_claim.py` | display, whitelist, value branch |
| `run_compare.py` | `_structured_flag` and `_side_flag` |
| `run_matrix.py`, `run_details.py`, `run_leaderboard.py`, `report.py` | no code change; all four go through the shared display/JSON dispatchers, so they are verification surfaces |
| `run_lineage.py` | no code change; its nodes reach `value_display` through `run_claim`, so it is a verification surface only |
| `migrate.py`, `run_comparability.py`, `cli.py`, `adapter.py` | targets, downgrade tuples, flags, texts, precedence, generator |

## 9. Verification plan

- Reader: canonical equality across `tensor_content` and typed forms for all four dtypes; packed
  and unpacked values; int32 `-1` vs `0xFFFFFFFF`; non-minimal varints; a truncated or overlong
  unpacked varint and a short wire-1/5 integer body refusing the file; an overlong packed varint
  failing a selected entry and being ignored only when `tensor_content` wins; a wrong wire on
  `float_val`/`double_val` and a packed float/double span with a bad length refusing the file;
  damage inside a selected packed varint span failing the entry; stray wire-1/5 integer
  occurrences not making a list non-empty while a wire-0 occurrence does;
  a one-byte packed span counting as a non-empty foreign list and a zero-length one not;
  conflicting typed lists; content plus typed; overflow beside content; shape merge; dimension
  0, negative and ninth dimension; unknown rank last-wins; unsupported dtypes and `_REF`; NaN
  payload and `-0.0` distinctness; element-count and canonical-length mismatch;
  1,000,000/1,000,001 elements; entry-local failures with sibling scalars intact; Summary-cap
  refusal.
- Differential: for scalar, string, histogram and media requests, `scan_tensorboard_09`
  returns the same selections and outcomes as 0.5–0.8 on the same bytes, including files where
  the frozen grammar refuses.
- Identity: per-kind occurrence, a scalar entry not consuming a tensor occurrence, tag filter,
  multifile names, occurrence 2,000,000,000; the two-outcome asymmetry, named precisely: a
  rank-2 tensor gives a scalar locator `selects an unsupported value`; a rank-1 `[1]` float32
  with exactly 4 content bytes (or float64 with 8) plus one or more `float_val`/`double_val`
  gives the scalar locator `selects an unsupported scalar encoding` and a tensor locator a value;
  a rank-1 `[1]` int32 with exactly 4 content bytes, or int64 with exactly 8, gives the same
  pair; a rank-1 `[1]` int32 with exactly one `int_val` (int64 with one `int64_val`) and no other
  non-empty typed list gives the same pair; a rank-1 `[1]` float32 with 4 content bytes plus an
  `int_val` stays a recorded scalar (tensor locator `selects an unsupported value`); a rank-0
  float32 with exactly 4 content bytes and no `float_val`/`double_val` is a recorded scalar,
  while any other non-zero content length, or any float/double typed value, gives the scalar
  locator `selects an unsupported scalar encoding` and the tensor locator `selects an
  unsupported tensor encoding`; zero-length content is absent and falls through to the typed
  path; a rank-0 int32 fails the profile with `selects an unsupported tensor encoding`; a
  `[1,1]` float gives the scalar locator `selects an unsupported value`. These examples assume
  one entry per `(tag, step)`; a sibling entry can change the aggregated scalar outcome.
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
