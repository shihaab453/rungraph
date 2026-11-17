# Schema 0.11 TF2 `[k,3]` histograms

Schema 0.11 is an explicit opt-in that adds **TensorFlow 2 histogram tensors**:
`tf.summary.histogram` writes a `DT_DOUBLE` tensor of shape `[k, 3]`, one row per bucket as
`[left_edge, right_edge, count]`. The first `k - 1` buckets are closed-open and the last is
closed-closed. Schemas 0.1–0.10 stay frozen.

## What gets recorded

Each conforming entry becomes one structured value:

```json
{
  "kind": "tf2-histogram",
  "buckets": [
    {"left": 0.0, "right": 0.1, "count": 3.0},
    {"left": 0.1, "right": 0.2, "count": 1.0}
  ]
}
```

The locator is new, so frozen manifests cannot carry it:

```json
{"kind": "tensorboard-tf2-histogram", "tag": "layer1/activate", "step": 3, "occurrence": 0}
```

`occurrence` counts recorded conforming histograms with that exact `(tag, step)` in the
artifact. `k` is 0–1024; a zero-bucket histogram records `"buckets": []`. Values are finite
binary64; counts are non-negative; a right edge is never below its left edge. Equal edges are
valid for a single-value histogram. The model does not require bucket continuity or integral
counts.

## Extraction, identification and limits

`init --include-tf2-histograms` is required and selects schema 0.11. It can be combined with
every other include flag.

Classification is **shape-based**: every known rank-2 `DT_DOUBLE` tensor whose second
dimension is 3 is claimed, including a negative or oversized first dimension. A non-conforming
claim is skipped and is never also recorded as a plain tensor, even with `--include-tensors`.
`Summary.Value.metadata` is not read. Enable this flag only when every such tensor is a
histogram; do not enable it for coordinate matrices or other `[k,3]` tensors. A `DT_FLOAT`
`[k,3]` tensor remains eligible for the plain-tensor kind.

A plain-tensor occurrence still counts every valid 0.9 numeric tensor, including a
histogram-recorded entry, so tensor occurrence numbers may have gaps. This preserves migrated
0.9/0.10 tensor locators.

A malformed entry (wrong dtype or shape, non-finite value, negative count, `right < left`, a
foreign value field, more than 1,024 buckets) yields no result and does not affect siblings.
Structural protobuf failures refuse the whole event file. The frozen 16 MiB summary cap,
128 MiB event-file cap, 10,000-result cap and shared 16 MiB histogram-JSON budget still apply.

## Verification and display

`validate` re-reads the event file, selects the entry by `(tag, step, occurrence)` and compares
every bucket exactly. A missing occurrence reports `was not found`; a claimed but malformed
`[k,3]` double reports `selects an unsupported tf2 histogram encoding`; another summary
reports `selects an unsupported value`; a changed bucket reports
`does not match the recorded value`.

Text shows `tf2-histogram(k buckets, num=<sum>)`, distinct from the legacy 0.7
`histogram(...)` form. `num` uses `math.fsum`; an overflowing sum prints `num=overflow`. JSON
output is exact. `leaderboard` treats the value as non-numeric.

## Migration and refusals

`migrate --to 0.11` is a version-only rewrite from 0.1–0.10; 0.1/0.2 keep the locator
precheck. Downgrades are `DOWNGRADE_UNSUPPORTED`. `init --force` refuses a declared 0.11
manifest. `conditions` and comparability cover 0.11 through the same first-class fields.

## Limits

- `DT_DOUBLE` only; no float32 histogram values, rank-1 `[3k]` encoding or broadcast forms.
- No plugin metadata interpretation, graph/run metadata or plugin-specific summaries.
- No histogram recomputation, rebucketing, statistical test or fairness claim.
