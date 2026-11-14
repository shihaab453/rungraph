# TensorBoard scalar locators — slice D design RFC (v0.1 draft)

Status: **DESIGN PROPOSAL. NOTHING IMPLEMENTED. NOTHING AUTHORIZED BY THIS DOCUMENT.**
A design gate and an owner schema-admission decision are required before any code.

## 0. Where this fits

`docs/tool-adapters-csv-tensorboard-decision-memo.md` ranked the locator contract first, JSONL
second, CSV third, TensorBoard after. JSONL shipped with slice B (schema 0.3) and CSV cells with
slice C (schema 0.4). This RFC covers **TensorBoard scalar summaries** only.

It reuses the slice-B/C machinery: one protected snapshot per targeted artifact, strict profiles
in core, a discriminated object locator, per-artifact structural caps and per-manifest eligibility
caps, fixed outcome codes, and the parent contract's privacy/display rules.

## 1. Problem and evidence

- TensorBoard `tfevents` files are the default training log for many frameworks. A recorded
  scalar such as `loss/val` at step 40 is currently unverifiable: the `generic` adapter records
  the file with digest and size, but no result can point at a scalar.
- The adapter memo's option C is the largest surface on the roadmap: TFRecord framing, CRC,
  protobuf, duplicate `(tag, step)` pairs from restarts, TF2 `tensor` summaries versus legacy
  `simple_value`, and a dependency/security gate.
- It also flags the architectural question: locator checking lives in core
  (`checks/results.py`). If core called plugin code, `validate` would depend on which adapters a
  machine has installed.

## 2. Scope and non-goals

In scope: one `tfevents` file per artifact; scalar summaries only; an occurrence-aware locator; a
strict event-file profile with CRC verification; caps; outcomes; display/privacy; migration; an
optional adapter that records scalar results; verification and benchmarks.

Out of scope: histograms, images, audio, text, graphs, hparams, TensorBoard UI/DB, multi-file or
multi-run aggregation, tag globbing in the locator, remote/streamed logs, other adapters, YAML,
parity, release/publication, and any change to 0.1-0.4.

## 3. Where verification lives (proposed D1)

**Core owns a dependency-free reader.** `validate` must produce the same result on every machine,
whether or not an optional package is installed. The existing strict JSON/JSONL/CSV profiles are
already in core; TensorBoard joins them.

Consequences:

- Core implements a minimal TFRecord reader and a minimal protobuf wire decoder for the `Event`,
  `Summary`, `Summary.Value` and `TensorProto` scalar fields. No generated code, no third-party
  protobuf runtime, no import of `tensorboard` or `tensorflow`.
- The optional adapter package depends on core, not the reverse. Its job is discovery and
  recording results, not verification.
- The plugin-based alternative is rejected for determinism. It can be revisited only with a
  separate decision that makes `validate` explicitly depend on installed plugins.

## 4. Schema admission — 0.5 (proposed D2)

**Admit a new schema `0.5`; 0.4 stays byte-frozen.** Each new locator kind has forked a schema so
older readers refuse rather than silently warn. `0.5` adds `tensorboard` and keeps string,
`jsonl` and `csv-cell` locators.

Models: `Manifest05`/`Result05` with the locator union
`string | JsonlLocator | CsvCellLocator | TensorboardLocator`, one dispatcher keyed only on an
explicit `"0.5"` source, `results` defaulting to an empty list, and a `tensorboard` cross-field
rule (any non-null locator requires `artifact_id`).

### 4.1 Locator shape

```json
{"kind": "tensorboard", "tag": "loss/val", "step": 40, "occurrence": 0}
```

- `kind` const `tensorboard`.
- `tag` `StrictStr`, 1..4,096 code points and at most 4,096 UTF-8 bytes at runtime.
- `step` `StrictInt`, `-2**63 .. 2**63-1` (the TensorBoard step is a signed 64-bit integer;
  negative steps are admitted because the format does not forbid them).
- `occurrence` `StrictInt`, `0..2**31-1`, default `0`: the zero-based position among the scalar
  events that match `(tag, step)` in **file order**. Duplicate `(tag, step)` pairs are normal after
  a restart, so this is part of identity, not a tie-break heuristic.
- The locator carries no file path; bytes come from `Result.artifact_id`.
- The artifact path's **filename must contain `.tfevents`** (case-insensitive). This matches
  `events.out.tfevents.<time>.<host>...` without requiring a suffix. A locator on any other path
  is `locator-kind-mismatch`.

### 4.2 Serialization and diagnostics

`Result.locator` stores the bare model (no wrapper); `model_dump` treats 0.5 like 0.4; a
`tensorboard` dict serializes with `tag`, `step`, `occurrence`; an absent `occurrence` is written
as `0` only when the caller requested defaults (`exclude_defaults=False`), matching 0.2+ fields.

