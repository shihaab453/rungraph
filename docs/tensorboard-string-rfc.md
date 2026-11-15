# TensorBoard string tensors — design RFC (v0.4)

Status: **DESIGN PROPOSAL. NOTHING IMPLEMENTED. NOTHING AUTHORIZED BY THIS DOCUMENT.**
A new design gate and an owner schema-0.6 admission decision are required before any code.
Schema 0.1–0.5, their readers/checkers and every delivered behavior stay frozen.

## 0. Changes in v0.4

- Folds the third review (DeepSeek): the 0.6 string parser is a separate 0.6-only pass; a
  wrong-wire `string_val` is a semantic non-string, never structural; the checker outcome table
  gets exact codes/texts; a full 0.6 admission checklist is added; truncation is explicitly
  0.6-scoped, so 0.1–0.5 output stays frozen.
- Keeps the v0.3 fixes: five-row hierarchy, two-tier unsupported distinction, parser scope,
  checker caps and 128 MiB eligibility, one-pass scanning, exact 0.6 texts, capability names,
  claim/lineage display and unknown-version fixture migration.

## 1. Problem and scope

TensorBoard writes text-like data as a scalar-shaped `TensorProto` with `dtype == DT_STRING`
and one `string_val`. The delivered reader classifies only rank-0/rank-1[1] float/double
tensors and `simple_value`, so those entries are ignored. Users record configs, commit hashes,
prompts and evaluation notes this way.

In scope: one string value per `Summary.Value`, exact identity and verification, opt-in adapter
extraction, schema 0.6, migration and display. Non-goals are in §13.

## 2. Why schema 0.6

A 0.5 manifest must validate the same on every install. A string under a 0.5 `tensorboard`
locator fails on every delivered 0.5 core by two paths:

- a scalar-shaped `DT_STRING` entry is not a supported scalar, so the 0.5 scanner reports
  `selects an unsupported scalar encoding`;
- even if such a value were selected, `compare_tensorboard` returns `None` for a non-number and
  the checker maps that to `selects an unsupported value`.

Both become `locator-unsupported-value`. Accepting strings on 0.5 would make validity depend on
the installed core. Schema 0.6 is "0.5 plus string tensors": 0.5 models, schema bytes, reader
behavior and checker behavior are unchanged; 0.6 adds `Manifest06`/`Result06`, one locator kind
and one checker route. Unknown-schema 0.5 cores fail closed on 0.6.

## 3. Reader profile and parser scope

The string profile is a **separate 0.6-only parse pass**. The delivered 0.5 `scan_scalars`
path is not modified: it never sees the string rules and keeps today's skip/refusal behavior,
so a 0.5 file containing a malformed string tensor next to a valid scalar still reads that
scalar. The 0.6 pass parses the same protected snapshot with the string rules below.

Structural failures that already refuse a file (truncation, CRC, trailing bytes, malformed
protobuf framing, over-cap) remain whole-file refusals and map to every locator of both kinds.
The string rules below are semantic: they make an entry non-string but never add a structural
refusal. In particular, a `string_val` with a wrong wire type is encoded wrongly for the string
profile but is skipped by the delivered framing grammar; it yields a non-string entry, never a
whole-file refusal.

For a `Summary.Value` whose winning oneof member is `tensor` (field 8):

- `TensorProto.dtype == 7` (`DT_STRING`); dtype is last-wins across merged occurrences;
- shape is scalar-shaped: `unknown_rank == false` and rank 0, or rank 1 with one dimension of 1;
- `tensor_content` (field 4) is absent or empty;
- after protobuf merge there is exactly one `string_val` (field 8, wire type 2);
- no typed value field is set: 5 `float_val`, 6 `double_val`, 7 `int_val`, 9 `scomplex_val`,
  10 `int64_val`, 11 `bool_val`, 12 `dcomplex_val`, 13 `half_val`, 14 `resource_handle_val`,
  15 `variant_val`, 16 `uint32_val`, 17 `uint64_val`, 18 `float8_val`. "Set" means at least one
  element after merge; a packed field with an empty payload is not set. A wrong wire type on
  any of these fields is skipped by the existing grammar and never refuses;
- the single value is strict UTF-8 (a leading BOM is kept), at most 65,536 bytes; an empty
  string is valid;
- `SummaryMetadata` and `plugin_data.plugin_name` are never read. The kind is a **string
  tensor**, not "the text plugin": TF2 writes plugin metadata on a tag's first event only.

