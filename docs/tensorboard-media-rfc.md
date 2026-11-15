# TensorBoard media references — design RFC (v0.1)

Status: **DESIGN PROPOSAL. NOTHING IMPLEMENTED. NOTHING AUTHORIZED BY THIS DOCUMENT.**
A new design gate and an owner schema-0.8 admission decision are required before any code.
Schema 0.1–0.7, their readers/checkers and every delivered behavior stay frozen.

## 0. Changes in v0.1

- First draft. Proposes digest-only references for TensorBoard images and audio, two locator
  kinds, opt-in flags, and schema 0.8.
- Pins: PNG-only images (`Summary.Image` field 4), RIFF/WAVE-only audio
  (`Summary.Audio` field 6), entry-local inner failures, oneof merge rules, per-kind
  occurrence namespaces, 16 MiB payload cap, exact verification, compact display.

## 1. Problem and scope

TensorBoard writers record images in `Summary.Value.image` (field 4, `Summary.Image`) and
audio in `Summary.Value.audio` (field 6, `Summary.Audio`). The delivered reader wire-checks
both fields and records nothing for them. Users track rendered samples, attention maps,
spectrograms and audio clips this way, but the manifest currently cannot state that any of them
existed or changed.

Recording the bytes is not an option. A 10,000-result manifest with inline 100 KiB images is a
gigabyte of JSON, and the read-only adapter contract forbids writing extracted files. The gate
therefore proposes **digest-only references**: the manifest records a SHA-256, the byte length
and a few header fields for the payload that is already inside the event file. Nothing is
extracted, nothing is copied, and verification re-reads the file.

In scope: one image or audio payload per `Summary.Value`, digest-only recording, exact
verification, opt-in extraction, schema 0.8, migration and compact display. Non-goals in §11.

## 2. Schema 0.8 and the value model

Schema 0.8 = 0.7 plus two reference values. `Result08.value` is
`Scalar | HistogramValue | ImageValue | AudioValue`.

An image value is a bounded object:

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

An audio value is a bounded object:

```json
{
  "kind": "audio",
  "media_type": "audio/wav",
  "sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
  "size_bytes": 88244,
  "sample_rate": 44100.0,
  "length_frames": 88200
}
```

Rules for both:

- strict (`extra="forbid"`) and all keys required, `kind` const;
- `sha256` is exactly 64 lowercase hexadecimal characters (no normalization: an uppercase
  digest is a validation error, not silently folded);
- `size_bytes` is an integer in `1..16_777_216`; a zero-length payload is not a media value;
- `media_type` is a `const`, not a free string: `image/png` for images and `audio/wav` for
  audio. A payload of any other type is unsupported and is skipped entry-locally (§3.4);
- `width`/`height` are integers in `1..65_535`;
- `sample_rate` is finite binary64 in `0 < x <= 768_000`; `length_frames` is an integer in
  `0..2**63-1`.

`ImageValue` requires exactly `kind, media_type, sha256, size_bytes, width, height`;
`AudioValue` requires exactly `kind, media_type, sha256, size_bytes, sample_rate,
length_frames`.

The digest is over the payload bytes exactly as stored in the event file, with no decoding,
re-encoding or whitespace normalization. Two files that carry structurally equal PNGs with
different compression produce different digests; that is intended and is stated in the docs.

## 3. Reader profile

### 3.1 Where media lives

`Summary.Value` is a oneof over `simple_value` (2), `obsolete_old_style_histogram` (3),
`image` (4), `histo` (5), `audio` (6) and `tensor` (8), plus the independent fields `tag` (1),
`node_name` (7), `metadata` (9) and unknown fields.

The delivered `_parse_value` already tracks the winning member and the restart offset for the
tensor. Schema 0.8 extends the same state machine with the payload range of the winning
`image` and `audio` member. Tag, `node_name`, `metadata` and unknown fields do not reset media
state; a different oneof member does.

### 3.2 Merge rule

Repeated occurrences of the same media member after the last different oneof member are
**merged field by field** in wire order, with last-wins per scalar field:

- `Summary.Image`: `height` (1, varint), `width` (2, varint), `colorspace` (3, varint),
  `encoded_image_string` (4, bytes). Last encoded value wins; a later occurrence with no
  encoded field leaves the earlier bytes in place.
- `Summary.Audio`: `sample_rate` (1, fixed32), `num_channels` (2, varint), `length_frames`
  (3, varint), `encoded_audio_string` (4, bytes), `content_type` (5, bytes). Last encoded
  value wins; an absent encoded field leaves the earlier bytes in place.

