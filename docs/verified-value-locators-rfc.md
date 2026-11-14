# Verified value locators: design RFC (v0.8)

Status: **v0.8 ERRATA FOR SLICE C.** Slice B is delivered (2026-11-13): JSONL object locators and
strict whole-document JSON verification for 0.3 string locators. This revision carries the
`csv-cell`/schema 0.4 errata accepted with the slice C design; the CSV verifier is a separate
implementation gate. TensorBoard and other adapters remain later slices. No Windows/macOS parity,
no R5, no release.
Answers the design gate requested by `docs/tool-adapters-csv-tensorboard-decision-memo.md`
v0.3. Acceptance does not authorize implementation; a separate implementation gate is
required.

## 1. Problem and evidence

`Result.locator` is an optional string documented as an RFC 6901 JSON Pointer into a
`.json` artifact. The checks use a case-insensitive suffix test and treat any other
artifact as `result-unverifiable` (a warning; `validate` exits `0` unless `--strict`, when
it exits `1`). A result with no locator is skipped silently.

Gaps: a number copied from JSONL/CSV cannot be tied to a place; a locator-shaped string on
a non-JSON artifact can look verified; G11
(`docs/dogfood-mlp-sweep-20261025.md`) records a request for JSONL/CSV locators for
per-epoch histories. G11 supports locator work, not a preference for JSONL over CSV.

## 2. Scope and non-goals

In scope: a structured locator union in an opt-in schema; the `jsonl` kind implemented
first; the `csv-cell` kind specified but admitted only with its verifier; verification
semantics, outcomes, privacy, limits, compatibility and gates.

Out of scope: TensorBoard (separate RFC), computed/aggregate values, YAML manifests,
changing 0.1/0.2 or their readers, new CLI routes or flags.

## 3. Manifest model

### 3.1 Version and union

`locator` becomes a union in an opt-in schema `0.3`:

- a **string** is a JSON Pointer (legacy form). In 0.3 it must be empty or start with `/`
  and use only `~0`/`~1` escapes (a `~` followed by anything else is invalid), so prefixed
  strings such as `jsonl:...` fail to load;
- an **object** is discriminated on `kind`.

0.3 admits **only** `jsonl`:

```json
{"kind": "jsonl", "line": 41, "pointer": "/val_rmse"}
```

`csv-cell` (§5.2) does not enter a published schema until its verifier ships. It will
enter the schema version that ships that verifier (not an amendment to 0.3), with:

```json
{"kind": "csv-cell", "row": 41, "column": 4, "header": "val_rmse"}
```

Rules:

- `locator` stays optional. An omitted locator and explicit `null` both mean "no locator".
- In 0.3 **any non-null locator requires `artifact_id`** (string or object); JSON Schema
  uses `if`/`then` for it. `result-unverifiable` is **never emitted under 0.3**.
- An absent locator yields `result-unverified` (warning, §4.3) in 0.3 and 0.4.
- The locator carries no file path. Bytes come only from `Result.artifact_id`.
- `kind` must match the artifact extension, case-insensitively: `jsonl` on `.jsonl`,
  `csv-cell` on `.csv`. `.ndjson` and `.tsv` are not admitted. A mismatch is
  `locator-kind-mismatch` (error), checked at validation.
- An unknown `kind` is a **load-time schema refusal** (`schema-invalid`, exit 2).
- An object locator in a 0.1/0.2 manifest is a schema error.
- 0.3 string locators must target `.json`; a string on any other artifact is
  `locator-kind-mismatch`, not a warning.

### 3.2 Index types and bases

- `line`, `row`, `column`: **all one-based**, spreadsheet-style. `line: 1` is the first
  physical line; `row: 1` is the first parsed data record after the header; `column: 1` is
  the first field.