Diagnostic vectors, pinned at implementation:

| Input | Type | Location | Category |
|---|---|---|---|
| `step: "40"` | `int_type` | `/results/0/locator/step` | `invalid_type` |
| unknown kind | `union_tag_invalid` | `/results/0/locator/kind` | `invalid_value` |
| missing kind | `union_tag_not_found` | `/results/0/locator/kind` | `missing_required` |
| empty `tag` | `string_too_short` | `/results/0/locator/tag` | `invalid_value` |
| extra key | `extra_forbidden` | `None` | `invalid_value` |
| non-str/non-dict locator | `value_error` | `/results/0/locator` | `invalid_value` |

A lone surrogate in `tag` is model-rejected (`string_unicode`, `other_validation`); pointer
strings keep the 0.3 grammar.

## 5. Event-file profile (proposed D3)

The artifact is read through the slice-B protected snapshot; the same bytes are hashed and parsed.
The profile is strict and platform-independent.

### 5.1 TFRecord framing

Each record is:

```
uint64 length (little-endian)
uint32 masked_crc32c(length bytes)
byte[length] data
uint32 masked_crc32c(data)
```

- CRC32C (Castagnoli) is implemented in core with a table; the mask is
  `((crc >> 15) | (crc << 17)) + 0xa282ead8 (mod 2**32)`.
- The whole file must be a sequence of complete records. A truncated trailing record, a length
  that exceeds the remaining bytes, a zero-length data record, a CRC mismatch on either field, or
  trailing bytes after the last complete record are structural refusals.
- `length > 16 MiB` is a structural refusal; `length` is checked before allocation.
- An empty file is a structural refusal.

### 5.2 Protobuf subset

Decode only the wire types the messages need: varint (0), 64-bit (1), length-delimited (2), 32-bit
(5). Groups (3/4) and unknown wire types are refused. Varints are bounded to 10 bytes; length
delimiters must fit the record; nested message depth is bounded (4); unknown fields are skipped
by wire type, never by length guesses.

- `Event`: `wall_time` (1, double) ignored, `step` (2, int64) retained, `summary` (5, Summary)
  parsed. Other top-level fields (file_version, graph_def, meta_graph_def, log_message, session
  status, tagged_run_metadata) are skipped.
- `Summary`: `value` (1) repeated `Value`.
- `Summary.Value`: `tag` (1, string), `simple_value` (2, float), `tensor` (8, TensorProto). A value
  that sets more than one of `simple_value`/`tensor` is structurally refused (ambiguous scalar).
  Other value kinds (histogram, image, audio) are skipped for indexing but still CRC-checked.
- `TensorProto` scalar: admit `dtype` `DT_FLOAT` (1) and `DT_DOUBLE` (2) only. A scalar has one
  element: `tensor_content` (4) of 4 or 8 bytes, or `float_val` (5)/`double_val` (6) with exactly
  one element. Any other dtype, element count, or encoding is `locator-unsupported-value` for a
  matching locator; non-scalar tensors are not indexed.
- A `simple_value` event's step is the enclosing `Event.step`; a `tensor` event's step is the
  same. Tags are byte strings decoded as UTF-8; invalid UTF-8 is a structural refusal.

### 5.3 Selection and duplicates

- Index entries are `(tag, step, occurrence, value)` in file order, one entry per scalar
  `Summary.Value`.
- `occurrence` counts only entries matching the locator's exact tag and step.
- A tag/step with fewer than `occurrence + 1` entries is `locator-mismatch` (`was not found`);
  a `(tag, step)` that exists only as a non-scalar summary is `locator-mismatch` (`was not
  selectable`), because the profile indexes scalars only.
- Tag comparison is exact (no Unicode normalization, no case folding, no glob).
- A file may contain multiple event records with the same tag/step; they are distinct entries.

### 5.4 Numbers

- Decoded `float32` values are widened to Python `float`; the source precision is float32
  (~7 significant digits), while `simple_value` is also float32.
- Comparison uses `compare_values` with a TensorBoard-specific tolerance: relative `1e-6`,
  absolute `1e-12` for any float32-origin value; `int`/`int` remains exact; `NaN` equals `NaN`;
  infinities equal themselves; booleans never equal numbers.
- A decoded non-finite value compares by those rules when the manifest can record it; otherwise
  it is `locator-unsupported-value`.
- Recorded `str`/`bool`/`None` is `locator-unsupported-value`.

## 6. Caps (proposed D4)

Per-artifact structural caps (a violation is a per-result `locator-unreadable`):

| Cap | Unit | Value |
|---|---|---|
| file | raw on-disk bytes | 128 MiB |
| record | `length` in a TFRecord | 16 MiB |
| tag | UTF-8 bytes of one tag | 4 KiB |
| protobuf depth | nested messages | 4 |