A merge that ends with no encoded bytes is not a media value. It is an entry-local failure for
the media kind, and the entry counts as unsupported (`selects an unsupported value`) for that
kind, exactly like a malformed histogram.

`colorspace` and `num_channels` are wire-checked and ignored: the value model does not carry
them, because the digest already pins the bytes and the PNG/WAVE header carries the effective
geometry and channel count.

### 3.3 Validation

Image payloads are PNG only:

- the 8-byte signature `89 50 4E 47 0D 0A 1A 0A`, then an IHDR chunk at offset 8
  (`00 00 00 0D 49 48 44 52`);
- `width` and `height` are the big-endian 32-bit IHDR values; each must be in `1..65_535`;
- when the wire `width`/`height` are non-zero they must equal the IHDR values; a mismatch is an
  entry-local failure, not a silent repair;
- `size_bytes` is the encoded payload length and must be in `1..16_777_216`.

Audio payloads are RIFF/WAVE only:

- `content_type` is `audio/wav` or absent (absent defaults to `audio/wav`); any other value is
  unsupported;
- the payload starts with `RIFF`, has that length field consistent with the payload, and has
  the `WAVE` form at offset 8;
- `sample_rate` must be finite and in `(0, 768_000]`; `length_frames` must be non-negative;
- `size_bytes` is the encoded payload length and must be in `1..16_777_216`.

Only the header is inspected. No pixel decode, no audio decode, no re-encode.

### 3.4 Failure policy

Every check in §3.2/§3.3 is **entry-local**: a failing media entry is skipped and produces no
result, while other entries in the same `Summary` and other event files are unaffected. This
matches the delivered histogram rule and the frozen outer Event grammar: the Event/Summary
wire walk still refuses the whole file on a structural error, but a media payload that is
merely unsupported or inconsistent never does.

A failed media entry does not consume a per-kind occurrence number. Occurrence counts only
conforming media entries.

### 3.5 Bounds

The existing 16 MiB merged-summary cap bounds every payload before it is hashed, and §2's
`size_bytes` cap repeats it per value. Hashing is streaming over the payload range; the reader
retains no bytes beyond the file buffer it already holds. No new manifest budget is needed:
a 200-byte value at the existing 10,000-result cap is 2 MB of manifest.

## 4. Locator, identity, ordering

Two locator kinds are added:

```json
{"kind": "tensorboard-image", "tag": "samples", "step": 3, "occurrence": 0}
{"kind": "tensorboard-audio", "tag": "clip", "step": 3, "occurrence": 0}
```

Rules, all inherited from the 0.5/0.6/0.7 locators:

- `tag` is the exact event tag, 1..4,096 code points and 1..4,096 UTF-8 bytes, no surrogate;
- `step` is a signed 64-bit integer, absent `Event.step` means 0;
- `occurrence` is the zero-based position among **conforming entries of the same kind** with
  that exact `(tag, step)` in file order, 0..10,000;
- the five kinds (scalar, string, histogram, image, audio) count occurrences independently.

Result names keep the delivered shape with a kind suffix: `samples @ 3 (image)` and
`clip @ 3 (audio)`. Multi-run path suffixes, cross-run alignment by name and the tag filter
apply unchanged. A tag recorded as both an image and an audio yields two results, because the
locator kinds differ.

## 5. Checker and consumers

`checks/results.py` routes each locator kind to the 0.8 scanner and requests
`(tag_bytes, step, occurrence)`. Comparison is exact over the whole value object:

- `sha256`, `size_bytes`, `media_type`, `width`, `height` for images;
- `sha256`, `size_bytes`, `media_type`, `sample_rate`, `length_frames` for audio.

Texts reuse the delivered findings:

- missing occurrence: `locator <kind>:step=<n>,occurrence=<k> was not found in '<path>'`;
- different kind at that position: `selects an unsupported value`;
- different bytes or metadata: `records <compact display> but <path> at <locator> contains
  <compact display>` under `result-value-mismatch`.

Human surfaces (`inspect`, `claim`, `lineage`, `run`, `compare`, `matrix`, `graph`,
`leaderboard`) render compact displays, scoped to results only; parameters keep their
delivered rendering:

- image: `image(64x64 png, 4321 bytes)`;
- audio: `audio(44100 Hz, 88200 frames, 88244 bytes)`.

`--json` emits the value object exactly. `leaderboard` treats both kinds as
`non_numeric_or_nonfinite`, as it already does for histograms and strings.

## 6. Adapter and CLI

- `init --include-images` and `init --include-audio` are opt-in and independent; either flag
  selects schema 0.8. `--include-histograms`, `--include-strings` keep their meaning and can be
  combined: 0.8 is the union schema and can carry every delivered kind.
