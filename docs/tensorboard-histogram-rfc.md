# TensorBoard histograms — design RFC (v0.3)

Status: **DESIGN PROPOSAL. NOTHING IMPLEMENTED. NOTHING AUTHORIZED BY THIS DOCUMENT.**
A new design gate and an owner schema-0.7 admission decision are required before any code.
Schema 0.1–0.6, their readers/checkers and every delivered behavior stay frozen.

## 0. Changes in v0.3

- Folds the first review from all four seats (Sol, Opus, Gemini, DeepSeek).
- Pins the strict `HistogramValue`/`Result07.value` contract, cross-kind value/locator rule,
  entry-local inner decode failures, exact oneof merge boundary, bounded 1,025-pair state,
  per-result JSON conversion, compact display on every human surface, 0.6 string budget,
  full fixed texts, and a per-function 0.7 admission inventory.
- Drops the non-binding 64 KiB per-histogram cap: 1,024 pairs of finite doubles are below it.
- The choice remains schema 0.7, legacy field 5 only, structured value (three seats vs Opus's
  digest; no wire/code contradiction forces the digest).

## 1. Problem and scope

PyTorch `add_histogram`, tensorboardX and TF1 write a `HistogramProto` in
`Summary.Value.histo` (field 5): five doubles plus repeated bucket limits and counts. The
delivered reader wire-checks field 5 and skips it. Users record weight, gradient and activation
distributions this way.

In scope: one histogram per `Summary.Value`, structured recording, exact verification, opt-in
extraction, schema 0.7, migration and compact display. Non-goals in §12.

## 2. Schema 0.7 and the value model

Schema 0.7 = 0.6 plus histograms. A histogram value is a bounded object:

```json
{
  "kind": "histogram",
  "min": -1.0, "max": 1.0, "num": 4.0,
  "sum": 0.0, "sum_squares": 1.0,
  "bucket_limit": [-0.5, 0.0, 0.5],
  "bucket": [1.0, 1.0, 2.0]
}
```

`Result07.value` is `Scalar | HistogramValue`, with these rules:

- `HistogramValue` is strict (`extra="forbid"`), all seven keys required, `kind` const;
- all numeric fields are finite binary64 (`allow_inf_nan=False`); JSON integers are accepted only
  when `float(value) == value` (so `9007199254740993` is rejected); booleans and numeric strings
  are rejected;
- `num >= 0`; every count finite and `>= 0`; array lengths equal; at most 1,024 pairs;
  `bucket_limit` strictly increasing (`-0.0` and `0.0` compare equal, so `[-0.0, 0.0]` is not
  increasing); zero or one pair is valid; no `min <= max` requirement (TF1 empty histograms use
  `min=DBL_MAX`, `max=-DBL_MAX`);
- extraction normalizes `-0.0` to `0.0`, so emitted manifests never contain `-0.0`;
- recorded statistics are preserved, never recomputed (`sum(bucket)` need not equal `num`);
- a `HistogramValue` is allowed only with a `tensorboard-histogram` locator or a null locator.
  A histogram object on a JSON/JSONL/CSV/TensorBoard/string locator fails model validation, so a
  `.json` pointer can never “verify” a histogram. A scalar value with a histogram locator is
  allowed and the checker reports it as unsupported.
- the compact JSON byte count is
  `len(json.dumps(value.model_dump(mode="json"), ensure_ascii=True, allow_nan=False,
  separators=(",", ":")).encode("utf-8"))`. At 1,024 finite pairs this is under 64 KiB, so no
  independent per-value cap exists.

Earlier value unions stay exactly as shipped.

## 3. Reader profile

### 3.1 Outer grammar (structural)

The delivered Event/Summary/Summary.Value grammar is unchanged. A `Summary.Value` field-5 key
whose wire type is not 2, a truncated outer length, framing/CRC failure, or the 16 MiB merged
summary cap refuses the file and maps to every locator of every kind, exactly as today.

### 3.2 Winning occurrences and merge

Within one `Summary.Value`, only `histo` occurrences after the last different `value` oneof
member are winning. `simple_value` (2), obsolete histogram (3), image (4), audio (6) and tensor
(8) reset the histogram region; `tag` (1), `node_name` (7), `metadata` (9) and unknown fields do
not. Winning `HistogramProto` messages merge in wire order:

- fields 1–5 (`min`, `max`, `num`, `sum`, `sum_squares`) are doubles, last-wins; an absent
  singular in every winning occurrence is `0.0`;
- fields 6 (`bucket_limit`) and 7 (`bucket`) are repeated doubles, packed wire 2 or unpacked
  wire 1; entries accumulate; a zero-length packed payload appends nothing;
- a later different oneof member discards all earlier histogram occurrences. `histo, simple,
  histo` keeps only the second histogram; `histo, histo` merges; `histo, metadata, histo`
  merges.

### 3.3 Inner decode failures (entry-local)

The histogram decoder never calls the whole-file `_refuse()` path. Inside a winning
`HistogramProto` slice, every decode failure makes **the entry** failing and leaves the rest of
the file readable: bad key varint, field number 0 or `> 2^29-1`, wire types 3/4/6/7, truncated
length or fixed64, packed length not a multiple of 8, wire type other than 1 on fields 1–5, wire
type other than 1/2 on fields 6/7, unknown fields with a valid wire type are skipped.

### 3.4 Bounded state and conformance

The decoder retains at most 1,025 limits and 1,025 counts. The 1,025th pair marks the entry
failing; later field 6/7 entries are skipped without retention. No list larger than 1,025 is
allocated, and retained state never exceeds the snapshot bytes.

An entry is conforming only when: equal lengths, at most 1,024 pairs, strictly increasing
finite limits, finite values, `num >= 0` and counts `>= 0`. Everything else is failing.

The parser runs only on schema 0.7. `scan_scalars`, `scan_tensorboard_06` and every 0.1–0.6 path
never execute histogram code.

## 4. Locator, identity, ordering

- Locator: `{"kind":"tensorboard-histogram","tag","step","occurrence"}`; tag/step/occurrence
  keep the 0.5 rules.
- Occurrence is per `(artifact_id, kind, tag, step)`, among conforming histograms only. Scalar
  and string occurrences, ids and names do not move.
- Result id: the multifile 0.5 payload plus `NUL + "histogram"`.
- Name: `f"{tag} @ {step} (histogram)"`, plus ` #occurrence` when > 0. A group spanning files
  adds `[basename] #<16 hex of SHA-256(UTF-8(artifact_id) + NUL + ASCII(occurrence) + NUL +
  "histogram")>`.
- A histogram may share a tag and step with a scalar or a string. Results interleave in discovery
  order, then record order, then `Summary.Value` order; only enabled kinds are emitted.

## 5. Checker and consumers

- One 0.7 traversal of the protected snapshot holds scalar, string and histogram requests;
  `scan_tensorboard_06` and `scan_scalars` remain for 0.6/0.5.
- Selection table (selection before the recorded-value check):

| Condition | Outcome | Code / message |
| --- | --- | --- |
| occurrence < conforming | value | — |
| occurrence >= conforming, conforming >= 1 | not found | `locator-mismatch` / `was not found` |
| conforming == 0, winning field-5 entry failing | unsupported encoding | `locator-unsupported-value` / `selects an unsupported histogram encoding` |
| conforming == 0, another winning entry | unsupported value | `locator-unsupported-value` / `selects an unsupported value` |
| tag/step never appears | not found | `locator-mismatch` / `was not found` |
| structural file refusal | unreadable | existing structural outcome |

- A recorded value that is not a histogram object on a histogram locator is
  `selects an unsupported value`.
- Comparison is exact on every component (signed zeros equal); no tolerance.
- Shared 0.7 cap: 10,000 eligible entries and 16 MiB of tag bytes across all three kinds, one
  finding per eligible artifact with
  `the manifest exceeds the tensorboard verification limit (10,000 entries or 16 MiB of tag
  metadata); no TensorBoard value was read`. 0.7 is eligible for the 128 MiB snapshot cap.
- Display: `tensorboard-histogram:step=<step>,occurrence=<n>`. `run_claim._display_details`
  (reused by lineage) adds the kind so the raw locator and tag never print.
- Human `compare`, `matrix`, `run`, `claim` and `lineage` render
  `histogram(<k> buckets, num=<num>)`, where `k = len(bucket)` and
  `num = json.dumps(value["num"])`; the rule applies whenever the recorded value is a
  `HistogramValue` in a 0.7 manifest. `--json` keeps the exact object. `leaderboard` excludes it
  as `non_numeric_or_nonfinite`.