Per-manifest eligibility caps (counted without reading a file; one cap finding per eligible
artifact):

| Cap | Unit | Value |
|---|---|---|
| tensorboard results | manifest-eligible `tensorboard` entries | 10,000 |
| tag metadata | sum of `tag` UTF-8 bytes over eligible entries | 16 MiB |

- Manifest-eligible: artifact non-directory, filename contains `.tfevents`, `sha256` non-null,
  `volatile` false.
- The 128 MiB file cap keeps one snapshot plus decode state inside the 300 MB working-set budget.
  A future streaming design could raise it; that is a separate decision.
- Structural caps are per artifact and never summed across artifacts; the two eligibility caps
  are per manifest and counted by the no-read pre-pass, exactly as slice C.

## 7. Outcomes and precedence

The order mirrors slice C:

1. Directory artifact: ordinary artifact check (zero or more findings) then one
   `directory-locator-unsupported` per result; return.
2. File state: `missing`/`reference-failed`/`unreadable`/`changed`/path refusals → one artifact
   finding, zero per-result.
3. Kind/extension: per result `locator-kind-mismatch` (`needs a TensorBoard event artifact`), then
   restore the artifact `no-digest` warning only when no usable locator remains and the state is
   `no-digest`.
4. Volatile → per-result `locator-unverified`; return.
5. No digest → per-result `locator-no-digest`; size still compared; return.
6. Over-cap file → per-result `locator-unreadable`; return.
7. Structural pass → per-result `locator-unreadable` with
   `could not be read under the strict TensorBoard profile`; selection/compare suppressed.
8. Capped manifest → one artifact-level `locator-unreadable` per eligible artifact, no reads.
9. Selection: per result `locator-mismatch` (`was not found` / `was not selectable`).
10. Recorded type: per result `locator-unsupported-value`.
11. Decode: per result `locator-unsupported-value` or `locator-unreadable` (D2 message).
12. Compare: per result `result-value-mismatch`.

Capped path: an eligible artifact in state `ok`/`over-cap` gets the one cap finding; missing/path
states get the ordinary artifact finding and no cap; non-tensorboard locators on the capped
artifact still get their kind finding. `hash-mismatch`/`size-mismatch` stay alongside the cap
finding; no scalar is read.

## 8. Display, privacy and injection

- Human display: `tensorboard:step=<step>,occurrence=<n>`, never the tag. Tags can contain secrets
  and are the scalar's assertion, analogous to a CSV header; `--json` keeps the structured locator
  including `tag`.
- Findings never echo the tag, scalar text, protobuf text, CRC details or OS error text. They may
  name `Finding.path`, the artifact path, digest prefixes, and recorded result fields.
- A result's recorded `name` may be the tag; that is recorded manifest data and is displayed as
  today.
- Human claim/lineage output renders a display copy of nested locators for `tensorboard` (and the
  existing `csv-cell` rule); 0.3/0.4 human bytes stay unchanged.
- No terminal control characters or spreadsheet formulas from tags/cells reach reports: tags are
  never printed, and existing report escaping rules apply.

## 9. Adapter (proposed D5)

An optional package `packages/runevidence-tensorboard` loaded through the existing adapter entry
point:

- `discover(root)` finds files whose name contains `.tfevents` (case-insensitive), deterministically
  sorted, and records each as a file artifact with digest and size.
- `extract(root, discovery)` records scalar results for a selected tag set:
  - default: all scalar tags, at most 10,000 results per event file;
  - `--tags` (or adapter config) accepts an exact tag or a simple `*` glob; the locator always
    stores the exact tag, never the pattern;
  - one event file is one run (run id derived from the file name, deduplicated);
  - result id is deterministic from `(tag, step, occurrence)` and the run id; a collision is an
    adapter error, not a silent overwrite;
  - recorded `value` is the decoded scalar widened to float; the locator carries the exact
    `(tag, step, occurrence)`;
  - over 10,000 scalar results: the adapter records the artifact and refuses to emit results,
    with a fixed message; it never truncates silently.
- The adapter imports the core reader; it never re-implements framing/CRC/protobuf.
- No adapter is required to verify: a hand-authored 0.5 manifest verifies with core only.

## 10. Compatibility and migration (proposed D6)

- 0.1-0.4 are unchanged; 0.4 stays byte-frozen and refuses `tensorboard`.
- 0.3/0.4 → 0.5: version-only bump (0.5 admits every earlier locator); values/locators/backups
  preserved.
- 0.1/0.2 → 0.5: the same manifest-only precheck as slice C; a 0.2 string locator on a non-`.json`
  artifact is refused.
