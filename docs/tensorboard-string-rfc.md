# TensorBoard string tensors — design draft (v0.12)

Status: **DESIGN PROPOSAL. NOTHING IMPLEMENTED. NOTHING AUTHORIZED BY THIS DOCUMENT.**
A new design gate and an owner schema-0.6 admission decision are required before any code.
Schema 0.1–0.5, their readers/checkers and every delivered behavior stay frozen.

## 0. Changes in v0.12

- Folds the round-5 review (Opus): fields 7/9–18 with wire types 0/1/5 are semantic non-strings
  while 3/4/6/7 stay structural; the deviation sentence is subject to the structural refusals;
  the vector is split; the duplicate 256 sentence and the `run`/`details` wording are fixed.
- Keeps the v0.11 edits: path-based volatile rule, field-8 naming, 120/256 inventory split.

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

The 0.6 path uses a **combined scanner**, `scan_tensorboard_06` (the adapter's
`iter_supported_summaries`): one traversal of the protected snapshot classifies scalars and
strings with the identical structural grammar. The delivered `scan_scalars` and
`iter_supported_scalars` are untouched and remain the 0.5 and default-`init` paths; they never
execute string code and keep today's skip/refusal behavior, so a 0.5 file containing a semantically malformed
string tensor (not a structural wire refusal) next to a valid scalar still reads that scalar.

Structural failures that already refuse a file remain whole-file refusals and map to every
locator of both kinds. The delivered grammar refuses: wire types 3, 4, 6 and 7 on any field (`_read_key`); wrong wire types on **TensorProto**
fields 1, 2, 4, 5 and 6; and a packed `float_val`/`double_val` whose length is not a multiple of
4/8. Event/Summary/Summary.Value/Shape/Dim wire checks, framing, CRC and the 16 MiB summary cap
are covered by the same general rule. Those refusals are unchanged and apply to the 0.6
traversal too; a wrong wire type on `Summary.Value` field 8 (the `tensor` member) stays
structural.

The string rules below are semantic: they make an entry non-string but never add a structural
refusal. A **TensorProto field 8 (`string_val`)** key with wire type 0, 1 or 5 is skipped and makes the
entry non-string; wire types 3/4/6/7 on that field remain the structural refusal above. A valid `string_val` plus a
field-8 occurrence with wire type 0/1/5 is **non-string** (the wrong occurrence wins); a
3/4/6/7 occurrence is structural.

For a `Summary.Value` whose winning oneof member is `tensor` (field 8):

- `TensorProto.dtype == 7` (`DT_STRING`); dtype is last-wins across merged occurrences;
- shape is scalar-shaped: `unknown_rank == false` and rank 0, or rank 1 with one dimension of 1;
- `tensor_content` (field 4) is absent or empty;
- after protobuf merge there is exactly one `string_val` (field 8, wire type 2);
- no typed value field is set: 5 `float_val`, 6 `double_val`, 7 `int_val`, 9 `scomplex_val`,
  10 `int64_val`, 11 `bool_val`, 12 `dcomplex_val`, 13 `half_val`, 14 `resource_handle_val`,
  15 `variant_val`, 16 `uint32_val`, 17 `uint64_val`, 18 `float8_val`. "Set" means any
  occurrence after merge except a zero-length wire-2 payload. Fields 5 and 6 keep the delivered
  structural refusal on a wrong wire type; fields 7 and 9–18 are skipped by the delivered
  grammar, so a wire type 0/1/5 there makes the entry **non-string**, while wire types 3/4/6/7
  remain the `_read_key` structural refusal;
- the single value is strict UTF-8 (a leading BOM is kept), at most 65,536 bytes; an empty
  string is valid;
- `SummaryMetadata` and `plugin_data.plugin_name` are never read. The kind is a **string
  tensor**, not "the text plugin": TF2 writes plugin metadata on a tag's first event only.

Subject to the structural refusals above, a semantic deviation in any field named in this
profile makes the entry non-string; fields outside the profile (for example `version_number` 3)
are skipped by the delivered grammar and do not change classification. Extraction skips
non-string entries.

