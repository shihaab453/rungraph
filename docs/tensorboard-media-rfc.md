# TensorBoard media references — design RFC (v0.4)

Status: **DESIGN PROPOSAL. NOTHING IMPLEMENTED. NOTHING AUTHORIZED BY THIS DOCUMENT.**
A new design gate and an owner schema-0.8 admission decision are required before any code.
Schema 0.1–0.7, their readers/checkers and every delivered behavior stay frozen, except the
additive dispatch, model, migration and text edits this document names explicitly.

## 0. Changes in v0.4

v0.4 folds the round-3 review. Each seat confirmed the round-2 fixes; together they rejected
v0.3 on one shared ladder defect and a `models.py` enumeration gap:

- **Ladder.** §5.2 is rebuilt as the delivered two-stage ladder: stage 1 decides what the file
  holds (value / missing / unsupported encoding / unsupported value, including a tag-only
  `Summary.Value` with no winning oneof member), and only a `value` outcome proceeds to stage 2,
  which checks the recorded type before comparing. This restores the reachability of
  `selects an unsupported value` for a scalar recorded on a media locator.
- **`Manifest08`.** The version-shape branch, per-version locator dispatch/adapters and
  scalar-shape override are named; without them 0.8 rejects every jsonl locator and accepts a
  null `artifact_id` with a non-null locator. The JSON Schema rule becomes three per-kind
  `if/then` branches plus the retained `artifact_id` rule.
- **Digest.** A new lowercase-only pattern (the shared `SHA256_PATTERN` accepts uppercase) and
  one definition of the digest scope shared by reader, checker and adapter.
- **Media validation.** The int32 wire-dimension truncation is pinned with a vector
  (`2**32 + 64` → `64`), at least one `IDAT` must be non-empty, the RIFF pad byte is any value,
  and the document states that chunk placement and truncation are validated, not image validity.
- **Consumers.** Display helpers handle the model and the JSON-dict form; locator display
  literals are pinned; compare's JSON gains media flags, so "JSON unchanged" is qualified.
- **Texts.** Capability errors become templates that interpolate the adapter name, with pinned
  output for `--adapter generic`; the init flag help and migrate help are verbatim; budget text
  selection is tabulated for all eight flag sets.
- **Corrections.** `is_manifest` already accepts a `Manifest08` subclass; the real gates are the
  `manifest.py` dispatch functions. The "graph" text is the conflicting-evidence message in
  `validate`/`inspect`; the Summary cap is checked per event; `--to 0.2` needs no edit; the
  leaderboard never serializes values; `manifest`'s value union is a membership tuple.

Earlier revisions: v0.2 folded round 1 (occurrence bound, value↔locator matrix, Summary-cap
precedence, complete PNG walk, pinned RIFF rule, audio metadata dropped, separate
`_parse_value_08`, delivered ladder and texts, admission obligations, migration branches,
force/volatile gates, schema precedence, fixed texts) and v0.3 folded round 2 (one-way matrix,
media budget dropped, bounded RIFF chunk walk, PNG walk end/order, adapter generator, results
and checks-init gates, `value_to_json`, `is_manifest`, `artifact_id` rule, display literals).

## 1. Problem and scope

TensorBoard writers record images in `Summary.Value.image` (field 4, `Summary.Image`) and
audio in `Summary.Value.audio` (field 6, `Summary.Audio`). The delivered reader wire-checks
both fields and records nothing for them. Users track rendered samples, attention maps and
audio clips this way, and the manifest cannot currently state that they existed or changed.

Recording bytes is not an option: a 10,000-result manifest with inline 100 KiB images is a
gigabyte of JSON and the read-only adapter contract forbids writing extracted files. The gate
therefore proposes **digest-only references**: the manifest records a SHA-256 and byte length
for the payload already inside the event file, plus the PNG geometry. Nothing is extracted or
copied; verification re-reads the file.

Coverage is TF1 and PyTorch/tensorboardX writers. `tf.summary.image` and `tf.summary.audio`
in TF2 write string tensors (field 8) of rank 3/2, which neither the 0.6 string reader nor this
slice records; that is a stated non-goal.

In scope: one image or audio payload per `Summary.Value`, digest-only recording, exact
verification, opt-in recording, schema 0.8, migration and compact display. The opt-in is about
recording, never about extracting bytes; non-goals in §11.

