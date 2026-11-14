# Schema 0.4 CSV cell locators (slice C)

Status: **slice C delivered (2026-11-14).** Schema 0.4 admits the `csv-cell`
locator and verifies one numeric CSV data cell per result against a strict
RFC 4180-derived profile. Schemas 0.1, 0.2 and 0.3 are unchanged; 0.3 remains
byte-frozen and still refuses `csv-cell`. The 0.4 schema is
documentation/file-only: `runevidence schema` still prints the 0.1 schema, and
no CLI route was added.

The design contract is
[docs/csv-locator-slice-c-rfc.md](csv-locator-slice-c-rfc.md) (v0.30). This
page summarizes the delivered behavior; the RFC remains normative for edge
cases.

## Manifest shape

Schema 0.4 keeps the 0.3 top-level shape: `schema_version: "0.4"`, `project`,
`runs`, `artifacts`, `results`, `evidence` and `relationships`. The only
version-specific change is the result locator union; `results` is a list of
`Result04`. A minimal manifest:

```json
{
  "schema_version": "0.4",
  "project": {"id": "demo", "name": "Demo"},
  "runs": [{"id": "train", "name": "training run"}],
  "artifacts": [
    {"id": "metrics", "path": "metrics.csv",
     "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
     "size_bytes": 42}
  ],
  "results": [
    {
      "id": "rmse",
      "run_id": "train",
      "name": "val_rmse",
      "value": 0.5,
      "artifact_id": "metrics",
      "locator": {"kind": "csv-cell", "row": 41, "column": 4, "header": "val_rmse"}
    }
  ],
  "evidence": []
}
```

All 0.2/0.3 fields remain admitted, including `run.conditions`,
`artifact.dataset_id` and `result.metric_definition`; 0.4 adds no other field
and keeps their existing rules.

## Locator union

Schema 0.4 is an explicit opt-in (`"schema_version": "0.4"`). A result accepts
the same locator forms as 0.3 plus `csv-cell`:

- a **string** JSON Pointer (empty, or `/`-prefixed with only `~0`/`~1`
  escapes);
- a **`jsonl`** object: `{"kind": "jsonl", "line": 41, "pointer": "/val_rmse"}`;
- a **`csv-cell`** object:

  ```json
  {"kind": "csv-cell", "row": 41, "column": 4, "header": "val_rmse"}
  ```

`kind` is a required constant. `row` and `column` are strict integers from 1
to `2**63 - 1`; JSON Schema draft 2020-12 also accepts `1.0` as an integer, but
the model rejects it, as it rejects `true` and strings. `header` is a strict
non-empty string of 1 to 65,536 code points; the parsed field is also checked
against a 65,536-UTF-8-byte runtime cap, and a lone surrogate is rejected at
model load (`string_unicode`). The object is closed
(`additionalProperties: false`).

Any non-null locator in 0.4 requires `artifact_id` (`if`/`then` in the schema,
and a model check). A `csv-cell` locator requires the artifact path to end in
`.csv`, case-insensitively. `kind` and extension must agree; a mismatch is
`locator-kind-mismatch`. Unknown or missing `kind`, a non-string/non-object
locator, a `row`/`column` outside the range, and an empty `header` are load
refusals (`schema-invalid`, exit 2), never a crash and never a silent
downgrade to a string locator. There is no `locator-unknown-kind` code.

The 0.4 JSON Schema is `schema/runevidence-0.4.schema.json` (draft 2020-12,
`$id` `urn:runevidence:manifest:0.4`, root title `Manifest04`, `$defs`
`Result04`, `JsonlLocator`, `CsvCellLocator`). Its `locator` union is a
hand-built `oneOf` (null / string / `JsonlLocator` / `CsvCellLocator`), not the
generated `anyOf`.

## Row, column and header rules

- `row` is one-based over **data records**; row 1 is the first record after the
  header. The header record is never selectable.
- `column` is one-based over the **parsed header**. A column beyond the header
  (or otherwise unselectable) is `locator-mismatch`.
- `header` is the exact parsed header assertion at that column. A mismatch is
  `locator-mismatch` with the fixed text `selected header does not match the
  header at this column`; the recorded or parsed header text is never echoed.
  A recorded `header` longer than the runtime 65,536-byte field cap can still
  load (the model limits code points), but it cannot match: it is a
  `locator-mismatch` when the parsed file header is within the field cap, and
  the structural `locator-unreadable` refusal applies first when the file
  header itself exceeds the field cap.
- Selection runs in the fixed order row → column → header. A row beyond the
  data is `was not found`; a column that cannot be selected is
  `was not selectable`.
- The file must have exactly one header record and at least one data record.
  The parsed header has 1 to 1,024 fields; every parsed header cell must be
  non-empty and unique. Empty files, header-only files, and empty or duplicate
  parsed headers are `locator-unreadable`.
- The header record is subject to the same 1 MiB record and 64 KiB field caps
  as data records.

## Strict profile

The verifier is strict on every platform: it never uses the stdlib `csv`
module and never splits on Unicode line boundaries.