**Conforming set.** For a `(tag, step)`, conforming strings are the §3-profile entries in record
order, then `Summary.Value` order. `occurrence` indexes exactly this list. A scalar-shaped
`DT_STRING` that fails §3 is *failing*; every remaining winning `Summary.Value` at the same
tag/step (float, scalar-shaped wrong dtype, rank-2, `unknown_rank`, histogram, image, audio,
empty member, and so on) is *other*.

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
- Every non-null 0.6 locator requires an `artifact_id`, as every non-null 0.5 locator
  (including string pointers) does. The
  0.5 schema and loader reject a `tensorboard-string` object; `Manifest06` is a distinct root
  model, not a subclass of `Manifest05`.
- `schema/runevidence-0.6.schema.json` is generated with `$id urn:runevidence:manifest:0.6`,
  root `Manifest06`, and `$defs` for `Result06`, `JsonlLocator`, `CsvCellLocator`,
  `TensorboardLocator`, `TensorboardStringLocator`; it is frozen like 0.5.

## 5. Identity, occurrence, names, order

- **Occurrence is per `(artifact_id, kind, tag, step)` — within one event file.** Text
  occurrence counts conforming strings in that file only. Scalar occurrences, ids and names are
  unchanged. The adapter keeps one per-file counter; the checker scans one artifact.
- **Text result id**: the multifile-updated 0.5 payload plus `NUL + "text"`:
  `UTF-8(run_id) + NUL + UTF-8(artifact_id) + NUL + UTF-8(tag) + NUL + ASCII(step) +
  NUL + ASCII(occurrence) + NUL + "text"`, SHA-256, 16 hex, `tb-res-…`. Scalar ids have no
  suffix and are unchanged.
- **Text name**: `f"{tag} @ {step} (text)"`, plus `f" #{occurrence}"` when occurrence > 0. A
  run-level `(kind, tag, step)` group spanning files uses
  `f"{tag} @ {step} (text) [{basename}] #<16 hex of SHA-256(UTF-8(artifact_id) + NUL +
  ASCII(occurrence) + NUL + "text")>"`. The text digest includes the kind, so scalar and text
  digests cannot coincide.
- Groups are keyed by `(run_id, kind, tag, step)`. A scalar and a string may share a tag and
  step; the `(text)` marker and the id suffix keep names and ids distinct. The run-level
  duplicate-name check still refuses an actual duplicate.
- Name families are disjoint by the digest, not by a syntactic suffix. A crafted tag/basename
  can make a scalar name and a text name share a prefix (for example a scalar tag containing
  `(text) [x.tfevents`), but equal names would force equal trailing 16-hex digests, and the
  text digest payload includes `NUL + "text"` while the scalar payload does not. The families
  therefore meet only through a 2^-64 truncation collision, which the run-level duplicate-name
  refusal catches within a run.
- **Result order with `--include-strings`**: one interleaved list in discovery order (event files
  sorted by path), then record order, then `Summary.Value` order. Scalar positions are unchanged
  relative to each other; text results appear at their record position.

## 6. Checker, outcomes, caps, display

On a 0.6 artifact, `scan_tensorboard_06` performs **one traversal** of the protected snapshot and
classifies both kinds; `scan_scalars` remains the unchanged 0.5 path. A structural refusal maps
to every locator of both kinds. Exact outcomes and texts:

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
A capped 0.6 manifest emits **exactly one** cap finding per eligible artifact, covering both
kinds, including when the manifest has only scalar locators, with
`the manifest exceeds the tensorboard verification limit (10,000 entries or 16 MiB of tag
metadata); no TensorBoard value was read`. The 0.5 cap text is unchanged.

Human display: `tensorboard-string:step=<step>,occurrence=<n>`. Findings never echo the text.
`run_claim._display_details` must add `tensorboard-string` so the raw locator dict never leaks;
`run_lineage` reuses it through `_display_row`.

