# CSV cell locators (`csv-cell`) — slice C design RFC (v0.31)

Status: **DESIGN PROPOSAL, REVISED AFTER FIFTEEN DESIGN PANEL ROUNDS AND A FINAL CONSISTENCY PASS. NOTHING IMPLEMENTED.
NOTHING AUTHORIZED BY THIS DOCUMENT.** Acceptance authorizes only a separate implementation gate
and the owner's schema-admission decision.

## 0. Changes in v0.31 (errata text aligned)

- **Parent reissued**: `docs/verified-value-locators-rfc.md` is now v0.8
  (`090cb77c5bea6f8f635a64dca6abfd249f4357389eab07c8e969e0e42f69c85f`); the pre-errata v0.7 was
  `f04ecfc8…`. Its over-cap sentences now match §9.4/§9.5 verbatim.
- **Parent version corrected**: the parent contract (v0.7) is reissued as **v0.8**, not the
  slice-C revision number; every reference now says v0.8.
- **The parent §5.2 bullet is quoted** (`Caps: file 50 MiB, …`) and replaced wholesale, keeping `file 50 MiB per artifact` and the units line, while making only the two
  eligibility caps per manifest.
- **Hint rules are pinned per finding**: reused `locator-no-digest`/`locator-unverified`/
  over-cap findings keep their delivered hints; the structural finding uses the fixed hint;
  `directory-locator-unsupported` uses the §10 0.4 hint for a csv-cell result (0.3 keeps the
  delivered text); the cap finding's hint is `None`; every other csv-cell-specific finding has
  hint `None`.
- **§9.4 quotes the remaining parent edits** (unknown-kind row/codes and the migration
  sentence); §9.2 carries the size row and the broken-set addition.
- **§9.2 inserts after the `Recorded bytes differ` row** and states that `size-mismatch` is one
  per artifact; the docs-test clause covers §9.2-§9.5.
- **Round-15 delta**: the lone-surrogate writer claim is narrowed to `migrate`
  (`_serialize_candidate`) and the `add`/`rehash` `UnicodeEncodeError` is recorded as a known
  pre-existing defect; §9.5 quotes the remaining parent errata with the §4.2 step-4 label and the
  §9 gate-4 over-cap text; the broken-set edit is one exact sentence in §9.2; §9.1/§16/D12 list
  every authorized edit, including the row widening.
- **§0.1-§0.3 retain** the round-8 to round-11 fixes (defaulted field and routing gates, caps
  split, fixture split, message/hint set, error-aware walker, `Manifest04.results`,
  tag-before-branch order, serialization/surrogate rules, budget and benchmark scope).

## 0.1 Changes retained from v0.12 (round-8 to round-11)

- **Locator default**: the 0.4 field is `… BeforeValidator(...) ] = None`; without the default a
  locator-less result fails to load. Absent-key, explicit-null and 0.3→0.4 locator-less vectors
  are pinned.
- **0.4 routing**: the four `checks/__init__.py` gates (`targeted_ids`, `targeted_result_ids`,
  `dangling_targeted`, `unverified`) must accept 0.4, or a wrong csv-cell value degrades to
  `result-unverifiable`/`locator-unreadable`; one test asserts it is an error.
- **JSONL offset pre-scan** is built only from `JsonlLocator`; a csv-cell routed into it raises
  `AttributeError`, so `check_targeted_results` is in the inventory with a one-csv-cell test.
- **`CsvCellLocator(_Strict)`** is pinned; a plain `BaseModel` breaks `_presence_exclude`.
- **The 0.4 locator schema is a hand-built `oneOf`** (the generated field is `anyOf`).
- **Claim/lineage locators are nested** at `row["details"]["locator"]` and
  `node["details"]["locator"]`; the display copy walks those, not a top-level key.
- **Parent §4.3 `size-mismatch` row erratum** added.
- **Wording**: “state” always means `TargetCheck.state` (hash/size findings do not change it);
  data bytes exclude NUL, bare CR and the terminator (so LF is not “data”); `1e999`, a 513-digit
  integer and `001` are in the refusal list.
- **Batch**: no new batch feature; existing batch readers accept 0.4 only through the dispatcher.

## 0.2 Changes retained from v0.10 (round-9 review findings)

- **Error-aware walker**: `_issue_location(loc, *, root_model=None, error_type="")` resolves the
  root at call time, keeps the delivered one-argument test calls, and appends `/kind` only for
  `union_tag_invalid` / `union_tag_not_found`. The raw `loc` for those two errors is identical to a `value_error`, so
  the error type is required.
- **Installed-wheel smoke uses `validate`**, not a nonexistent `check` route.
- **Real object-locator predicates named**: `type(locator) is not str` (`edit.py`,
  `cli.py`), `_is_object_locator`/`object_anywhere` (`checks/results.py`), `migrate.py`’s
  locator test; there is no `has_object_locator`.
- **`Result04` overrides the inherited `_locator_scalar_shape`**, and the dispatcher accepts
  already-built `JsonlLocator`/`CsvCellLocator` instances (`Result04` accepts both instance kinds; the 0.3 `Result` already accepts a `JsonlLocator` and
  keeps rejecting a `CsvCellLocator`).
- **One module-level `TypeAdapter`** (building it per call costs 58-291 µs per locator; a
  500k-entry manifest would add 29-145 s of load time).
- **Budget asserts the raw `VmHWM − baseline`**; the finding-footprint subtraction is reported
  as information only, and the benchmark is Linux-only (`/proc/self/clear_refs`) with explicit
  skip accounting.
- **`hash-mismatch` and `size-mismatch` are never both emitted**: a digest mismatch carries a
  size note; a size-only mismatch is `size-mismatch`.
- **§8 gains the restored artifact `no-digest` warning row**, and the structural, header-mismatch
  and D2 messages are pinned as full renderings of the shared
  `result {id!r} locator {display} … in {path!r}` form.
- **Vectors**: `1e-400` moves to the refusal list; an interior-BOM accept vector is added; the
  wrong-digest directory fixture pins an unchanged total size; NUL is named in §13.
- **§4.2 erratum** also states that a nonvolatile digestless artifact still compares size.
- **0.4 schema `$defs`/title named**: root `Manifest04`, `Result04`, `JsonlLocator`,
  `CsvCellLocator`.
- **Implementation brief** refreshed (parent v0.8, manifest-eligible, hashing still happens on
  the capped path).

## 0.3 Changes retained from v0.9 (round-8 findings)

- **Locator storage fixed**: `Result04.locator` is a field-typed
  `Annotated[Optional[Union[StrictStr, JsonlLocator, CsvCellLocator]], BeforeValidator(...)]`,
  not a `RootModel` wrapper. The wrapper broke `_presence_exclude`, `edit.py`, `results.py` and
  `display_locator`; the field-typed form reproduces all five pinned vectors and stores the bare
  value. `Result04` is named here, and every consumer (`display_locator`, `_is_object_locator`,
  `_extension_matches`, `_check_one_targeted`) is listed.
- **Five, not four, exact error lists** are pinned; the walker signature and its callers are
  defined, and the string grammar reuses `is_valid_locator_pointer`.
- **Lone surrogate** is rejected by an explicit `PydanticCustomError("string_unicode", ...)`;
  `StrictStr` alone accepts it in Python-mode validation (measured).
- **Directory artifacts** produce **zero or more** ordinary findings (matching directory with the
  flag produces none), then one `directory-locator-unsupported` per result.
- **No-digest/volatile/over-cap return before selection and comparison**: `no-digest` still
  compares size (so `size-mismatch` may appear) but never compares a value; volatile compares
  neither.
- **Normal-path size-mismatch** is pinned: a correct digest with a wrong `size_bytes` still
  parses and compares, and a differing value adds `result-value-mismatch`.
- **Budget**: the ≤300 MB assertion covers only vectors whose finding count is bounded by the
  10,000-entry cap; cap-finding and 500k digestless/volatile vectors are record-only and report
  RSS plus finding count.
- **§11 and §13 are self-contained**: the dialect, oracle, fuzz, packaging and departures
  requirements are written out instead of pointing at an unreachable v0.7.
- **Parent errata** now include §4.2 step 4 (a capped artifact does not compare values on
  `hash-mismatch`).
- **Texts and vectors**: header-mismatch, cap/structural/D2 and 0.4 CLI texts pinned; benchmark
  (a) outcome stated; 0.4 schema has three description overrides; refused-path cap spellings
  disambiguated; advisory path warnings named; benchmark reference box named.

## 1. Problem and evidence

- Slice B delivered string pointers and `jsonl` locators under 0.3.
- CSV is the most common tabular training log; the adapters memo records option B, and G11 asks
  for JSONL/CSV locators.
- CSV cells cannot use a line/pointer model: records are parsed, quoting allows commas and
  newlines, headers matter, and the stdlib `csv` module is too lax for evidence.

## 2. Scope and non-goals

In scope: `csv-cell`, schema admission and artifacts, byte pre-scan, RFC 4180-derived profile,
selection, numeric comparison, outcomes, privacy, caps, migration, verification.