A `string_val` with the wrong wire type makes **the entry** non-string; it never refuses the
file and never changes 0.5 structure. Any other deviation makes the entry non-string; entries
are never whole-file refusals. Extraction skips non-string entries.

**Conforming set.** For a `(tag, step)`, conforming strings are the §3-profile entries in record
order, then `Summary.Value` order. `occurrence` indexes exactly this list. A scalar-shaped
`DT_STRING` that fails §3 is *failing*; a float, histogram, image, audio, rank-2 or empty-member
entry at the same tag/step is *other*.

## 4. Locator and schema 0.6

```json
{"kind": "tensorboard-string", "tag": "config", "step": 0, "occurrence": 0}
```

- `kind` is the literal `tensorboard-string`.
- `tag`, `step`, `occurrence` keep the 0.5 `TensorboardLocator` rules.
- The locator requires an artifact whose basename contains `.tfevents` (unchanged predicate).
- `Manifest06` admits **every 0.5 locator plus `tensorboard-string`**; `tensorboard` keeps its
  exact 0.5 semantics.
- `Result06.value` does **not** constrain its type by locator kind. A recorded non-`str` is
  reachable and reported by the checker; a recorded string is not length-bounded by the model.
- Every non-null 0.6 locator requires an `artifact_id`, exactly as 0.5 object locators do. The
  0.5 schema and loader reject a `tensorboard-string` object; `Manifest06` is a distinct root
  model, not a subclass of `Manifest05`.
- `schema/runevidence-0.6.schema.json` is generated with `$id urn:runevidence:manifest:0.6`,
  root `Manifest06`, and `$defs` for `Result06`, `JsonlLocator`, `CsvCellLocator`,
  `TensorboardLocator`, `TensorboardStringLocator`; it is frozen like 0.5.

## 5. Identity, occurrence, names, order

- **Occurrence is per `(kind, tag, step)`**: text occurrence counts conforming strings only.
  Scalar occurrences, ids and names are unchanged.
- **Text result id**: the 0.5 payload plus `NUL + "text"`:
  `UTF-8(run_id) + NUL + UTF-8(artifact_id) + NUL + UTF-8(tag) + NUL + ASCII(step) +
  NUL + ASCII(occurrence) + NUL + "text"`, SHA-256, 16 hex, `tb-res-…`. Scalar ids have no
  suffix and are unchanged.
- **Text name**: `f"{tag} @ {step} (text)"`, plus `f" #{occurrence}"` when occurrence > 0. A
  `(kind, tag, step)` group spanning files uses
  `f"{tag} @ {step} (text) [{basename}] #<16 hex of SHA-256(artifact_id + NUL + occurrence +
  NUL + "text")>"`. The text digest includes the kind, so scalar and text digests cannot
  coincide.
- Groups are keyed by `(run_id, kind, tag, step)`. A scalar and a string may share a tag and
  step; the `(text)` marker and the id suffix keep names and ids distinct. The run-level
  duplicate-name check still refuses an actual duplicate.
- Name families stay disjoint: a text name ends with `)` or ` #` plus at most four digits, or a
  qualified `] #` plus 16 hex; a scalar name ends at an integer step or ` #` plus at most four
  digits. A crafted tag/basename can force the same qualified digest only within one kind; the
  kind in the digest and the `(text)` marker keep the families apart.
- **Result order with `--include-strings`**: one interleaved list in discovery order (event files
  sorted by path), then record order, then `Summary.Value` order. Scalar positions are unchanged
  relative to each other; text results appear at their record position.

## 6. Checker, outcomes, caps, display

The text route uses one protected snapshot and **one scan per artifact** for both kinds; a
structural refusal maps to every locator of both kinds. Exact outcomes and texts:

| Condition | Outcome | Code / message |
| --- | --- | --- |
| occurrence < conforming, recorded non-`str` | unsupported value | `locator-unsupported-value` / `selects an unsupported value` |
| occurrence < conforming, equal codepoints | pass | — |
| occurrence < conforming, different codepoints | mismatch | `result-value-mismatch` / `does not match the recorded value` |
| occurrence >= conforming, conforming >= 1 | not found | `locator-mismatch` / `was not found` |
| conforming == 0, failing scalar-shaped string | unsupported encoding | `locator-unsupported-value` / `selects an unsupported scalar encoding` |
| conforming == 0, no failing, other entry | unsupported value | `locator-unsupported-value` / `selects an unsupported value` |
| tag/step never appears | not found | `locator-mismatch` / `was not found` |
| structural file refusal | unreadable | existing structural outcome for both kinds |

