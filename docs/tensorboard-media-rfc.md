# TensorBoard media references — design RFC (v0.2)

Status: **DESIGN PROPOSAL. NOTHING IMPLEMENTED. NOTHING AUTHORIZED BY THIS DOCUMENT.**
A new design gate and an owner schema-0.8 admission decision are required before any code.
Schema 0.1–0.7, their readers/checkers and every delivered behavior stay frozen, except the
additive dispatch, model, migration and text edits this document names explicitly.

## 0. Changes in v0.2

Round-1 review (three seats; the fourth was out of quota) rejected v0.1. This revision folds
every blocker:

- Adds `manifest.py`, `checks/__init__.py`, `cli.py`, `locator_profiles.py`, `checks/graph.py`,
  `run_claim.py` and `run_leaderboard.py` obligations, with the failure each one causes when
  omitted (0.8 would otherwise load nothing, cap nothing, and crash on display).
- Inherits `occurrence` `0..2**31-1` instead of inventing 10,000.
- Pins the 0.8 value↔locator compatibility matrix and its `allOf` rules.
- States that the frozen 16 MiB Summary cap refuses the whole file and takes precedence over
  every entry-local media failure; the per-payload cap can therefore never be reached alone.
- Replaces the ad-hoc validation with a complete, bounded header walk for PNG and a pinned
  RIFF/WAVE header rule, so occurrence identity is decidable.
- Drops `sample_rate` and `length_frames` from the audio value: the digest pins the bytes, and
  no recorded field would be derived from audio header data.
- Names a separate `_parse_value_08` parser with the histogram state machine, so frozen
  0.5/0.6 `_parse_value` is untouched.
- Copies the delivered checker ladder (found / differs / unsupported encoding / exhausted /
  other kind / absent / wrong recorded type) and the delivered finding texts verbatim.
- Completes the fixed-text families: 0.8 init refusal, migrate target list, conditions,
  adapter capability errors, tag texts, and one budget line per real flag combination.
- Extends the checklist with `init --force`, `--volatile`, `Manifest.model_dump` 0.2 fields,
  the schema-selection precedence, and the graph/locator-display/claim-whitelist gates.

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
slice records; that is a stated non-goal, not an accidental gap.

