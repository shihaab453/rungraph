# TF2 media summaries — design RFC (v0.2)

Status: **DESIGN PROPOSAL. NOTHING IMPLEMENTED. NOTHING AUTHORIZED BY THIS DOCUMENT.**
A new design gate and an owner schema-0.10 admission decision are required before any code.
Schema 0.1–0.9, their readers/checkers and every delivered behavior stay frozen, except the
additive dispatch, model, migration and text edits this document names explicitly.

## 0. Changes in v0.2

All three round-1 seats rejected v0.1. v0.2 rebuilds the design around the real writers:

- **Real layouts.** TF2 images are rank-1 `[2 + k]` string tensors: elements 0 and 1 are the
  width and height as decimal strings, then `k` PNG payloads. TF2 audio is rank-2 `[k, 2]`:
  each row is a WAV and a label string. The v0.1 "all elements are payloads" rule matched
  neither.
- **Problem statement corrected.** A PNG never becomes text (its first byte is not valid UTF-8);
  a small WAV can, but the real layouts are disjoint from the 0.6 text shapes, so **no
  precedence rule is needed** and no text locator can reclassify after migration.
- **New 0.10-only locator kinds.** `tensorboard-tf2-image` and `tensorboard-tf2-audio` with a
  required `element`. The shared 0.8/0.9 classes are untouched, their generated and pinned
  schemas cannot widen, and output bytes do not change.
- **Separate occurrence namespaces**, so legacy image occurrence 0 keeps meaning the same entry
  before and after migration; adapter filtering cannot desynchronise the checker.
- **Dedicated bounded state.** A new reader state retains at most 66 (images) or 128 (audio)
  element offsets, fails the entry past the bound, and never touches `_TensorState`.
- **Complete inventory.** Every downgrade arm, the graph gates, the budget and tag call sites,
  the generator's lower kinds, the adapter id/name plumbing and the test builder are named; the
  element ladder row and a derivable id rule are pinned.

## 1. Problem and scope

The delivered media slice reads only the legacy `Summary.Image` (field 4) and `Summary.Audio`
(field 6) messages. Modern TensorFlow 2 writes media as `DT_STRING` tensors (field 8):

- `tf.summary.image` writes `[width, height, png0, …]`: two decimal strings and the encoded
  PNGs;
- `tf.summary.audio` writes `[k, 2]`: one WAV and one label per row.

Neither form is read today: the tensor is rank 1 with more than one element, or rank 2, so the
0.6 string profile rejects it and no media kind looks at string tensors. The result is that TF2
users' images and audio are invisible to every schema.

PNG entries never became text: `_classify_string` requires valid UTF-8 and a PNG starts with
`0x89`. A WAV can be valid UTF-8, but the real TF2 layouts are rank 1 `[≥3]` or rank 2, which the
text profile never accepts. 0.10 therefore needs **no text-precedence rule**.

In scope: reading those two layouts as digest references, element indexing, exact verification,
schema 0.10, migration and display. Non-goals in §11.

## 2. Schema 0.10 and the locators

Schema 0.10 adds two locator kinds and **no new value models**:

```json
{"kind": "tensorboard-tf2-image", "tag": "samples", "step": 3, "occurrence": 0, "element": 0}
{"kind": "tensorboard-tf2-audio", "tag": "clip", "step": 4, "occurrence": 0, "element": 1}
```

Rules:

- `element` is **required** and is an integer `0..63`;
- `occurrence` is zero-based among conforming entries of that kind with the exact
  `(tag, step)`, exactly as the other kinds count entries;
- the value↔locator matrix extends one-way: `ImageValue` requires `tensorboard-image`,
  `tensorboard-tf2-image` or `null`; `AudioValue` requires `tensorboard-audio`,
  `tensorboard-tf2-audio` or `null`; scalars keep every locator kind;
- `Result10` and `Manifest10` add the two kinds (`Manifest10._version_specific_shape` lists ten
  structured kinds) and nothing else; `TensorboardImageLocator`/`TensorboardAudioLocator` are
  unchanged, so 0.8/0.9 models, generated schemas and pinned files cannot widen;
- a 0.10 manifest never emits `element` on a legacy locator, and a 0.8/0.9 manifest still
  rejects `element` on a media locator.