Out of scope: aggregates, delimiter sniffing, semicolon/TAB dialects, header-only/multi-file
tables, quoted-field rewriting, TensorBoard, adapters, YAML, parity, release/publication, any new
batch **feature**, and any new manifest-size cap (a separate decision). Existing batch readers
(`validate-batch`, the `directory_batch` sites) accept 0.4 only through the one dispatcher; the
schema is not a new batch surface.

## 3. Schema admission — 0.4

**Proposal (D1): admit `csv-cell` in a new schema `0.4`; 0.3 stays byte-frozen.**

### 3.1 Dispatcher, 0.4 model and locator diagnostics

- Base `Manifest` admits only `Literal["0.1","0.2","0.3"]`; `Manifest04` uses `Literal["0.4"]`.
  One `validate_manifest(raw)` dispatches only on raw `schema_version == "0.4"`; every direct
  validation/identity site uses it or `is_manifest(value)` (defined in `models.py` as
`isinstance(value, (Manifest, Manifest04))`):
  `manifest.parse_manifest_source`; `edit.plan_rehash`; `edit.plan_add`;
  `migrate._validate_candidate`; `directory_authoring` (x2); `directory_apply`;
  `directory_audit` (x2); `directory_batch` (x2); `conditions_authoring`; the two experimental
  readers. `type(...) is not Manifest` becomes `not is_manifest(...)`.
- 0.4 model rules (load, exit 2): any non-null locator requires `artifact_id`; a string locator
  uses the 0.3 pointer grammar; object `kind` is `jsonl`/`csv-cell`; unknown/missing kind is
  `schema-invalid`; `row`/`column` are `StrictInt` `1..2**63-1`; `header` is `StrictStr`
  `1..65,536` code points; a lone surrogate **in `header`** is model-rejected (string pointers and
`JsonlLocator.pointer` keep the 0.3 grammar and may contain one; §10 pins the migration
consequence); `additionalProperties:false`.

**Locator storage (concrete):**

`models.py` gains `SCHEMA_VERSION_0_4 = "0.4"`, `Manifest04`, and a `Result04` whose field is:

`Manifest04.results` is `list[Result04] = Field(default_factory=list)` (a field override that keeps
the inherited default); without the item type a 0.4 csv-cell or jsonl result cannot load through
`Manifest04`, and without the default a valid no-results 0.4 manifest (including `init` output)
would fail. A no-results 0.4 vector is pinned.

```python
locator: Annotated[
    Optional[Union[StrictStr, JsonlLocator, CsvCellLocator]],
    BeforeValidator(_dispatch_locator_04),
] = None
```

The explicit `= None` (or `Field(default=None)`) is required: without it pydantic makes the field
required, so an ordinary locator-less 0.4 result fails to load and `result-unverified` becomes
unreachable. Pinned vectors: a 0.4 result with the key absent and with an explicit `null` both
load; a 0.3→0.4 migration of a locator-less result preserves it.

The base `Result`/`Manifest` stay narrow (0.3 and earlier), so the 0.3 model and schema bytes do
not change. No `Locator04` wrapper class is introduced. `Result04` **overrides** the inherited
`_locator_scalar_shape` before-validator; otherwise it rejects an already-constructed
`CsvCellLocator` before the dispatcher runs. The base 0.3 `Result` already accepts a
`JsonlLocator` instance and keeps rejecting a `CsvCellLocator`.

`_dispatch_locator_04(value)`:

- `None` → `None`;
- `str` → require `is_valid_locator_pointer(value)` and return it; otherwise raise one fixed
  `value_error` (the 0.3 pointer grammar already excludes CR, LF, U+2028 and U+2029 via
  `LOCATOR_POINTER_PATTERN_TEXT`);
- `JsonlLocator` / `CsvCellLocator` instance → return it (models are already validated);
- `dict` → validate with one **module-level** adapter built once,
  `_LOCATOR_ADAPTER = TypeAdapter(Annotated[Union[JsonlLocator, CsvCellLocator],
  Field(discriminator="kind")])`, returning the model; a raised `ValidationError` propagates so
  the field location is prefixed and the discriminator tag stays in `loc` for the walker;
- any other type (`5`, `[]`) → one fixed `value_error` at the field, never a multi-branch union
  error.

This makes a dict never produce a `str`-branch `string_type` error, and `result.locator` stores a
`str`, a `JsonlLocator`, or a `CsvCellLocator` — exactly what the delivered consumers expect.
`locator_to_json`/`dump_manifest` therefore serialize a 0.4 value without a `.root` unwrap.

**Consumers that must accept the third kind** (inventory; each with a 0.4 test):

- `locator_profiles.display_locator`: add `csv-cell` → `csv-cell:<row>,<column>`; keep the
  dict/`jsonl`/string branches;
- `checks/results._check_one_targeted` and `check_targeted_results`: `isinstance(locator,
  CsvCellLocator)` routes to the CSV verifier; `_is_object_locator` and `object_anywhere` already
  use a `type(...) is not str` negation, so `CsvCellLocator` is included without a change. The
  JSONL offset pre-scan (`selected_lines = [result.locator.line ...]`) is built **only from
  `JsonlLocator` entries**; a csv-cell entry must be routed before it, or the pre-scan raises
  `AttributeError`. One test places a single csv-cell result on a `.csv` artifact.
- `checks/__init__.py` has four 0.3 gates on the targeted path — `targeted_ids`,
  `targeted_result_ids`, `dangling_targeted` and `unverified` — each keyed on `is_0_3`. All four
  must accept 0.4, or a wrong csv-cell value is reported as `result-unverifiable` (warning) or
  `locator-unreadable` “was not checked without a shared snapshot” instead of an error. One 0.4
  test asserts a wrong csv-cell value is `result-value-mismatch` (not merely “an error”, which a
  spurious `locator-unreadable` would also satisfy).
- the object-locator predicates on the rehash/edit paths (`type(locator) is not str` at
  `edit.py:204` and `cli.py:1815`, `migrate.py:306`) already accept the new kind; one 0.4 refusal
  test each for `plan_rehash` and `_directory_rehash_checked`;
- `run_comparability`, `run_details`, `run_claim`, `run_lineage`,
  `run_fingerprint_explain`, `fingerprint`, `rehash`, `conditions`: 0.4 refusals or display, as
  §10 lists.

**`_issue_location(loc, *, root_model=None, error_type="")`** — `root_model` is resolved to the
version class at call time (`Manifest` when omitted); a `Manifest` default would capture the
class in the function definition and defeat a monkeypatched root. The delivered one-argument
calls in `tests/test_validation_issue_locations.py`, `tests/test_schema_0_3.py:285,290` and
`tests/test_cli_locator_0_3.py:310` keep working. Both direct call sites — `manifest._schema_message` and
`parse_manifest_source`’s `_SchemaFinding.issue_location` (`manifest.py:282`) — forward
`root_model=Manifest04` for a 0.4 source and the error type (with the 0.3 default, a `csv-cell`
label names no option and `row: 0` would yield `None`); the five vectors assert the
`issue_location` field as well as the message. It:

- starts at `root_model` (the version-specific class: `Manifest` or `Manifest04`);
- unwraps `Annotated` and a single-option `Optional` while walking;
- maps a locator discriminator tag (`jsonl` → `JsonlLocator`, `csv-cell` → `CsvCellLocator`)
  **before** the generic branch-label match, advances to the branch model, and does not append
  the tag (the generic matcher would otherwise return `None` for `('…','locator','csv-cell','row')`);
- at a multi-option `Union`, accepts only a branch label naming one of its own options, switches
  to that option, and does not append the label;
- skips a `tagged-union[...]` label (only inside the locator union);
- appends `kind` to the location **only when `error_type` is `union_tag_invalid` or
  `union_tag_not_found`** (the raw `loc` is identical to a `value_error` on the same field, so
  the type is the only discriminator);
- every existing 0.1/0.2/0.3 field path is otherwise unchanged.

`_issue_category`: `union_tag_invalid` → `invalid_value`; `union_tag_not_found` →
`missing_required`; existing sets otherwise. `string_unicode` is not in `_VALUE_ERRORS`, so it
falls to `other_validation`.

Pinned 0.4 cases (error type, location, category), probed on pydantic 2.14.3 and 2.6.0:

| Input | Type | Location | Category |
|---|---|---|---|
| `row: 0` | `greater_than_equal` | `/results/0/locator/row` | `invalid_value` |
| unknown kind | `union_tag_invalid` | `/results/0/locator/kind` | `invalid_value` |
| missing kind | `union_tag_not_found` | `/results/0/locator/kind` | `missing_required` |
| extra key | `extra_forbidden` | `None` | `invalid_value` |
| non-str/non-dict locator | `value_error` | `/results/0/locator` | `invalid_value` |

`header` is validated by an after-mode field validator (or a before-mode one that returns any
non-`str` value unchanged, so `StrictStr` still emits `string_type` for `5`) that encodes the
value: if encoding raises `UnicodeEncodeError` (a lone surrogate), it raises
`PydanticCustomError("string_unicode", ...)`; tests assert the type/category only, because
pydantic-core rewrites the custom message text. This is required
because the dict path already raises `string_unicode` through the discriminated adapter
(measured on 2.14.3 and 2.6.0), but an already-built `CsvCellLocator` and a bare `StrictStr`
would otherwise accept `"\ud800x"`; a plain `ValueError` would be reported as
`value_error`/`invalid_value`.

