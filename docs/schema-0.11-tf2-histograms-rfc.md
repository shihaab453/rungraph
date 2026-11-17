# TF2 `[k,3]` histogram summaries — design RFC (v0.2)

Status: **DESIGN PROPOSAL. NOTHING IMPLEMENTED.** The owner selected this P3 capability on
2026-11-17. A design gate and the repository's admission review are required before code.
Schemas 0.1–0.10, their readers, checkers and every delivered output stay frozen except the
additive dispatch, model, migration, schema and text edits named here.

## 0. Changes in v0.2

v0.2 folds both design reviews:

- pins plain-tensor occurrence numbering when a `[k,3]` entry is also a histogram, including
  gaps and migrated manifests;
- resolves classification: shape-based and explicit, with no plugin-metadata reading, declared
  as a limitation; the §9 "meaning" exclusion is removed;
- specifies the delivered merge/last-wins rules, representation precedence, structural versus
  entry-local failures, and `string_val`;
- pins the verification ladder and fixed mismatch text; drops per-bucket mismatch prose;
- makes text summation overflow-safe and distinguishes the kind in text;
- resolves the budget question (one shared histogram-JSON budget), the occurrence bound,
  serialization/version whitelists and the frozen unknown-version vectors;
- adds the complete integration checklist and the mixed-occurrence/migration tests.

## 1. Problem and scope

TensorFlow 2's `tf.summary.histogram` writes a `DT_DOUBLE` tensor of shape `[k, 3]` under
`Summary.Value.tensor` (field 8). Each row is `[left_edge, right_edge, count]`; the first
`k - 1` buckets are closed-open and the last is closed-closed. With no data and a positive
bucket count the tensor is all zeros; with a single unique value all edges are equal and only
the last count is non-zero. The default bucket count is 30; `buckets=0` can produce shape
`[0, 3]`. (TensorBoard 2.20 `summary_v2._buckets`.)

Schema 0.9 records such a tensor as an opaque `TensorValue` digest, and schema 0.10 keeps that
behavior when `--include-tensors` accompanies `--include-tf2-media`. Neither reads the bucket
structure, so a user cannot compare or explain recorded distribution drift. Schema 0.7 covers
only the legacy `Summary.HistogramProto`, not this tensor layout.

In scope: read a conforming `[k,3]` tensor, record every bucket, verify it exactly, display it,
provide schema 0.11, migration and comparability coverage. Non-goals are in §11.

## 2. Schema 0.11 additions

### 2.1 Value

Schema 0.11 adds one structured value:

```json
{
  "kind": "tf2-histogram",
  "buckets": [
    {"left": 0.0, "right": 0.1, "count": 3.0},
    {"left": 0.1, "right": 0.2, "count": 1.0}
  ]
}
```

`buckets` is `0..1024` entries. Each entry requires exactly `left`, `right` and `count`, all
finite binary64 (JSON integers accepted only when exactly representable; `-0.0` is normalized
to `0.0` by the existing `_exact_binary64` helper, which is why verification uses numeric
equality). `count` is non-negative and `right >= left`. The model does not require bucket
continuity, monotonic edges across rows, integral counts or a closed last bucket: TensorFlow
writes those, but the recorded tensor is the authority. `left == right` is valid and is how a
single-value histogram is represented.

The 0.7 `HistogramValue` is unchanged. A `tf2-histogram` value never carries `min`, `max`,
`sum` or `sum_squares`, because those fields are not in the tensor. `right >= left` and the
finite-value rule are model/runtime checks, not JSON-Schema-expressible constraints.

### 2.2 Locator

```json
{"kind": "tensorboard-tf2-histogram", "tag": "layer1/activate", "step": 3, "occurrence": 0}
```

`tag` uses the delivered TensorBoard tag bounds (1..4096 code points and UTF-8 bytes, no lone
surrogate). `step` is the delivered signed 64-bit step. `occurrence` is an integer
`0..2**31 - 1`, zero-based among recorded conforming `tf2-histogram` entries with the exact
`(tag, step)` in that artifact. There is no `element` and no per-bucket index.

### 2.3 Result, manifest, dispatch and serialization

- `Result11` is `Result10` plus the new value and locator union members.
- `Manifest11` has `schema_version: "0.11"` and `results: list[Result11]`.
- The value↔locator matrix is one-way: `Tf2HistogramValue` requires
  `tensorboard-tf2-histogram` or `null`. `TensorValue` keeps `tensorboard-tensor` only, so a
  plain tensor cannot claim to be a histogram. Scalar-valued results may keep any structured
  locator, as delivered.