Object values remain pinned to their own kinds; the ten structured locator kinds are jsonl,
csv-cell, tensorboard, tensorboard-string, tensorboard-histogram, tensorboard-image,
tensorboard-audio, tensorboard-tensor, tensorboard-tf2-image and tensorboard-tf2-audio.

## 3. Reader profile

### 3.1 Candidate entries

A `Summary.Value` whose winning member is `tensor` (8) is a TF2 media candidate when:

- dtype is `DT_STRING` (7) and the shape is known;
- images: rank 1, one dimension `2 + k` with `1 <= k <= 64`;
- audio: rank 2, dimensions `[k, 2]` with `1 <= k <= 64`;
- the number of `string_val` values equals the shape product exactly (no broadcasting, no
  excess, none missing);
- no `tensor_content` and no other typed value field is present (a non-empty `tensor_content`,
  `float_val`, `double_val`, `int_val`, `int64_val`, `half_val`, `bool_val`, complex, variant,
  resource or `float8_val` field fails the entry);
- every `string_val` is at most 16 MiB and non-empty where a payload is required.

Rank 0 and rank-1 `[1]` string tensors are **not** TF2 media; they keep the delivered 0.6
behavior. `tf.make_tensor_proto` of a single encoded image produces that shape, and it is
explicitly out of scope (§11).

### 3.2 Image layout

For a rank-1 `[2 + k]` entry:

- element 0 is the width and element 1 the height, ASCII decimal digits only, values
  `1..65535`; leading zeros are accepted, an empty string, sign, whitespace or non-digit fails;
- the decoded dimensions must equal the PNG IHDR width/height of **every** payload; a mismatch
  fails the entry;
- elements 2..2+k are PNG payloads, validated exactly as in 0.8;
- the result's `element` is the payload index `0..k-1` (element 2 is `element` 0).

### 3.3 Audio layout

For a rank-2 `[k, 2]` entry, row `i` is:

- element `(i, 0)`: the WAV payload, validated exactly as in 0.8;
- element `(i, 1)`: the label, any bytes up to 4,096 long, never decoded, compared or stored;
- the result's `element` is the row index `0..k-1`.

A label longer than 4,096 bytes fails the entry; an empty label is accepted.

### 3.4 Validation and bounds

Each payload reuses the delivered 0.8 validators unchanged: PNG signature, IHDR length/type/
CRC-32 (zlib), dimensions 1..65,535, at least one non-empty `IDAT`, a final `IEND` ending the
element; RIFF/WAVE magic, `riff_size == length - 8`, first `fmt ` of at least 16 bytes, exactly
one non-empty `data`, odd-size padding. Digests cover each element's bytes exactly.

Element count is capped at 64. The merged-summary cap (16 MiB per Event) still bounds the whole
entry, so the per-element 16 MiB bound is a defensive ceiling, not a reachable budget.

### 3.5 Failure policy

A candidate that fails any check is an entry-local failure: it yields no result, consumes no
occurrence, and does not affect siblings. Structural failures the frozen walk already refuses —
bad keys, wrong wires, truncation, the summary cap — still refuse the whole file.

### 3.6 Merge and retention

A batch is **one** tensor message: one `dim [2+k]` or `[k, 2]` and exactly that many
`string_val`s. Repeated field-8 occurrences merge as delivered (shape dimensions append,
`string_val` appends); a merge that produces any other rank, a different dimension count, or a
count mismatch fails the entry.

The 0.10 state is separate from `_TensorState` and retains at most **66** image element offsets
(2 dimensions + 64 payloads) or **128** audio offsets, plus counters. A 67th/129th retained
offset fails the entry while reading; nothing else is retained. `_TensorState` and every
0.5–0.9 reader stay byte-identical.

## 4. Identity, ordering, names

- Occurrence counts conforming **entries** per `(tag, step)` and kind; `element` indexes the
  payload inside the entry. Two results selecting different elements of one entry therefore
  share `(tag, step, occurrence)` and differ only by `element`, so checker requests are
  four-tuples.
- Result ids append `NUL + kind` and then `NUL + "e" + str(element)` for both TF2 kinds,
  **always**, including element 0, so the id is derivable from the locator alone. Scalar ids
  keep the delivered formula with no kind suffix.
- Names: `tag @ step (tf2 image) element N` / `(tf2 audio) element N`, then the delivered
  occurrence suffix ` #n` when the entry occurrence is above zero, then the multifile
  `[basename] #digest` suffix where the digest includes kind and element. Legacy names are
  unchanged.