Selection runs before the recorded-value type check.


No tolerance, no NFC/NFD, no case folding, no newline normalization, no markdown.

**Checker caps.** A 0.6 manifest participates in the same per-manifest cap as 0.5: 10,000
eligible `tensorboard` + `tensorboard-string` entries and 16 MiB of locator tag bytes, counted
without reading files. Recorded value bytes are **not** capped at validate time. A 0.6 manifest
is eligible for the 128 MiB snapshot cap and uses `file_over_cap_128`, like `tensorboard`.
A capped 0.6 manifest emits one cap finding per artifact for both kinds, with
`the manifest exceeds the tensorboard verification limit (10,000 entries or 16 MiB of tag
metadata); no TensorBoard value was read`. The 0.5 cap text is unchanged.

Human display: `tensorboard-string:step=<step>,occurrence=<n>`. Findings never echo the text.
`run_claim` and `run_lineage` `_display_details` must add `tensorboard-string` so the raw locator
dict never leaks. `run`/`claim`/`lineage` details for a `tensorboard-string` value use the same
scoped truncation.

**Truncation scope**: only results whose locator is `tensorboard-string`, and only in human
output of `compare`, `matrix`, `run`, `claim` and `lineage`. The recorded string is cut to 120
code points before escaping, then `...` is appended **outside** the closing quote; the value
cell is then escaped as today. `--json` keeps the exact value. Names, parameters, JSON-locator
strings and all 0.1–0.5 output are untouched.

`leaderboard` keeps excluding non-numeric values as `non_numeric_or_nonfinite`.

## 7. Adapter and CLI

- `TensorboardAdapter` declares `supports_include_strings = True` and
  `extract(..., include_strings: bool = False, tag_filter: TagFilter | None = None)`; both are
  explicit keywords.
- `init --include-strings` is opt-in. Without it the adapter emits **schema 0.5**, ignores
  string entries entirely (they are never matched against `--tags` and never charged), and is
  byte-identical.
- With it the adapter emits **schema 0.6**: scalar results keep 0.5 ids/names/occurrence; string
  results use §5; results are interleaved per §5.
- With `--include-strings`, `--tags` matches string tags too; a pattern matching only a string
  tag is then not a warning, and the warning text is
  `WARNING: --tags pattern {ascii(pattern)} matches no representable supported summary tag`.
  Without the flag the existing `scalar tag` text and semantics are unchanged.
- Nonconforming string entries (tables, `tensor_content`, wrong wire type, invalid UTF-8,
  over 64 KiB) are skipped silently. A conforming string with an unrepresentable tag (empty,
  invalid UTF-8, over 4,096 bytes) is also skipped; a **scalar** with an unrepresentable tag
  still refuses with the existing 0.5 text. Skipped entries never consume a result slot.
- Budgets: 10,000 results shared by both kinds; 16 MiB of tag bytes **shared** by both kinds,
  charged once per selected result (the delivered 0.5 rule); 16 MiB of selected string value
  UTF-8 bytes; 64 KiB per value; 128 MiB file and 16 MiB merged-summary caps unchanged.
  Selection streams per file, scalars and strings in record order; the checker's separate
  10,000-entry/16 MiB tag cap is described in §6 and does not read files.
- Failure precedence: existing-manifest refusal, adapter lookup, `supports_include_strings` when
  the flag is present, the `include_strings` signature contract, `supports_tag_filters` when
  `--tags` is present, the `tag_filter` signature contract, pattern validation,
  discovery/selection.
- Fixed errors:
  - `ERROR: adapter {adapter.name!r} does not support --include-strings. Nothing was written.`
  - `ERROR: adapter {adapter.name!r} cannot apply --include-strings. Nothing was written.`
  - the existing `--tags` unsupported/contract errors are unchanged.
- `init` without `--include-strings` never writes 0.6.

## 8. Migration, refusals, fixed texts

- `migrate --to 0.6` is the identity on a 0.5 manifest: version only; values, locators, names,
  ids and order unchanged. 0.3/0.4 → 0.6 is a version-only bump. 0.1/0.2 → 0.6 runs the
  existing locator precheck.
- 0.6 → 0.2/0.3/0.4/0.5 is `DOWNGRADE_UNSUPPORTED` for every target.
- `init --force` on an existing 0.6 manifest:
  `ERROR: the existing manifest declares schema 0.6. init --force would regenerate the manifest
  and cannot carry over first-class values or locators. Nothing was written. Move the manifest
  aside, run init, then run runevidence migrate PATH --to 0.6 (unless the adapter writes 0.6
  itself) and reapply the first-class values and locators.`