- Types: `int` only, no boolean/string coercion, `>= 1`, `<= 2**63 - 1`.
- The header is never a selectable row.
- JSON Schema uses `type: integer`, `minimum: 1`, `maximum: 9223372036854775807`,
  `additionalProperties: false`, `oneOf` with `const` on `kind`, and `if`/`then` for
  `artifact_id`. **Schema/runtime difference:** draft 2020-12 accepts `1.0` as an integer;
  the runtime model uses `StrictInt`, which rejects `1.0` and `true`. The gate therefore
  expects schema-accept + model-reject for `1.0`, and rejection by both for `true`, `0`,
  `-1`, `2**63`. This distinction is documented, not treated as parity.

### 3.3 Shape vs reference checks

- **Load (schema):** union shape, `kind` membership, integer type/range, pointer grammar
  (empty allowed; `/` prefix; only `~0`/`~1`), `artifact_id` presence for any non-null
  locator.
- **Validate (needs the manifest):** `artifact_id` exists and resolves (else
  `dangling-reference`), is a file artifact (a directory artifact uses
  `directory-locator-unsupported`; `not-a-file` as today), extension matches, artifact is
  readable, locator resolves.
- Reference failures are errors, before content checks, and add the result to the broken
  set (§4.3).

## 4. Verification semantics

### 4.1 Snapshot safety and scope

Only artifacts targeted by a 0.3 or 0.4 locator (string, `jsonl` or `csv-cell`) get a retained
in-memory snapshot. Every other artifact keeps today's streaming hash; 0.1/0.2 behavior is unchanged.
For a targeted artifact, the **file check and the locator checks share one snapshot**; the
file check does not hash the artifact a second time.

The snapshot is built with a protected open:

1. Resolve the artifact path under the existing loader rules (a symlink alias to the file
   is permitted).
2. Open with `O_RDONLY|O_NOFOLLOW|O_CLOEXEC|O_NONBLOCK|O_NOCTTY`, refusing special files
   without blocking. This applies to the **initial hash too**, so a regular-file-to-FIFO
   swap cannot block validation before the snapshot exists.
3. `fstat` the open descriptor, require a regular file, and compare its `(st_dev, st_ino)`
   with the `lstat` of the path immediately before opening and again **after the read
   completes**. Open failures map as: `ENOENT` → `missing-file`, `EACCES`/`EPERM` →
   `locator-unreadable`, `ELOOP` or another identity mismatch → `artifact-changed`. If the
   open succeeds but `fstat` reports a non-regular file → `artifact-changed` (the reference
   check already confirmed a file); any other errno (`ENXIO`, `EIO`, `EMFILE`, ...) →
   `locator-unreadable`.
4. Read bounded bytes, hash them, and parse the same bytes. Aggregate memory is one
   artifact snapshot at a time (released after its locators are checked), capped at 50 MiB.
   **Over the cap:** the file check still streams the hash through the same protected open
   (integrity checking is never dropped by adding a locator), while each locator on the normal
   path emits `locator-unreadable`; a csv-cell-capped artifact (eligible csv-cell entries in a
   capped manifest) emits one cap finding instead, one per eligible artifact (including an
   over-cap artifact).

Verification means: the recorded digest equals the digest of the bytes parsed. The identity
checks detect a path swap before opening and a replacement during the read; they do not
prove the file stayed unchanged after the read. No cross-run cache.

### 4.2 Ordering and stale/race handling

For a targeted artifact, one snapshot serves both checks:

1. Reference checks (missing file, dangling ID, directory artifact, not-a-file,
   `locator-kind-mismatch`). If the file is missing, the file check emits `missing-file` and
   locator checks are skipped.
2. Missing digest or volatile artifact (`sha256` ignored) is decided **before** any digest
   comparison: no recorded bytes exist to compare. Each located result emits
   `locator-no-digest` (missing) or `locator-unverified` (volatile); values are not
   compared. For a targeted artifact the existing artifact-level `no-digest` warning is
   **suppressed** because the result-level finding is kind-aware and authoritative: the hint
   says that `rehash` can record a missing digest when only string locators target the
   artifact, and cannot when an object locator does. The artifact-level `no-digest` warning is
   restored only when no usable locator remains and the file state is `no-digest` (not
   volatile).
