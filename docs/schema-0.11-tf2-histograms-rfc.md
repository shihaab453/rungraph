# TF2 `[k,3]` histogram summaries — design RFC (v0.1)

Status: **DESIGN PROPOSAL. NOTHING IMPLEMENTED.** The owner selected this P3 capability on
2026-11-17. A design gate and the repository's admission review are required before code.
Schemas 0.1–0.10, their readers, checkers and every delivered output stay frozen except the
additive dispatch, model, migration, schema and text edits named here.

## 0. Changes in this version

This is the first version. It defines the problem, the schema-0.11 additions, the wire profile,
the extraction and flag rules, verification, migration, limits, tests and open questions.

## 1. Problem and scope

TensorFlow 2's `tf.summary.histogram` writes a `DT_DOUBLE` tensor of shape `[k, 3]` under
`Summary.Value.tensor` (field 8). Each row is `[left_edge, right_edge, count]`; the first
`k - 1` buckets are closed-open and the last is closed-closed. With no data and a positive
bucket count the tensor is all zeros; with a single unique value all edges are equal and only
the last count is non-zero. The default bucket count is 30; `buckets=0` can produce shape
`[0, 3]`.

Today schema 0.9 records such a tensor as an opaque `TensorValue` digest, and schema 0.10 does
not look at it. The histogram's bucket structure is lost, so a user cannot compare, rank or
explain distribution drift from recorded values. Schema 0.7 covers only the legacy
`Summary.HistogramProto`, not this tensor layout.

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
finite binary64 (JSON integers are accepted only when exactly representable). `count` is
non-negative and `right >= left`. The model does not require bucket continuity, monotonic
edges across rows, or integral counts: TensorFlow writes integral counts as doubles, but a
custom producer may use rounding, and the recorded tensor is the authority. `left == right`
is valid and is how a single-value histogram is represented.

The 0.7 `HistogramValue` is unchanged. A `tf2-histogram` value never carries `min`, `max`,
`sum` or `sum_squares`, because those fields are not in the tensor.

### 2.2 Locator

```json
{"kind": "tensorboard-tf2-histogram", "tag": "layer1/activate", "step": 3, "occurrence": 0}
```

`tag` uses the delivered TensorBoard tag bounds (1..4096 code points and UTF-8 bytes, no lone
surrogate). `step` is the delivered signed 64-bit step. `occurrence` is zero-based among
conforming `tf2-histogram` entries with the exact `(tag, step)` in that artifact, like every
other TensorBoard kind. There is no `element` and no per-bucket index.

### 2.3 Result, manifest and dispatch

- `Result11` is `Result10` plus the new value and locator union members.
- `Manifest11` has `schema_version: "0.11"` and `results: list[Result11]`.
- The value↔locator matrix is one-way: `Tf2HistogramValue` requires
  `tensorboard-tf2-histogram` or `null`. `TensorValue` keeps `tensorboard-tensor` only, so a
  plain tensor cannot claim to be a histogram.
- A 0.11 manifest accepts the eleven structured locator kinds: the ten 0.10 kinds plus
  `tensorboard-tf2-histogram`. A 0.10 or earlier manifest still rejects the new kind.
- Result IDs append `tf2-histogram`; names read `tag @ step (tf2 histogram)`; the text summary
  reads `histogram(k buckets, num=<sum of counts>)`.
- JSON output is exact; no float is rounded or normalized.

## 3. Reader profile

### 3.1 Candidate

A `Summary.Value` whose winning member is `tensor` (8) is a TF2-histogram candidate when:

- `dtype` is `DT_DOUBLE` (2);
- the shape is known, rank 2, dimensions exactly `[k, 3]`, with `0 <= k <= 1024`;
- the tensor has exactly `3 * k` values in one representation:
  - a non-empty `tensor_content` of exactly `24 * k` bytes, little-endian binary64; or
  - exactly `3 * k` `double_val` entries, packed or unpacked;
  - never both, and never another typed value field (`float_val`, `int_val`, `int64_val`,
    `half_val`, `bool_val`, `uint32_val`, `uint64_val`, complex, variant, resource or
    `float8_val`), which makes the entry unsupported;
- every value is finite; every count is non-negative; every right edge is at least its left
  edge.

The entry is local: a malformed candidate yields no structured result, consumes no
occurrence, and does not affect sibling summaries. Structural failures the frozen walk already
refuses (bad keys, forbidden wire types, truncation, the 16 MiB summary cap) still refuse the
whole file.

`k = 0` is a conforming empty histogram and records `"buckets": []`. A `[0,3]` tensor with any
non-empty value representation is not conforming.

`DT_FLOAT` is not a histogram candidate. `tf.summary.histogram` casts to float64 before
`make_tensor_proto`; accepting float32 would silently round edges and counts. A float32
`[k,3]` tensor remains eligible for the delivered plain-tensor kind when `--include-tensors` is
selected.

### 3.2 Selection precedence

`--include-tf2-histograms` selects schema 0.11. The flag is independent of the other include
flags:

- with only `--include-tf2-histograms`, conforming `[k,3]` double tensors become structured
  histograms; other tensors are not recorded;
- with `--include-tensors` as well, conforming `[k,3]` double tensors become structured
  histograms and every other supported numeric tensor (including a malformed histogram
  candidate) becomes a plain `TensorValue`;
- with `--include-tf2-media`, `--include-images`, `--include-audio`, `--include-histograms` or
  `--include-strings`, their delivered kinds keep their own selection and the 0.11 schema is
  a superset;
- with none of those flags, 0.11 is still selected by `--include-tf2-histograms`; the baseline
  scalar kind is recorded as in 0.5.

A conforming `[k,3]` tensor is never recorded twice under one manifest: structured when the
histogram flag is set, otherwise plain when `--include-tensors` is set.

### 3.3 Limits and budgets

- at most 1,024 buckets per entry;
- at most 10,000 selected results per manifest;
- at most 16 MiB of selected tag bytes and 16 MiB of serialized histogram JSON per manifest,
  matching the delivered 0.7 histogram budget;
- the frozen 16 MiB summary cap and 128 MiB event-file cap are unchanged.

Over-budget selection is the existing fixed budget refusal and writes nothing.

## 4. Verification

`validate` re-reads the event file through the protected snapshot, selects the entry by
`(tag, step, occurrence)`, parses it under §3.1, and compares the recorded bucket list exactly:
same length and same `left`, `right`, `count` values. Numeric equality is used (so `-0.0`
equals `0.0`), matching the delivered histogram checker.

Refusals:

- an entry that is not a tensor, or is a legacy histogram/scalar, reports
  `selects an unsupported value`;
- a tensor that is not a conforming `[k,3]` double histogram reports
  `selects an unsupported tf2 histogram encoding`;
- a missing `(tag, step, occurrence)` reports `was not found`;
- an over-cap or unreadable file keeps its delivered file-level outcome.

An entry that is still a conforming histogram but whose values changed reports a value
mismatch naming the first differing bucket. The frozen scalar/string/legacy histogram/tensor
ladders are unchanged.

## 5. Display and machine-readable output

Text: `histogram(k buckets, num=<sum>)` alongside the tag and step. JSON: the exact value
object; no bucket samples, no recomputation, no scientific claim. A 1,024-bucket histogram is
large by design and is subject to the JSON budget in §3.3.

## 6. Migration, conditions and composition

- `migrate --to 0.11` is a version-only rewrite from 0.3–0.10, with the 0.1/0.2 locator
  precheck unchanged.
- Downgrade from 0.11 is `DOWNGRADE_UNSUPPORTED`.
- `init --force` refuses a declared 0.11 manifest, as it does for 0.10.
- `conditions` accepts 0.11 wherever it accepts 0.2–0.10, and comparability covers it through
  the same first-class condition fields.
- `rungraph` loads 0.11 manifests through the ordinary manifest loader; no workspace format
  change.
- The committed schema file, freeze pin, schema page and `schema`/`migrate` help texts are
  added; the 0.1–0.10 bytes stay frozen.

## 7. Test and admission inventory

- Model/schema: 0.11 round-trip, value/locator matrix, 0/1/30/1024 buckets, 1025 refused,
  non-finite and negative counts refused, `right < left` refused, `[k,3]` with a wrong value
  count refused, `DT_FLOAT` not structured.
- Reader: tensor_content and packed/unpacked `double_val`, both representations refused,
  other typed fields refused, empty `[0,3]`, single-value equal edges, all-zero empty-data
  histogram, truncation/over-cap whole-file refusals, malformed entry local failure.
- Adapter: flag selection, schema 0.11, `--include-tensors` fallback, media/string
  combinations, result IDs/names, occurrence, budgets, no duplicate recording.
- Checker: exact match, changed bucket, missing occurrence, unsupported encoding, unsupported
  value, text/JSON output.
- CLI/migration: init flag, `--help`, migrate targets and refusals, conditions, comparability,
  workspace load, parser pins and reader pins.
- Docs: schema page, adapter guide, README flag/rows, completion contract P3 line, docs
  contract.
- Independent Opus/Sol review of this RFC and of the implementation.

## 8. Decisions requested

1. Accept `DT_DOUBLE` only (recommended) and leave float32 `[k,3]` to the plain-tensor kind.
2. Accept `k = 0` as `"buckets": []` (recommended) instead of a special empty marker.
3. Use a list of `{left,right,count}` objects (recommended for self-description) rather than
   three parallel arrays.
4. With both `--include-tf2-histograms` and `--include-tensors`, fall back to a plain tensor
   for a malformed histogram candidate (recommended: no data loss) rather than omitting it.

## 9. Non-goals

- Legacy `Summary.HistogramProto` behavior (0.7) is unchanged.
- Plugin-specific summaries, graphs, run metadata, text, PR curves and tables remain out of
  scope.
- No histogram recomputation, rebucketing, statistical test or fairness claim.
- No float32 histogram values, no rank-1 `[3k]` encoding, no `[k,3]` tensors with a different
  dtype or meaning.
- No release, tag or version bump.