**Truncation scope**: only results whose locator is `tensorboard-string`, and only in human
output of `compare`, `matrix` and `run`. When `isinstance(value, str) and len(value) > 120`,
render `json.dumps(value[:120], ensure_ascii=True) + "..."`; otherwise, including every non-`str`
recorded value reachable under §4, keep the delivered rendering.
`claim` and `lineage` keep their existing 256-character shortening (the marker stays inside the
quotes) and only gain the `tensorboard-string` locator display; `run` is the `run_details.py`
surface. `--json` keeps the exact value.
Names, parameters, JSON-locator strings and every 0.1–0.5 value keep their delivered rendering.

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
  invalid UTF-8, over 4,096 bytes) is also skipped. A **scalar** with an unrepresentable tag
  keeps the delivered 0.5 behavior: refuse without `--tags`, skip with it. Skipped entries never
  consume a result slot.
- Under `--include-strings` with `--tags`, a string entry is classified under §3 **before** tag
  matching: nonconforming entries and entries with unrepresentable tags are never passed to
  `TagFilter.matches`. Conforming string tags share the same `TagFilter` cache and
  50,000,000-unit matching budget with supported scalars; each distinct `(tag, pattern)` pair is
  charged at most once across both kinds. The 16 MiB string-value limit sits in the `caps`
  tier: path > profile > unrepresentable scalar tag > too-expensive pattern > caps (10,000
  results / 16 MiB tag / 16 MiB string value) > fixed-id collision > result-name collision >
  volatile.
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
  - a scalar with an unrepresentable tag under `--include-strings` uses
    `a scalar tag cannot be represented in schema 0.6; nothing was written`; the delivered 0.5
    text is unchanged without the flag;
  - `ERROR: adapter {adapter.name!r} does not support --include-strings. Nothing was written.`
  - `ERROR: adapter {adapter.name!r} cannot apply --include-strings. Nothing was written.`
  - the existing `--tags` unsupported/contract errors are unchanged.
- The TensorBoard adapter never writes 0.6 without `--include-strings`; `init` passes the flag
  only when given. A third-party adapter that writes 0.6 on its own is out of contract for this
  gate.

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
- The delivered 0.5 `init` refusal advice stays correct for 0.5; a user who wants strings moves
  the manifest aside and runs `init --include-strings`. No `migrate --to 0.6` step is needed
  because the adapter writes 0.6 directly.
- `migrate` target error:
  `ERROR (UNSUPPORTED_TARGET): only --to 0.2, --to 0.3, --to 0.4, --to 0.5 or --to 0.6 is
  supported; downgrade and other versions are not available. Nothing was written.`
- `conditions` on 0.6:
  `ERROR (UNSUPPORTED_VERSION): the conditions command requires an explicit schema 0.2
  manifest. A schema 0.1 manifest can be migrated with runevidence migrate PATH --to 0.2;
  schema 0.3, 0.4, 0.5 and 0.6 have no downgrade path. Nothing was written.`
- `comparability`, `fingerprint` and `conditions` refuse 0.6 **by `schema_version`**, not by
  locator `isinstance` tuples.
- `add`, `add --directory`, `rehash` and `rehash --directory` follow the delivered 0.4/0.5
  rules, not a new version refusal:
  - `add` and the directory authoring/audit helpers accept 0.6 through `validate_manifest`
    (they add artifacts only and never touch results or locators);
  - `rehash` keeps `type(result.locator) is not str`: a 0.6 manifest containing any object
    locator, including `tensorboard-string`, refuses with the delivered
    `ERROR (LOCATOR_REFRESH_UNSUPPORTED): rehash cannot refresh a manifest that contains an
    object locator. Nothing was written.` A 0.6 manifest with only JSON-pointer strings or null
    locators is rehashable, exactly like 0.4/0.5; a `tensorboard-string` is an object locator
    and therefore refuses.
  - the delivered no-digest hint stays correct for JSON-pointer/null-only 0.6 manifests.
- **0.6 cap wiring** (part of the checklist below): `_locator_kind` returns
  `tensorboard-string`; `capped_kinds` contains both TensorBoard kinds so neither is skipped;
  `_csv_cell_cap`, `_tensorboard_cap` and `_tensorboard_snapshot_ids` include 0.6; the
  `kind == "tensorboard"` branch that selects `file_over_cap_128` includes
  `tensorboard-string`.