## 2. Schema 0.8 and the value model

Schema 0.8 = 0.7 plus two reference values. `Result08.value` is
`Scalar | HistogramValue | ImageValue | AudioValue`.

```json
{
  "kind": "image",
  "media_type": "image/png",
  "sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
  "size_bytes": 4321,
  "width": 64,
  "height": 64
}
```

```json
{
  "kind": "audio",
  "media_type": "audio/wav",
  "sha256": "3a7bd3e2360a3d29eea436fcfb7e44c735d117c42d1c1835420b6b9942dd4f1b",
  "size_bytes": 88244
}
```

Rules:

- both models are strict (`extra="forbid"`), every key is required, `kind` is const;
- `sha256` is exactly 64 lowercase hexadecimal characters, enforced by a **new**
  lowercase-only pattern constant. The shared `SHA256_PATTERN` accepts uppercase and the
  artifact path folds case; reusing it would silently accept an uppercase digest;
- `size_bytes` is an integer in `1..16_777_216`; a zero-length payload is not a media value;
- `media_type` is const (`image/png`, `audio/wav`); any other payload type is unsupported and
  produces no result (§3.4–§3.5);
- `width`/`height` are integers in `1..65_535` and must equal the PNG IHDR values (§3.4).

The digest covers the merged `encoded_image_string` / `encoded_audio_string` bytes exactly as
they appear in the event file, and `size_bytes` is the length of that same range. The reader,
the checker and the adapter use this one definition; nothing is decoded, re-encoded or
normalized before hashing.

Audio carries no sample rate, frame count or channel count. The digest pins the payload, no
recorded field is derived from the WAV body, and a metadata field the checker cannot recompute
would be an unverified claim. The compact display reports only type and size.

### Value ↔ locator compatibility (`allOf`)

`Result08` keeps the delivered 0.7 rule, which is one-way: an **object** value is restricted to
its own locator kind or `null`, while a **scalar** value (including `null`) stays legal on every
locator kind, exactly as in 0.3–0.7.

| Value | Permitted locator (`null` always allowed) |
| --- | --- |
| `ImageValue` | `tensorboard-image` |
| `AudioValue` | `tensorboard-audio` |
| `HistogramValue` | `tensorboard-histogram` |
| `Scalar` (including `null`) | plain string JSON Pointer, `jsonl`, `csv-cell`, `tensorboard`, `tensorboard-string`, `tensorboard-histogram`, `tensorboard-image`, `tensorboard-audio` |

Consequences that are part of the contract:

- identity migration from 0.3–0.7 stays a version-only rewrite: every delivered scalar/pointer
  combination, including a scalar on a `tensorboard-histogram` locator or a `/score` string
  pointer, remains valid in 0.8;
- the checker's "recorded value is not an `ImageValue`/`AudioValue`" row stays reachable: a
  scalar recorded on a media locator is schema-valid and fails at verification time, exactly
  like a scalar on a histogram locator does today;
- an object value on a mismatched locator is rejected by both the model and the published JSON
  Schema;
- the delivered non-null-locator rule is retained: a result with a non-null locator also
  requires `artifact_id`, and a null locator still permits a null `artifact_id`;
- the JSON Schema expresses the object rule as **three per-kind `if/then` branches** keyed on
  `value.kind` (`image` → `tensorboard-image`, `audio` → `tensorboard-audio`, `histogram` →
  `tensorboard-histogram`, each allowing `null`), alongside the retained `artifact_id` rule. A
  single `value is object → locator anyOf` branch cannot express kind-to-kind and would admit a
  histogram value on an image locator;
- the model enforces the same matrix in `Manifest08._version_specific_shape`, with its own
  locator dispatch/adapters and scalar-shape override. Without that branch, a `Manifest08`
  subclass rejects every `jsonl` locator (the inherited `else` rejects them), accepts a null
  `artifact_id` with a non-null locator, and rejects a media locator model built in Python mode
  even though the dict path validates.

## 3. Reader profile

### 3.1 A separate 0.8 parser