0.3 diagnostics are pinned as **seven** findings for a 0.3 csv-cell object: one `string_type`
at `/results/N/locator`; one `literal_error` at `/results/N/locator/kind`; two `missing` at
`/results/N/locator/line` and `/results/N/locator/pointer`; three `extra_forbidden` at `None`
(row/column/header).

### 3.2 0.4 artifacts and schema vectors

- `schema/runevidence-0.4.schema.json` from `json_schema_0_4()`: draft 2020-12, `$id`
  `urn:runevidence:manifest:0.4`, root title `Manifest04`, and `$defs` `Result04`,
  `JsonlLocator`, `CsvCellLocator`; `oneOf`/`const`, integer ranges, `header` limits, string
  pointer `pattern`, `additionalProperties:false`, `Result04.if`/`then` `artifact_id`.
  The generated field for `Result04.locator` is an `anyOf`; the 0.4 file hand-builds the
  **`oneOf`** the 0.3 code already uses for `locator` (null / string / `JsonlLocator` /
  `CsvCellLocator`) and keeps `additionalProperties:false`.
- **Three description overrides** (the 0.3 text otherwise leaks into 0.4): `Result.locator`
  (“In 0.3 any non-null locator …”), the `JsonlLocator` field description (“0.3 admits only
  jsonl”), and the `JsonlLocator` **class** description (“Only the `jsonl` kind is admitted by
  0.3”). The 0.4 file says “0.4 admits `jsonl`; the locator union admits `jsonl`/`csv-cell`”; the 0.3
file stays byte-identical.
- A 0.4 docs page and README/exit-table updates; 0.3 docs unchanged.
- 0.4 schema vectors: accept/reject the csv-cell shape; row/column ranges (`1.0`
  schema-accept/model-reject; `true`/`0`/`2**63`/string reject); header length/type/surrogate;
  string pointer grammar; `oneOf`/`if`/`then`; csv-cell without `artifact_id`; extra key.
- The 0.4 schema is file/docs-only; the `schema` CLI route is unchanged.
  `test_schema_freeze.py` pins the 0.4 file.

### 3.3 Inventory (each with a 0.4 test)

`cli.py`: `init --force` (`INIT_0_4_REFUSAL`); conditions `UNSUPPORTED_VERSION`; migrate
`UNSUPPORTED_TARGET`, `--to` help, subcommand `help=` and `cmd_migrate` docstring; parser
route/arg pins otherwise unchanged. `run_comparability.py`: the four 0.3 equality guards reject
0.4. `checks/__init__.py`: dispatch, `unverified`, and the four targeted gates listed above.
`checks/results.py`: checks, `result-unverified`, directory hint, kind-keyed
`_extension_matches`, `_is_object_locator`/`object_anywhere`, the csv-cell route before the JSONL
offset pre-scan, 0.4 finding lists. `checks/graph.py`: `claim-unverified`.
`checks/files.py`: per-artifact state for the normal/capped paths (not the manifest pre-pass).
`manifest.py`: dispatcher, `_issue_location`, `_issue_category`, `model_dump` (0.4 preserves
0.2/0.3 fields), `artifact_to_dict` (accepts 0.4). `models.py`: base Literal, `Manifest04`,
`Result04`, `CsvCellLocator(_Strict)`, `SCHEMA_VERSION_0_4`, `_version_specific_shape`, the
locator before-validator. `_version_specific_shape` gains a 0.4 branch: any non-null locator
requires `artifact_id`, and string/`jsonl`/`csv-cell` are admitted (the inherited else branch
says “object locators require schema_version 0.3” and would reject a 0.4 `jsonl`); one
“0.4 jsonl loads” vector. `migrate.py`: target branch, `TARGET_VERSIONS`,
precheck/`UNSUPPORTED_TARGET`, 0.4→0.3 `DOWNGRADE_UNSUPPORTED`. `locator_profiles.display_locator`
accepts dicts/models; `run_details`, `run_claim`, `run_lineage`; `run_fingerprint_explain.py`;
`fingerprint`/`rehash`/`conditions` refusals; CLI error-text plumbing.

Old readers refuse 0.4 (exit 2). A 0.4-capable `init --force` refuses with
`INIT_0_4_REFUSAL`; a pre-0.4 binary replaces with a backup (documented).

### 3.4 Locator shape

```json
{"kind": "csv-cell", "row": 41, "column": 4, "header": "val_rmse"}
```

`kind` const; `row`/`column` `StrictInt` `1..2**63-1`; `header` `StrictStr` `1..65,536` code
points, runtime field cap 65,536 UTF-8 bytes, exact parsed-header match; `additionalProperties:
false`; `.csv` case-insensitive. `CsvCellLocator` subclasses `_Strict` exactly as `JsonlLocator`
does, so `Manifest04.model_dump`’s `_presence_exclude` finds `_zero_two_only_fields`; a plain
`BaseModel` with `extra="forbid"` is not sufficient (verified `AttributeError`).

## 4. Byte pre-scan, decoding and memory

The file is read through the slice-B protected snapshot. **Never one full-file `str`.** All
decoding goes through one helper accepting at most 1 MiB (one record/field), asserted in tests;
UTF-8 validation is incremental in ≤1 MiB chunks.

1. **File cap:** raw on-disk bytes > 50 MiB → over-cap behavior; decided by the per-artifact
   check, not the manifest pre-pass; `no-digest` precedes over-cap.
2. **NUL:** any `0x00` → profile refusal.
3. **UTF-8:** incremental whole-file validation; one leading BOM stripped; interior BOM data.
4. **Newlines:** LF/CRLF terminate records; a bare CR is refused file-wide (including quotes);
   CRLF inside quotes is content, preserved as `\r\n`.
5. **Other bytes:** every byte other than NUL, a bare CR, and the LF/CRLF terminator is data
   (TAB/VT/FF/C0 excluding CR/LF/NUL, DEL/C1/NEL/LS/PS/non-ASCII). Never `str.splitlines()`; the
   verifier never uses the stdlib `csv` module; never loop per byte.
6. **Retention:** snapshot ≤50 MiB; current record ≤1 MiB raw; one decoded field ≤65,536 bytes
   (≤4× as `str`); header from one record; offsets ≤10,000; no full-file `str`, no cell table,
   no retained per-result strings.
7. **Budget and its measurement.** The design budget is ≤300 MB of **additional CSV verification
   working-set memory above the post-load baseline**, measured in a subprocess: load the
   manifest, write `5` to `/proc/self/clear_refs`, read `VmRSS`, run the CSV checks, and report
   `VmHWM − baseline`, the finding count, and (as information only)
   `VmHWM − baseline − finding_footprint`. The **asserted** number is the raw
   `VmHWM − baseline`; the subtracted figure is an estimate, because subtracting a separately
   measured footprint from a peak can understate it. `finding_footprint` comes from a second
   subprocess that materializes the same number of equivalent `Finding` objects.
   The measured callable is the classification pre-pass plus `check_files` with the targeted
   callback; `check_results` and `check_graph` are excluded. The assertion applies to benchmarks
   (a)-(c) only — the vectors whose findings are not manifest-proportional. Cap findings are
   O(artifacts) and per-result findings for non-eligible (digestless/volatile) entries are
   O(results); both are manifest-proportional, and their vectors (benchmark (f), 500k one-entry
   artifacts, 500k digestless, and benchmark (e)) are **record-only**: RSS and finding count are
   reported, and a manifest-size/result-count cap remains a separate decision. Benchmark (e) is
   record-only because `run_checks`/`check_graph` alone adds ~265 MB at 500k results, unrelated to
   CSV. The memory benchmark is
   Linux-only (`/proc/self/clear_refs`); it skips elsewhere with a counted reason, and the
   Windows CI job does not run it.

## 5. RFC 4180-derived record profile

### 5.1 Dialect

- Comma only; no sniffing. Records end at LF/CRLF; a final terminator adds no record; an
  unterminated final record is valid.
- Fields unquoted or quoted; `""` is one `"`; quoted fields may contain commas, LF and CRLF. A
  quote in an unquoted field, an unclosed quote, or any byte after a closing quote other than
  comma/record-end/EOF is refused.
- Quoting is transport only (`"0.5"` is `0.5`). Whitespace is data.
- A **blank record** is a zero-byte span at a record boundary outside quotes, refused even in a
  one-column file (`""` is the empty cell). A blank line inside quotes is content.
  `a,b\n1,2\n` accepted; `a,b\n1,2\n\n` (and CRLF) refused.
- A record of empty fields (`,,`) is valid; an empty selected cell fails the grammar.
- Ragged records refused; the whole file must satisfy the profile.

### 5.2 Header

- Exactly one header record and at least one data record; header-only refused.
- 1..1,024 fields; parsed header cells non-empty and unique (`"",b`, `a,"a"` refused).
- Header record subject to the 1 MiB record and 64 KiB field caps; header never selectable.
- Empty file/header-only/empty/duplicate parsed headers → `locator-unreadable`.

### 5.3 Caps (split: per-artifact structural; per-manifest eligibility)

**Per-artifact structural caps** — checked on the file; a violation is a per-result
`locator-unreadable`:

