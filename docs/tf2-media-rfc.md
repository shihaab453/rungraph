# TF2 media summaries — design RFC (v0.1)

Status: **DESIGN PROPOSAL. NOTHING IMPLEMENTED. NOTHING AUTHORIZED BY THIS DOCUMENT.**
A new design gate and an owner schema-0.10 admission decision are required before any code.
Schema 0.1–0.9, their readers/checkers and every delivered behavior stay frozen, except the
additive dispatch, model, migration and text edits this document names explicitly.

## 0. Changes in v0.1

- First draft. Proposes reading `tf.summary.image` and `tf.summary.audio`, which write
  `DT_STRING` tensors, into the delivered media value models under schema 0.10.
- Pins: a new `element` field on both media locators, entry-level occurrence with element
  indexing, one-kind-per-entry validation, opt-in `--include-tf2-media`, text fallback rules,
  caps, and the full admission/text inventory.

## 1. Problem and scope

The delivered media slice reads only the legacy `Summary.Image` (field 4) and `Summary.Audio`
(field 6) messages. Modern TensorFlow 2 writes media differently:

- `tf.summary.image` encodes every image as PNG and writes a `DT_STRING` tensor (field 8,
  rank 1, `k` elements) into `Summary.Value.tensor`;
- `tf.summary.audio` encodes every clip as WAV and does the same.

Today those entries are invisible to `--include-images`/`--include-audio`, and a rank-1 `[1]`
string tensor that happens to be a PNG is recorded as **text** by the 0.6 reader — the wrong
kind and with no digest verification of the payload.

In scope: reading those string-tensor media entries as digest references, element indexing,
exact verification, schema 0.10, migration and display. Non-goals in §11.

## 2. Schema 0.10 and the locator

Schema 0.10 adds **no value model and no locator kind**. It reuses `ImageValue`,
`AudioValue`, `tensorboard-image` and `tensorboard-audio`, and adds one optional locator field:

```json
{"kind": "tensorboard-image", "tag": "samples", "step": 3, "occurrence": 0, "element": 2}
```

Rules:

- `element` is an integer `0..63`, default `0`, and is absent-or-zero for every legacy entry;
  an emitted manifest omits it when it is 0 so legacy output stays byte-identical;
- `occurrence` keeps its delivered meaning: zero-based among conforming **entries** of that
  kind with the exact `(tag, step)`, not among elements;
- the value↔locator matrix is unchanged: `ImageValue` requires `tensorboard-image` or `null`,
  `AudioValue` requires `tensorboard-audio` or `null`;
- `Manifest10` and `Result10` differ from 0.9 only by the optional `element` field. Existing
  0.9 locators gain the default and remain valid, so migration is a version-only rewrite.

## 3. Reader profile

### 3.1 Which entries

A `Summary.Value` whose winning member is `tensor` (8) is a TF2 media candidate when:

- the tensor dtype is `DT_STRING` (7);
- the shape is rank 1 with a single dimension `k`, `1 <= k <= 64`, `unknown_rank` false;
- every `string_val` is present, non-empty, at most 16 MiB, and validates as exactly one media
  kind — all PNG, or all RIFF/WAVE; a mixed entry fails;
- the delivered scalar and string classifiers do not record a **different** kind for the entry
  under §3.4.

A candidate that fails any check is an entry-local failure: it yields no media result, consumes
no occurrence, and does not affect siblings.

### 3.2 Payload validation

Each element reuses the delivered validators unchanged:

- PNG: signature, IHDR length/type/CRC-32 (zlib), dimensions `1..65_535`, at least one non-empty
  `IDAT`, a final `IEND` ending the element exactly; width/height come from IHDR, and no wire
  dimensions exist to compare against;
- RIFF/WAVE: `RIFF`/`WAVE`, the RIFF size equal to the element length minus 8, a first `fmt `
  chunk of at least 16 bytes, exactly one non-empty `data` chunk, and odd-size padding.

The digest covers each element's bytes exactly, as in 0.8.

### 3.3 Merge and bounds

Repeated field-8 occurrences merge as delivered: shape dimensions append, `string_val`
appends, `tensor_content` is irrelevant for `DT_STRING` and is ignored after a wire check. The
rank-1 cap and the 64-element cap are enforced while reading; a 65th element fails the entry.
The frozen 16 MiB merged-summary cap still refuses the whole file.

### 3.4 Precedence against the 0.6 string reader

For a `DT_STRING` tensor, the 0.6 string profile records rank-0 or rank-1 `[1]` entries as text.
Under 0.10 with TF2 media enabled:

- if the tensor has rank 1, `k >= 1`, and **every** element validates as one media kind, the
  entry is media: it yields `k` media results and **no** text result;
- otherwise the entry falls back to the delivered 0.6 behavior (text when scalar-shaped,
  unsupported otherwise).

The rule is evaluated per entry, is deterministic, and never changes a 0.5–0.9 outcome because
those schemas do not enable TF2 media.

### 3.5 Failure policy

Entry-local: a malformed string tensor, a mixed or oversized element, an unsupported kind, or an
element that fails validation produces no result for that entry. Structural failures the frozen
walk already refuses — bad keys, wrong wires, truncation, the summary cap — still refuse the
whole file.

## 4. Identity, ordering, names

- Occurrence counts conforming entries per `(tag, step)` and kind, as delivered;
- `element` is the zero-based position of the payload inside that entry;
- result ids append `NUL + kind` and, for TF2 entries, `NUL + "e" + element`, so ids stay unique;
- names keep `tag @ step (image)`/`(audio)` and append ` [element N]` only when `k > 1`.
  Legacy entries never carry the suffix;