The 0.5/0.6 `_parse_value` stays byte-for-byte frozen. Schema 0.8 gets `_parse_value_08`,
modelled on `_parse_value_07`: same oneof tracking, same histogram merge/reset state, plus the
`image` (4) and `audio` (6) payload ranges. `_parse_value_07` also stays untouched.
`_parse_value_08` has exactly two callers: `scan_tensorboard_08` (checker requests) and
`iter_supported_summaries_08` (tag-agnostic adapter generator).

### 3.2 Where media lives, and the merge rule

`Summary.Value` is a oneof over `simple_value` (2), legacy histogram (3), `image` (4), `histo`
(5), `audio` (6) and `tensor` (8), plus independent `tag` (1), `node_name` (7), `metadata` (9)
and unknown fields. Tag, `node_name`, `metadata` and unknown fields do not reset media state; a
different oneof member does.

Repeated occurrences of the same media member after the last different oneof member merge field
by field, last-wins:

- `Summary.Image`: `height` (1, varint), `width` (2, varint), `colorspace` (3, varint),
  `encoded_image_string` (4, bytes);
- `Summary.Audio`: `sample_rate` (1, fixed32), `num_channels` (2, varint), `length_frames`
  (3, varint), `encoded_audio_string` (4, bytes), `content_type` (5, bytes).

Only the encoded bytes, the image `width`/`height`, and the audio `content_type` survive the
merge; the other fields are wire-checked and ignored. A merge that ends with no encoded bytes
is a failing entry for that kind, not a value.

### 3.3 Inner failure set (entry-local)

Inside a winning media member, key decoding is lenient exactly like the histogram decoder:

- wrong wire type for a known field, field number 0, field number greater than `2**29-1`, wire
  types 3/4/6/7, a truncated length or a malformed varint make **that entry** fail for that
  kind;
- the outer Event/Summary walk keeps its frozen behaviour and still refuses the file on a
  structural error outside the media payload;
- a failed entry is not a value, consumes no occurrence number, and does not affect sibling
  entries, other values, or other files;
- if a merge had already accumulated fields when the lenient failure occurred, the accumulated
  state is discarded; the entry is unsupported, never partially recorded;
- a failure is sticky **until a different oneof member appears**: a later well-formed occurrence
  of the same member after the failure does not rescue the entry, and a different member resets
  the state and discards the failure, as the delivered histogram decoder does.

Unknown fields inside `Summary.Image`/`Summary.Audio` are skipped, as the histogram decoder
does.

### 3.4 PNG validation (images)

A payload is a valid image only if all of this holds; otherwise the entry fails locally:

1. length is at least 33 bytes and starts with `89 50 4E 47 0D 0A 1A 0A`;
2. the first chunk starts at offset 8 with a 4-byte big-endian length equal to 13 and type
   `IHDR`; its CRC-32 (the zlib polynomial used by PNG, not the CRC32C this module uses for
   event records) matches its type+data bytes; a second `IHDR` later in the stream is a
   failure;
3. `width` and `height` read big-endian from IHDR are each in `1..65_535`;
4. the chunk walk starts at offset 8, stays inside the payload, and stops at the **first**
   `IEND`, which must have length 0 and end exactly at the last byte; every chunk length is
   `0..2**31-1`; at least one `IDAT` chunk must appear after `IHDR` and before `IEND`; a chunk
   after `IEND` is a failure, not ignored data;
5. the wire `width`/`height` fields are protobuf `int32`: take the low 32 bits of the varint
   as a two's-complement integer, exactly as protobuf does. `2**32 + 64` therefore decodes to
   `64` and matches an IHDR width of 64; `0` means "not set"; a negative value is a failure; a
   non-zero value must equal the IHDR value;
6. at least one `IDAT` chunk has a non-zero length.

Only the IHDR CRC is verified; other chunk CRCs are not, because the SHA-256 already pins every
byte. Chunk placement and truncation are validated, not image validity: consecutive `IDAT`
chunks and `PLTE` ordering are not checked, and no pixel or IDAT decompression happens.

### 3.5 Audio validation

A payload is a valid audio reference only if all of this holds:

1. `content_type` is absent, empty, or exactly `audio/wav`; any other value is unsupported;
2. length is at least 12 and in `1..16_777_216`;
3. bytes 0..4 are `RIFF`, bytes 8..12 are `WAVE`;
4. the little-endian `uint32` at offset 4 equals `size_bytes - 8` exactly;
5. a bounded chunk walk from offset 12 stays inside the payload and ends exactly at the last
   byte, where each chunk is a 4-byte id, a 4-byte little-endian size, `size` data bytes and a
   pad byte of any value when `size` is odd;