- Every consumer converts a live model with `model_dump(mode="json")`; `run_compare`,
  `run_matrix`, `run_details`, `run_claim`, `run_lineage`, `run_leaderboard` and `report` are
  tested with a real `HistogramValue`.

## 6. Adapter and CLI

- `supports_include_histograms`, `extract(..., include_histograms: bool = False)`;
  `init --include-histograms`.
- Combinations: no flag → 0.5; strings only → 0.6; histograms only or both → 0.7. Without the
  histogram flag, histograms are ignored; existing output is byte-identical.
- Classification happens before tag matching. Nonconforming histograms and conforming histograms
  with unrepresentable tags (empty, invalid UTF-8, >4,096 bytes) are skipped and never passed to
  `TagFilter.matches`; scalars keep the delivered refuse-without-tags/skip-with-tags behavior;
  strings keep the 0.6 skip rule.
- Conforming scalar, string and histogram tags share one `TagFilter` cache and 50,000,000-unit
  budget; each distinct `(tag, pattern)` pair is charged once. A pattern matching only a
  histogram tag is not a warning; with either include flag the no-match line ends
  `matches no representable supported summary tag`.
- Check order: existing manifest, adapter lookup, `supports_include_strings` when strings are
  requested, the `include_strings` signature contract, `supports_include_histograms` when
  histograms are requested, the `include_histograms` signature contract,
  `supports_tag_filters` when tags are requested, the `tag_filter` signature contract, pattern
  validation, discovery.
- Budgets: 10,000 results shared; 16 MiB tag bytes shared; 16 MiB selected string UTF-8 bytes
  (the 0.6 cap, retained); 16 MiB selected histogram JSON; 1,024 buckets per histogram; 128 MiB
  file and 16 MiB merged-summary caps unchanged. Histogram JSON bytes are exactly
  `json.dumps(model_dump, ensure_ascii=True, allow_nan=False, separators=(",", ":"))` UTF-8,
  charged once per selected histogram.
- Failure precedence: path, profile, unrepresentable scalar tag, too-expensive pattern, caps,
  fixed-id collision, result-name collision, volatile. Nonconforming histogram/string entries
  are skipped and never occupy a tier.
- Fixed errors:
  - `ERROR: adapter {name!r} does not support --include-histograms. Nothing was written.`
  - `ERROR: adapter {name!r} cannot apply --include-histograms. Nothing was written.`
  - a scalar tag under either include flag:
    `a scalar tag cannot be represented in schema 0.7; nothing was written`
  - budget:
    `the selected TensorBoard summaries exceed the 10,000-result, 16 MiB tag, 16 MiB
    string-value or 16 MiB histogram-JSON limit; nothing was written`

## 7. Migration, refusals, fixed texts

- `migrate --to 0.7`: identity from 0.3/0.4/0.5/0.6; 0.1/0.2 run the locator precheck; 0.7 → 0.7
  is a no-op; 0.7 → 0.2/0.3/0.4/0.5/0.6 is `DOWNGRADE_UNSUPPORTED`.
- `INIT_0_7_REFUSAL`:
  `ERROR: the existing manifest declares schema 0.7. init --force would regenerate the manifest
  and cannot carry over first-class values or locators. Nothing was written. Move the manifest
  aside, run init, then run runevidence migrate PATH --to 0.7 (unless the adapter writes 0.7
  itself) and reapply the first-class values and locators.`
- `migrate` target error:
  `ERROR (UNSUPPORTED_TARGET): only --to 0.2, --to 0.3, --to 0.4, --to 0.5, --to 0.6 or --to 0.7
  is supported; downgrade and other versions are not available. Nothing was written.`
- `conditions` on 0.7:
  `ERROR (UNSUPPORTED_VERSION): the conditions command requires an explicit schema 0.2
  manifest. A schema 0.1 manifest can be migrated with runevidence migrate PATH --to 0.2;
  schema 0.3, 0.4, 0.5, 0.6 and 0.7 have no downgrade path. Nothing was written.`
  Older versions keep their frozen lines.
- `comparability`, `fingerprint` and `conditions` refuse 0.7 by schema version; `add` accepts;
  `rehash` keeps the object-locator rule; event-volatile accepts `Manifest07` and stays
  path-based.