- `migrate` target error:
  `ERROR (UNSUPPORTED_TARGET): only --to 0.2, --to 0.3, --to 0.4, --to 0.5 or --to 0.6 is
  supported; downgrade and other versions are not available. Nothing was written.`
- `conditions` on 0.6:
  `ERROR (UNSUPPORTED_VERSION): the conditions command requires an explicit schema 0.2
  manifest. A schema 0.1 manifest can be migrated with runevidence migrate PATH --to 0.2;
  schema 0.3, 0.4, 0.5 and 0.6 have no downgrade path. Nothing was written.`
- A 0.6 manifest is refused by `comparability`, `fingerprint`, `conditions`, `rehash` and `add`
  **by `schema_version`**, not by locator `isinstance` tuples. `add` gets the same schema
  refusal until a follow-up gate. `rehash` refuses 0.6 even when it has no object locator.
- **0.6 admission checklist**, each with a regression test: loader dispatch and
  `_root_model_for` (`manifest.py`); save through `artifact_to_dict`; `Manifest.model_dump` and
  `_version_specific_shape` preserving 0.2-only fields and admitting every 0.5 locator;
  `_LOCATOR_TAGS`; `is_manifest`; `_LOCATOR_SCHEMAS` and both `has_locators` tuples; the shared
  cap and 128 MiB snapshot sets; locator routing (`_extension_matches`/`_locator_kind`); the
  `init --force` declared tuple; `_event_artifact_is_volatile` accepting `Manifest05` and
  `Manifest06`; the frozen 0.5 schema pin and the new 0.6 pin.
- `checks/results.py`: `SCHEMA_VERSION_0_6` gets the directory hint
  `Point the result at a single JSON, JSONL, CSV or TensorBoard event file artifact, or drop the
  locator.` and the unverified warning
  `result {res.id!r} has no locator in schema 0.6; its recorded value was not checked`.
- `checks/graph.py`: `locator_schema` includes `SCHEMA_VERSION_0_6`, and the `claim-at-risk` cap
  hint applies to 0.6.
- Budget text with strings:
  `the selected TensorBoard summaries exceed the 10,000-result, 16 MiB tag or 16 MiB
  string-value limit; nothing was written`. The 0.5 text is unchanged.
- Package coordination: unreleased gate. The adapter's top-level 0.6 imports mean a pre-gate
  core fails to load the adapter even for default 0.5 `init`; core and adapter must come from
  matched checkouts. The dependency floor changes only at an authorized release.

## 9. Dispatch inventory (0.5 behavior unchanged, code touched)

- `models.py`: `Manifest06`, `Result06`, `TensorboardStringLocator`, locator unions,
  `locator_to_json`, `_locator_kind` (must not fall back to the JSON `string` name),
  `Manifest.model_dump`, `_version_specific_shape`, `is_manifest`, `json_schema_0_6`
  descriptions.
- `manifest.py`: 0.6 load dispatch, `_root_model_for`, `_LOCATOR_TAGS`, `artifact_to_dict`,
  schema-version refusals.
- `migrate.py`: `TARGET_VERSIONS`, `--to 0.6`, downgrade refusals.
- `cli.py`: `--include-strings`, 0.6 init refusal, `declared` tuple, migrate choices and
  `UNSUPPORTED_TARGET` text, conditions 0.6 text, check-route refusals, CLI hash rotation.
- `tensorboard_events.py`: additive `string_val`/typed-field state and `scan_strings`; one
  region scan; `scan_scalars` behavior unchanged.
- `checks/results.py`: 0.6 dispatch, `_LOCATOR_SCHEMAS`/`has_locators`, shared cap, 128 MiB
  eligibility, text route, artifact hint, unverified warning.
- `checks/graph.py`: 0.6 locator schema and cap hint.
- `locator_profiles.py`: text comparison, `display_locator`.
- `run_compare.py`, `run_matrix.py`, `run_details.py`, `run_claim.py`, `run_lineage.py`: scoped
  display truncation and `tensorboard-string` in `_display_details`.
- `edit.py`: 0.6 refusals by version.
- `schema/runevidence-0.6.schema.json`: generated and frozen.
- Tests that use `"0.6"` as the rejected unknown version move to `"0.7"`.

## 10. Consumers