- A 0.11 manifest accepts the eleven structured locator kinds: the ten 0.10 kinds plus
  `tensorboard-tf2-histogram`. A 0.10 or earlier manifest rejects the new kind.
- The version whitelists in `models.Manifest._version_specific_shape`,
  `manifest` serialization, artifact serialization, `conditions_authoring`, comparability and
  checks must include 0.11. An in-memory subclass that only changes the version is not enough:
  preservation tests must cover `conditions`, `dataset_id` and `metric_definition` on
  serialization.
- Result IDs append `tf2-histogram`; names read `tag @ step (tf2 histogram)`.

## 3. Reader profile

### 3.1 Winning tensor and merge rules

The new profile inherits the delivered TensorProto walk exactly
(`tensorboard_events._parse_tensor`, `_merge_tensor_v09`, `_scan_tensor_region`,
`_iter_winning_values`):

- a different `Summary.Value` oneof member resets tensor accumulation; only tensor
  occurrences after the last different member are merged (`tensor(A), simple_value, tensor(B)`
  yields only B);
- dtype is last-wins;
- shape dimension lists append across occurrences; `unknown_rank` is last-wins; the final
  shape is the accumulated list;
- `tensor_content` is last-wins and an empty range is absent;
- packed and unpacked `double_val` entries append in wire order;
- an empty foreign typed field is absent; any non-empty `float_val`, `int_val`,
  `int64_val`, `half_val`, `bool_val`, `uint32_val`, `uint64_val`, `string_val`, complex,
  variant, resource or `float8_val` makes the entry non-conforming;
- a non-empty `tensor_content` wins over `double_val`, as delivered; its length must be exactly
  `24 * k` and those bytes are the values;
- if `tensor_content` is absent/empty, `double_val` must hold exactly `3 * k` values;
- a packed `double_val` span whose byte length is not a multiple of 8 is a structural refusal
  (whole file), as the delivered walker already does; a wrong value count is entry-local.

The final shape must be known, rank 2, exactly `[k, 3]`, with `0 <= k <= 1024`. `k = 0` is
valid only with no values. The final dtype must be `DT_DOUBLE` (2).

### 3.2 Numerical conformance

Every value must be finite; every count is non-negative; every right edge is at least its left
edge. A conforming `[0,3]` tensor records `"buckets": []`. Non-finite edges (NaN/Inf, common
in diverged training) are non-conforming and are skipped under the shape reservation below.

### 3.3 Shape reservation and classification

**Classification is shape-based; `Summary.Value.metadata` is not read.** TensorBoard's producer
marks histograms with `plugin_data.plugin_name == "histograms"`, but this reader deliberately
does not, because metadata inheritance is writer- and version-dependent and the manifest has no
metadata field. Enabling `--include-tf2-histograms` is an explicit assertion that every
`DT_DOUBLE` `[k,3]` tensor in the selected folders is a histogram. Do not enable it for
coordinate matrices, boxes or other `[k,3]` tensors. This limitation replaces the v0.1
"another meaning" exclusion.

When the flag is set, every `DT_DOUBLE` tensor with a known rank-2 `[k,3]` shape is **claimed**
by the histogram kind:

- a numerically conforming claim is recorded as a histogram;
- a non-conforming claim yields no result (entry-local) and is **never** also recorded as a
  plain tensor, even with `--include-tensors`;
- a `DT_FLOAT` `[k,3]` tensor, or any other dtype/shape, remains eligible for the delivered
  plain-tensor kind when that flag is set.

This avoids per-step kind flipping and makes the shape reservation unambiguous. A malformed
`[0,3]` cannot fall back to a plain tensor anyway, because 0.9 requires dimensions `>= 1`.

### 3.4 Selection precedence

`--include-tf2-histograms` selects schema 0.11. The flag is independent of the other include
flags:

- with only `--include-tf2-histograms`, conforming `[k,3]` doubles become structured
  histograms; other tensors are not recorded;
- with `--include-tensors` as well, conforming `[k,3]` doubles become structured histograms,
  claimed non-conforming `[k,3]` doubles are skipped, and every other supported numeric tensor
  becomes a plain `TensorValue`;
