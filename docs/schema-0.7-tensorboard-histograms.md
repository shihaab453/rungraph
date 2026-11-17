# Schema 0.7 TensorBoard histograms

Schema 0.7 is an explicit opt-in that adds **legacy TensorBoard histograms**
(`Summary.Value.histo`, `HistogramProto`) to the 0.6 string-tensor surface.
Schemas 0.1–0.6, their bytes, readers, checkers and output stay frozen.

## What gets recorded

One histogram per `Summary.Value`, as a structured value:

```json
{
  "kind": "histogram",
  "min": -1.0, "max": 1.0, "num": 4.0,
  "sum": 0.0, "sum_squares": 1.0,
  "bucket_limit": [-0.5, 0.0, 0.5],
  "bucket": [1.0, 1.0, 2.0]
}
```

Numbers are finite binary64, `num` and counts are nonnegative, arrays have
equal lengths, limits strictly increase, and there are at most 1,024 buckets.
Counts are per-bucket as written, not cumulative. Statistics are preserved, not
recomputed. TF2 `[k,3]` tensor histograms are covered by schema 0.11, not this legacy form.

The locator is
`{"kind":"tensorboard-histogram","tag":…,"step":…,"occurrence":…}`; occurrence
is per artifact and per kind. Result ids append `NUL + "histogram"`, names use
`tag @ step (histogram)`, and multifile groups append the basename and a
16-hex digest.

## Extraction and verification

`init --include-histograms` is required; histograms-only or with
`--include-strings` writes schema 0.7. Without the histograms flag, 0.5/0.6
output is unchanged. Budgets: 10,000 results shared, 16 MiB tag bytes, 16 MiB
selected histogram JSON, 16 MiB string bytes, 1,024 buckets per histogram.

`validate` re-reads the event file and compares every component exactly.
Malformed inner histogram bytes make that entry unsupported, never refuse the
file; outer framing/wire failures remain structural. Human output shows
`histogram(<k> buckets, num=<num>)`; `--json` is exact.

## Migration

`migrate --to 0.7` is identity from 0.3–0.6; 0.1/0.2 run the locator precheck.
There is no downgrade. `init --force` refuses an existing 0.7 manifest with
`INIT_0_7_REFUSAL`. `comparability`, `fingerprint` and `conditions` refuse 0.7;
`add` accepts; `rehash` keeps the object-locator rule.

See [the design RFC](tensorboard-histogram-rfc.md).
