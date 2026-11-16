# Schema 0.9 TensorBoard tensor digests

Schema 0.9 is an explicit opt-in that adds **numeric TensorBoard tensors**
(`Summary.Value.tensor`) to the 0.8 media surface. Schemas 0.1–0.8, their bytes,
readers, checkers and output stay frozen.

## What gets recorded

A tensor the scalar and string readers do not record becomes a **canonical
digest**:

```json
{
  "kind": "tensor",
  "dtype": "float32",
  "shape": [2, 3],
  "element_count": 6,
  "sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
}
```

The digest covers a canonical little-endian row-major encoding, so a writer that
stores `tensor_content` and one that stores typed values produce the same digest
for the same array. Only `float32`, `float64`, `int32` and `int64` are recorded;
every other dtype is skipped. `int32` elements take the low 32 bits as two's
complement, so `-1` and `0xFFFFFFFF` agree. Typed floats hash their raw wire
bytes, so signalling NaNs and `-0.0` keep their exact bit patterns.

Shapes have 1–8 dimensions, each at most `2**31-1`, and at most 1,000,000
elements. `element_count` must equal the shape product; that rule is enforced by
the model because JSON Schema cannot express it. A non-empty `tensor_content`
wins over typed values; otherwise the list matching the dtype must supply every
element, and a non-empty list for another dtype fails the entry.

The locator is
`{"kind":"tensorboard-tensor","tag":…,"step":…,"occurrence":…}`. Occurrence is
per artifact and per kind; result ids append `NUL + "tensor"`, names use
`tag @ step (tensor)`.

## Extraction and verification

`init --include-tensors` is required and selects schema 0.9. It can be combined
with `--include-strings`, `--include-histograms`, `--include-images` and
`--include-audio`; tensors take precedence over every lower schema. `--tags`
filters tensor tags like any other kind.

`validate` re-reads the event file and re-canonicalizes the payload, comparing
`dtype`, `shape`, `element_count` and `sha256` exactly. Profile failures
(unsupported dtype, bad shape, count or length mismatch, foreign typed lists,
overflow) fail only that entry. Structural failures the frozen reader already
refuses — malformed keys, a wrong wire on `float_val`/`double_val`, unpacked
varint damage, packed float/double length damage — still refuse the whole file,
so a version-only migration never changes an existing check outcome.

Human surfaces show `tensor(float32[2x3], 6 elements)`; `--json` emits the value
object exactly. Compare rows gain `baseline_tensor`/`target_tensor`, and the
leaderboard excludes the kind as `non_numeric_or_nonfinite`.

## Migration and refusals

`migrate --to 0.9` is a version-only rewrite from 0.3–0.8; 0.1/0.2 keep the
locator precheck. `migrate --to 0.8` or lower from a 0.9 manifest is
`DOWNGRADE_UNSUPPORTED`, comparability refuses 0.9 by version, and `init
--force` refuses to regenerate a declared 0.9 manifest.

## Limits

- Digest-only: no arrays are inlined and nothing is extracted.
- No tolerance or statistical comparison; the digest is exact.
- String, bool, complex, half, variant, resource and sparse tensors are out of
  scope. Big-endian writers are out of scope.
- TF2 `[k,3]` histogram tensors are recorded as plain tensors; a future TF2
  histogram gate would decide whether to supersede them.