6. the first chunk is `fmt ` with size at least 16, and exactly one `data` chunk with size at
   least 1 exists.

The `fmt ` body and the audio samples are never decoded; no recorded field depends on them.
The walk exists so padding and truncation are decidable, which fixes occurrence identity: a
45-byte WAVE whose odd `data` chunk omits the pad byte fails rule 5, and the 46-byte padded
form passes.

### 3.6 Failure precedence and caps

The frozen 16 MiB cap is on the whole merged `Summary` message and raises the whole-file
`EventFileError` when that Summary exceeds it. The check is per event, so earlier events in the
same file are classified before the refusal; the outcome is still a whole-file refusal. It
therefore outranks every entry-local rule here:

- a media payload large enough to push its Summary past 16 MiB is a **file-level refusal**; the
  cap is `>`, so a Summary of exactly 16 MiB is accepted;
- below that line, §3.3–§3.5 failures are entry-local;
- `size_bytes` `1..16_777_216` is the model bound; in practice a payload near 16 MiB always
  trips the Summary cap first, so boundary tests at 16 MiB and 16 MiB + 1 are file-level.

Nothing is retained beyond offsets and a streaming digest.

## 4. Locator, identity, ordering

Two locator kinds join `tensorboard`, `tensorboard-string` and `tensorboard-histogram`:

```json
{"kind": "tensorboard-image", "tag": "samples", "step": 3, "occurrence": 0}
{"kind": "tensorboard-audio", "tag": "clip", "step": 3, "occurrence": 0}
```

Rules, inherited unchanged from the delivered locators:

- `tag` is the exact event tag, 1..4,096 code points and 1..4,096 UTF-8 bytes, no surrogate;
- `step` is a signed 64-bit integer, absent `Event.step` means 0;
- `occurrence` is a zero-based position among **conforming entries of the same kind** with that
  exact `(tag, step)` in file order, bounded by `MAX_TENSORBOARD_OCCURRENCE = 2**31 - 1`. The
  RFC does not tighten this bound; a 0.7 locator with occurrence 20,000 stays valid in 0.8;
- the five kinds count occurrences independently.

Result names keep the delivered shape with a kind suffix: `samples @ 3 (image)`,
`clip @ 3 (audio)`. Multi-run path suffixes, cross-run alignment and `--tags` apply unchanged.

## 5. Checker and consumers

### 5.1 Admission in `checks/__init__.py` and `checks/results.py`

This is mandatory, not optional: without it 0.8 verifies nothing or crashes.

`checks/__init__.py`:

- `_LOCATOR_SCHEMAS` gains 0.8, so `run_checks` computes `has_locators` and the targeted path
  is taken;
- the locator-class tuples used by the TensorBoard cap and the snapshot selection (the two
  `isinstance` groups around the cap and eligibility helpers) gain the two new classes; adding
  only the schema version leaves the classes unrecognized;
- `_tensorboard_cap` admits 0.8 and counts all five kinds against the shared 10,000-result and
  16 MiB tag limits;
- `_csv_cell_cap` admits 0.8 (0.8 is a union schema and inherits csv-cell);
- `_tensorboard_snapshot_ids` admits 0.8 so a 128 MiB event file keeps its snapshot instead of
  the 50 MiB generic limit.

`checks/results.py`:

- `_extension_matches` and `_expected_extension` need both locator kinds, or every media result
  on a `.tfevents` file gets `locator-kind-mismatch` "needs a .jsonl artifact";
- `_locator_kind` must return the two new kinds;
- `capped_kinds`, the per-kind 128 MiB branch, the directory hint and the cap-message gate must
  include them, or media results get the 50 MiB cap and the message says "no TensorBoard scalar
  was read" — the delivered `TENSORBOARD_CAP_MESSAGE_06` text ("no TensorBoard value was read")
  is used for 0.8;
- `check_results` has its own `has_locators` gate and must admit 0.8, or a 0.8 result with a
  null locator never receives `result-unverified`;
- the `tensorboard_results` tuple that builds the targeted scan request must include both new
  locator classes, or media results fall through to `_check_one_targeted` and raise
  `AttributeError` on `.line`.