3. Protected snapshot; identity before open and after read. A change is
   `artifact-changed`; the value is not compared.
4. Snapshot digest vs recorded digest: different → the file check reports `hash-mismatch`.
   The value is still compared (preserving the existing JSON behavior), except on a
   csv-cell-capped artifact, where no value is compared; the result is **never called
   verified**.
5. Snapshot digest equals the recorded digest → normal verification.

Because the snapshot is shared, there is no second read and no digest disagreement between
the file check and the locator check. `artifact-changed` is the only race signal.

### 4.3 Outcomes and propagation

Exit precedence: a manifest/load/schema failure is exit `2` and wins. Otherwise any error
finding is exit `1`; warnings only are exit `0` (`1` with `--strict`).

| Condition | Code | Severity | Exit |
| --- | --- | --- | --- |
| Artifact file missing | `missing-file` (existing) | error | 1 |
| Recorded bytes differ | `hash-mismatch` (existing) | error | 1 |
| Recorded size differs on a nonvolatile artifact with a matching digest (or no recorded digest) | `size-mismatch` (existing) | error | 1 |
| Locator kind/extension mismatch | `locator-kind-mismatch` (new) | error | 1 |
| Line/row/column out of range | `locator-mismatch` (new) | error | 1 |
| Header assertion differs | `locator-mismatch` (new) | error | 1 |
| Pointer not found / malformed at run time | `locator-mismatch` (new) | error | 1 |
| Profile refusal (encoding, dialect, caps, parse) | `locator-unreadable` (new) | error | 1 |
| Unsupported target type / non-scalar | `locator-unsupported-value` (new) | error | 1 |
| Snapshot identity changed | `artifact-changed` (new) | error | 1 |
| Targeted artifact has no digest | `locator-no-digest` (new) | error | 1 |
| Volatile artifact with a locator | `locator-unverified` (new) | error | 1 |
| Absent locator | `result-unverified` (new) | warning | 0 / 1 strict |
| Claim supported only by unverified results | `claim-unverified` (new) | warning | 0 / 1 strict |
| Resolved scalar differs | `result-value-mismatch` (existing) | error | 1 |
| Dangling artifact reference | `dangling-reference` (existing) | error | 1 |
| Directory artifact locator | `directory-locator-unsupported` (existing) | error | 1 |
| Artifact is not a file | `not-a-file` (existing) | error | 1 |
| Unknown kind | `schema-invalid` (existing) | load refusal | 2 |
| Kind declared but no verifier | `locator-verifier-unavailable` (new) | load refusal | 2 (defensive; unreachable by construction) |
| Invalid manifest / command cannot run | existing | n/a | 2 |

A csv-cell-capped artifact emits at most one cap finding, alongside any hash/size or kind
finding; no value is compared. `size-mismatch` is emitted once per artifact.

Broken-set membership (drives claim risk through existing graph propagation):
`missing-file`, `hash-mismatch`, `locator-kind-mismatch`, `locator-mismatch`,
`locator-unreadable`, `locator-unsupported-value`, `artifact-changed`,
`locator-no-digest`, `locator-unverified`, `result-value-mismatch`, `dangling-reference`,
`directory-locator-unsupported`, `not-a-file`, and `size-mismatch`. A failed locator reference therefore
marks the result broken even when the artifact itself is only warned about.

`result-unverified` does **not** add the result to the broken set; a claim supported only
by unverified results gets the `claim-unverified` warning (no exit change unless
`--strict`).

## 5. Strict profiles

### 5.1 `jsonl`