- with `--include-tf2-media`, `--include-images`, `--include-audio`, `--include-histograms` or
  `--include-strings`, their delivered kinds keep their own selection and 0.11 is a superset;
- with none of those flags, 0.11 is still selected by `--include-tf2-histograms`; the baseline
  scalar kind is recorded as in 0.5.

### 3.5 Occurrence identity (mandatory)

Plain-tensor occurrence numbering is independent of output suppression:

- the `tensor` ladder still counts **every conforming numeric tensor** at `(tag, step)`,
  including a `[k,3]` double that is recorded as a histogram;
- the adapter increments the tensor occurrence counter for every such entry even when it emits
  no plain-tensor result; plain-tensor result occurrences may therefore have **gaps**;
- histogram occurrence counts only recorded conforming `tf2-histogram` entries; a claimed but
  malformed entry consumes no histogram occurrence;
- verification uses the unchanged tensor ladder, so a migrated 0.9/0.10 `tensorboard-tensor`
  locator still selects the same entry after `migrate --to 0.11`.

Example: at `(t, 0)`, A is a conforming `[2,3]` double and B is a float32 `[4]`. With both
flags, B is recorded at tensor occurrence **1**, not 0; A is a histogram at occurrence 0.
With only the histogram flag, no plain-tensor result is emitted. A 0.9 manifest with a tensor
result on A keeps selecting A after migration.

## 4. Verification

`validate` re-reads the event file through the protected snapshot, selects by
`(tag, step, occurrence)`, parses it under §3 and compares the recorded bucket list exactly:
same length and same `left`, `right`, `count` values, using numeric equality (so `-0.0`
equals `0.0`). No tolerance and no rebucketing.

Outcome ladder:

1. a conforming histogram exists at the occurrence index → compare values; a difference is the
   fixed `does not match the recorded value` (no per-bucket prose);
2. no conforming histogram at that index but at least one claimed `[k,3]` double exists at
   `(tag, step)` → `selects an unsupported tf2 histogram encoding`;
3. otherwise, a tensor or another summary exists at `(tag, step)` → `selects an unsupported
   value` (this includes TF2 rank-0 scalars and string tensors);
4. otherwise → `was not found`.

A missing/out-of-range occurrence with a conforming histogram present uses outcome 1's
"was not found" branch; the exact delivered missing text is reused. The delivered scalar,
string, legacy histogram and plain-tensor ladders are unchanged.

## 5. Display and machine-readable output

Text: `tf2-histogram(k buckets, num=<sum>)`, distinct from the legacy `histogram(...)` form.
`num` is `math.fsum` of the counts in recorded row order; if the result is not finite, print
`num=overflow` instead of `inf`. For `k = 0`, `num=0`. JSON: the exact value object; no
recomputation, rounding or sampling.

`compare` treats structured values as non-numeric, as delivered; its row flags name the value
kind (`baseline_tf2-histogram`/`target_tf2-histogram` or the shared structured-value flag) and
`leaderboard` excludes it as `non_numeric_or_nonfinite`. The `value_to_json` and
`histogram_display` helpers must handle the new model, or compare crashes on `json.dumps`.

## 6. Integration checklist

Every site below must be updated or tested as part of the implementation:

- `models`: `SCHEMA_VERSION_0_11`, bucket/value/locator models, `Result11`, `Manifest11`,
  dispatch, value/locator matrix, version whitelist, generated schema 0.11, freeze pin.
- `manifest.py`: manifest and artifact serialization version whitelists; preservation of
  `conditions`, `dataset_id`, `metric_definition`.
- `checks/__init__.py`: eligibility sets, 10,000-entry and 16 MiB tag caps, and the 128 MiB
  event-file snapshot selection (0.11 must use the same TensorBoard cap, not the 50 MiB
  manifest cap).
- `checks/results.py`: locator-kind dispatch, tensor-ladder requests, shared histogram budget.
- `checks/graph.py`: locator-kind inventory.
- `locator_profiles.py`: `display_locator` must not fall through to `.line`.
- `histogram_display.py`, `run_claim.py`, `run_compare.py`: value-to-JSON, display and row
  flags.
- `adapter.py`: flag, claim/skip, occurrence counters, labels, budget message.
- `cli.py`: `--include-tf2-histograms`, help, 0.11 texts, `init --force` refusal before
  validation, migrate target, parser pins.