`histogram_display.value_to_json` is the shared JSON converter for compare, details, lineage,
matrix, leaderboard and claim; it must convert `ImageValue`/`AudioValue` with
`model_dump(mode="json")` or `--json` raises `TypeError`.

`manifest._LOCATOR_TAGS` and the value-union membership tuple (`bool/int/float/str` plus the
object values) must learn both kinds, or validation errors lose their location.

### 5.2 Comparison ladder

Per kind, the ladder is the delivered histogram ladder in **two stages**. Stage 1 decides what
the file holds, in this order; the first matching row wins:

| Stage 1 condition | Outcome |
| --- | --- |
| conforming entry at that occurrence | `value` (go to stage 2) |
| conforming entries exist but the occurrence is exhausted | `missing` |
| entries exist at the tag/step and the same kind failed validation | `unsupported_encoding` |
| entries exist at the tag/step with a different oneof member, **or with no winning oneof member at all** (a tag-only `Summary.Value`) | `unsupported_value` |
| no entry at the tag/step | `missing` |

Stage 2 runs only when stage 1 returned `value`:

| Stage 2 condition | Finding |
| --- | --- |
| recorded value is not an `ImageValue`/`AudioValue` (a scalar, a histogram, or `null`) | `locator-unsupported-value`, text `selects an unsupported value` |
| recorded value differs | `result-value-mismatch`, text `does not match the recorded value` |
| recorded value matches | none |

Stage 1 outcomes map to findings: `missing` → `locator-mismatch`, text `was not found`;
`unsupported_encoding` → `locator-unsupported-value`, text `selects an unsupported image
encoding` / `selects an unsupported audio encoding`; `unsupported_value` →
`locator-unsupported-value`, text `selects an unsupported value`.

There is no cross-kind "position": each kind counts only its own conforming entries, so a valid
image does not shift an audio occurrence. The finding text is the delivered
`result '<id>' locator <locator> does not match the recorded value in '<path>'`; recorded
values are never injected into locator-level findings.

### 5.3 Display

Human surfaces (`inspect`, `claim`, `lineage`, `run`, `compare`, `matrix`, `leaderboard`)
render, for results only:

- `image(64x64 png, 4321 bytes)`;
- `audio(wav, 88244 bytes)`.

Every display helper needs **both** the model form and the JSON-dict form, because claim,
lineage, matrix and details pass `value_to_json` output while `report` and the graph check pass
the model. `display_locator` gains the literal forms
`tensorboard-image:step=<n>,occurrence=<k>` and `tensorboard-audio:step=<n>,occurrence=<k>`,
never the tag. `run_claim`'s value display gains a media branch so claim output uses the compact
string rather than a model repr. In `compare`,
media rows set `baseline_image`/`target_image` or `baseline_audio`/`target_audio` flags, the
analogues of the delivered `baseline_histogram`/`target_histogram` flags, and never
`baseline_histogram`.

Parameters keep their delivered rendering. `--json` emits the value object exactly.
`leaderboard` treats both kinds as `non_numeric_or_nonfinite`.

`locator_profiles.display_locator` needs branches for both kinds, and `run_claim`'s display
whitelist needs both, or the first finding crashes with `AttributeError` and the claim surface
prints the raw tag, breaking the delivered "never the recorded tag" rule.

`checks/graph.py` has two version gates (`locator_schema`, capped-artifact hint) that must
admit 0.8, and its conflicting-evidence message must use the compact display rather than
`repr(ImageValue(...))`. There is no `graph` subcommand: this text appears in `validate` and
`inspect` output.

`models.is_manifest` already returns `True` for any `Manifest08` because it is a `Manifest`
subclass; listing it is a harmless no-op, not a gate. The real gates are `validate_manifest`,
`_root_model_for` and `artifact_to_dict` in `manifest.py`, plus the directory/experimental
helpers that dispatch on the manifest type; those are admitted to 0.8 as decided in §7.

## 6. Adapter and CLI

- `init --include-images` and `init --include-audio` are opt-in and independent.
- Schema selection precedence, in this order: any media flag → 0.8; else `--include-histograms`
  → 0.7; else `--include-strings` → 0.6; else 0.5. Putting the media test after the histogram
  test would emit a 0.7 manifest and silently discard every requested image, so the order is
  part of the contract and is tested directly.