- 0.5 → 0.4/0.3/0.2: `DOWNGRADE_UNSUPPORTED`; never `INTERNAL_ERROR`.
- `rehash` with any tensorboard object locator refuses `LOCATOR_REFRESH_UNSUPPORTED`; the adapter
  can re-record.
- Conditions/comparability/fingerprint refuse 0.5 as they do 0.4; the `schema` route stays
  file/docs-only for 0.5.
- A pre-0.5 binary replaces instead of rewriting a 0.5 manifest (documented limitation, as for
  0.4).

## 11. Verification plan

- **Framing:** CRC32C standard vectors; masked CRC; valid/invalid length and data CRCs; truncated
  length, truncated data, trailing bytes, zero-length record, record over cap.
- **Protobuf:** varint bounds, 64-bit/32-bit fields, unknown fields skipped by wire type, groups
  refused, nested depth over cap, truncated length-delimited field, invalid UTF-8 tag.
- **Messages:** legacy `simple_value`; TF2 scalar `tensor` with `tensor_content` float and double,
  and one-element `float_val`/`double_val`; both scalar fields set (refuse); non-scalar summary
  ignored but CRC-checked; header `file_version` ignored; unknown top-level fields ignored.
- **Selection:** tag/step exact; duplicate pairs and occurrence; occurrence out of range; tag only
  as non-scalar (not selectable); negative step; multiple files out of scope (kind mismatch or
  single artifact rule).
- **Numbers:** float32 widening and tolerance; int/int exact; NaN/inf; recorded str/bool/None.
- **Precedence:** directory, missing, path refusals, kind, volatile/no-digest, over-cap,
  structural, capped, size/hash, value mismatch; no-digest size comparison; capped mixed locators.
- **Caps:** record 16 MiB, tag 4 KiB, file 128 MiB, 10,000 entries/10,001, 16 MiB tag metadata
  edges, many-artifact cap findings.
- **Privacy:** tag canary absent from run/claim/lineage human output and findings; present in
  `--json`; 0.3/0.4 human bytes unchanged.
- **Migration/CLI:** 0.3/0.4→0.5 version-only; 0.1/0.2 precheck classes; downgrade; help text.
- **Oracle/fuzz:** generate event files with an independent test-only writer (protobuf built by
  hand) and compare decoded entries; seeded fuzz over framing/protobuf mutations, asserting the
  closed refusal set and no crash/parser text.
- **Benchmarks:** (a) 10,000 scalar events, 10 results; (b) 1,000,000 scalar events near 100 MiB,
  10 results; (c) a 127 MiB file at the cap. Assert wall ≤20 s and additional working set
  ≤300 MB on the reference box.

## 12. Risks and open points

- TensorBoard's format is stable but not a versioned contract; TF2 `tensor` encodings vary
  (float_val, tensor_content, bfloat16 in the wild). The profile admits float/double scalars only
  and refuses the rest for a matching locator.
- CRC32C and protobuf are security-relevant parsers; caps, bounds and fuzzing are mandatory.
- Duplicate `(tag, step)` semantics are pinned as occurrence; users must understand that a
  restart changes which entry an occurrence names.
- float32 tolerance differs from JSON/CSV; documented and tested.
- Tags can contain secrets; the locator display hides them, but a result `name` may carry one.
- The 128 MiB file cap may refuse large event files; a streaming design is separate.
- One event file per artifact excludes multi-file logs; a directory/multi-file design is separate.
- Adapter tag selection and run identity are conventions; hand-authored manifests do not depend
  on them.

## 13. Decisions requested

- **D1:** verification in core with a dependency-free reader; adapter is optional and unprivileged.
- **D2 (owner):** admit schema 0.5 with `Manifest05`/`Result05` and the `tensorboard` kind.
- **D3:** strict TFRecord/protobuf profile as §5; CRC32C; float/double scalars only.
- **D4:** caps as §6 (128 MiB file, 16 MiB record, 4 KiB tag, 10,000 entries, 16 MiB metadata).
- **D5:** optional adapter with exact-tag/glob selection, one file = one run, 10,000-result
  refusal.
- **D6:** migration 0.3/0.4 version-only, 0.1/0.2 precheck, no downgrade.
- **D7:** display `tensorboard:step=<step>,occurrence=<n>`; tags only in `--json`; no tag in
  findings.
- **D8:** tolerance: rel `1e-6`, abs `1e-12` for float32-origin values; int/int exact.
- **D9:** occurrence is part of locator identity; tag/step exact, no normalization or glob.

## 14. What acceptance would authorize

Acceptance authorizes only a separate implementation gate for schema 0.5 with the core reader and
the optional adapter under these constraints, plus the parent-contract errata the gate lists. It
does not authorize release, version bump, publication, multi-file logs, other summary kinds, or
parity claims.