| Cap | Unit | Value |
|---|---|---|
| record | raw UTF-8 bytes after BOM strip, excluding terminator; header and data | 1 MiB |
| field | unescaped cell value UTF-8 bytes; CRLF in quotes counts 2 | 64 KiB |
| columns | header fields | 1,024 |
| data rows | parsed data records | 1,000,000 |
| total cells | data rows × columns (header excluded) | 10,000,000 |

**Per-manifest eligibility caps** — counted without reading a file; a violation is one cap
finding per artifact:

| Cap | Unit | Value |
|---|---|---|
| csv-cell results | manifest-eligible `csv-cell` entries | 10,000 |
| csv-cell header metadata | sum of `header` UTF-8 bytes over eligible entries | 16 MiB |

- **Manifest-eligible** (no file read): artifact non-directory, path ends `.csv`
  case-insensitive, `sha256` non-null, `volatile` false. Runtime states do not reduce the count.
- Only the two **eligibility thresholds** are per manifest and counted by the no-read pre-pass.
  The structural caps are per file and are never summed across artifacts: two 600,000-row files
  are each under the row cap and neither caps. The **cap finding** is one per artifact: subject =
  artifact id, path = artifact path, `locator-unreadable`, artifact broken. Cap findings are
  O(artifacts) and excluded from the CSV working-set budget (measured separately, §4.7).
- Capped path: an eligible artifact in state `ok`/`over-cap` (with or without a hash/size
  finding) gets the one cap finding; states `missing`, `reference-failed`, `unreadable`, `changed`,
  `symlinked-parent`, `refused-path`, `refused-alias` or `escapes-root` get one artifact finding
  instead (the finding `_prepare_target` already emitted); no value comparison.
- Non-csv-cell locators on a capped `.csv` still get their normal-path `locator-kind-mismatch`
  (additive to the one cap finding).
- Structural caps → per-result `locator-unreadable`; >1,024 file columns → `locator-unreadable`;
  a locator column beyond a valid header → `locator-mismatch`.
- 16 MiB rationale: 256 × 65,536-byte headers; 10,000 × 1,678 bytes is refused; a future
  decision may raise it or count distinct headers.
- N/N+1: field ASCII 65,536/65,537 and `"é"*32768`/`"é"*32768+"a"`; record 1 MiB/+1 including a
  header at the cap; cells 500,000×20 and 909,091×11; eligible entries 10,000/10,001; metadata
  exactly 16,777,216 (uncapped) and 16,777,217 (255 × 65,536 + 32,768 `é` + `a` = 65,537-byte
  header; capped).

## 6. Evaluation order

### 6.1 Manifest pre-pass (classification only, no reads, no findings)

Resolve each locator's artifact_id; mark dangling (check_graph owns `dangling-reference`),
directory (non-eligible), extension kind, digestless/volatile (non-eligible). Count
manifest-eligible `csv-cell` entries and their `header` bytes; set `csv_cell_capped` when either
threshold is exceeded. Emit nothing.

### 6.2 Normal path (not capped), per artifact in manifest order

The delivered `TargetCheck.state` values (a plain `str` field, documented here; if runtime
enforcement is wanted, add `__post_init__` validation and a test asserting the set) are `ok`, `over-cap`, `no-digest`, `volatile`,
`directory`, `missing`, `reference-failed`, `unreadable`, `changed`, `symlinked-parent`,
`refused-path`, `refused-alias`, `escapes-root`. Hash and size mismatches are **findings**, not
states: on the targeted path `check_artifact` returns `ok`/`over-cap` after either, except a nonvolatile digestless
artifact with a size mismatch, which emits `size-mismatch` and returns `no-digest`. `not-a-file` is the artifact
finding `_prepare_target` emits before returning `reference-failed`.

1. **Directory artifact:** run the ordinary artifact check first. It emits **zero or more**
   findings: without the flag, `not-a-file` for an existing directory and `missing-file` for a
   missing one; with `--directory-artifacts`, `missing-directory`, `directory-hash-mismatch`,
   `directory-size-mismatch`, `directory-no-digest`, `directory-unsafe`, … — a matching directory
   emits **none**. Then emit `directory-locator-unsupported` per result and **return** (no later
   per-result stage, no kind mismatch even when the spelling disagrees).
2. **File state and digest:** a normally missing file returns `reference-failed` after
   `_prepare_target` emitted `missing-file`; the `missing` state is the post-`_prepare_target`
   read-race path. Both emit only `missing-file` and zero per-result. `reference-failed` may
   instead carry `not-a-file` or `path-escapes-root` (with the `absolute-path` advisory), all
   pre-emitted by `_prepare_target`, zero per-result; state `unreadable`, `changed`,
   `symlinked-parent`, `refused-path`, `refused-alias` or `escapes-root` → one artifact-level
   finding, zero per-result. Otherwise the artifact check emits `hash-mismatch` (with a “Size
   also changed” note when the total size also differs) **or** `size-mismatch` when a recorded
   size differs — including a nonvolatile digestless artifact — never both, here, before any
   per-result finding, and marks the artifact broken. Pre-existing advisory path warnings (for a
   non-clean spelling) may accompany any of these; cardinality statements below exclude them.
3. **Extension/kind:** per result `locator-kind-mismatch`. If no usable locator remains **and
   the file state is `no-digest`** (digestless and not volatile), restore the artifact-level
   `no-digest` warning.
4. **Volatile:** per result `locator-unverified`; **return** before selection and comparison
   (no hash or size check).
5. **No digest:** per result `locator-no-digest`; size was already compared in step 2, so
   `size-mismatch` may appear; **return** before selection and comparison (a verdict never
   compares values here).
6. **Over-cap file:** per result `locator-unreadable`; **return**.
7. **Structural pass:** whole-file; refusal → per-result `locator-unreadable` (structural
   message); selection/compare suppressed.
8. **Selection:** per result `locator-mismatch`, in the fixed order row → column → header: a
   row beyond the data is `was not found`; otherwise a column beyond the header (or otherwise
   unselectable) is `was not selectable`; otherwise a header mismatch uses the fixed text
   `selected header does not match the header at this column` (no header echoed). A vector with
   row 5 and header `x` on a 2-row file pins row-first.
9. **Recorded type:** per result `locator-unsupported-value`.
10. **Cell parse:** per result `locator-unreadable` (D2 message).
11. **Compare:** per result `result-value-mismatch`.
12. **Comparison qualification:** values are parsed and compared only when the snapshot is
    readable (`TargetCheck.state == "ok"`; hash/size findings do not change that state and never
    suppress comparison) and step 7 did not refuse. A difference adds `result-value-mismatch`.
    Capped, structurally refused, volatile and no-digest artifacts never compare values. “State”
    here always means the `TargetCheck` enum, never “a hash/size finding was emitted”.

### 6.3 Capped path

For each artifact in manifest order:

1. Artifacts with **no eligible entries** follow all of §6.2 (including file state, kind,
   volatile/no-digest); they are unaffected by the cap.
2. For an artifact with eligible entries: file state/digest as §6.2 step 2. States `missing`,
   `reference-failed`, `unreadable`, `changed`, `symlinked-parent`, `refused-path`,
   `refused-alias` or `escapes-root` → one artifact finding (or the one `_prepare_target`
   already emitted), **no cap finding**. Otherwise emit `hash-mismatch`/`size-mismatch` as
   applicable.
3. Non-csv-cell locators on that `.csv` get their normal-path `locator-kind-mismatch`
   (additive) **only when the cap finding is emitted** (state `ok`/`over-cap`); a state that
   suppresses the cap emits no per-result kind finding.
4. Emit **one** cap finding for the eligible entries (state `ok`/`over-cap`, with or without a
   hash/size finding), add the artifact to broken, and do not parse or compare any cell.
5. An artifact whose entries are all non-eligible (digestless/volatile) follows **all** of
   §6.2, including the step-2 size comparison, with no cap; `jsonl`/string entries on other
   artifacts follow §6.2 normally.

### 6.4 Two-pass streaming (normal path)

Pass 1 validates the whole file's UTF-8 incrementally, enforces NUL/CR/caps, parses records, and
stores requested offsets plus the header; decodes only the header and selected field boundaries
via the ≤1 MiB helper. Pass 2 decodes/compares/discards each requested field. No cell table;
embedded newlines do not change row indices.

## 7. Numbers and comparison

- Numeric recorded values only: `type(value) in (int, float)`; `bool` excluded; `str`/`None` →
  `locator-unsupported-value`.
- Quoting is transport; the unescaped text is matched with `re.fullmatch`:

  `-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?`

  or `NaN`/`Infinity`/`-Infinity`. `1abc`, `1_000`, `+1`, `.5`, `1.`, ` 1`, `1 `, decimal
  commas, empty cells refused.
- Non-numeric cell → `locator-unreadable`; full rendered finding:
  `result 'r1' locator csv-cell:41,4 selected cell is not a number under the csv-cell profile in
  'metrics.csv'` (D2; JSONL divergence documented).
- Exact 512-digit ints; floats for fraction/exponent; underflow via `_significand_nonzero`;
  overflow refused; digit-limit mapped to fixed refusal.
- Comparator `compare_values`: int/int exact; mixed int/float approximate (rel 1e-9, abs 1e-12);
  NaN equals NaN; infinities equal themselves; booleans never equal numbers; `1` ≠ `"001"`.