- The adapter needs a tag-agnostic generator, `iter_supported_summaries_08`, mirroring
  `iter_supported_summaries_07`: it yields `ImageRecord`/`AudioRecord` (tag, step, value) in
  file order for the enabled kinds, because `_read_event_file` does not know tags in advance
  and the request-based `scan_tensorboard_08` cannot replace it. `scan_tensorboard_08` stays the
  checker-side reader.
- Adapter gates that must learn the new kinds, or 0.8 records the wrong thing:
  - the locator-kind `if/elif` that builds each result locator;
  - the `result_type`/`manifest_type` selection;
  - `_read_event_file`'s per-kind filters, which today skip strings only: a 0.8 generator that
    yields histograms or media must be filtered by `include_histograms` / `include_images` /
    `include_audio`, or a flag would record a kind that was not asked for;
  - `_decoded_tag`, whose text is selected by booleans: a media-only 0.8 run must print the 0.8
    tag text, not the 0.5 one.
- A flag never records a kind that was not asked for.
- `--tags` filters media tags like the other kinds.
- Unusable tags: without `--tags`, an unrepresentable scalar tag refuses `init`; with `--tags`,
  it is skipped, exactly as delivered. An unrepresentable string, histogram, image or audio tag
  is skipped in both cases.
- Media adds no budget of its own: a media value is at most 168 bytes, so the delivered
  10,000-result and 16 MiB tag limits bound it. Budget text selection is fixed per flag set:

| Flags | Text |
| --- | --- |
| none (scalars only) | delivered `BUDGET_MESSAGE` |
| strings | delivered `BUDGET_MESSAGE_06` |
| histograms | delivered `BUDGET_MESSAGE_07_HIST` |
| strings + histograms | delivered `BUDGET_MESSAGE_07` |
| media only | new media-only line below |
| media + strings | `BUDGET_MESSAGE_06` |
| media + histograms | `BUDGET_MESSAGE_07_HIST` |
| media + strings + histograms | `BUDGET_MESSAGE_07` |

  The single new line:

```
the selected TensorBoard summaries exceed the 10,000-result or 16 MiB tag limit; nothing was written
```

  No text names a limit it did not enable, and no media combination falls back to the scalar
  text.

## 7. Migration, refusals, fixed texts

- `migrate --to 0.8` admits every source. 0.1/0.2 run the existing locator precheck; 0.3–0.7
  are version-only identity rewrites, because 0.8 admits every delivered locator and value.
- `TARGET_VERSIONS`, the per-target branches, `--to` help text and `UNSUPPORTED_TARGET` gain
  0.8.
- `--to 0.7`, `0.6`, `0.5`, `0.4` and `0.3` from 0.8 raise `DOWNGRADE_UNSUPPORTED`. The `--to 0.2`
  branch already raises it for any source outside 0.1/0.2 and needs no edit. This is an
  additive edit to frozen migration branches and is explicitly authorised here.
- `Manifest.model_dump` treats 0.8 like 0.7 (keep `conditions`, `dataset_id`,
  `metric_definition`).
- `models.is_manifest` includes `Manifest08`, and the directory/experimental helpers that
  dispatch on manifest type accept it.
- `manifest.py` dispatch (`validate_manifest`, `_root_model_for`, `artifact_to_dict`) admits
  0.8.
- `cli.py` gains `INIT_0_8_REFUSAL`, includes 0.8 in the `init --force` declared-version guard
  **before discovery, backup or any write**, adds `Manifest08` to
  `_event_artifact_is_volatile`, adds `supports_include_images`/`supports_include_audio`
  capability checks, and extends the conditions version family.
- Comparability refuses 0.8 by version in both `compare_paths` and `preflight_paths`.
- Fixed texts, all literal:

```
ERROR: the existing manifest declares schema 0.8. init --force would regenerate the manifest and cannot carry over first-class values or locators. Nothing was written. Move the manifest aside, run init, then run runevidence migrate PATH --to 0.8 (unless the adapter writes 0.8 itself) and reapply the first-class values and locators.
ERROR (UNSUPPORTED_TARGET): only --to 0.2, --to 0.3, --to 0.4, --to 0.5, --to 0.6, --to 0.7 or --to 0.8 is supported; downgrade and other versions are not available. Nothing was written.
ERROR (UNSUPPORTED_VERSION): the conditions command requires an explicit schema 0.2 manifest. A schema 0.1 manifest can be migrated with runevidence migrate PATH --to 0.2; schema 0.3, 0.4, 0.5, 0.6, 0.7 and 0.8 have no downgrade path. Nothing was written.
ERROR: adapter {name!r} does not support --include-images. Nothing was written.
ERROR: adapter {name!r} cannot apply --include-images. Nothing was written.
ERROR: adapter {name!r} does not support --include-audio. Nothing was written.
ERROR: adapter {name!r} cannot apply --include-audio. Nothing was written.
a scalar tag cannot be represented in schema 0.8; nothing was written
```

The last four are templates: `{name!r}` interpolates the selected adapter, as the delivered
capability errors do. Expected output for `--adapter generic --include-images` is
`ERROR: adapter 'generic' does not support --include-images. Nothing was written.`; the
"cannot apply" form is reached only by an adapter that declares `supports_include_images` but
does not accept the `include_images` keyword. The init flag help texts are:

```
--include-images   also record legacy TensorBoard image summaries as schema 0.8 results (opt-in; implies schema 0.8)
--include-audio    also record legacy TensorBoard audio summaries as schema 0.8 results (opt-in; implies schema 0.8)
```

`--to` help becomes `target schema version (0.2, 0.3, 0.4, 0.5, 0.6, 0.7 or 0.8)`, and the
migrate parser help becomes `declare an existing manifest as schema 0.2, 0.3, 0.4, 0.5, 0.6,
0.7 or 0.8 without changing any recorded value (explicit, no inference)`. The cap text for a 0.8 manifest is the delivered
`TENSORBOARD_CAP_MESSAGE_06` ("no TensorBoard value was read").

## 8. Admission checklist (per module)

| Module | Change |
| --- | --- |
| `models.py` | `ImageValue`, `AudioValue`, lowercase-only digest pattern, two locators, `Result08` one-way compatibility, `Manifest08`, the `_version_specific_shape` 0.8 branch (six locator classes, pointer grammar, `artifact_id` rule), per-version locator dispatch/adapters and scalar-shape override, dispatch/save/load, `model_dump` 0.2 fields, three per-kind `allOf` branches plus the `artifact_id` rule, schema 0.8 file and pin |
| `manifest.py` | `validate_manifest`, `_root_model_for`, `artifact_to_dict`, `_LOCATOR_TAGS`, value-union membership tuple admit 0.8 |
| `tensorboard_events.py` | `_parse_value_08` (histogram + media state), lenient inner keys, PNG/RIFF validation, `scan_tensorboard_08`, per-kind occurrence namespaces; 0.5–0.7 scanners and `_parse_value_07` untouched |
| `checks/__init__.py` | locator admission, five-kind shared caps, csv cap, 128 MiB snapshot eligibility |
| `checks/results.py` | 0.8 scanner routing, extension/kind mapping, cap counting and per-kind 128 MiB, directory hint, cap-message gate, `check_results` `has_locators`, comparison ladder |
| `checks/graph.py` | version gates, capped hint, compact conflicting-evidence display |
| `histogram_display.py` | `value_to_json` and the display helper handle both media values |
| `locator_profiles.py` | `display_locator` branches for both kinds, in the model and dict forms |
| `run_claim.py` | display whitelist for both kinds |
| `run_compare.py`, `run_matrix.py`, `run_details.py`, `run_lineage.py`, `run_leaderboard.py`, `report.py` | compact display for results only; JSON gains the media flags in compare and is otherwise unchanged |
| `migrate.py` | `TARGET_VERSIONS`, 0.8 identity from every source, downgrade tuples |
| `run_comparability.py` | 0.8 in the unsupported-by-version set |
| `cli.py` | flags, capability checks, `INIT_0_8_REFUSAL`, force guard, volatile admission, `UNSUPPORTED_TARGET`, conditions 0.8, help text |
| `packages/runevidence-tensorboard/adapter.py` | flags, precedence, locator-kind and type selection, per-kind filters, `_decoded_tag`, ids/names, budget table, tag rules |
| `docs/`, `schema/`, `README.md` | 0.8 page, schema file, flag documentation |

## 9. Verification plan