In scope: one image or audio payload per `Summary.Value`, digest-only recording, exact
verification, opt-in extraction, schema 0.8, migration and compact display. Non-goals in §11.

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
  "sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
  "size_bytes": 88244
}
```

Rules:

- both models are strict (`extra="forbid"`), every key is required, `kind` is const;
- `sha256` is exactly 64 lowercase hexadecimal characters; an uppercase digest is a validation
  error, never silently folded;
- `size_bytes` is an integer in `1..16_777_216`; a zero-length payload is not a media value;
- `media_type` is const (`image/png`, `audio/wav`); any other payload type is unsupported and
  produces no result (§3.6);
- `width`/`height` are integers in `1..65_535` and must equal the PNG IHDR values (§3.4).

Audio carries no sample rate, frame count or channel count. The digest pins the payload, no
recorded field is derived from the WAV body, and a metadata field the checker cannot recompute
would be an unverified claim. The compact display therefore reports only type and size.

### Value ↔ locator compatibility (`allOf`)

`Result08` enforces, in the model and in the published JSON Schema:

| Value | Permitted locator (`null` always allowed) |
| --- | --- |
| `ImageValue` | `tensorboard-image` |
| `AudioValue` | `tensorboard-audio` |
| `HistogramValue` | `tensorboard-histogram` |
| `Scalar` | `jsonl`, `csv-cell`, `tensorboard`, `tensorboard-string` |

A media value on any other locator kind, a histogram value on a media locator, or a scalar on a
media locator is rejected by both layers. This is the 0.7 rule generalised: a first-class value
can never sit on a pointer that cannot re-read it. `null` remains allowed because a hand-built
manifest may describe a value whose bytes are not locatable; such a result is unverifiable, as
in 0.5–0.7.

## 3. Reader profile

### 3.1 A separate 0.8 parser

The 0.5/0.6 `_parse_value` stays byte-for-byte frozen. Schema 0.8 gets `_parse_value_08`,
modelled on `_parse_value_07`: same oneof tracking, same histogram merge/reset state, plus the
`image` (4) and `audio` (6) payload ranges. `_parse_value_07` also stays untouched; the 0.8
scanner is the only caller of `_parse_value_08`, and `scan_tensorboard_07` keeps its own path.

### 3.2 Where media lives, and the merge rule

`Summary.Value` is a oneof over `simple_value` (2), legacy histogram (3), `image` (4), `histo`
(5), `audio` (6) and `tensor` (8), plus independent `tag` (1), `node_name` (7), `metadata` (9)
and unknown fields. Tag, `node_name`, `metadata` and unknown fields do not reset media state; a
different oneof member does.

Repeated occurrences of the same media member after the last different oneof member merge
field by field, last-wins:

- `Summary.Image`: `height` (1, varint), `width` (2, varint), `colorspace` (3, varint),
  `encoded_image_string` (4, bytes);
- `Summary.Audio`: `sample_rate` (1, fixed32), `num_channels` (2, varint), `length_frames`
  (3, varint), `encoded_audio_string` (4, bytes), `content_type` (5, bytes).

Only the encoded bytes, the image `width`/`height`, and the audio `content_type` survive the
merge; the other fields are wire-checked and ignored (see §3.5 for why). A merge that ends
with no encoded bytes is a failing entry for that kind, not a value.

### 3.3 Inner failure set (entry-local)

Inside a winning media member, key decoding is lenient exactly like the histogram decoder:

- wrong wire type for a known field, field number 0, wire types 3/4/6/7, a truncated length or
  a malformed varint make **that entry** fail for that kind;
- the outer Event/Summary walk keeps its frozen behaviour and still refuses the file on a
  structural error outside the media payload;
- a failed entry is not a value, consumes no occurrence number, and does not affect sibling
  entries, other values, or other files;
- if a merge had already accumulated fields when the lenient failure occurred, the accumulated
  state is discarded; the entry is unsupported, never partially recorded.

Unknown fields inside `Summary.Image`/`Summary.Audio` are skipped, as the histogram decoder
does.

### 3.4 PNG validation (images)

A payload is a valid image only if all of this holds; otherwise the entry fails locally:

1. length is at least 33 bytes and starts with `89 50 4E 47 0D 0A 1A 0A`;
2. the first chunk starts at offset 8 with a 4-byte big-endian length equal to 13 and type
   `IHDR`, and the IHDR CRC-32 matches its type+data bytes;
3. `width` and `height` read big-endian from IHDR are each in `1..65_535`;
4. a chunk walk from offset 8 stays inside the payload, every chunk length is
   `0..2**31-1`, at least one `IDAT` chunk exists, and the walk ends with an `IEND` chunk of
   length 0 exactly at the last byte;
5. when the wire `width`/`height` are non-zero they equal the IHDR values; wire values are
   unsigned varints, and `0` means "not set".

Only the IHDR CRC is verified; other chunk CRCs are not, because the SHA-256 already pins every
byte and chunk CRC verification would add O(payload) work for no identity gain. No pixel or
IDAT decompression happens.

### 3.5 Audio validation

A payload is a valid audio reference only if all of this holds:

1. `content_type` is absent, empty, or exactly `audio/wav`; any other value is unsupported;
2. length is at least 12 and in `1..16_777_216`;
3. bytes 0..4 are `RIFF`, bytes 8..12 are `WAVE`;
4. the little-endian `uint32` at offset 4 equals `size_bytes - 8` exactly. A writer that pads
   an odd payload and includes the pad in the RIFF size still satisfies this; a writer that
   omits the pad does not, and the entry fails.

The `fmt ` and `data` chunks are not parsed. No recorded field derives from them (the value
model carries no rate or duration), so parsing them would create claims the checker must also
recompute, for no identity gain. The digest pins the bytes.

### 3.6 Failure precedence and caps

The frozen 16 MiB cap is on the whole merged `Summary` message and refuses the whole file
before any value is classified. It therefore outranks every entry-local rule here:

- a media payload large enough to push its Summary past 16 MiB is a **file-level refusal**, not
  an entry-local failure; this is unchanged 0.5–0.7 behaviour and is tested as such;
- below that line, §3.3–§3.5 failures are entry-local;
- `size_bytes` `1..16_777_216` is the model bound; in practice a payload near 16 MiB always
  trips the Summary cap first, so boundary tests at 16 MiB and 16 MiB + 1 are file-level, and
  the largest reachable media value is a Summary-cap artefact.

Nothing is retained beyond offsets and a streaming digest; the file buffer already exists.

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

This is mandatory, not optional: without it 0.8 verifies nothing.

- `_LOCATOR_SCHEMAS` gains 0.8, so `run_checks` computes `has_locators` and
  `check_results` takes the targeted path;
- `_tensorboard_cap` admits 0.8 and counts all five kinds against the shared 10,000-result and
  16 MiB tag limits;
- `_csv_cell_cap` admits 0.8 (0.8 is a union schema and inherits csv-cell);
- `_tensorboard_snapshot_ids` admits 0.8 so a 128 MiB event file keeps its snapshot instead of
  the 50 MiB generic limit;
- without these, a 0.8 locator falls to the legacy JSON path and ends as an
  unverifiable warning, so `validate` exits 0 having verified nothing.

### 5.2 Comparison ladder

Per kind, the outcome ladder is the delivered histogram ladder, restated here so the wording
cannot drift:

| Condition | Outcome | Finding |
| --- | --- | --- |
| conforming entry at that occurrence | compare | none, or mismatch below |
| recorded value differs | mismatch | `result-value-mismatch`, text `does not match the recorded value` |
| conforming entries exist but the occurrence is exhausted | missing | `locator-mismatch`, text `was not found` |
| entries exist at the tag/step, the same kind failed validation | unsupported encoding | `locator-unsupported-value`, text `selects an unsupported image encoding` / `selects an unsupported audio encoding` |
| entries exist at the tag/step with a different oneof member | unsupported value | `locator-unsupported-value`, text `selects an unsupported value` |
| no entry at the tag/step | missing | `was not found` |
| recorded value is not an `ImageValue`/`AudioValue` | unsupported value | `selects an unsupported value` |

There is no cross-kind "position": each kind counts only its own conforming entries, so a valid
image does not shift an audio occurrence. The finding text is the delivered
`result '<id>' locator <locator> does not match the recorded value in '<path>'`; recorded
values are never injected into locator-level findings.

### 5.3 Display

Human surfaces (`inspect`, `claim`, `lineage`, `run`, `compare`, `matrix`, `graph`,
`leaderboard`) render, for results only:

- `image(64x64 png, 4321 bytes)`;
- `audio(wav, 88244 bytes)`.

Parameters keep their delivered rendering. `--json` emits the value object exactly.
`leaderboard` treats both kinds as `non_numeric_or_nonfinite`.

`locator_profiles.display_locator` needs branches for both kinds, and `run_claim`'s display
whitelist needs both, or the first finding crashes with `AttributeError` and the claim surface
prints the raw tag, breaking the delivered "never the recorded tag" rule.

`checks/graph.py` has two version gates (`locator_schema`, capped-artifact hint) that must
admit 0.8, or a 0.8 result reports "no locator in schema 0.3".

## 6. Adapter and CLI

- `init --include-images` and `init --include-audio` are opt-in and independent.
- Schema selection precedence, in this order: any media flag → 0.8; else `--include-histograms`
  → 0.7; else `--include-strings` → 0.6; else 0.5. Putting the media test after the histogram
  test would emit a 0.7 manifest and silently discard every requested image, so the order is
  part of the contract and is tested directly.
- The 0.8 scanner yields scalar, string, histogram, image and audio entries according to the
  flags actually passed; a flag never records a kind that was not asked for.
- `--tags` filters media tags like the other kinds.
- Unusable tags: a scalar tag that is not representable refuses `init` (delivered rule); a
  string, histogram, image or audio tag that is not representable is skipped, and is also
  skipped when `--tags` is present, exactly like the delivered non-scalar rules.
- Budget texts are fixed per real flag combination, naming only the limits in play:

```
the selected TensorBoard summaries exceed the 10,000-result, 16 MiB tag or 16 MiB media-reference limit; nothing was written
the selected TensorBoard summaries exceed the 10,000-result, 16 MiB tag, 16 MiB string-value or 16 MiB media-reference limit; nothing was written
the selected TensorBoard summaries exceed the 10,000-result, 16 MiB tag, 16 MiB histogram-JSON or 16 MiB media-reference limit; nothing was written
the selected TensorBoard summaries exceed the 10,000-result, 16 MiB tag, 16 MiB string-value, 16 MiB histogram-JSON or 16 MiB media-reference limit; nothing was written
```

  The four delivered texts (0.5 scalars, 0.6 strings, 0.7 histograms, 0.7 both) are unchanged,
  and no flag combination reuses a text that names a limit it did not enable.

## 7. Migration, refusals, fixed texts

- `migrate --to 0.8` admits every source. 0.1/0.2 run the existing locator precheck; 0.3–0.7
  are version-only identity rewrites, because 0.8 admits every delivered locator and value.
- `TARGET_VERSIONS`, the per-target branches, `--to` help text and `UNSUPPORTED_TARGET` gain
  0.8.
- `--to 0.7` (and 0.6, 0.5, 0.4, 0.3, 0.2) from 0.8 raises `DOWNGRADE_UNSUPPORTED`. This is an
  additive edit to frozen migration branches and is explicitly authorised here; without it the
  frozen branches return `INTERNAL_ERROR`.
- `Manifest.model_dump` must treat 0.8 like 0.7 (keep `conditions`, `dataset_id`,
  `metric_definition`), or a saved 0.8 manifest silently loses 0.2 fields.
- `manifest.py` dispatch (`validate_manifest`, `_root_model_for`, `artifact_to_dict`) must
  admit 0.8, or `init` raises an uncaught `ValueError` after reading the tree and
  `migrate --to 0.8` reports `INTERNAL_ERROR`.
- `cli.py` gains `INIT_0_8_REFUSAL` and includes 0.8 in the `init --force` declared-version
  guard **before discovery, backup or any write**; it also adds `Manifest08` to
  `_event_artifact_is_volatile`.
- Comparability refuses 0.8 by version in both `compare_paths` and `preflight_paths`.
- Fixed texts, all literal:

```
ERROR: the existing manifest declares schema 0.8. init --force would regenerate the manifest and cannot carry over first-class values or locators. Nothing was written. Move the manifest aside, run init, then run runevidence migrate PATH --to 0.8 (unless the adapter writes 0.8 itself) and reapply the first-class values and locators.
ERROR (UNSUPPORTED_TARGET): only --to 0.2, --to 0.3, --to 0.4, --to 0.5, --to 0.6, --to 0.7 or --to 0.8 is supported; downgrade and other versions are not available. Nothing was written.
a scalar tag cannot be represented in schema 0.8; nothing was written
no TensorBoard value was read
```

The adapter capability errors and the conditions `UNSUPPORTED_VERSION` family keep their
delivered text and gain the 0.8 version value where the schema list is printed.

## 8. Admission checklist (per module)

| Module | Change |
| --- | --- |
| `models.py` | `ImageValue`, `AudioValue`, two locators, `Result08` compatibility matrix, `Manifest08`, dispatch/save/load, `model_dump` 0.2 fields, `$defs`/`allOf`, schema 0.8 file and pin |
| `manifest.py` | `validate_manifest`, `_root_model_for`, `artifact_to_dict` admit 0.8 |
| `tensorboard_events.py` | `_parse_value_08` (histogram + media state), lenient inner keys, PNG/RIFF validation, `scan_tensorboard_08`, per-kind occurrence namespaces; 0.5–0.7 scanners and `_parse_value_07` untouched |
| `checks/__init__.py` | locator admission, five-kind shared caps, csv cap, 128 MiB snapshot eligibility |
| `checks/results.py` | 0.8 scanner routing, comparison ladder, finding texts, cap text |
| `checks/graph.py` | locator schema gate and capped-artifact hint admit 0.8 |
| `locator_profiles.py` | admission and `display_locator` branches for both kinds |
| `run_claim.py` | display whitelist for both kinds |
| `run_compare.py`, `run_matrix.py`, `run_details.py`, `run_lineage.py`, `run_leaderboard.py`, `report.py` | compact display for results only, JSON unchanged |
| `migrate.py` | `TARGET_VERSIONS`, 0.8 identity from every source, downgrade tuples |
| `run_comparability.py` | 0.8 in the unsupported-by-version set |
| `cli.py` | flags, `INIT_0_8_REFUSAL`, force guard, volatile admission, `UNSUPPORTED_TARGET`, help text |
| `packages/runevidence-tensorboard/adapter.py` | flags, precedence, ids/names, budget texts, tag rules |
| `docs/`, `schema/`, `README.md` | 0.8 page, schema file, flag documentation |

## 9. Verification plan

- Reader: PNG signature, truncated IHDR, bad IHDR CRC, zero/oversized dims, wire dims 0 and
  mismatched, missing IDAT, missing IEND, chunk overrun, JPEG/GIF payloads skipped; RIFF magic,
  wrong RIFF size, odd-payload padding, `content_type` variants, zero-length payload; merge
  across repeated occurrences; reset by a different oneof member; lenient inner key failures;
  Summary-cap precedence at exactly 16 MiB and 16 MiB + 1 (file-level).
- Identity: per-kind occurrences, a valid image not shifting an audio occurrence, kind suffix
  names, tag filter, multi-run suffixes, occurrence 2,000,000,000 accepted.
- Checker: all seven ladder rows for both kinds, finding texts byte-exact, cap counting with
  five kinds, csv cap on 0.8, 128 MiB snapshot eligibility, `validate` verifying rather than
  warning.
- Adapter/CLI: flag matrix (none, images, audio, both, with strings, with histograms, all
  three), precedence order, budget texts, tag skip/refuse rules, `--volatile` refusal,
  `init --force` refusal before discovery.
- Manifest/migration: save and load a 0.8 manifest with `conditions`/`metric_definition`,
  `artifact_to_dict`, 0.1/0.2/0.3–0.7 → 0.8, 0.8 → 0.7 downgrade refusal, comparability
  refusal, `UNSUPPORTED_TARGET` text.
- Consumers: live values through every human surface, JSON exactness, parameter rendering
  unchanged, claim display hides the tag, graph labels.
- Mutation probes: drop the PNG signature check, drop the RIFF size check, drop the digest
  comparison, remove 0.8 from `_LOCATOR_SCHEMAS`, remove the `artifact_to_dict` branch, admit
  0.8 in comparability, drop the `init --force` guard.

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
- Pixel or audio decoding: only the PNG chunk walk with the IHDR CRC, and the RIFF header.
- Animated PNG/GIF, EXIF, MP4, audio transcoding, video.
- TF2 `tf.summary.image`/`tf.summary.audio` string tensors and other higher-rank numeric
  tensors; that is a separate gate.
- Plugin-specific summaries (PR curves, text/HTML plugins) and graph/run metadata as results.