- multifile groups keep the basename/digest suffix rules.

## 5. Checker and consumers

Admission follows the 0.8 slice: `_LOCATOR_SCHEMAS`, the six-kind caps, the csv cap, the 128 MiB
snapshot, extension/kind maps, `capped_kinds`, the directory hint, the cap-message gate,
`check_results.has_locators`, the request tuples, and an explicit 0.10 scanner branch.

The checker selects the entry by `(tag, step, occurrence)` and then the `element` payload,
compares the digest and metadata exactly, and uses the delivered ladder. Findings render the
locator as `tensorboard-image:step=<n>,occurrence=<k>,element=<e>` when the element is non-zero,
and omit `element` when it is zero so legacy output is unchanged. Human surfaces keep the
compact `image(64x64 png, N bytes)` form; `--json` emits the value.

## 6. Adapter and CLI

- One new flag, `init --include-tf2-media`, selects schema 0.10 and reads **both** TF2 image and
  audio string tensors. The 0.8 flags keep their delivered behavior and schema.
- Precedence: `--include-tf2-media` → 0.10; tensors → 0.9; media → 0.8; histograms → 0.7;
  strings → 0.6; else 0.5.
- The adapter needs a tag-agnostic `iter_supported_summaries_10`; `_read_event_file` filters TF2
  media by the flag; `_decoded_tag` gains `TAG_MESSAGE_10`.
- Budgets: TF2 media adds no manifest budget; the 10,000-result and 16 MiB tag limits apply, and
  a digest value is small. The budget text for a TF2-media-only run is the delivered
  `BUDGET_MESSAGE_08` line; mixed runs reuse the delivered texts by family.
- Unusable tags follow the delivered rules: scalars refuse without `--tags`, non-scalars skip.

## 7. Migration, refusals, fixed texts

- `migrate --to 0.10` admits 0.1/0.2 through the locator precheck and 0.3–0.9 as version-only
  rewrites; the added `element` field defaults to 0.
- `--to 0.9` or lower from 0.10 is `DOWNGRADE_UNSUPPORTED`; comparability refuses 0.10 by
  version; `init --force` refuses a declared 0.10 manifest before discovery;
  `_event_artifact_is_volatile` admits `Manifest10`.
- Fixed texts:

```
ERROR: the existing manifest declares schema 0.10. init --force would regenerate the manifest and cannot carry over first-class values or locators. Nothing was written. Move the manifest aside, run init, then run runevidence migrate PATH --to 0.10 (unless the adapter writes 0.10 itself) and reapply the first-class values and locators.
ERROR (UNSUPPORTED_TARGET): only --to 0.2, --to 0.3, --to 0.4, --to 0.5, --to 0.6, --to 0.7, --to 0.8, --to 0.9 or --to 0.10 is supported; downgrade and other versions are not available. Nothing was written.
a scalar tag cannot be represented in schema 0.10; nothing was written
```

The conditions text follows the same version list; the 0.10 cap text is
`TENSORBOARD_CAP_MESSAGE_06`.

## 8. Admission checklist (per module)

| Module | Change |
| --- | --- |
| `models.py` | optional `element` on both media locators, `Result10`, `Manifest10` version shape (nine locator kinds), dispatch/adapters, `model_dump` 0.10, `allOf` branches unchanged in count, schema file and pin |
| `manifest.py` | dispatch, `_root_model_for`, `artifact_to_dict`, `_LOCATOR_TAGS`, value-union tuple admit 0.10 |
| `tensorboard_events.py` | TF2 string-tensor extraction from the frozen tensor state, element validation, `scan_tensorboard_10`, `iter_supported_summaries_10`; no change to 0.5–0.9 readers |
| `checks/__init__.py`, `checks/results.py` | 0.10 admission, element selection, ladder, locator rendering |
| `locator_profiles.py`, `histogram_display.py`, `run_claim.py` | element-aware display |
| `migrate.py`, `run_comparability.py`, `cli.py`, `adapter.py` | targets, downgrade tuple, flag, texts, generator |
| `docs/`, `schema/`, `README.md` | 0.10 page, schema file, flag documentation |

## 9. Verification plan

- Reader: single and batched images/audio; mixed entry fails; 64 and 65 elements; per-element
  truncation, bad CRC, unpadded WAV; zero-length element; text fallback for scalar-shaped
  non-media strings; no double recording; occurrence unaffected by elements; whole-file damage
  still refuses.
- Identity: element in ids and names only when `k > 1`; multifile suffixes; tag filter.
- Checker: element selection, digest mismatch, missing element, wrong kind, ladder texts, locator
  rendering with and without `element`.
- Adapter/CLI: flag precedence, text fallback, budget text, tag texts, capability error.
- Migration: 0.9 → 0.10 identity, 0.10 → 0.9 downgrade, comparability, force guard.
- Consumers: compact display for every element; `--json` exactness.
- Mutation probes: drop the element check, drop the mixed-kind rule, remove the 0.10 scan
  branch, admit 0.10 in comparability, drop the force guard.

## 10. Decisions requested

1. One `--include-tf2-media` flag (recommended) versus separate image/audio flags.
2. An optional `element` locator field (recommended) versus one result per entry that records
   only the first element.
3. A 64-element cap (recommended) versus a larger bound.
4. Media-precedes-text for validated entries (recommended) versus recording both.

## 11. Non-goals

- TF2 `[k,3]` histogram tensors.
- Plugin-specific summaries (PR curves, embeddings).
- Graph/run metadata; audio/video transcoding; extraction.
