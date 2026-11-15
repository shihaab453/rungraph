# Schema 0.6 TensorBoard string tensors

Schema 0.6 is an explicit opt-in that adds **string tensors** to the 0.5
TensorBoard locator surface. Schema 0.5, its schema bytes, reader behavior and
checker behavior stay frozen. A 0.5 binary rejects a 0.6 manifest; a 0.6 binary
accepts 0.5 unchanged.

## What gets recorded

A `Summary.Value` whose winning member is `tensor` with `dtype == DT_STRING`
(7), a scalar shape (rank 0 or rank 1 `[1]`), exactly one `string_val` and no
typed value field. The string must be valid UTF-8, at most 64 KiB, and can be
empty. Plugin metadata is ignored; the kind is a string tensor, not "the text
plugin". Tables, `tensor_content`, multiple values and invalid UTF-8 are
skipped.

```json
{
  "id": "tb-res-…",
  "run_id": "tb-run-…",
  "name": "config @ 0 (text)",
  "value": "lr=3e-4\nbatch=64",
  "artifact_id": "tb-art-…",
  "locator": {"kind": "tensorboard-string", "tag": "config", "step": 0, "occurrence": 0}
}
```

## Identity

- One run per directory (the 0.5/multifile rule).
- Occurrence is per artifact and per kind:
  `(artifact_id, kind, tag, step)`.
- Text result id = the multifile 0.5 payload plus `NUL + "text"`; scalar ids are
  unchanged.
- Text name = `tag @ step (text)`, plus ` #occurrence` for repeats. A group
  spanning files adds `[basename] #<16-hex digest>`; the digest includes
  `"text"`.
- A scalar and a string may share a tag and step.

## Extraction and budgets

`init --include-strings` is required. Without it, the adapter writes schema 0.5
and ignores string entries; existing output is byte-identical.

With the flag the adapter writes schema 0.6, and `--tags` matches string tags
too. Budgets: 10,000 results shared, 16 MiB of tag bytes, 16 MiB of selected
string value bytes, 64 KiB per value. The 128 MiB event-file and 16 MiB summary
caps are unchanged.

## Verification and display

`validate` re-reads the event file and compares the recorded string exactly: no
tolerance, normalization, case folding or newline handling. Findings never echo
the text. Human display is `tensorboard-string:step=<step>,occurrence=<n>`;
compare/matrix/run truncate a displayed text value to 120 code points, while
claim/lineage keep their existing 256-character rule. `--json` is exact.

## Migration

`migrate --to 0.6` is the identity on 0.5. There is no downgrade. `init --force`
refuses an existing 0.6 manifest with `INIT_0_6_REFUSAL`; move it aside and run
`init --include-strings` instead. `comparability`, `fingerprint` and
`conditions` refuse 0.6. `add` accepts 0.6; `rehash` keeps the delivered
object-locator rule, so a string-only (JSON-pointer) 0.6 manifest is rehashable
and a text locator is not.

See [the design RFC](tensorboard-string-rfc.md) for the full contract.
