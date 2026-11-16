# Schema 0.8 TensorBoard media references

Schema 0.8 is an explicit opt-in that adds **legacy TensorBoard images**
(`Summary.Value.image`, `Summary.Image`) and **audio** (`Summary.Value.audio`,
`Summary.Audio`) to the 0.7 histogram surface. Schemas 0.1–0.7, their bytes,
readers, checkers and output stay frozen.

## What gets recorded

Media is recorded as a **reference**, not a copy. The manifest stores the digest
and size of bytes that already live inside the event file:

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

Nothing is extracted, decoded or written beside the manifest. The digest is
SHA-256 over the encoded payload exactly as stored (lowercase hex), and
`size_bytes` is that payload's length.

Images are PNG only. The reader verifies the signature, the IHDR length, type,
CRC-32 (IEEE/zlib), dimensions (1–65,535), at least one non-empty `IDAT`, and a
final `IEND` that ends the payload; wire `width`/`height` must be 0 or match.
Audio is RIFF/WAVE only: `RIFF`/`WAVE`, the RIFF size equal to the payload
length minus 8, a first `fmt ` chunk of at least 16 bytes, exactly one non-empty
`data` chunk, and correct padding for odd chunk sizes. No sample rate or
duration is recorded, because no field could be recomputed by the checker.

The locators are
`{"kind":"tensorboard-image","tag":…,"step":…,"occurrence":…}` and
`{"kind":"tensorboard-audio","tag":…,"step":…,"occurrence":…}`. Occurrence is
per artifact and per kind. Result ids append `NUL + "image"`/`"audio"`, names
use `tag @ step (image)`/`(audio)`, and multifile groups append the basename and
a 16-hex digest.

A scalar value may still carry any locator kind, including the media ones; such
a result is schema-valid and verifies as `selects an unsupported value`.

## Extraction and verification

`init --include-images` and `init --include-audio` are required. Either flag
selects schema 0.8; combined with `--include-strings` and/or
`--include-histograms` it records those kinds too. Without a media flag, 0.5,
0.6 and 0.7 output is unchanged. Budgets: 10,000 results shared and 16 MiB of
tag bytes; media adds no budget of its own. `--tags` filters media tags like
any other kind.

`validate` re-reads the event file and compares every field exactly. A
malformed or unsupported media payload (JPEG, GIF, MP3, truncated PNG, unpadded
odd WAV chunk) makes that entry unsupported, never refuses the file. The
exception is the frozen 16 MiB merged-summary cap: an oversized summary refuses
the whole file, as it always has.

Human surfaces show compact forms, `image(64x64 png, 4321 bytes)` and
`audio(wav, 88244 bytes)`; `--json` emits the value object exactly. The
leaderboard excludes both kinds as `non_numeric_or_nonfinite`.

## Migration and refusals

`migrate --to 0.8` is a version-only rewrite from 0.3–0.7; 0.1/0.2 keep the
locator precheck. `migrate --to 0.7` or lower from a 0.8 manifest is
`DOWNGRADE_UNSUPPORTED`, comparability refuses 0.8 by version, and `init
--force` refuses to regenerate a declared 0.8 manifest.

## Limits

- PNG and RIFF/WAVE only; TF2 `tf.summary.image`/`tf.summary.audio` string
  tensors and other higher-rank tensors are out of scope.
- No pixel or audio decoding beyond the header checks above.
- No sidecar extraction: the adapter stays read-only.