- **Dialect:** comma only; no delimiter sniffing and no semicolon/TAB dialect.
  Records end at LF or CRLF; a final terminator adds no record and an
  unterminated final record is valid. A bare CR is refused anywhere, including
  inside quotes. A blank record at a record boundary is refused (so `""` is the
  empty cell, not an empty line). A quote in an unquoted field, an unclosed
  quote, and any byte after a closing quote other than comma/record-end/EOF are
  refused. `""` inside a quoted field is one `"`. Quoted fields may contain
  commas, LF and CRLF; a CRLF inside quotes is preserved as `\r\n`. Whitespace
  is data and is never trimmed. Ragged records are refused.
- **Encoding:** UTF-8 only. One leading BOM is stripped; an interior BOM is
  data; invalid UTF-8 and NUL anywhere are refused.
- **Numbers:** only a recorded integer or float is compared (booleans, strings,
  and null are `locator-unsupported-value`). The selected unescaped cell must
  match `-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?` or be `NaN`,
  `Infinity` or `-Infinity`. Leading `+`, `.5`, `1.`, underscores, hex,
  whitespace, thousands separators and empty cells are refused
  (`locator-unreadable`). Integer literals up to 512 digits are exact; overflow
  and nonzero underflow are refused. `int`/`int` compares exactly; mixed
  `int`/`float` uses the recorded-value tolerance (relative 1e-9, absolute
  1e-12); `NaN` equals `NaN`; infinities equal themselves.
- **Reading:** the file is read once through the slice-B protected snapshot.
  The implementation never materializes one full-file `str`, never retains a
  cell table, and decodes at most 1 MiB per record/field. Two passes cover the
  byte/UTF-8/structure pre-scan and the selected cells.
- **POSIX-first:** artifact path protection uses the slice-B
  component-by-component directory-descriptor walk. As with 0.3, no Windows or
  macOS parity is claimed for that race protection; the CSV profile itself is
  platform-independent.

### Caps

Per-artifact structural caps (a violation is a per-result
`locator-unreadable`):

| Cap | Unit | Value |
| --- | --- | --- |
| record | raw UTF-8 bytes after BOM strip, excluding the terminator | 1 MiB |
| field | unescaped cell value in UTF-8 bytes (CRLF in quotes counts 2) | 64 KiB |
| columns | parsed header fields | 1,024 |
| data rows | parsed data records | 1,000,000 |
| total cells | data rows × columns | 10,000,000 |

The file cap is 50 MiB per artifact (raw on-disk bytes; the shared slice-B
over-cap path). Structural caps are per artifact and are never summed across
artifacts.

Per-manifest eligibility caps (counted without reading a file):

| Cap | Unit | Value |
| --- | --- | --- |
| csv-cell results | manifest-eligible `csv-cell` entries | 10,000 |
| csv-cell header metadata | sum of `header` UTF-8 bytes over eligible entries | 16 MiB |

A manifest-eligible entry has a non-directory artifact path ending in `.csv`
(case-insensitive), a recorded `sha256`, and `volatile: false`; runtime file
states do not reduce the count. When either threshold is exceeded, each
eligible artifact gets **one** cap finding
(`locator-unreadable`, message `the manifest exceeds the csv-cell verification
limit (10,000 entries or 16 MiB of header metadata); no CSV cell was read`),
the artifact is marked broken, and no CSV cell is read or compared. Non-CSV
locators on the same `.csv` artifact still get their normal kind finding.
Artifacts in `missing`, `reference-failed`, `unreadable`, `changed`,
`symlinked-parent`, `refused-path`, `refused-alias` or `escapes-root` states
keep their ordinary artifact finding and get no cap finding.

## Outcomes

Reference and file states run first. A directory artifact runs its ordinary
artifact check (zero or more findings) and then gets one
`directory-locator-unsupported` per result; no kind, volatile/no-digest,
structural, selection or value finding follows. A missing file emits
`missing-file` and skips locator checks. A hash mismatch still compares the
value on the normal path; no-digest, volatile, over-cap, refused and capped
artifacts never compare a value.

| Condition | Code | Severity | Cardinality |
| --- | --- | --- | --- |
| Invalid 0.4 shape, unknown/missing kind, bad range/header/pointer, missing `artifact_id` | `schema-invalid` | exit 2 | per load issue |
| Dangling `artifact_id` | `dangling-reference` | error | per result |
| Directory artifact with a locator | `directory-locator-unsupported` | error | per result, plus any ordinary directory finding |
| Kind/extension mismatch | `locator-kind-mismatch` | error | per result |
| Missing file | `missing-file` | error | one per artifact |
| Unreadable/changed/symlinked-parent/refused-path/refused-alias/escapes-root/not-a-file | existing slice-B codes | error | one per artifact |
| Volatile artifact with a locator | `locator-unverified` | error | per result (no value compare) |
| Digestless artifact with a locator | `locator-no-digest` | error | per result; size still compared, no value compare |
| Digestless artifact with no usable locator remains | `no-digest` | warning | one per artifact |
| Over-cap file on the normal path | `locator-unreadable` | error | per result |
| Manifest exceeds the csv-cell eligibility caps | `locator-unreadable` (cap message) | error | one per eligible artifact; artifact broken |
| Strict-profile refusal (dialect, encoding, header, structural cap) | `locator-unreadable` (structural message) | error | per result |
| `hash-mismatch` / `size-mismatch` | existing codes | error | one per artifact; `size-mismatch` once per artifact |
| Row/column out of range or header mismatch | `locator-mismatch` | error | per result |
| Recorded `str`/`bool`/`null` for a cell | `locator-unsupported-value` | error | per result |
| Selected cell is not a number | `locator-unreadable` | error | per result |
| Selected value differs | `result-value-mismatch` | error | per result |
| Absent locator | `result-unverified` | warning | per result |
| Claim cites only results with no locator | `claim-unverified` | warning | per claim |
| Claim rests on a capped or broken artifact | `claim-at-risk` | error | per claim |