- `--tags` filters media tags exactly like the other kinds.
- Per-kind ids and names follow the delivered pattern; the multifile digest suffix and
  conditional directory naming are unchanged.
- Budget lines name the flags that were actually passed, per kind. Histograms-only output still
  excludes strings, and media-only output excludes both, so a flag never records a kind it did
  not ask for.
- Non-UTF-8 tags get the 0.8 tag message (`schema 0.8`), and the 0.6/0.7 messages stay with
  their own flags.

## 7. Migration, refusals, fixed texts

- `migrate --to 0.8` from 0.3–0.7 is an identity rewrite; every delivered locator and value is
  valid in 0.8.
- `migrate --to 0.7` (or lower) from a 0.8 manifest is `DOWNGRADE_UNSUPPORTED`; 0.8 cannot be
  represented below 0.8.
- Comparability refuses 0.8 by version, like 0.7, before any bridge work.
- Fixed texts:

```
an image tag cannot be represented in schema 0.7; nothing was written
an audio tag cannot be represented in schema 0.7; nothing was written
no TensorBoard value was read
```

The last line is the existing cap text and is reused unchanged.

## 8. Admission checklist (per module)

| Module | Change |
| --- | --- |
| `models.py` | `ImageValue`, `AudioValue`, two locators, `Result08`, `Manifest08`, dispatch/save/load, `$defs`/`allOf`, schema 0.8 file and pin |
| `tensorboard_events.py` | media state in `_parse_value`, merge/validate, `scan_tensorboard_08`, per-kind occurrence namespaces; 0.5–0.7 scanners untouched |
| `checks/results.py` | locator routing, exact comparison, ladder messages, cap/128 MiB path |
| `locator_profiles.py` | 0.8 locator admission |
| `migrate.py` | 0.8 identity from 0.3–0.7, downgrade refusal |
| `run_comparability.py` | 0.8 in the unsupported-by-version set |
| `run_compare.py`, `run_matrix.py`, `run_details.py`, `run_claim.py`, `run_lineage.py`, `checks/graph.py`, `report.py` | compact display for results only, JSON unchanged |
| `packages/runevidence-tensorboard/adapter.py` | flags, schema selection, ids/names, budgets, tag messages |
| `cli.py` | `--include-images`, `--include-audio` |
| `docs/`, `schema/`, `README.md` | 0.8 page, schema file, flag documentation |

## 9. Verification plan

- Reader: PNG signature/IHDR dims, JPEG/GIF payloads skipped, truncated payloads, wire
  `width`/`height` mismatch, RIFF/WAVE validation, bad `content_type`, non-finite
  `sample_rate`, zero-length payloads, merge across repeated occurrences, reset by a different
  oneof member, 16 MiB boundary at 16 MiB and 16 MiB + 1.
- Identity: per-kind occurrence namespaces, kind suffix in names, tag filter, multi-run
  suffixes.
- Checker: found/missing/unsupported/mismatch for each field, digest-only equality, cap text,
  128 MiB routing.
- Adapter/CLI: flag matrix (none, images, audio, both, with strings, with histograms), budget
  texts, tag messages per schema, histograms-only and media-only exclusions.
- Consumers: live `ImageValue`/`AudioValue` through every human surface, JSON exactness,
  parameter rendering unchanged.
- Migration: 0.3–0.7 → 0.8 identity, 0.8 → 0.7 downgrade refusal, comparability refusal.
- Mutation probes: drop the PNG signature check, drop the RIFF check, drop the digest
  comparison, admit 0.8 in comparability, remove 0.8 scanner dispatch.

## 10. Decisions requested

1. Digest-only references (recommended) versus inline base64 media in the manifest.
2. PNG-only images and RIFF/WAVE-only audio, everything else skipped entry-locally
   (recommended), versus JPEG/GIF/MP3 support now.
3. Two locator kinds (recommended) versus one `tensorboard-media` kind with a media type.
4. `--include-images` / `--include-audio` (recommended) versus a single `--include-media`.
5. Whether `colorspace` and `num_channels` belong in the value model (recommended: no).

## 11. Non-goals

- Extracting payloads to sidecar files; the adapter stays read-only.
- Pixel or audio decoding beyond the PNG IHDR and RIFF/WAVE header checks.
- Animated PNG/GIF, EXIF handling, audio transcoding, video.
- Plugin-specific image rendering (matplotlib figures, PR curves, text summaries).
- Higher-rank numeric tensors and graph/run metadata; each needs its own gate.
