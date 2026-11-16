# Schema 0.10 TF2 image and audio summaries

Schema 0.10 is an explicit opt-in that adds **TensorFlow 2 media summaries**:
`tf.summary.image` and `tf.summary.audio` write `DT_STRING` tensors, which the
0.6 string reader never records and the 0.8 media reader never looked at.
Schemas 0.1–0.9 stay frozen.

## What gets recorded

A TF2 media entry is a `Summary.Value.tensor` holding a string tensor in one of
two layouts:

- **images:** rank 1 `[2 + k]` — element 0 is the width and element 1 the height
  as decimal strings, then `k` PNG payloads;
- **audio:** rank 2 `[k, 2]` — each row is a WAV payload and a label string.

Each payload becomes a digest-only reference, exactly like 0.8:

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

The two locator kinds are new, so frozen manifests cannot carry them:

```json
{"kind": "tensorboard-tf2-image", "tag": "samples", "step": 3, "occurrence": 0, "element": 1}
{"kind": "tensorboard-tf2-audio", "tag": "clip", "step": 4, "occurrence": 0, "element": 0}
```

`element` is the zero-based payload index inside the entry, `0..63`. `occurrence`
still counts **entries** with that exact `(tag, step)`, so the two elements of one
batch share it and differ only by `element`. Result ids append `tf2-image`/
`tf2-audio` and always `e<element>`; names read
`samples @ 3 (tf2 image) element 1`.

## Extraction, limits and verification

`init --include-tf2-media` is required and selects schema 0.10. It can be
combined with every other flag; tensors and legacy media keep their own kinds.
`--tags` filters the entry tag.

The width/height strings accept at most 32 bytes and five significant digits and
must equal the IHDR dimensions of every payload. Batches hold 1–64 payloads;
labels are at most 4,096 bytes and never stored. A mixed, malformed, oversized
or text-shaped string tensor fails only that entry. The frozen 16 MiB summary
cap still refuses the whole file.

`validate` re-reads the event file, selects the entry by `(tag, step,
occurrence)` and the payload by `element`, and compares the digest exactly. A
missing element is `was not found`; a string tensor that is not a valid TF2
layout reports `selects an unsupported tf2 image/audio encoding`; a legacy image
or other member reports `selects an unsupported value`. The delivered kinds keep
their 0.5–0.9 ladders unchanged.

## Migration and refusals

`migrate --to 0.10` is a version-only rewrite from 0.3–0.9; 0.1/0.2 keep the
locator precheck. Downgrades are `DOWNGRADE_UNSUPPORTED`, comparability refuses
0.10, and `init --force` refuses a declared 0.10 manifest.

## Limits

- PNG and RIFF/WAVE only; rank-0 and `[1]` single-payload tensors are out of
  scope and keep the delivered text behavior.
- No extraction, transcoding or pixel decoding.
- TF2 `[k,3]` histogram tensors and plugin-specific summaries are out of scope.