- `conditions_authoring.py`, `run_comparability.py`: 0.11 coverage.
- `migrate.py`: targets, downgrade refusals, 0.1/0.2 precheck.
- `tensorboard_events.py`: histogram walker/values and reader pin.
- Docs: schema page, adapters guide, README flag/rows, completion contract, docs contract.
- Frozen unknown-version vectors (`"0.11"` currently used as "unknown") move to `"0.12"`:
  `tests/data/schema-vectors/reject-unknown-version.json`, `test_cli_migrate.py`,
  `test_migrate_0_3.py`, `test_migrate_0_4.py`, `test_migrate_0_5.py`,
  `test_cli_conditions.py`, `test_manifest_0_2.py`.

## 7. Migration, conditions and composition

- `migrate --to 0.11` is a version-only rewrite from 0.3–0.10, with the 0.1/0.2 locator
  precheck unchanged; values, types and field presence are preserved.
- Downgrade from 0.11 is `DOWNGRADE_UNSUPPORTED` for every older target.
- `init --force` refuses a declared 0.11 manifest, including an invalid manifest that merely
  declares 0.11; the guard precedes validation.
- `conditions` accepts 0.11 wherever it accepts 0.2–0.10; comparability covers it through the
  same first-class fields. The stale "comparability refuses 0.9/0.10" prose in older pages is
  not copied.
- `rungraph` loads 0.11 through the ordinary manifest loader; no workspace format change.
- The committed schema file, freeze pin, schema page and `schema`/`migrate` help texts are
  added; 0.1–0.10 bytes stay frozen.

## 8. Limits and budgets

- at most 1,024 buckets per entry; retained values are bounded even for oversized malformed
  entries;
- at most 10,000 selected results and 16 MiB of selected tag bytes per manifest;
- one **shared** 16 MiB histogram-JSON budget across legacy 0.7 and TF2 histograms, using the
  delivered compact serialization calculation; the budget message names histogram JSON, and
  continues checking later files before raising the highest-precedence failure;
- the frozen 16 MiB summary cap and 128 MiB event-file cap are unchanged.

## 9. Test and admission inventory

- Model/schema: 0.11 round-trip, value/locator matrix, 0/1/30/1024 buckets, 1025 refused,
  non-finite and negative counts refused, `right < left` refused, wrong value count refused,
  `DT_FLOAT` not structured, serialization preservation.
- Reader: tensor_content and packed/unpacked `double_val`; non-empty content precedence; empty
  content absent; shape-fragment merge; oneof reset; empty foreign fields; non-empty foreign
  fields including `string_val`; malformed packed span structural refusal; empty `[0,3]`;
  single-value equal edges; all-zero empty-data histogram; truncation/over-cap whole-file
  refusals; malformed entry local failure.
- Occurrence: mixed A/B sequence (tensor occurrence gap), histogram occurrence, migrated 0.9
  and 0.10 tensor results on a hist-shaped entry, no duplicate recording.
- Adapter: flag selection, schema, claim/skip, unrelated flags, IDs/names, budgets, labels.
- Checker: exact match, changed bucket, missing occurrence, unsupported encoding, unsupported
  value, fixed texts, text/JSON output, overflow-safe `num`.
- CLI/migration: init flag, help, targets and refusals, conditions, comparability, workspace
  load, parser/reader pins, unknown-version vectors moved to 0.12.
- Docs: schema page, adapter guide, README flag/rows, completion contract, docs contract.
- Independent Opus/Sol review of this RFC and of the implementation.

## 10. Decisions

1. `DT_DOUBLE` only; float32 `[k,3]` stays with the plain-tensor kind (resolved).
2. `k = 0` records `"buckets": []` (resolved).
3. A list of `{left,right,count}` objects (resolved).
4. A claimed `[k,3]` double is never also a plain tensor; malformed claims are skipped
   (resolved, replacing fallback).
5. Classification is shape-based; metadata is not consulted, declared as a limitation
   (resolved).

## 11. Non-goals

- Legacy `Summary.HistogramProto` behavior (0.7) is unchanged.
- Plugin-specific summaries, graphs, run metadata, text, PR curves and tables remain out of
  scope.
- No plugin-metadata interpretation or tag-level metadata inheritance.
- No histogram recomputation, rebucketing, statistical test or fairness claim.
- No float32 histogram values, rank-1 `[3k]` encoding, broadcast forms, or `[k,3]` tensors with
  non-finite values.
- No release, tag or version bump.