- `2**53`: int/int exact; mixed forms approximate and documented.
- Real writers' `nan`/`inf`/`-inf`/empty are refused.

## 8. Outcomes

| Condition | Code | Severity | Cardinality |
|---|---|---|---|
| Unknown kind / wrong/missing field / range / empty header / lone surrogate in `header` / missing artifact_id / bad string pointer / non-str-non-dict locator | `schema-invalid` | exit 2 | per load issue |
| Dangling id | `dangling-reference` | error | per result (`check_graph`) |
| Directory artifact: the ordinary artifact finding(s), if any (`missing-file`, `not-a-file`, `missing-directory`, `directory-*`) | existing codes | error/warning | zero or more per artifact |
| Directory artifact with a locator | `directory-locator-unsupported` | error | per result |
| Kind/extension mismatch | `locator-kind-mismatch` | error | per result |
| Missing | `missing-file` | error | one per artifact |
| Unreadable/changed/symlinked-parent/refused-path/refused-alias/escapes-root/not-a-file | existing slice-B codes | error | one per artifact |
| Volatile | `locator-unverified` | error | per result (before no-digest; no compare) |
| No digest | `locator-no-digest` | error | per result; size still compared, no value compare |
| Digestless artifact with no usable locator remains | `no-digest` | warning | one per artifact (restored; the size check still runs) |
| Over-cap file (normal path) | `locator-unreadable` | error | per result |
| csv-cell cap (capped path) | `locator-unreadable` (cap message) | error | **one per artifact**; artifact broken |
| Structural/profile refusal | `locator-unreadable` (structural message) | error | per result |
| `hash-mismatch` / `size-mismatch` | existing codes | error | one per artifact; compared unless capped/over-cap/refused/volatile/no-digest; a difference adds `result-value-mismatch`; artifact broken |
| Row/column out of range, header mismatch | `locator-mismatch` | error | per result |
| Recorded `str`/`bool`/`None` | `locator-unsupported-value` | error | per result |
| Cell not a number | `locator-unreadable` (D2) | error | per result |
| Value differs | `result-value-mismatch` | error | per result |
| Absent locator | `result-unverified` | warning | per result |
| Claim only from unverified | `claim-unverified` | warning | per claim |
| Claim rests on a capped/broken artifact | `claim-at-risk` | error | per claim |

Cap message: `the manifest exceeds the csv-cell verification limit (10,000 entries or 16 MiB of
header metadata); no CSV cell was read`. The structural finding's **hint** is
`the CSV artifact does not satisfy the strict csv-cell profile`; its **message** is the per-result
rendering below. Hint rules:

- the structural finding's hint is the fixed sentence above;
- every 0.4 `directory-locator-unsupported` uses the §10 hint; 0.3 keeps the delivered hint;
- the reused `locator-no-digest`, `locator-unverified` and over-cap findings keep their delivered
  hints verbatim;
- the cap finding's hint is `None`, and every other csv-cell-specific finding has hint `None`.

The CSV-profile value findings share the delivered rendering
`result {id!r} locator {display} {reason} in {path!r}`; `locator-kind-mismatch`
(`needs a .csv artifact, not {path!r}`) and `directory-locator-unsupported`
(`targets a directory artifact {path!r}`) keep their own forms. The pinned full forms are:

- structural refusal: `result 'r1' locator csv-cell:41,4 could not be read under the strict
  profile in 'metrics.csv'`;
- header mismatch: `result 'r1' locator csv-cell:41,4 selected header does not match the header
  at this column in 'metrics.csv'`;
- row beyond the data: `result 'r1' locator csv-cell:41,4 was not found in 'metrics.csv'`;
- column beyond the header (or otherwise unselectable): `result 'r1' locator csv-cell:41,4 was
  not selectable in 'metrics.csv'`;
- recorded `str`/`bool`/`None`: `result 'r1' locator csv-cell:41,4 selects an unsupported value
  in 'metrics.csv'` (the delivered shared reason);
- non-numeric cell (D2): `result 'r1' locator csv-cell:41,4 selected cell is not a number under
  the csv-cell profile in 'metrics.csv'`;
- cap: the artifact-level message quoted above (no `result` prefix).

Fuzz scope: fuzz asserts the **verification** findings — the CSV-profile findings plus the
artifact-level findings the same pass emits; every emitted finding must be a member of the closed
set. A companion claim-propagation test (same corpus) asserts `claim-at-risk` (delivered text,
severity error) for a claim citing a result on a capped/broken artifact. Graph/lifecycle findings
(`claim-at-risk`, `claim-unverified`, `dangling-reference`) are outside the fuzz set and are
covered by their own tests.

Fuzz closed set (templates; reused delivered texts cited by source):

- `schema-invalid` (load-time; locations per §3.1);
- the directory artifact finding(s) and `directory-locator-unsupported` (0.3 texts; 0.4 uses the
  §10 hint);
- `locator-kind-mismatch`: `result {id!r} locator {display} needs a {expected} artifact, not
  {path!r}`, with `expected` in `{.json, .jsonl, .csv}`;
- `missing-file`, the slice-B unreadable/path codes, `locator-unverified`, `locator-no-digest`,
  `locator-unreadable` (over-cap), `result-value-mismatch`, `hash-mismatch`, `size-mismatch` —
  delivered texts, unchanged;
- `no-digest` (restored artifact warning, delivered text);
- the six pinned csv-cell renderings above and the cap message.

`claim-unverified` is 0.3-unchanged and 0.4-versioned per §10; it is outside the fuzz set. No
parser, `strerror`, header text, or cell text ever appears.

Capped propagation: the artifact is in `files.broken`; `check_graph` propagates through
`reported_in` and `input_to` lineage. The existing `claim-at-risk` text ("missing or changed") is
reused and is imprecise for a resource cap; this limitation is documented and vector-tested.

## 9. Privacy and display

- **Scope:** a `csv-cell` finding message/hint never contains cell text, parsed header text,
  `locator.header`, raw row text, OS `strerror`, or parser exception text.
- **Permitted:** `Finding.path`; `in {artifact_path!r}`; `hash-mismatch` digest prefixes;
  `missing-file` path text; `report.to_json`'s manifest path; recorded manifest fields.
- **Display:** `csv-cell:<row>,<column>` (numbers only, no `@header`).
- **Human vs JSON:** `claim_details`/`claim_lineage` keep
  `locator_to_json(obj.locator)` for `--json`. The locators are nested at
  `row["details"]["locator"]` (each `supports`/`contradicts` row) and
  `node["details"]["locator"]` (each lineage node), not at a top-level `details` key.
  `format_claim_details`/`format_claim_lineage` therefore walk those rows and nodes and render a
  **copy** in which each nested `csv-cell` locator is replaced by `display_locator(...)`; nested
  `jsonl` locators and 0.3 strings are left exactly as today, so 0.3 human bytes do not change.
  The data dict/list is untouched; a literal top-level copy would leave `header` in the human
  output. `display_locator` accepts
  dicts, `CsvCellLocator` and `JsonlLocator`. Only `csv-cell` values are humanized this way; 0.3
  human output is unchanged. `run_details` human output uses `display_locator` likewise. Human
  outputs show `csv-cell:<row>,<column>` and never the header; `--json` keeps the structured
  locator including `header`.
- **Canaries:** forbidden tokens in locator messages; a negative assertion that the header token
  is absent from `format_run_details`, claim and lineage human output (lineage covering the
  `roots` rows as well as `nodes`, even though human lineage currently prints only node details); positive assertions for permitted
  `Finding.path`; a 0.4 golden vector with a header distinct from names/values/paths.

### 9.1 Parent v0.8 errata (authorized by §16)

The parent document is **`docs/verified-value-locators-rfc.md`**, reissued as **v0.8**
(`090cb77c5bea6f8f635a64dca6abfd249f4357389eab07c8e969e0e42f69c85f`; pre-errata v0.7 was
`f04ecfc8…`). It carries replacement text for:
- §6: display `csv-cell:<row>,<column>`, no header;
- §7: findings never echo the header assertion for `csv-cell`;
- §3.1/§4 table/§8: unknown kind = `schema-invalid`, no `locator-unknown-kind`; the §8 migration
  sentence never lists result IDs; 0.4 is the admitting schema; `.csv`/csv-cell extension;
- §4.1 item 4 and §9 item 4: per-locator over-cap findings apply only on the normal path; a
  capped manifest emits one artifact-level cap finding;
- §4.2: the artifact `no-digest` warning is suppressed for a targeted artifact, restored only
  when no usable locator remains **and** the file state is `no-digest` (not volatile); a
  nonvolatile digestless artifact still compares `size_bytes` before the result-level finding
  (the size outcome itself is the §4.3 `size-mismatch` row quoted in §9.2);
- §4.2 step 4: “on hash-mismatch the value is still compared” gains the exception “except on a
  csv-cell-capped artifact, where no value is compared”;
- §4.3: **insert** the `size-mismatch` row quoted in §9.2, the capped and once-per-artifact
  sentences, and the broken-set extension, and widen `Absent locator (0.3)` and
  `Directory artifact locator (0.3)` to 0.3/0.4 (rows quoted in §9.4);
