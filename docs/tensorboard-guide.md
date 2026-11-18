# TensorBoard in RunEvidence: the complete surface

This page is the one-stop guide to reading TensorBoard event files with
RunEvidence. It covers every delivered schema from 0.5 to 0.9, the flags that
select them, what is recorded, how verification works, and the limits that are
deliberate.

Schemas are additive: each flag opts into more kinds and selects the highest
schema it needs. Schemas 0.1–0.4 and their behavior are untouched.

## Quick reference

| Flag | Records | Schema | Locator kind | Value |
| --- | --- | --- | --- | --- |
| *(none)* | scalars | 0.5 | `tensorboard` | number |
| `--include-strings` | scalar-shaped `DT_STRING` tensors | 0.6 | `tensorboard-string` | string |
| `--include-histograms` | legacy `HistogramProto` | 0.7 | `tensorboard-histogram` | structured histogram |
| `--include-images` | legacy `Summary.Image` | 0.8 | `tensorboard-image` | digest reference |
| `--include-audio` | legacy `Summary.Audio` | 0.8 | `tensorboard-audio` | digest reference |
| `--include-tensors` | numeric `TensorProto` | 0.9 | `tensorboard-tensor` | digest reference |
| `--include-tf2-media` | TF2 image/audio string tensors | 0.10 | `tensorboard-tf2-image`, `tensorboard-tf2-audio` | digest reference per element |
| `--include-tf2-histograms` | TF2 `[k,3]` histogram tensors | 0.11 | `tensorboard-tf2-histogram` | structured bucket list |

Precedence: TF2 histograms → 0.11, TF2 media → 0.10, tensors → 0.9, media → 0.8, legacy histograms → 0.7,
strings → 0.6, else 0.5. Flags combine; the manifest carries only the kinds actually requested.

```bash
runevidence init logs/ --adapter tensorboard \
  --include-strings --include-histograms \
  --include-images --include-audio --include-tensors --include-tf2-media
runevidence validate logs/
runevidence inspect logs/
```

## Identity

One run per directory that directly contains event files, named by the
directory's relative path (or qualified by basename when a `(tag, step)` group
spans files). Each result records a locator with the exact tag, the signed
64-bit step and a zero-based `occurrence`. Occurrences are counted per artifact
per kind, so a scalar never shifts a string, histogram, image, audio or tensor
occurrence.

`init --tags PATTERN` (repeatable) keeps matching tags; an unrepresentable
scalar tag refuses `init` without a filter and is skipped with one, while
non-scalar tags are skipped in both cases.

## What each kind records

- **Scalars (0.5):** rank-0 or rank-1 `[1]` float/double tensors and legacy
  `simple_value`.
- **Strings (0.6):** scalar-shaped `DT_STRING` tensors, up to 64 KiB per value,
  sharing a 16 MiB text budget.
- **Histograms (0.7):** legacy `HistogramProto` with statistics preserved, at
  most 1,024 buckets, exact comparison.
- **Images and audio (0.8):** digest-only references. PNG images carry verified
  IHDR width/height; RIFF/WAVE audio carries the byte length. The bytes stay in
  the event file and `validate` re-hashes them.
- **TF2 media (0.10):** `tf.summary.image`/`audio` string tensors, `[2+k]` images
  with verified width/height and `[k,2]` audio with labels, one digest per element.
- **Tensors (0.9):** float32/float64/int32/int64 arrays as canonical digests
  (dtype, shape, element count, SHA-256). Content and typed writer forms hash
  equally; int32 uses the low 32 bits; NaN payloads and `-0.0` are preserved.

## Verification

`validate` re-reads each event file and compares recorded values exactly. A
profile problem in one entry — unsupported dtype, bad shape, mismatched count or
length, foreign typed lists, an unrepresentable tag — affects only that entry.
Structural problems the reader always refused stay whole-file refusals, so
migrating a manifest to a newer schema never changes an existing check outcome.

Findings use the locator, never the tag:
`tensorboard-image:step=3,occurrence=0`, `tensorboard-tensor:step=1,occurrence=2`,
and so on.

## Caps and budgets

| Limit | Value |
| --- | --- |
| Event file | 128 MiB |
| Selected results per manifest | 10,000 |
| Selected tag bytes | 16 MiB |
| String value | 64 KiB, 16 MiB total |
| Histogram buckets | 1,024 |
| Histogram JSON / media payload | 16 MiB |
| Tensor dimensions / elements | 8 / 1,000,000 |
| TF2 media payloads per entry | 64 |
| TF2 media label | 4,096 bytes (not stored) |

Tensor payloads and media references add no manifest budget of their own; the
shared 10,000-result and tag limits bound them.

If a default scalar scan hits the 10,000-result limit, the refusal includes up
to 20 tags ordered by entry count, with each tag JSON-escaped on its own line.
That is enough to form `--tags` patterns without reading binary event files. It
is a bounded diagnostic, not a complete inventory of every tag or kind.

## Human output

Results render compactly:

```
loss @ 1 = 0.5
config @ 2 (text) = 'lr=3e-4'
weights @ 3 (histogram) = histogram(2 buckets, num=5.0)
samples @ 4 (image) = image(64x64 png, 4321 bytes)
clip @ 5 (audio) = audio(wav, 88244 bytes)
logits @ 6 (tensor) = tensor(float32[2x3], 6 elements)
```

`--json` emits the exact stored value. `compare` adds per-kind flags
(`baseline_image`, `target_tensor`, …); the leaderboard excludes non-numeric
kinds.

## Migration and refusals

`migrate --to 0.11` is a version-only rewrite from 0.1–0.10; 0.1/0.2 run the
locator precheck. Downgrades are refused (`DOWNGRADE_UNSUPPORTED`), condition
comparability covers 0.2–0.11, and `init --force` refuses to regenerate a
declared manifest whose schema it cannot carry over. An event artifact can never
be marked `--volatile`.

For a schema 0.5 manifest, `rehash PATH ID --refresh-locators` verifies every
recorded TensorBoard scalar locator from one protected read and updates only the
artifact digest and size. Values must already match; record an intentional
change with `result edit` first. It accepts explicit exact IDs only and refuses
`--all`, volatile, directory, mixed-locator and schema 0.6+ targets.

## Deliberate limits

- TF2 `tf.summary.image`/`tf.summary.audio` are read under `--include-tf2-media` (0.10).
  TF2 `[k,3]` histogram tensors are recorded as structured histograms under
  `--include-tf2-histograms` (0.11), or as plain tensors without it when
  `--include-tensors` is on.
- Graph definitions, run metadata and plugin-specific payloads are not results.
- No arrays or media bytes are inlined; nothing is extracted.
- Exact comparison only; there is no tolerance, statistical or distributional
  comparison.

## Per-schema pages

- [Schema 0.5 scalars](schema-0.5-tensorboard.md)
- [Schema 0.6 strings](schema-0.6-tensorboard-strings.md)
- [Schema 0.7 histograms](schema-0.7-tensorboard-histograms.md)
- [Schema 0.8 media](schema-0.8-tensorboard-media.md)
- [Schema 0.9 tensors](schema-0.9-tensorboard-tensors.md)
- [Schema 0.10 TF2 media](schema-0.10-tf2-media.md)
- [Schema 0.11 TF2 histograms](schema-0.11-tf2-histograms.md)
- [Adapter guide](adapters.md)