## 5. Checker and consumers

Admission follows 0.8: `_LOCATOR_SCHEMAS`, the six-kind caps become eight TensorBoard kinds (all
sharing the 10,000-result and 16 MiB tag limits), the csv cap, the 128 MiB snapshot tuple,
extension/kind maps, `capped_kinds`, the directory hint, the cap-message gate,
`check_results.has_locators`, and the `tensorboard_results` list learn both kinds. The scan has
an **explicit** `schema_version == SCHEMA_VERSION_0_10` branch; without it TF2 requests fall
through to `scan_scalars` and vanish.

Requests for these kinds are `(tag, step, occurrence, element)`; a dedicated state keeps one
selection per four-tuple. Stage 1, first matching row wins:

| Stage 1 condition | Outcome |
| --- | --- |
| conforming entry at that occurrence, element `< k` | `value` (stage 2) |
| conforming entry, element `>= k` | `missing` |
| entries exist at the tag/step whose tensor is a string TF2 candidate but fails §3.1–§3.4 | `unsupported_encoding` |
| entries exist at the tag/step with another member (scalar, histogram, legacy image/audio, numeric tensor) or no member | `unsupported_value` |
| no entry at the tag/step | `missing` |

Stage 2: recorded value not an `ImageValue`/`AudioValue` → `locator-unsupported-value`,
`selects an unsupported value`; differs → `result-value-mismatch`, `does not match the recorded
value`; matches → none. `unsupported_encoding` renders `selects an unsupported tf2 image
encoding` / `selects an unsupported tf2 audio encoding`.

`display_locator` renders `tensorboard-tf2-image:step=<n>,occurrence=<k>,element=<e>` (and the
audio analogue). Human surfaces keep the compact `image(64x64 png, N bytes)` / `audio(wav, N
bytes)` values; `--json` is exact; compare rows gain `baseline_tf2_image`/`baseline_tf2_audio`
flags (and targets); the leaderboard excludes both kinds as `non_numeric_or_nonfinite`.
`checks/graph.py` admits 0.10 in both version gates, or a claim on an unverified 0.10 result
reports "schema 0.3" and loses the cap hint.

## 6. Adapter and CLI

- `init --include-tf2-media` is opt-in and selects schema 0.10, reading both TF2 kinds. The 0.8
  flags keep their delivered behavior and schema.
- Precedence: tf2 media → 0.10; tensors → 0.9; media → 0.8; histograms → 0.7; strings → 0.6;
  else 0.5. `iter_supported_summaries_10` yields **every** lower kind as 0.9 does, plus both TF2
  records, so combined flags never drop kinds.
- `_read_event_file` filters TF2 records by the flag and keeps per-kind occurrence counters
  advancing only for records that survive filtering **and** conform, matching the checker's
  namespace; legacy counters are independent.
- The budget text for a TF2-media-only run is `BUDGET_MESSAGE_08` (the media family line);
  `_budget_message` gains the 0.10 flag in its first test. Each element charges one result.
- Tags: media records pass `text=True`, so an unusable tag is skipped, never fatal; the no-match
  warning counts the flag so the noun is "summary tag". `TAG_MESSAGE_10` exists for scalar tags
  and is reached only through the delivered scalar path.
- Adapter plumbing that must learn `element`: `_Observation`, the reservation pass, the emit
  pass, `_result_id`, `_assign_names` and the multifile name digest. The two passes must derive
  identical ids or the collision error fires.

## 7. Migration, refusals, fixed texts

- `migrate --to 0.10` admits 0.1/0.2 through the locator precheck and 0.3–0.9 as version-only
  rewrites. No JSON field is added or removed: `element` exists only on the new kinds, which no
  older manifest can carry.
- Every target branch from 0.2 to 0.9 gains 0.10 in its downgrade arm; the 0.9 arm currently
  falls through to `INTERNAL_ERROR` without it.
- Comparability refuses 0.10 by version; `init --force` refuses a declared 0.10 manifest before
  discovery; `_event_artifact_is_volatile` admits `Manifest10`.
- Fixed texts, all literal:

```
ERROR: the existing manifest declares schema 0.10. init --force would regenerate the manifest and cannot carry over first-class values or locators. Nothing was written. Move the manifest aside, run init, then run runevidence migrate PATH --to 0.10 (unless the adapter writes 0.10 itself) and reapply the first-class values and locators.
ERROR (UNSUPPORTED_TARGET): only --to 0.2, --to 0.3, --to 0.4, --to 0.5, --to 0.6, --to 0.7, --to 0.8, --to 0.9 or --to 0.10 is supported; downgrade and other versions are not available. Nothing was written.
ERROR (UNSUPPORTED_VERSION): the conditions command requires an explicit schema 0.2 manifest. A schema 0.1 manifest can be migrated with runevidence migrate PATH --to 0.2; schema 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9 and 0.10 have no downgrade path. Nothing was written.
a scalar tag cannot be represented in schema 0.10; nothing was written
```

`--include-tf2-media` help: `also record TensorFlow 2 image and audio string summaries as
schema 0.10 results (opt-in; implies schema 0.10)`. The histogram and tensor help texts gain
"or a TF2 media flag selects 0.10". `--to` and migrate parser help list 0.10. The 0.10 cap text
is `TENSORBOARD_CAP_MESSAGE_06`.

## 8. Admission checklist (per module)

| Module | Change |
| --- | --- |
| `models.py` | `TensorboardTf2ImageLocator`, `TensorboardTf2AudioLocator` (required `element`), `Result10`, `Manifest10` version shape with ten kinds, dispatch/adapters, `model_dump` 0.10, value↔locator matrix, schema file and pin |
| `manifest.py` | dispatch, `_root_model_for`, `artifact_to_dict`, `_LOCATOR_TAGS`, value-union tuple |
| `tensorboard_events.py` | TF2 layout extraction with its own bounded state, `scan_tensorboard_10`, `iter_supported_summaries_10` (all lower kinds plus TF2), four-tuple selection |
| `checks/__init__.py`, `checks/results.py` | 0.10 admission, eight-kind caps, element requests and ladder |
| `checks/graph.py` | both version gates |
| `locator_profiles.py`, `run_claim.py` | element-aware locator display and whitelist |
| `run_compare.py` | per-kind flags and `_side_flag` |
| `migrate.py`, `run_comparability.py`, `cli.py`, `adapter.py` | six downgrade arms, comparability, flag, texts, call sites, id/name plumbing |
| `tests/tensorboard_builder.py` | `string_val` support |
| `docs/`, `schema/`, `README.md` | 0.10 page, schema file, flag documentation |

## 9. Verification plan

- Layouts: single and batched images/audio; width/height parsing and IHDR mismatch; labels;
  `k = 0`; 64 and 65 payloads; shape/count mismatch; excess `string_val`; rank-0 and `[1]`
  stay text/unsupported; `tensor_content` and typed fields fail; repeated-message merge fails.
- Payloads: per-element truncation, bad CRC, unpadded WAV, empty payload.
- Identity: occurrence per entry unaffected by elements; element ids always present and
  derivable; names and multifile suffixes unique across batches; tag filter.
- Checker: element out of range → `was not found`; wrong layout → unsupported encoding; other
  member → unsupported value; digest mismatch; locator rendering with `element`.
- Differential: for scalar, string, histogram, media and numeric-tensor requests,
  `scan_tensorboard_10` returns the same selections and outcomes as 0.5–0.9 on the same bytes.
- Migration: 0.9 → 0.10 identity; 0.10 → every lower target `DOWNGRADE_UNSUPPORTED`;
  comparability; force guard; 0.8/0.9 still reject both new kinds and `element`.
- Adapter/CLI: flag precedence, combined flags keep lower kinds, budget text, tag skip, no-match
  noun, capability error.
- Consumers: compact display, compare flags, JSON exactness.
- Mutation probes: drop the width/height check, drop the count check, drop the element bound,
  remove the 0.10 scan branch, admit 0.10 in comparability, drop the force guard.

## 10. Decisions requested

1. Two new locator kinds with a required `element` (recommended) versus one shared field on the
   frozen media classes.
2. One `--include-tf2-media` flag (recommended) versus separate image/audio flags.
3. A 64-payload cap (recommended) versus a larger bound.
4. Rank-0/`[1]` single-payload tensors out of scope (recommended), keeping the delivered text
   behavior.

## 11. Non-goals

- TF2 `[k,3]` histogram tensors and `tf.make_tensor_proto` rank-0 media.
- Plugin-specific summaries (PR curves, embeddings).
- Graph/run metadata; transcoding; extraction.