- §4.1: snapshot scope and the over-cap clause; §5.4 comparator; §9 gates 1, 2 and 4
  (exact text in §9.5);
- §5.2: replace the caps bullet per §9.3 — the two eligibility caps are per manifest, the
  structural caps per artifact, never summed.

### 9.2 Exact parent §4.3 replacement

Insert immediately after the parent’s `Recorded bytes differ | \`hash-mismatch\` (existing)` row,
matching the `Condition | Code | Severity | Exit` columns and the `(existing)` spelling:

```
| Recorded size differs on a nonvolatile artifact with a matching digest (or no recorded digest) | `size-mismatch` (existing) | error | 1 |
```

Immediately below the table add: “A csv-cell-capped artifact emits at most one cap finding,
alongside any hash/size or kind finding; no value is compared.” and “`size-mismatch` is emitted
once per artifact.” Replace the broken-set sentence ending “, `directory-locator-unsupported`,
and `not-a-file`.” with “, `directory-locator-unsupported`, `not-a-file`, and `size-mismatch`.”
The Exit column is always `1`; cardinality is stated in the sentence, not in the table.

### 9.3 Exact parent §5.2 replacement

The parent §5.2 bullet reads as follows (line-wrapped in the file; compare after whitespace
normalization):

> Caps: file 50 MiB, record 1 MiB, field 64 KiB, rows 1,000,000, columns 1,024, total cells
> 10,000,000, locator results per artifact 10,000. Lengths are bytes of decoded UTF-8.

Replace that whole bullet with:

> Caps: file 50 MiB per artifact; record 1 MiB, field 64 KiB, rows 1,000,000, columns 1,024 and
> total cells 10,000,000 per artifact, never summed across artifacts; csv-cell locator results
> 10,000 and csv-cell header metadata 16 MiB per manifest, counting only manifest-eligible
> entries (non-directory `.csv` artifact with a recorded sha256, not volatile), without reading a
> file. Lengths are bytes of decoded UTF-8; the file cap uses raw on-disk bytes (a leading BOM
> counts) and the record cap uses raw record bytes after BOM strip, excluding the terminator.

### 9.4 Exact parent unknown-kind and §8 edits

- Replace the §4.3 row

  ```
  | Unknown kind | `locator-unknown-kind` (new) | load refusal | 2 |
  ```

  with

  ```
  | Unknown kind | `schema-invalid` (existing) | load refusal | 2 |
  ```

  and remove `locator-unknown-kind` from the §8 code list.
- Replace the parent §3.1 sentence

  > An unknown `kind` is a **load-time schema refusal** (`locator-unknown-kind`,
  > `schema-invalid`, exit 2).

  with

  > An unknown `kind` is a **load-time schema refusal** (`schema-invalid`, exit 2).
- Replace the §3.1 extension sentence

  > `kind` must match the artifact extension, case-insensitively: `jsonl` on `.jsonl`.
  > `.ndjson` and `.tsv` are not admitted. A mismatch is `locator-kind-mismatch` (error),
  > checked at validation.

  with

  > `kind` must match the artifact extension, case-insensitively: `jsonl` on `.jsonl`,
  > `csv-cell` on `.csv`. `.ndjson` and `.tsv` are not admitted. A mismatch is
  > `locator-kind-mismatch` (error), checked at validation.
- Replace the §4.3 row `| Absent locator (0.3) | ... |` with `| Absent locator | ... |` and the row
  `| Directory artifact locator (0.3) | ... |` with `| Directory artifact locator | ... |` (the
  codes, severities and exits are unchanged); both now apply to 0.3 and 0.4.
- Replace the parent §6 display example `csv-cell:41,4@val_rmse` with `csv-cell:41,4` and change
  “Header and pointer components escape …” to “Pointer components escape …; the csv-cell display
  is numbers only, and length is bounded.”
- Replace the §8 migration sentence “refuses the **whole** migration before writing, listing the
  offending result IDs.” with “refuses the **whole** migration before writing. Migration never
  lists result IDs.”

### 9.5 Exact remaining errata

- §7: replace “They name the artifact-relative path, the escaped locator, the fixed code and the
  recorded assertion (header).” with “They name the artifact-relative path, the escaped locator,
  the fixed code, and - for `jsonl`/string locators - the recorded assertion (pointer); a
  `csv-cell` finding never names the header.”
- §4.1: replace “Only artifacts targeted by a 0.3 locator (string or object) get a retained
  in-memory snapshot.” with “Only artifacts targeted by a 0.3 or 0.4 locator (string, `jsonl` or
  `csv-cell`) get a retained in-memory snapshot.”
- §4.1 (over-cap rule): replace “while each locator emits `locator-unreadable` because the value
  cannot be read under the profile cap” with “while each locator on the normal path emits
  `locator-unreadable`; a csv-cell-capped artifact (eligible csv-cell entries in a capped
  manifest) emits one cap finding instead, one per eligible artifact (including an over-cap
  artifact)”.
- §4.2 step 4: replace “The value is still compared (preserving the existing JSON behavior) and
  the result is **never called verified**.” with “The value is still compared (preserving the
  existing JSON behavior), except on a csv-cell-capped artifact, where no value is compared; the
  result is **never called verified**.”
- §4.2: after “and cannot when an object locator does.”, add “The artifact-level `no-digest`
  warning is restored only when no usable locator remains and the file state is `no-digest` (not
  volatile).”
- §5.4: replace “**Comparator:** 0.3 locators (string and object) use a new comparator:” with
  “**Comparator:** 0.3 and 0.4 locators (string, `jsonl` and `csv-cell`) use a new comparator:”.
- §9 gate 1: replace “extension matching (`.json`/`.jsonl`; `.ndjson`/`.tsv` refused)” with
  “extension matching (`.json`/`.jsonl`/`.csv`; `.ndjson`/`.tsv` refused)”.
- §9 gate 2: replace “absent locator (0.3)” with “absent locator (0.3 and 0.4)”.
- §9 gate 4: replace “locators `locator-unreadable`” with “locators `locator-unreadable` on the
  normal path; a csv-cell-capped artifact (eligible csv-cell entries in a capped manifest)
  emits one cap finding, one per eligible artifact (including an over-cap artifact)”.

The exact sentences in §9.2-§9.5 are checked by docs tests; the implementation brief
`progress/slicec-implementation-brief-2026-11-13.md` must match (cap finding per artifact, full
errata list, serialization).

## 10. Compatibility, migration and tests

- 0.1/0.2 readers unchanged. 0.3 unchanged (model, diagnostics, schema output); it refuses
  `csv-cell`.
- **0.1/0.2 → 0.4:** the same manifest-only precheck as 0.2 → 0.3 (defensive object locator;
  missing `artifact_id`; invalid/prefixed pointer; directory; volatile; no-digest; non-`.json`
  string target). Refuse before backups/writes with `LOCATOR_MIGRATION_UNSUPPORTED`. No
  pointer-resolution/dangling-id verification. A 0.2 string locator on `metrics.csv` is refused.
- **0.3 → 0.4:** version-only bump for any schema-valid **and serializable** 0.3 manifest; skips
  the precheck; values/locators/backups preserved. A 0.3 string containing a lone surrogate
  (e.g. `"/\ud800"`) is schema-valid but not serializable and is refused as
  `SERIALIZATION_FAILED` before any write (the path is `migrate._serialize_candidate`). A hand-written 0.4
  string or `JsonlLocator.pointer` containing a lone surrogate also loads (the 0.3 grammar is
  unchanged); only migration refuses it. The delivered `dump_manifest` returns a `str` that still
  contains the surrogate, and the `add`/`rehash` writer path (`load_raw` → `save_raw`/`dump_raw`)
  raises a bare `UnicodeEncodeError` from `save_raw` before the manifest is replaced. That traceback is a **pre-existing
  0.3/0.4 defect**, out of scope for this gate and recorded in §14; no 0.3 writer behavior
  changes here.
- **0.4 → 0.3/0.2:** `DOWNGRADE_UNSUPPORTED`; the 0.4→0.3 branch must not fall to
  `INTERNAL_ERROR`.
