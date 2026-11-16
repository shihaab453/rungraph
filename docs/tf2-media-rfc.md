# TF2 media summaries — design RFC (v0.4)

Status: **DESIGN PROPOSAL. NOTHING IMPLEMENTED. NOTHING AUTHORIZED BY THIS DOCUMENT.**
A new design gate and an owner schema-0.10 admission decision are required before any code.
Schema 0.1–0.9, their readers/checkers and every delivered behavior stay frozen, except the
additive dispatch, model, migration and text edits this document names explicitly.

## 0. Changes in v0.4

Round 3 confirmed the v0.3 design and rejected it on missed table rows, stale lines and an
incomplete inventory. v0.4 fixes them:

- **Ladder completed.** Precedence is value, exhausted occurrence (`missing`), element beyond
  the entry's payload count (`missing`), string-tensor encoding failure, anything outside
  structural recognition (`unsupported_value`), absent (`missing`). Recognition is structural
  (member 8 + `DT_STRING`), so dtype 0/20/21 tensors fall to the exhaustive fallback; validation
  is per requested kind, so `k=0`, `k=65`, `[k,3]`, `tensor_content`, rank-0 text and the opposite
  kind all have rows.
- **One merge rule, everywhere.** Repeated field-8 fragments merge by protobuf rules and the
  merged state is validated; the stale "merge fails" test line and the stale compare-flag row
  are removed.
- **Derivable ids and a pinned counting unit.** The adapter kind tokens are `tf2-image` and
  `tf2-audio`; ids append them and `e<element>` always. Occurrence advances once per conforming
  **entry**, and element records share it.
- **No new compare flags.** Compare flags derive from the value kind, so TF2 results already set
  `baseline_image`/`baseline_audio`.
- Corrections folded: payload range `2..k+1`, `uint32_val`/`uint64_val` and wrong-wire
  `string_val`, 128-offset retention until the rank is known, seven downgrade edits (0.2 generic,
  0.3–0.9 explicit), the explicit `--to 0.10` branch, a bounded width/height parse, the
  docs-contract and guide updates, the legacy media help strings, the capability-error texts,
  and the multifile name replacing the occurrence suffix.

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
- no non-empty `tensor_content` and no other typed value field is present (an empty
  `tensor_content` is absent, as in the delivered readers; a non-empty `tensor_content`,
  `float_val`, `double_val`, `int_val`, `int64_val`, `half_val`, `bool_val`, `uint32_val`,
  `uint64_val`, complex, variant, resource or `float8_val` field fails the entry, and a
  `string_val` sent on a non-length wire fails the entry);
- every `string_val` is at most 16 MiB and non-empty where a payload is required.

Rank 0 and rank-1 `[1]` string tensors are **not** TF2 media. For string requests they keep the
delivered 0.6 behavior; a TF2 media request on such an entry reports `unsupported_encoding`
(it is a string tensor that conforms as neither TF2 layout). `tf.make_tensor_proto` of a single
encoded image produces that shape, and it is explicitly out of scope (§11).

### 3.2 Image layout

For a rank-1 `[2 + k]` entry:

- element 0 is the width and element 1 the height: ASCII decimal digits only, at most 32 bytes
  each, at most five significant digits after stripping leading zeros, values `1..65535`; an
  empty string, sign, whitespace, non-digit, longer field or longer significant run fails the
  entry, so the conversion is always bounded;
- the decoded dimensions must equal the PNG IHDR width/height of **every** payload; a mismatch
  fails the entry;
- elements 2..k+1 are PNG payloads, validated exactly as in 0.8;
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

A batch is normally one tensor message: one `dim [2+k]` or `[k, 2]` and exactly that many
`string_val`s. Repeated field-8 occurrences merge exactly as the delivered reader merges them —
shape dimensions append, `string_val` appends, last-wins fields stay last-wins — and the merged
state is then validated against the requested layout in §3.1. There is no separate merge
failure rule: a merge that yields the right shape and count is a batch, and any other merge
fails the entry through the ordinary layout checks.