- Reader and generator: `iter_supported_summaries_08` emits media in file order and per-kind
  occurrences; `scan_tensorboard_08` returns the same selections for checker requests.
- Reader: PNG signature, truncated IHDR, bad IHDR CRC, duplicate IHDR, zero/oversized dims,
  wire dims 0/negative/mismatched, missing IDAT, missing IEND, data after IEND, chunk overrun,
  JPEG/GIF payloads skipped; RIFF magic, wrong RIFF size, unpadded odd chunk rejected and the
  padded form accepted, missing `fmt `, duplicate/absent `data`, `content_type` variants,
  zero-length payload; merge across repeated occurrences; reset by a different oneof member;
  sticky failure that a later good occurrence cannot rescue; lenient inner key failures;
  Summary cap at exactly 16 MiB (accepted) and 16 MiB + 1 (file-level refusal).
- Identity: per-kind occurrences, a valid image not shifting an audio occurrence, kind suffix
  names, tag filter, multi-run suffixes, occurrence 2,000,000,000 accepted, wire dims
  `2**32 + 64` decoding to 64.
- Ladder: a scalar recorded on a media locator yields `selects an unsupported value`; a
  tag-only `Summary.Value` yields the same, not `was not found`.
- Schema parity: each per-kind `allOf` branch rejects a cross-kind object in both the model and
  the published schema; a `Manifest08` with a jsonl locator validates, and a non-null locator
  with a null `artifact_id` is rejected.
- Checker: all seven ladder rows for both kinds, finding texts byte-exact, five-kind cap
  counting, csv cap on 0.8, 128 MiB snapshot eligibility, `validate` verifying rather than
  warning, scalar-on-media-locator mismatch, media on a scalar locator rejected by the schema.
- Adapter/CLI: all eight flag sets with their pinned budget text, precedence order, tag
  skip/refuse rules, the 0.8 tag text for a media-only run, `--volatile` refusal,
  `init --force` refusal before discovery, and the capability templates for `--adapter generic`
  (does not support) and a keyword-incompatible adapter (cannot apply).
- Manifest/migration: save and load a 0.8 manifest carrying `conditions`/`metric_definition`,
  `artifact_to_dict`, 0.1/0.2/0.3–0.7 → 0.8, 0.8 → 0.3–0.7 downgrade refusal, comparability
  refusal, `UNSUPPORTED_TARGET` and `--to` help text.
- Consumers: live values through every human surface, in model and dict form, `--json`
  exactness (compare rows gain `baseline_image`/`target_image`/`baseline_audio`/`target_audio`,
  so its JSON is not unchanged), parameter rendering unchanged, claim display hides the tag and
  uses the compact value, the conflicting-evidence text uses the display, locator-display
  literals.
- Manifest helpers: `is_manifest` accepts 0.8; a 0.8 manifest survives the directory and
  experimental helper dispatch paths that only inspect the model type.
- Mutation probes: drop the PNG signature check, drop the RIFF size check, drop the digest
  comparison, remove 0.8 from `_LOCATOR_SCHEMAS`, remove the `artifact_to_dict` branch, remove
  the `value_to_json` media branch, admit 0.8 in comparability, drop the `init --force` guard.

## 10. Decisions requested

1. Digest-only references (recommended) versus inline base64 media.
2. PNG-only images and RIFF/WAVE-only audio, other formats skipped entry-locally
   (recommended), versus JPEG/GIF/MP3 now.
3. Two locator kinds (recommended) versus one `tensorboard-media` kind with a media type.
4. `--include-images` / `--include-audio` (recommended) versus one `--include-media`.
5. No audio metadata in the value (recommended, §2) versus recording an unverified rate or
   duration.
6. Provider metadata (`colorspace`, `num_channels`) excluded (recommended), since the digest
   pins the bytes.

## 11. Non-goals

- Extracting payloads to sidecar files; the adapter stays read-only.
- Pixel or audio decoding: only the PNG chunk walk with the IHDR CRC, and the RIFF chunk walk.
- Animated PNG/GIF, EXIF, MP4, audio transcoding, video.
- TF2 `tf.summary.image`/`tf.summary.audio` string tensors and other higher-rank numeric
  tensors; that is a separate gate.
- Plugin-specific summaries (PR curves, text/HTML plugins) and graph/run metadata as results.