The capped/broken propagation reuses the delivered `claim-at-risk` text
("missing or changed"), which is imprecise for a resource cap; this is a
documented limitation. The structural finding's hint is
`the CSV artifact does not satisfy the strict csv-cell profile`; the cap
finding's hint is `None`.

## Display and privacy

A finding never contains cell text, parsed or recorded header text, raw row
text, OS `strerror`, or parser exception text. Permitted are the artifact path,
the `in 'metrics.csv'` context, digest prefixes on `hash-mismatch`,
`missing-file` path text, `report.to_json`'s manifest path, and recorded
manifest fields.

Human output renders a `csv-cell` locator as **`csv-cell:<row>,<column>`**:
numbers only, with no `@header` suffix. `run`, `claim` and `lineage` replace
each nested csv-cell locator (`results[i].locator`, each
`details.locator`) with that display form in a **copy**; the recorded data is
never mutated. Nested `jsonl` locators and 0.3 strings are left exactly as
before, so 0.3 human output is byte-identical.

`--json` keeps the structured locator through `locator_to_json`, including
`header`, for `runevidence run --json`, `claim --json` and `lineage --json`.
Diagnostics do not print the locator at all; no human route shows the header.

## Migration and compatibility

- `runevidence migrate PATH --to 0.4` is the supported way to opt a manifest
  into 0.4. `--to 0.2`, `--to 0.3` and `--to 0.4` are the supported targets.
- 0.1/0.2 → 0.4 runs the same manifest-only precheck as 0.2 → 0.3 (defensive
  object locator; missing `artifact_id`; invalid/prefixed pointer; directory,
  volatile or digestless artifact; non-`.json` string target). It refuses the
  whole migration before any backup or write with the fixed, value-free
  `LOCATOR_MIGRATION_UNSUPPORTED`; it never lists result IDs. A 0.2 string
  locator on `metrics.csv` is refused.
- 0.3 → 0.4 is a version-only bump for any schema-valid and serializable 0.3
  manifest; it skips the precheck and preserves values, locators and backups.
  A 0.3 string containing a lone surrogate cannot be serialized and is refused
  as `SERIALIZATION_FAILED` before any write.
- There is no downgrade. 0.4 → 0.3/0.2 is `DOWNGRADE_UNSUPPORTED`, and a 0.4
  target on an older reader is a load refusal (exit 2).
- `init --force` on a 0.4-capable build refuses an existing 0.4 manifest
  before adapter discovery, backup or save (`INIT_0_4_REFUSAL`); move the
  manifest aside, run `init`, then `migrate --to 0.4` and reapply the
  first-class values and locators. **Limitation:** a pre-0.4 binary does not
  know 0.4, so `init --force` from that build backs the manifest up and
  replaces it with 0.1. Keep 0.4 manifests away from older builds.
- 0.1/0.2/0.3 readers are otherwise unchanged. 0.3 still refuses `csv-cell`,
  and the base manifest model still rejects `0.4`; only the 0.4 dispatcher
  loads the new version.
- `validate`, `inspect`, `validate-batch` and the other manifest-reading
  routes load 0.4 through the one dispatcher. `fingerprint`/`fingerprint-compare`
  keep fingerprint v1's 0.1-only guard; `comparability` refuses 0.3/0.4 with
  `UNSUPPORTED_VERSION`; `conditions` remains 0.2-only; `rehash` refuses any
  csv-cell and any object locator with `LOCATOR_REFRESH_UNSUPPORTED` (a
  manifest with no object locator still rehashes normally); `add` accepts a
  0.4 manifest.
- The `schema` CLI route is unchanged (it prints 0.1). The 0.4 schema is
  available as a file and through `json_schema_0_4()`.

## Not implemented

No aggregates, delimiter sniffing, semicolon/TAB/other dialects, header-only
or multi-file tables, quoted-field rewriting, TensorBoard/other adapters, YAML,
Windows/macOS parity, or new batch feature. The csv-cell eligibility limits
are not a general manifest-size or result-count cap. Real writers' `nan`,
`inf`, `-inf` and empty cells cannot be verified by this numeric-only profile.