The 0.10 state is separate from `_TensorState` and retains at most **128** element offsets, which
covers the largest valid layout (audio `[64, 2]` = 128 values) and the image form (2 + 64 = 66).
A 129th retained string fails the entry while reading. Besides those offsets it retains only the
dimension count and the first two dimension values (the layouts are rank 1 or rank 2); nothing
else. `_TensorState` and every 0.5–0.9 reader stay byte-identical.

## 4. Identity, ordering, names

- Occurrence counts conforming **entries** per artifact, `(tag, step)` and kind, as the delivered
  locators already say ("in this artifact"); the adapter's per-file counters and the checker's
  per-artifact snapshot agree. `element` indexes the payload inside the entry. Two results
  selecting different elements of one entry therefore share `(tag, step, occurrence)` and differ
  only by `element`, so checker requests are four-tuples.
- Result ids append `NUL + kind` with the adapter-internal tokens `tf2-image` and `tf2-audio`,
  then `NUL + "e" + str(element)` **always**, including element 0. Only the two TF2 kinds append
  the element suffix, so the id is derivable from those locators alone; every legacy kind keeps
  its delivered token and bytes (the string token is `text`, not the locator kind). Occurrence advances once per conforming **entry**, and every
  element record of that entry carries the same occurrence.
- Names: `tag @ step (tf2 image) element N` / `(tf2 audio) element N`, then ` #n` when the entry
  occurrence is above zero. A multifile group **replaces** that occurrence suffix with
  `[basename] #<digest>`, where the digest includes the kind and the element; the delivered
  reader uses the same replace-not-append shape. Legacy names are unchanged.

## 5. Checker and consumers

Admission follows 0.8: `_LOCATOR_SCHEMAS`, the six-kind caps become eight TensorBoard kinds (all
sharing the 10,000-result and 16 MiB tag limits), the csv cap, the 128 MiB snapshot tuple,
extension/kind maps, `capped_kinds`, the directory hint, the cap-message gate,
`check_results.has_locators`, and the `tensorboard_results` list learn both kinds. The scan has
an **explicit** `schema_version == SCHEMA_VERSION_0_10` branch; without it TF2 requests fall
through to `scan_scalars` and vanish.

Requests for these kinds are `(tag, step, occurrence, element)`; a dedicated state keeps one
selection per four-tuple. Recognition is structural: an entry is a **string-tensor entry** when
its winning member is `tensor` (8) and its merged dtype is `DT_STRING` (7), whatever its shape.
Validation is per requested kind, so a string tensor with the wrong rank, dimensions, count,
typed fields or payloads is still recognisable and can be diagnosed.

Stage 1, first matching row wins, mirroring the delivered `_media_results` precedence:

| Stage 1 condition | Outcome |
| --- | --- |
| conforming entry at that occurrence, element `< k` | `value` (stage 2) |
| conforming entries exist and the occurrence is exhausted | `missing` |
| conforming entry at that occurrence, element `>= k` | `missing` |
| entries exist that are string-tensor entries, conform as the **other** TF2 kind, and never as the requested kind | `unsupported_value` |
| entries exist that are string-tensor entries but conform as neither TF2 kind | `unsupported_encoding` |
| any other entry at the tag/step: another member, a tensor outside structural recognition (including dtype 0/20/21), or no winning member | `unsupported_value` |
| no entry at the tag/step | `missing` |

"Conforming" means §3.1–§3.4 pass for the requested kind; it includes `k >= 1`, the 64 cap,
the exact string count, the layout, the width/height agreement, the typed-field exclusion and
each payload's validation. Rows 2 and 3 are checked before the unsupported rows, exactly as every delivered kind checks
identity exhaustion before an unsupported reason. An element `>= k` on an otherwise conforming
entry is `missing`: the identity is `(tag, step, occurrence, element)` and the checker never
searches later entries for a different element. The fallback row is deliberately exhaustive:
anything that is not a conforming string-tensor entry and not a string-tensor encoding failure
is `selects an unsupported value`, which covers dtype 0, `DT_RESOURCE`, `DT_VARIANT` and every
other tensor form.