- **Exact texts:**
  - `UNSUPPORTED_TARGET` before: `ERROR (UNSUPPORTED_TARGET): only --to 0.2 or --to 0.3 is
    supported; downgrade and other versions are not available. Nothing was written.`
    after: `ERROR (UNSUPPORTED_TARGET): only --to 0.2, --to 0.3 or --to 0.4 is supported;
    downgrade and other versions are not available. Nothing was written.` Carved out of the
    byte-identical claim; the `--to` help, subcommand `help=`, and `cmd_migrate` docstring move.
  - `LOCATOR_MIGRATION_UNSUPPORTED` renders the target (`schema 0.3` byte-identical; `schema 0.4`).
  - `result-unverified` 0.4: `result {id!r} has no locator in schema 0.4; its recorded value was
    not checked`; `claim-unverified` 0.4: `Claim {id!r} cites only results with no locator in
    schema 0.4; their recorded values were not checked`.
  - `INIT_0_4_REFUSAL`: `ERROR: the existing manifest declares schema 0.4. init --force would
    regenerate it as schema 0.1 and cannot carry over first-class values or locators. Nothing was
    written. Move the manifest aside, run init, then runevidence migrate --to 0.4 and reapply the
    first-class values and locators.`
  - Kind mismatch: `result {id!r} locator {display} needs a .csv artifact, not {path!r}`.
  - Directory hint 0.4: `Point the result at a single JSON, JSONL or CSV file artifact, or drop
    the locator.`
  - Conditions source, version-keyed: the 0.3 source keeps today's bytes (`… schema 0.3 has no
    downgrade path. Nothing was written.`); the 0.4 source emits `ERROR (UNSUPPORTED_VERSION):
    the conditions command requires an explicit schema 0.2 manifest. A schema 0.1 manifest can be
    migrated with runevidence migrate PATH --to 0.2; schema 0.3 and 0.4 have no downgrade path.
    Nothing was written.`; 0.1/0.2 sources keep today's bytes. The CLI needs a version carrier
    to select the text: `_conditions_failure(..., version)` (or a `ConditionsError.version`
    attribute), asserted in `test_cli_conditions.py`.
  - Comparability `UNSUPPORTED_VERSION` has no version text; the four guards reject 0.4.
- **Fixture changes, split by intent:**
  - *Keep rejecting 0.4 (base model / 0.2 schema):* `test_manifest_0_2.py:162` (base `Manifest`
    still `Literal["0.1","0.2","0.3"]`) and `test_schema_0_2.py:271` (the 0.2 schema rejects 0.4).
  - *Change to accept 0.4:* `test_manifest_0_2.py:720` must flip — `artifact_to_dict(..., "0.4")`
    now accepts, and the raise moves to a 0.5 probe.
  - *Change to the new 0.4 behavior:* `test_cli_migrate.py:310` and `test_migrate_0_3.py:285`
    (with its help pin `:298-303`) no longer parameterize `--to 0.4` as
    `UNSUPPORTED_TARGET`; `test_conditions_authoring.py:1807` can only expect `UNSUPPORTED_VERSION` (its `_expect`
    checks `ConditionsError.code`, not text); `test_cli_conditions.py:758-780` moves its unknown-version
    assertion to 0.5 (the current `0.4 not in` check must not see the new 0.4 text) and pins
    the 0.4 text separately.
  - *Move to 0.5 (true loader/dispatcher unknown version):*
    `tests/data/schema-vectors/reject-unknown-version.json` and any 0.4-as-unknown loader test.
  - *Re-pin:* `test_experimental_cli_parser_contract.py`'s `cli.py` LF/CRLF hashes — the §3.3
    text edits change the source bytes (the 26-route list is unchanged).
  - *Add:* the `test_schema_freeze.py` 0.4 pin (it does not exist yet).
- 0.4-capable `init --force` refuses with `INIT_0_4_REFUSAL`; old readers refuse 0.4 (exit 2).
- `rehash`: add on 0.4 succeeds; rehash with any csv-cell refuses `LOCATOR_REFRESH_UNSUPPORTED`;
  without object locators succeeds. The `type(locator) is not str` predicates already include
`CsvCellLocator`.
- **Serialization:** `Manifest.model_dump` treats 0.4 like 0.2/0.3 (preserves `conditions`,
  `dataset_id`, `metric_definition`); `artifact_to_dict(..., "0.4")` accepts; required tests:
  `dump_manifest(Manifest04)` round-trip, a 0.4 manifest with a string locator dumping as a
  string (not a wrapper), and `migrate 0.3→0.4` value preservation.
- `conditions`/fingerprint/comparability refuse 0.4; no new CLI route; `schema`
  file/docs-only.

## 11. Verification plan

### 11.1 Dialect vectors (accept)

`a,b\n1,2\n`; CRLF `a,b\r\n1,2\r\n`; quoted fields `"a","b"\n"1","2"`; comma in quotes
`a,b\n"x,y",2\n`; LF in quotes `a,b\n"x\ny",2\n`; CRLF in quotes preserved as `\r\n`;
`""` escape `a\n"x""y"\n`; unterminated final record `a,b\n1,2`; empty field `a,b\n,2\n`;
single column `v\n1\n`; leading BOM stripped; an **interior BOM treated as data**; TAB/VT/FF/C0-excluding-CR/LF/NUL/
DEL/C1/NEL/LS/PS as data; multibyte UTF-8 in a cell and header; exactly 1,024 header fields; a
1 MiB record and a 65,536-byte field at the cap (quoted and unquoted); `"1"` selects numeric 1.

### 11.2 Dialect vectors (refuse)

NUL anywhere; a bare CR anywhere, including inside quotes; a blank record at a boundary (LF and
CRLF, one-column too); a quote in an unquoted field; an unclosed quote; any byte after a closing
quote other than comma/record-end/EOF; ragged records; header-only; empty file; empty parsed
header cell (`"",b`); duplicate parsed header (`a,"a"`); 1,025 columns; record 1 MiB+1; field
65,537 bytes (`"é"*32768+"a"`); 1,000,001 rows; 10,000,001 cells; invalid UTF-8; a 51 MiB file
(over-cap path).

### 11.3 Selection, numbers, oracle, fuzz

- Selection: row 1 = first data record; header never selectable; column beyond the header →
  `locator-mismatch`; header mismatch → the fixed text.
- Numbers accept: `0`, `-1`, `1.5`, `1e10`, `1E-3`, `NaN`, `Infinity`, `-Infinity`, a 512-digit
  integer, `-0`.
- Numbers refuse: `1abc`, `1_000`, `+1`, `.5`, `1.`, ` 1`, `1 `, `1,5`, empty, `0x1`, `true`,
  `null`, `inf`, `nan`, `1e-400` (underflow), `1e999` (overflow), a 513-digit integer, `001`
  (leading zero) — all `locator-unreadable`.
- Recorded non-numeric (`str`/`bool`/`None`) → `locator-unsupported-value`.
- **Oracle:** a deliberately simple test-only reference parser (whole-file read allowed for
  fixtures ≤5 MiB) cross-checks the record/cell extraction on every accepted fixture; the
  production verifier never uses it and the oracle never runs at runtime.
- **Fuzz:** fixed, recorded seeds; a grammar generating quoted/unquoted fields, commas, LF/CRLF,
  BOM, control bytes and multibyte UTF-8; assertions: no exception escapes, findings are exactly
  the closed set in §8, accepted documents' selected cells equal the oracle, the verifier never reopens or
  re-reads the artifact path (it consumes the one in-memory snapshot), and no cell/header text
  appears in any message.

### 11.4 Precedence and cardinality vectors

- missing+digestless (only missing-file); missing+volatile (only missing-file); missing+wrong-
  extension (only missing-file); dangling+csv-cell (one dangling-reference);
  volatile+digestless (only locator-unverified); volatile+digestless+all-kind-mismatched (only
  locator-kind-mismatch); digestless (not volatile)+all-kind-mismatched (per-result kind + the
  restored artifact no-digest).
- **Directory artifacts** (flag × state): existing directory without the flag → `not-a-file`
  plus one `directory-locator-unsupported` per result; missing directory without the flag →
  `missing-file` plus per-result; missing with the flag → `missing-directory` plus per-result;
  wrong digest with the flag → `directory-hash-mismatch` plus per-result (the fixture keeps the
  total size identical, so `directory-size-mismatch` is not also raised); matching directory with
  the flag → no ordinary finding, only per-result `directory-locator-unsupported`; and in every
  case no kind/no-digest/volatile/structural/selection/value finding. These five are
  illustrative: `not-a-directory`, a volatile directory, a digestless directory and an
  unreadable directory follow the same zero-or-more rule.
- **Size/digest:** matching digest + wrong `size_bytes` + equal value → only `size-mismatch`;
  matching digest + wrong size + differing value → `size-mismatch` +
  `result-value-mismatch`; matching digest + wrong size + usable csv-cell is parsed and
  compared.
- **No-digest:** digestless + wrong size + a usable, differing csv-cell → `size-mismatch` and
  per-result `locator-no-digest`, **no** `result-value-mismatch`.
- **Refused spellings:** `a/../metrics.csv` (with `a` an existing ordinary directory, so the
  spelling reaches the refused-path check rather than `missing-file`) counts toward the manifest
  threshold but at runtime emits only its refused-path finding (no cap finding); `metrics.csv/` is ineligible and also
  gets no cap finding.
- **Capped path:** 500k entries on one ok artifact → one cap; 500k on a 51 MiB artifact with a
  **correct** digest → one cap (wrong digest → hash-mismatch + one cap); hash/size + one cap and
  no compare; missing/unreadable/changed/path → one artifact finding, no cap; 10,000 exactly →
  normal; 5,001+5,001 → capped, one per artifact; a jsonl locator on the same capped .csv →
  kind mismatch + one cap; 500k digestless/volatile → per-result, record-only; 500,000 one-entry
  artifacts → 500k cap findings, record-only.
- **Advisory warnings:** non-clean spellings (backslash, path-case) may add the pre-existing
  advisory path warnings alongside the state or cap finding; the cardinality vectors use clean
  spellings or state the advisory explicitly.

### 11.5 Diagnostics, display, benchmarks, migration

- **0.4 diagnostics:** the five pinned cases with exact locations/categories; no spurious string
  branch; 0.3 seven-finding csv-cell list; 0.3 schema byte-identical; 0.4 schema vectors;
  lone-surrogate model-only refusal (`string_unicode`, `other_validation`).
- **Display/JSON:** `--json` keeps `locator_to_json`; human run/claim/lineage show
  `csv-cell:<row>,<column>` and never the header; 0.3 human output unchanged.
- **Benchmarks (exact; reference box: GitHub `ubuntu-latest` standard runner, 4 vCPU/16 GiB,
  Python 3.12; record `nproc` and MemTotal when run elsewhere):**
  - (a) 2 columns `v,w`, header + 648 rows; rows 1-647 select column 1, cell
    `"0."+"0"*65534` (65,536 bytes), column 2 `1`; row 648 `1,😀` unselected; total
    42,403,744 bytes; each record <1 MiB. **647 results, recorded 0.0, correct digest, all
    verified.**
  - (b) header `h0..h9`, 1,000,000 data rows, each of 10 fields `"1"` (quoted), row 40 bytes,
    total 40,000,030 bytes, 10,000,000 cells; 1,000 results on the last row (100 per column),
    recorded 1, cell `1`, correct digest, verified.
  - (c) header `v`, 730,000 data rows, each cell `"0."+"0"*64` (66 bytes), row 67 bytes, total
    48,910,002 bytes; 10 results on the last 10 rows, recorded 0.0, correct digest, verified.
  - (d) metadata: 256 × 65,536-byte headers = 16,777,216 (uncapped); 255 × 65,536 + 32,768 `é` +
    `a` = 16,777,217 (capped).
  - (e) capped memory: 500k entries, under-cap and 51 MiB over-cap, one finding each.
  - (f) load-time/many-artifact RSS: post-load total, additional CSV working set, 500k one-entry
    artifacts' cap findings (record-only).
- **Migration/CLI/fixtures/suites/packaging:** the §10 fixtures; the 26-route parser contract is
  unchanged (no new route). The ten manifest-reading routes (`compare`, `leaderboard`, `matrix`,
  `impact`, `coverage`, `coverage-gate`, `coverage-diff`, `review-diff`, `impact-diff`,
  `review-gate`) each get one 0.4 load test through the dispatcher; `fingerprint-compare` refuses
  through `manifest_fingerprint`'s 0.1 guard; full suites
  3.12/3.10 and 3.14; pydantic 2.6 and 2.14; wheel+sdist build, `twine check`, and an installed-
  wheel smoke (`init`, `add`, `validate`, `inspect --json`, `migrate --to 0.4`); `dump_manifest`
  and migrate-preservation tests; the quoted texts.

## 12. Performance and memory

- Budget: additional **CSV working-set** memory above the post-load baseline ≤300 MB (asserted
  for benchmarks (a)-(c) only, §4.7); wall ≤10 s for (a)/(b)/(c) on the reference box (benchmark (e) is record-only) (a
  generous regression bound; the machine is named in §11.5), in a capped subprocess.
- Two passes over the snapshot; cap findings O(artifacts); no full-file str, no cell table, no
  retained per-result strings; manifest-proportional findings are reported separately from the
  working-set number.

## 13. RFC 4180-derived departures (closed list)

Relative to RFC 4180 and to the stdlib `csv` reader, the profile deliberately:

1. accepts comma only and performs no delimiter sniffing or alternate dialect;
2. terminates records at LF or CRLF and refuses a bare CR anywhere, including inside quotes;
3. refuses a blank record at a record boundary (stdlib/RFC 4180 tolerate empty lines), so row
   indices are unambiguous; `""` remains the empty cell;
4. requires a doubled quote for a literal quote and refuses a quote in an unquoted field;
5. refuses any byte after a closing quote other than comma/record-end/EOF (stdlib tolerates
   trailing spaces);
6. treats whitespace as data and never trims;
7. preserves a CRLF inside quotes as the two bytes `\r\n` rather than normalizing it;
8. requires exactly one header record with 1..1,024 non-empty unique parsed cells and at least
   one data record;
9. refuses ragged records;
10. treats every byte other than NUL, a bare CR, and the LF/CRLF terminator as data (TAB/VT/FF/
    C0-minus-CR/LF/NUL, DEL/C1/NEL/LS/PS included), never splitting on Unicode line boundaries;
    NUL is refused anywhere (stdlib `csv` on Python 3.11+ accepts it);
11. accepts UTF-8 only, strips one leading BOM, treats an interior BOM as data, and refuses
    invalid UTF-8;
12. enforces the §5.3 caps: the two count caps per manifest, the record/field/column/row/cell
    caps per file;
13. never uses the stdlib `csv` module in the verifier;
14. refuses an empty file and adds no record for a final terminator;
15. defines an ASCII-only numeric grammar (no leading `+`, no `.5`, no `1.`, no underscores, no
    hex, no whitespace, no thousands separators; `NaN`/`Infinity`/`-Infinity` accepted as
    JSON-compatible spellings).

## 14. Risks and open points

- New security-relevant parser; differential/fuzz/cap tests mandatory.
- Non-RFC dialects refused; separate design needed.
- Real writers' `nan`/`inf`/`-inf`/empty cannot verify.
- 16 MiB metadata cap may refuse a legitimate 10,000 × 1,678-byte-header manifest.
- A capped artifact marks the artifact broken and can propagate claim risk through `input_to`
  lineage; the `claim-at-risk` wording is imprecise for a resource cap.
- Each new kind forking a schema version is a deliberate cost; 0.4 has no path back.
- Known pre-existing defect (out of scope): a 0.3 or 0.4 string/pointer containing a lone
  surrogate loads, and the `add`/`rehash` writer path raises a bare `UnicodeEncodeError`
  traceback. Migration refuses it cleanly (`SERIALIZATION_FAILED`); fixing the edit path is a
  separate change because it would alter 0.3 behavior.

## 15. Decisions requested

- **D1 (owner):** admit schema 0.4 with the verifier in the same gate.
- **D2:** non-numeric cell → `locator-unreadable`, message pinned.
- **D3:** required `StrictStr` header, min 1, max 65,536 code points; runtime bytes; over-byte
  header → `locator-mismatch` only when the parsed file header is within the field cap; an
  over-cap file header is structurally refused first; lone surrogates model-rejected through
  `PydanticCustomError("string_unicode", ...)`.
- **D4:** row 1 = first data record; header never selectable.
- **D5:** empty/duplicate parsed headers, blank-boundary/ragged, header-only are file-wide
  `locator-unreadable`.
- **D6:** comma only, no sniffing, whitespace data, quoting transport.
- **D7:** caps as §5.3; total cells = data rows × columns.
- **D8:** no new CLI route; 0.4 schema file/docs-only.
- **D9:** the two eligibility thresholds are per manifest and counted without reads; the
  structural caps are per artifact and never summed; one cap finding per artifact (including
  over-cap), O(artifacts) and measured separately from the working set; capped path checks
  state/digest, hash/size alongside, no compare; non-csv-cell kind mismatches additive; artifact
  broken.
- **D10:** control-character profile (only NUL and bare CR refused).
- **D11:** base Manifest 0.1-0.3, `Manifest04`/`Result04`, one dispatcher; the 0.4 locator is a
  field-typed `Annotated[Optional[Union[StrictStr, JsonlLocator, CsvCellLocator]],
  BeforeValidator(...)]` (no RootModel) with `Result04` overriding `_locator_scalar_shape` and one
  module-level `TypeAdapter`; error-aware `_issue_location` with tag-mapped/tag-appended
  diagnostics; three schema description overrides.
- **D12:** `csv-cell:<row>,<column>` display; JSON keeps locator_to_json; human formatters use a
  display copy; the errata are exactly the list authorized in §16.
- **D13:** migration manifest-only; seven classes; 0.3→0.4 skips the precheck; 0.4→0.3
  `DOWNGRADE_UNSUPPORTED`.
- **D14:** classification-only pre-pass; normal/capped paths as §6; no-digest/volatile/over-cap
  return before selection and comparison.
- **D15:** 0.4-as-unknown fixtures move to 0.5; exact texts; 0.4 schema pin and vector set;
  CLI/schema hash pins; serialization tests.
- **D16:** memory budget CSV-working-set, asserted as the raw `VmHWM − baseline`; decode helper
  ≤1 MiB; manifest-proportional findings measured and reported separately; Linux-only benchmark
  with counted skips.

## 16. What acceptance authorizes

Acceptance authorizes only a separate implementation gate for schema 0.4 + `csv-cell` under these
rules, plus the **`docs/verified-value-locators-rfc.md`** v0.7→v0.8 errata (display §6/§7;
unknown-kind §3.1/§4/§8; over-cap §4.1/§9; §4.2 restoration; §4.3 `size-mismatch` row, capped and
once-per-artifact sentences, broken-set addition and the `Absent locator`/`Directory artifact
locator` row widening; §4.1 snapshot scope and over-cap clause; §4.2 step 4; §5.2 caps; §5.4
comparator; §9 gates 1, 2 and 4; §3.1 extension). It does not authorize TensorBoard, adapters, YAML, new batch
features, Windows/macOS parity, S5/R5, a release, a package version bump, or publication. The
package version stays unchanged; the 10,000-entry/16 MiB csv-cell eligibility limits are not the
general manifest-size/result-count cap reserved by §2 and §4.7.