- 0.7 directory hint and unverified warning:
  `Point the result at a single JSON, JSONL, CSV or TensorBoard event file artifact, or drop the
  locator.` and
  `result {res.id!r} has no locator in schema 0.7; its recorded value was not checked`.
- `checks/graph.py` names schema 0.7 and applies the cap hint. 0.1–0.6 texts are unchanged.

## 8. Admission checklist (per module)

- `models.py`: `Manifest07`, `Result07`, `HistogramValue`, `TensorboardHistogramLocator`
  (not a subclass of `TensorboardLocator`), `_LOCATOR_ADAPTER_07`/`_dispatch_locator_07`,
  scalar-shape guard, `model_dump`/`_version_specific_shape` 0.7, cross-field value/locator
  rule, `is_manifest`, `_rewrite_descriptions` (0.3–0.6 → 0.7), `json_schema_0_7` with
  `$defs.HistogramValue`, seven locator branches and `Result07.value` anyOf.
- `manifest.py`: dispatch/`_root_model_for`, `_LOCATOR_TAGS`, `_issue_location` for the value
  union, `artifact_to_dict` 0.7.
- `checks/__init__.py`: `_LOCATOR_SCHEMAS`, `_csv_cell_cap`, `_tensorboard_cap` (three kinds),
  `_tensorboard_snapshot_ids`, cap text 0.7.
- `checks/results.py`: `has_locators`, `_extension_matches`, `_expected_extension`,
  `_locator_kind`, `capped_kinds`, 128 MiB branch, `scan_tensorboard_07`, histogram checker,
  JSON/JSONL reject of histogram values, directory hint.
- `checks/graph.py`, `run_claim.py`/`run_lineage.py`, `run_comparability.py`,
  `run_compare.py`, `run_matrix.py`, `run_details.py`, `run_leaderboard.py`, `report.py`.
- `migrate.py`/`cli.py`: target 0.7, texts, `declared` tuple, `Manifest07` volatile, CLI hash
  rotation.
- `schema/runevidence-0.7.schema.json` added and pinned; unknown-version vectors moved from
  0.7 to 0.8; tests that used 0.7 as unsupported move to 0.8.

## 9. Verification plan

- Real PyTorch/tensorboardX fixtures plus hand-built vectors: packed/unpacked/merged buckets,
  zero-length packed, last-wins doubles, empty histogram (`DBL_MAX/-DBL_MAX`), 0/1/1,024/1,025
  pairs, 1,025th failing, 16 MiB stress, non-finite/negative/unequal/non-increasing, inner
  malformed keys/truncations/packed lengths, losing payloads, reset/merge sequences, outer
  wrong wire structural.
- Value model: bool/string/NaN/inf/lossy integer rejection, `-0.0` normalization, cross-kind
  locator rejection, exact JSON bytes, live model through every consumer.
- Identity/names/ids, mixed tags, multifile groups, insertion and delete-then-add.
- Checker ladder, exact comparison, cap text/cardinality, 128 MiB, claim/lineage canary,
  graph labels, JSON exactness, 0.6 regression pins.
- Adapter/CLI flags and texts, budgets including 16 MiB string and histogram boundaries,
  skipped entries, capability errors, migration/refusals.
- Full matrices 3.10/3.12/3.14, pydantic 2.6/2.14, Windows, packaging.

## 10. Decisions requested

- **D1:** schema 0.7 = 0.6 plus histograms; 0.1–0.6 frozen.
- **D2:** structured `HistogramValue`; legacy field-5 only; TF2 tensors out.
- **D3:** locator, per-kind occurrence, names/id/digest suffix.
- **D4:** exact comparison, compact human display, no new finding codes.
- **D5:** opt-in `--include-histograms`; flag combinations.
- **D6:** 1,024 buckets, 16 MiB histogram JSON, 16 MiB string bytes, 10,000 shared results.
- **D7:** identity migration, `INIT_0_7_REFUSAL`, version refusals and fixed texts.
- **D8:** the per-module admission checklist above is part of the slice.

## 11. Non-goals

TF2 `[k,3]` tensor histograms and higher-rank tensors, images/audio/video, tables, graphs, run
metadata, hparams/custom scalars, obsolete field 3, percentiles, re-binning, distribution
distances, cross-file merging, bucket-dump artifacts, plugin metadata, new dependencies, any
change to 0.1–0.6, release or parity claims.