Stage 2: recorded value not an `ImageValue`/`AudioValue` → `locator-unsupported-value`,
`selects an unsupported value`; differs → `result-value-mismatch`, `does not match the recorded
value`; matches → none. The delivered helper uses one `kind` for both the value model and the
message; 0.10 adds a separate display label so `unsupported_encoding` renders `selects an
unsupported tf2 image encoding` / `selects an unsupported tf2 audio encoding` while the value
check still uses `image`/`audio`. A text table that happens to have the right shape is reported
the same way (it is a string tensor that does not validate as media, which is accurate).

`display_locator` renders `tensorboard-tf2-image:step=<n>,occurrence=<k>,element=<e>` (and the
audio analogue). Human surfaces keep the compact `image(64x64 png, N bytes)` / `audio(wav, N
bytes)` values; `--json` is exact. No new compare flags are added: compare derives its flags
from the value kind, so TF2 results already set `baseline_image`/`baseline_audio` (and targets).
The leaderboard excludes both kinds as `non_numeric_or_nonfinite`.
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
- Seven target paths need edits: 0.3 through 0.8 each have an explicit downgrade list, and the
  0.9 arm has none and needs one; the 0.2 arm already refuses every non-0.1 source generically.
  The new `--to 0.10` target needs its own explicit branch, because the final `else` in the
  dispatcher is the 0.5 arm.
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
"or a TF2 media flag selects 0.10", and the `--include-images`/`--include-audio` help becomes
"implies schema 0.8 unless a tensor or TF2 media flag selects a later schema". Capability errors
for the new flag are pinned as `ERROR: adapter {name!r} does not support --include-tf2-media.
Nothing was written.` and the matching "cannot apply" line. `--to` and migrate parser help list 0.10. The 0.10 cap text
is `TENSORBOARD_CAP_MESSAGE_06`.

## 8. Admission checklist (per module)

| Module | Change |
| --- | --- |
| `models.py` | `TensorboardTf2ImageLocator`, `TensorboardTf2AudioLocator` (required `element`), `Result10`, `Manifest10`, the `Manifest._version_specific_shape` 0.10 branch with ten kinds, dispatch/adapters, `model_dump` 0.10, value↔locator matrix, schema file and pin |
| `manifest.py` | dispatch, `_root_model_for`, `artifact_to_dict`, `_LOCATOR_TAGS`, value-union tuple |
| `tensorboard_events.py` | TF2 layout extraction with its own bounded state, `scan_tensorboard_10`, `iter_supported_summaries_10` (all lower kinds plus TF2), four-tuple selection |
| `checks/__init__.py`, `checks/results.py` | 0.10 admission, eight-kind caps, element requests and ladder |
| `checks/graph.py` | both version gates |
| `locator_profiles.py`, `run_claim.py` | element-aware locator display and whitelist |
| `run_compare.py` | no change; flags derive from the value kind, so TF2 results already set the image/audio flags |
| `migrate.py` | seven target edits: explicit lists in 0.3-0.8, a new 0.9 arm, and the 0.10 target branch (0.2 already refuses generically) |
| `run_comparability.py`, `cli.py`, `adapter.py` | comparability, flag, capability/keyword checks, texts, call sites, id/name plumbing |
| `tests/tensorboard_builder.py` | `string_val` support (single and repeated fragments) |
| `tests/test_cli_migrate.py`, `tests/test_migrate_0_4.py`, `tests/test_migrate_0_5.py`, `tests/test_manifest_0_2.py` | the 0.10-as-unsupported expectations move to 0.11 |
| `tests/test_cli_conditions.py` | a 0.10 manifest now loads and reaches the 0.10 `UNSUPPORTED_VERSION` text |
| `tests/test_docs_contract.py` | page map, flag lists, migrate-target and locator-kind assertions gain 0.10 |
| `docs/tensorboard-guide.md` | deliberate-limits section now says TF2 media IS read under 0.10 |
| `docs/`, `schema/`, `README.md` | 0.10 page, schema file, flag documentation |

## 9. Verification plan

- Layouts: single and batched images/audio; bounded width/height parsing (including a 5,000-digit
  decoy) and IHDR mismatch; labels; `k = 0`; 64 and 65 payloads; shape/count mismatch; excess
  `string_val`; rank-0 and `[1]`
  stay text/unsupported; `tensor_content` and typed fields fail; a split fragment merge that
  forms a valid layout is recorded, and one that does not fails the entry.
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