- File-wide refusals: invalid UTF-8, NUL, a bare CR not immediately before LF, any line
  over 1 MiB, file over 50 MiB. One UTF-8 BOM at the very start of the file is stripped;
  a BOM anywhere else is data (so a BOM before line 1's JSON does not make it unparsable).
- Split on the LF byte only. A single trailing `\r` before LF is stripped; other bytes are
  data. Do not use `str.splitlines()`.
- Physical line numbers count every line, including blanks. A trailing final newline
  terminates the last line; it does not add a record. A second newline creates a counted,
  **never selectable** blank line; selecting it (including a whitespace-only line) is
  `locator-mismatch`.
- Selected-line rules: the selected line must be one JSON value; other malformed lines do
  not fail the check. An unterminated final line is still selectable. The 64-level nesting
  check is applied to the **selected line** before `json.loads` (deep input must not raise
  `RecursionError`).
- Duplicate keys are rejected at every depth (`object_pairs_hook`). `NaN`, `Infinity` and
  `-Infinity` are accepted (training logs write them; the manifest loader accepts the same
  tokens at `results[*].value`) and compared by a documented rule: NaN equals NaN, infinities
  compare to themselves, booleans never equal numbers. A numeric literal that overflows to
  infinity (`1e999`) or underflows from a nonzero significand is refused.
- Supported values: every JSON scalar: string, number, boolean, null. Arrays/objects are
  `locator-unsupported-value` when targeted.

### 5.2 `csv-cell` (verifier ships with a later schema)

- Byte pre-scan before any CSV parsing; the stdlib CSV module alone is not sufficient
  (it accepts a lone CR, a quote inside an unquoted field, NUL, and does not strip a BOM,
  while `field_size_limit` is process-global and must not be changed).
- UTF-8, one optional leading BOM; CRLF and LF accepted; bare CR, NUL and invalid UTF-8
  refused; comma delimiter only (never sniffed); RFC 4180 quoting with `""`; a quote in an
  unquoted field refused.
- Exactly one header row; empty or duplicate headers refused. `header` is required and must
  equal the parsed header at `column`.
- Rows are parsed records, not physical lines. Blank/ragged records and a header-only file
  are refused. The whole file must satisfy the profile, including records after the
  selected row. Whitespace is data.
- First profile: numeric recorded values only. The cell must match the number grammar
  (§5.3) or the closed non-finite tokens `NaN`/`Infinity`/`-Infinity`. A recorded string,
  boolean or null is `locator-unsupported-value`; numeric `1` never equals `"001"`.
- Caps: file 50 MiB per artifact; record 1 MiB, field 64 KiB, rows 1,000,000, columns 1,024 and
  total cells 10,000,000 per artifact, never summed across artifacts; csv-cell locator results
  10,000 and csv-cell header metadata 16 MiB per manifest, counting only manifest-eligible
  entries (non-directory `.csv` artifact with a recorded sha256, not volatile), without reading
  a file. Lengths are bytes of decoded UTF-8; the file cap uses raw on-disk bytes (a leading BOM
  counts) and the record cap uses raw record bytes after BOM strip, excluding the terminator.

### 5.3 JSON (0.3 string locators)

A 0.3 string locator targets a `.json` artifact and applies the selected-line rules of
§5.1 to the **whole document**: UTF-8 with one optional BOM, NUL and bare CR refused,
duplicate keys rejected, `NaN`/`Infinity`/`-Infinity` accepted, overflowing literals
refused, 512-digit integer literals, 64-level nesting pre-checked, file cap 50 MiB. This is
stricter than 0.1/0.2 JSON parsing, which stays unchanged.

### 5.4 Numbers and comparison

- Grammar (regex): `-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?`, plus the closed
  non-finite tokens above. `1_000`, `+1`, `.5`, `1.`, decimal commas and empty cells are
  refused.
- An integer literal (no fraction/exponent) within 512 digits is parsed as an exact
  Python `int`; no string coercion. Larger integer literals are refused. A
  fraction/exponent literal becomes a float; overflow to infinity or underflow from a
  nonzero significand is refused. Python's own 4300-digit conversion limit is mapped to
  `locator-unreadable`, never a crash.
- **Comparator:** 0.3 and 0.4 locators (string, `jsonl` and `csv-cell`) use a new comparator: int-to-int is
  exact; mixed int/float uses `values_match` (`rel_tol=1e-9`, `abs_tol=1e-12`) and is
  documented as approximate; booleans compare as booleans (`true` never equals `1`); NaN
  equals NaN; null equals null. 0.1/0.2 keep the existing `values_match` unchanged, even
  though it approximates adjacent large integers.

## 6. Canonical form and display

Fingerprint v1 is frozen: it accepts schema **0.1 only** and refuses both 0.2 and 0.3. A
0.1 manifest's fingerprint must remain unchanged. Cross-version fingerprint equality for a
string-only 0.3 migration is **not** a v1 requirement; it becomes a requirement on a future
fingerprint v2, which is a separate design. Canonical object form (for future use): UTF-8
JSON, sorted keys, no insignificant whitespace, decimal integers, no floats, no Unicode
normalization, JSON escaping only.

Human rendering is display-only and never fingerprint input: canonical JSON for machine
output; compact escaped text for humans (`jsonl:41#/val_rmse`; empty pointer rendered
`jsonl:41#`; `csv-cell:41,4`). Pointer components escape `@`, `,`, `#`, control characters and
backslash; the csv-cell display is numbers only, and length is bounded.

## 7. Privacy and output safety

- Verification diagnostics never expose newly read source values, raw exception text or
  parser messages. They name the artifact-relative path, the escaped locator, the fixed code,
  and - for `jsonl`/string locators - the recorded assertion (pointer); a `csv-cell` finding
  never names the header. `OSError`/`UnicodeDecodeError` text is never echoed.
- Recorded `Result.value`, header assertions and normal reports intentionally contain
  recorded data. A product-wide "no values" rule is not claimed.
- The existing JSON mismatch message prints recorded and actual values. It stays as a
  **documented legacy exception** (README) for 0.1/0.2; 0.3 string/object locators use the
  no-values diagnostics above.
- Spreadsheet-formula neutralization (`= + - @`) applies to report/CSV writers, not
  terminal output. Terminal output escapes control sequences.

## 8. Compatibility and migration

- `schema/runevidence-0.3.schema.json` new; 0.1/0.2 untouched.
- `models.py` version gate/union; `manifest.py` serialization and round-trip; `findings.py`
  codes (`locator-kind-mismatch`, `locator-mismatch`, `locator-unreadable`,
  `locator-unsupported-value`, `locator-unverified`, `locator-no-digest`,
  `locator-verifier-unavailable`, `artifact-changed`,
  `result-unverified`, `claim-unverified`, `locator-refresh-unsupported`, which uses
  the existing edit-refusal path and exits `2`);
  `checks/results.py` dispatch and profiles; `checks/files.py` protected snapshot and
  observed-digest handoff; `checks/__init__.py` ordering, broken-set and claim propagation;
  `run_fingerprint.py` v1 refusals; `edit.py` transactional refusal; `run_details.py`
  rendering; `run_claim.py`, `run_lineage.py` locator-valued JSON output;
  `directory_audit.py`; `inspect`; `directory_authoring`/`directory_publication` wording;
  `migrate`; README/docs/exit tables.
- Omitted `schema_version` is 0.1. New readers read 0.1/0.2 without rewriting them; old
  readers reject 0.3.
- **Migration 0.2→0.3 is pre-checked and transactional.** A valid 0.2 manifest can contain
  locators that 0.3 rejects or that cannot be verified: prefixed/invalid pointer strings,
  strings on non-`.json` artifacts, locators without `artifact_id`, locators on volatile
  artifacts, locators on artifacts without a digest, and locators on **directory
  artifacts** (a 0.2 warning becomes a 0.3 error regardless of the flag). Any of these
  refuses the **whole** migration before writing. Migration never lists result IDs. **If a
  downgrade path is added**, it must refuse object locators.
- **Rehash:** any selected artifact/result with an object locator refuses the whole rehash
  before writing (`locator-refresh-unsupported`). No escape hatch in the first experimental
  0.3. String locators keep the existing re-read behavior; only object locators are refused,
  because their refresh semantics are new. A targeted artifact without a digest can be
  fixed by `rehash` when **only string locators** target it (rehash records the digest);
  when an **object locator** targets it, `rehash` refuses, and the fix is a manually
  recorded digest or regenerating the manifest; the `locator-no-digest` hint says which.
  For 0.3 string locators this re-read can write a value that the stricter 0.3 profile
  later refuses (duplicate keys, overflowing literals); `validate` catches that afterward,
  so nothing passes silently.
- **Directory artifacts:** in 0.3 a locator on a directory artifact is
  `directory-locator-unsupported` regardless of the public `--directory-artifacts` flag.
  0.1/0.2 keep the existing flag-gated behavior.
- **Sequencing:** 0.3 follows stabilization of the experimental 0.2 comparability work;
  this RFC does not schedule the schema file ahead of that.

## 9. Gates before implementation is authorized

1. Schema/model matrix: object in 0.2 rejected; unknown kind load-refused; pointer grammar
   (empty, `~0`/`~1`, `~2` rejected, `/` prefix); integer schema-vs-model behavior
   (`1.0` schema-accepted but model-rejected; `true`/`0`/`-1`/`2**63` rejected);
   `artifact_id` required for any non-null 0.3 locator; extension matching
   (`.json`/`.jsonl`/`.csv`; `.ndjson`/`.tsv` refused); directory artifact behavior with the flag
   on and off; `not-a-file`.
2. Outcome table: every row in §4.3, including stale+malformed, missing digest, volatile,
   absent locator (0.3 and 0.4) vs legacy 0.1/0.2 `result-unverifiable`, unsupported verifier
   (fault-injected; unreachable by design), broken-set membership, claim propagation, exit
   precedence, and over-cap targeted artifacts.
3. Profiles: lone CR, bare quote, BOM in a CSV header, JSONL leading BOM (stripped) and a
   mid-file BOM (data), trailing blank record, U+2028, `1e999`, underflow, deep nesting
   (selected line and `.json`), duplicate JSON keys, `Infinity`/`NaN`, mixed int/float,
   512-digit integer, 4300-digit limit, caps.
4. Snapshot: protected open for the initial hash, descriptor identity, path/FIFO swap
   before and during the read, containment, one snapshot per targeted artifact, streaming
   for others, aggregate memory, an explicit 60 MiB over-cap fixture (hash still checked,
   locators `locator-unreadable` on the normal path; a csv-cell-capped artifact (eligible
   csv-cell entries in a capped manifest) emits one cap finding, one per eligible artifact
   (including an over-cap artifact)), no cross-run cache.
5. Privacy: canaries for values, headers, pointer, absolute path, OS text; formula/terminal
   injection; legacy JSON echo documented.
6. Version matrix: 0.1 fingerprints unchanged; v1 refuses 0.2 and 0.3; 0.1/0.2 checks
   unchanged (warnings, `--strict`, exits); 0.3 reads; old readers reject 0.3; migrations
   pre-checked and transactional; rehash refuses.
7. Packaging: full suite, skip accounting, installed wheel/sdist, no new core dependency
   (jsonl uses the standard library).

## 10. Open questions

1. Is `jsonl` still the right first kind after the profile work above?
2. `csv-cell` waits for a schema that ships its verifier (answered; confirm).
3. Exact integer and approximate mixed comparison (answered; confirm).
4. Rehash refusal with no escape hatch (answered for 0.3; confirm).
5. Should the legacy JSON value-echo be removed in a later amendment, or kept indefinitely
   as documented?