`compare` already treats equal strings as `same` and unequal as `changed` without a delta;
`matrix` records the value; `leaderboard` excludes non-numbers. The only changes are the scoped
human truncation in §6, claim/lineage display, and 0.6 acceptance in loaders. `--json` output is
exact everywhere.

## 11. Verification plan

- Real TF2 `tf.summary.text` and PyTorch `add_text` fixtures, plus hand-built `DT_STRING`
  tensors; pin the PyTorch tag suffix and field layout (`DT_STRING = 7`, `string_val = 8`,
  `tensor_content = 4`).
- Profile: multiple `string_val`; wrong wire type after `dtype`; `tensor_content`; rank 2;
  `unknown_rank`; empty string; invalid UTF-8; 64 KiB and 64 KiB + 1; leading BOM preserved;
  merged `TensorProto` with dtype last-wins; packed-empty `float_val` beside `string_val`;
  oneof sequence tensor → simple_value → tensor; a `DT_FLOAT` tensor with a stray field 8 still
  classifies as a 0.5 scalar.
- Identity: 0.5 output byte-identical without the flag; scalar ids/names/occurrence unchanged
  with strings; text ids/names; mixed tag; multifile text; crafted scalar/text digest family;
  insertion; delete-then-add.
- Checker: every ladder outcome, including occurrence out of range with a table present;
  non-`str` recorded value; failing/other entries; text removal; 60 MiB file with only a text
  locator uses 128 MiB; cap finding text and cardinality; one-pass scanning; display;
  truncation with astral and combining characters; `--json` exactness.
- Adapter/CLI: flag precedence and both capability errors; `--tags` with and without the flag;
  unrepresentable string tag skipped vs scalar refused; budgets; skipped entries; default 0.5
  bytes.
- Schema/migration: 0.6 validates; 0.5 rejects 0.6 and a `tensorboard-string` object; 0.6
  rejects a text locator without `artifact_id`; identity migration preserves 0.2-only fields;
  0.3/0.4→0.6 with jsonl/csv-cell locators; downgrade codes; init refusal; event-volatile;
  migrate/conditions/results/graph 0.6 texts; unknown-version tests moved to 0.7.
- Extra vectors: wrong-wire `string_val` expected non-string; rank-2 `DT_STRING`; `simple_value`
  at a text tag; multiple `string_val`; `tensor_content`; empty string; invalid UTF-8; 65,536 vs
  65,537; merged oneof/tensor interleaving; metadata after tensor; text vs absent/occurrence
  beyond; text and scalar at one tag/step; forced text-name digest collision; NUL/ESC/newline
  values; `--tags` matching only a text tag with and without the flag; nonconforming text tag;
  `details`/claim/lineage display; a 0.5 string value is still printed in full (freeze pin).
- Consumers: compare/matrix/run/claim/lineage/details/leaderboard on a string restart fixture.
- Full matrices 3.10/3.12/3.14, pydantic 2.6/2.14, Windows, packaging, 0.5 regression pins.

## 12. Decisions requested

- **D1:** schema 0.6 ("0.5 plus string tensors"); 0.5 frozen.
- **D2:** `tensorboard-string` locator; `tensorboard` unchanged.
- **D3:** scalar-shaped `DT_STRING`, one `string_val`, typed fields absent; plugin metadata
  ignored; nonconforming entries skipped.
- **D4:** per-kind occurrence, `(text)` names, kind in the text digest, `NUL + "text"` id
  suffix, mixed tags allowed.
- **D5:** opt-in `--include-strings` (`supports_include_strings`, `include_strings`); default
  0.5 byte-identical.
- **D6:** 64 KiB/value, 16 MiB text bytes, 10,000 shared results.
- **D7:** exact comparison, five-row ladder, scoped truncation, no new finding codes.
- **D8:** identity migration, `INIT_0_6_REFUSAL`, 0.6 refusals by schema version, fixed 0.6
  texts.
- **D9:** every 0.6 admission gate in §8 is part of the slice, including save/load dispatch,
  `_version_specific_shape`, caps, routing, `init --force` and event-volatile; 0.5 output and
  schema bytes stay unchanged.

## 13. Non-goals

Histograms, images, audio, tables/multi-row text, higher-rank tensors, graphs, hparams or
custom-scalars payloads, `tensor_content` varint framing, plugin-name filtering, cross-file text
merging, normalization or fuzzy matching, redaction tooling, TensorBoard UI/database access,
new dependencies, any byte change to 0.1–0.5, release or parity claims.