- **0.6 admission checklist**, each with a regression test: loader dispatch and
  `_root_model_for` (`manifest.py`); save through `artifact_to_dict`; `Manifest.model_dump` and
  `_version_specific_shape` preserving 0.2-only fields and admitting every 0.5 locator;
  `_LOCATOR_TAGS`; `is_manifest`; `_LOCATOR_SCHEMAS`, both `has_locators` tuples, the
  `_csv_cell_cap` 0.6 branch, the shared cap and 128 MiB snapshot sets; locator routing
  (`_extension_matches`, `_expected_extension`, `_locator_kind`, `display_locator`); the
  `init --force` declared tuple; `_event_artifact_is_volatile` accepting `Manifest05` and
  `Manifest06` and checking **every regular-file event artifact by path**, independent of
  locator kind (the multifile RFC's rule); if a legacy result-targeted form remains, its
  targeted set must include both `TensorboardLocator` and `TensorboardStringLocator`;
  `TensorboardStringLocator` is **not** a subclass of `TensorboardLocator` (routing and caps use
  `isinstance`); `json_schema_0_6()` rewrites every inherited 0.3/0.4/0.5
  description to 0.6, gives `Result06.properties.locator.oneOf` six branches and the `if`/`then`
  `artifact_id` rule; the frozen 0.5 schema pin and the new 0.6 pin.
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
  `locator_to_json`, `Manifest.model_dump`, `_version_specific_shape`, `is_manifest`,
  `json_schema_0_6` descriptions.
- `manifest.py`: 0.6 load dispatch, `_root_model_for`, `_LOCATOR_TAGS`, `artifact_to_dict`,
  schema-version refusals.
- `migrate.py`: `TARGET_VERSIONS`, `--to 0.6`, downgrade refusals.
- `cli.py`: `--include-strings`, 0.6 init refusal, `declared` tuple, migrate choices and
  `UNSUPPORTED_TARGET` text, conditions 0.6 text, check-route refusals, CLI hash rotation.
- `tensorboard_events.py`: `scan_tensorboard_06` (one combined traversal) and the adapter's
  `iter_supported_summaries`; `scan_scalars`/`iter_supported_scalars` untouched.
- `checks/results.py`: 0.6 dispatch, `_LOCATOR_SCHEMAS`/`has_locators`, `_locator_kind`
  (must return `tensorboard-string`, not the JSON `string` fallback), `_expected_extension`,
  `capped_kinds`, the 128 MiB branch, text route, artifact hint, unverified warning.
- `checks/__init__.py`: shared caps, `_csv_cell_cap` 0.6, 128 MiB eligibility.
- `checks/graph.py`: 0.6 locator schema and cap hint.
- `locator_profiles.py`: text comparison, `display_locator`.
- `run_compare.py`, `run_matrix.py`, `run_details.py`: the guarded 120-code-point rendering for
  `tensorboard-string` result values.
- `run_claim.py`, `run_lineage.py`: keep the delivered 256-character rule; add
  `tensorboard-string` to `_display_details` (lineage reuses it via `_display_row`).
- `edit.py`: no new 0.6 version refusal; `add` and `rehash` keep the delivered 0.4/0.5 rules.
- `run_comparability.py`: its deny-list must include 0.6 explicitly; a missing entry would
  silently admit the manifest.
- `packages/runevidence-tensorboard/src/runevidence_tensorboard/adapter.py`: the §7
  `supports_include_strings`/`include_strings` contract, 0.6 emission, budgets and interleaving.
- `cli.py`/`migrate.py` help and docstrings that still say "0.5" gain 0.6.
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
- Profile: multiple `string_val`; valid plus wrong-wire field 8; wire types 0/1/5 vs 3/4/6/7;
  wrong wire on fields 1/2/4/5/6; packed length not a multiple of 4/8; `tensor_content`; rank 2;
  `unknown_rank`; empty string; invalid UTF-8; 64 KiB and 64 KiB + 1; leading BOM preserved;
  merged `TensorProto` with dtype last-wins; packed-empty `float_val` beside `string_val`;
  oneof sequence tensor → simple_value → tensor; a `DT_FLOAT` tensor with a stray field 8 still
  classifies as a 0.5 scalar.
- Identity: 0.5 output byte-identical without the flag; scalar ids/names/occurrence unchanged
  with strings; text ids/names; mixed tag; multifile text; crafted scalar/text digest family;
  insertion; delete-then-add.
- Checker: every ladder outcome, including occurrence out of range with a table present;
  non-`str` recorded value; failing/other entries; text removal; 60 MiB file with only a text
  locator uses 128 MiB; cap finding text and exact cardinality (one per artifact, scalar-only
  manifests included); one 0.6 traversal; display;
  truncation with astral and combining characters; `--json` exactness.
- Adapter/CLI: flag precedence and both capability errors; `--tags` with and without the flag;
  unrepresentable string tag skipped vs scalar refused; budgets; skipped entries; default 0.5
  bytes.
- Schema/migration: 0.6 validates; 0.5 rejects 0.6 and a `tensorboard-string` object; 0.6
  rejects a text locator without `artifact_id`; a `tensorboard-string` on a non-`.tfevents`
  artifact reports the TensorBoard extension, not `.csv`/`.jsonl`; `add` on 0.6 succeeds;
  `rehash` succeeds on JSON-pointer/null-only 0.6 and refuses when any object locator, including
  `tensorboard-string`, is present; identity
  migration preserves 0.2-only fields;
  0.3/0.4→0.6 with jsonl/csv-cell locators; downgrade codes; init refusal; event-volatile;
  migrate/conditions/results/graph 0.6 texts; unknown-version tests moved to 0.7.
- Extra vectors: two files in one run each with `cfg@0` text use occurrence 0 per file; 0.5
  human output byte-identical (claim/lineage keep 256); 10,001 csv-cell entries and both
  TensorBoard kinds under the shared caps; a 60 MiB file with only text locators uses 128 MiB;
  a text locator on a `.json` artifact reports the TensorBoard extension; wrong-wire
  `string_val` expected non-string; rank-2 `DT_STRING`; `simple_value`
  at a text tag; multiple `string_val`; `tensor_content`; empty string; invalid UTF-8; 65,536 vs
  65,537; merged oneof/tensor interleaving; metadata after tensor; text vs absent/occurrence
  beyond; text and scalar at one tag/step; forced text-name digest collision; NUL/ESC/newline
  values; `--tags` matching only a text tag with and without the flag; nonconforming text tag;
  the 0.6 scalar-tag refusal text; a non-empty typed field beside `string_val`; field 7/9–18
  wire 0/1/5 is non-string; field 7/9–18 wire 3/4/6/7 is structural; a text-only event file
  matched by `--volatile` is refused; text-only 0.6 cap cardinality; scalar/text interleaving
  order; the full §7 caps-tier precedence (path > profile > unrepresentable scalar tag >
  too-expensive > caps > fixed-id collision > result-name collision > volatile);
  comparability 0.6 refusal; `add --directory`/`rehash --directory` on 0.6; `run_details.py`
  and claim/lineage display; a 0.5 value keeps its delivered rendering.
- Consumers: compare/matrix/run (details)/claim/lineage/leaderboard on a string restart
  fixture;
  truncation exactly at 120/121 and with astral/combining characters; claim/lineage keep their
  256-character rule; a 0.5 value keeps its delivered rendering.
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
- **D7:** exact comparison, seven selection rows plus the structural row, scoped truncation,
  no new finding codes.
- **D8:** identity migration, `INIT_0_6_REFUSAL`, version refusals by comparability/fingerprint/
  conditions, add/rehash per 0.4/0.5, fixed 0.6 texts.
- **D9:** every 0.6 admission gate in §8 is part of the slice, including save/load dispatch,
  `_version_specific_shape`, caps, routing, `init --force` and event-volatile; 0.5 output and
  schema bytes stay unchanged.

## 13. Non-goals

Histograms, images, audio, tables/multi-row text, higher-rank tensors, graphs, hparams or
custom-scalars payloads, `tensor_content` varint framing, plugin-name filtering, cross-file text
merging, normalization or fuzzy matching, redaction tooling, TensorBoard UI/database access,
new dependencies, any byte change to 0.1–0.5, release or parity claims.
