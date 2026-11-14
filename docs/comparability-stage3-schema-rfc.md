# Stage 3 comparability schema and versioning RFC (v0.3)

Status: REVISED AFTER TWO FOCUSED RE-REVIEWS — design ready for a bounded 3b+3c
implementation gate; not implemented.
Date: 2026-11-11.
Supersedes v0.2 (SHA-256 c1e1d9ab...d091) and v0.1 (59421bbe...4653).
Evidence: `docs/comparability-dogfood-2026-11-11.md`, Stage 2 contract v0.3,
`examples/comparability-recorded/`, Stage 2b CLI.

Panel: both v0.2 re-reviews ACCEPT WITH CHANGES. The remaining blockers were:
(1) a complete parent/selector/environment presence matrix; (2) scope strict selector IDs
without changing 0.1 acceptance; (3) published-schema nested-null semantics; (4) executable
canonical equality and failure precedence preserving 0.1 baseline-first behavior. This
revision records those decisions.

## 1. Problem and goal

Stage 2 conditions are free-form metadata conventions. The dogfood shows clean validation
does not imply comparability readiness, multi-dataset runs are rejected, and typo risk is
invisible. Add optional first-class 0.2 fields, explicit dataset/checkpoint selection, strict
types, bounded environment values, version-aware serialization, and a staged implementation,
without rewriting 0.1 evidence or changing 0.1 interpretation.

Non-goals: automatic inference/migration, general train/validation/test role modeling,
same-run comparison, SQLite, directory scanning, adapters, network, production qualification,
or promising that `validate` proves comparability readiness.

## 2. Compatibility decision

Adopt schema 0.2 for first-class fields. Options B/C (fields under 0.1) fail because
`_Strict` extra=forbid means old readers reject new keys even when null. A sidecar splits
authority. A namespaced metadata profile preserves old-reader loading but old readers ignore
its meaning; it is a possible advisory validator, not a substitute for first-class fields.

Compatibility statement:

> This release loads manifest schema 0.1 and explicitly versioned 0.2. Existing 0.1
> interpretation is preserved. First-class fields require `schema_version: "0.2"`. Readers
> that support only 0.1 reject 0.2 manifests, including those without new fields.
> Serialization preserves the declared version and does not add 0.2-only fields to 0.1
> output. Reading does not migrate or rewrite evidence. Support for individual commands is
> documented separately; loading a version does not imply every command supports it.

## 3. Model surface

```python
SelectorId = Annotated[StrictStr, Field(pattern=ID_PATTERN)]  # new fields only; Id unchanged

class RunConditions(_Strict):
    evaluation_split: Optional[StrictStr] = None
    evaluation_protocol: Optional[EvaluationProtocol] = None
    evaluation_dataset_artifact_id: Optional[SelectorId] = None
    model_checkpoint_artifact_id: Optional[SelectorId] = None
    source_revision: Optional[StrictStr] = None
    environment: Optional[dict[str, Any]] = None

class EvaluationProtocol(_Strict):
    protocol_id: StrictStr
    version: Optional[StrictInt | StrictStr] = None

class MetricDefinition(_Strict):
    definition_id: StrictStr
    higher_is_better: Optional[StrictBool] = None
    averaging: Optional[StrictStr] = None
    version: Optional[StrictInt | StrictStr] = None

class Artifact(_Strict):
    ...
    dataset_id: Optional[StrictStr] = None   # semantic label, not an artifact ID

class Run(_Strict):
    ...
    conditions: Optional[RunConditions] = None

class Result(_Strict):
    ...
    metric_definition: Optional[MetricDefinition] = None
```

Type rules:
- `StrictBool`/`StrictInt` reject `true` as int, `1.0` as int, `"false"` as bool.
- `StrictStr`/`StrictInt` may accept and normalize a subclass; this RFC does **not** claim
  subclass rejection. Normal JSON-loaded manifests contain plain values. If subclass
  rejection is required later, use pre-normalization validators under a separate decision.
- The global `Id = str` alias is unchanged. Only the two new selectors use `SelectorId`;
  existing 0.1 reference fields keep their current acceptance behavior (e.g. a relationship
  endpoint with spaces remains loadable and is diagnosed by graph checks where applicable).
- `evaluation_split`, `source_revision`, `protocol_id`, `definition_id`, `averaging` are
  non-blank when supplied; no trimming or case folding.
- `version` is `StrictInt` or non-blank `StrictStr`; `1`, `"1"`, `"v1"` are distinct.
- `Artifact.dataset_id` is a non-blank semantic label; multiple artifacts may share it.
- Nested optional members (`version`, `averaging`, `higher_is_better`) are nonnull when
  present: omission is allowed; explicit null is rejected at runtime.
- `environment` is bounded nested JSON: depth 8 relative to the value root, 16 KiB
  serialized, keys/strings <= 1024, finite numbers, JSON-representable. Limits are enforced
  at comparison; schema validity is separate.

## 4. Presence and selection matrix (normative)

Presence uses Pydantic `model_fields_set` and survives dump/reload.

| State | Meaning / effect |
|---|---|
| `conditions` absent | No first-class run conditions; every dimension falls back to legacy metadata independently. |
| `conditions: null` | **Invalid** (explicit null parent rejected). |
| `conditions: {}` | Valid; every child is absent; every dimension falls back independently. |
| `evaluation_split` absent | Fallback to `run.metadata["evaluation"]["split"]`. |
| `evaluation_split: null` | Recorded UNKNOWN; no fallback. |
| `evaluation_split: ""` / blank | Schema error (first-class values must be nonblank). |
| `evaluation_protocol` absent | Fallback to metadata protocol. |
| `evaluation_protocol: null` | Recorded UNKNOWN; no fallback. |
| `evaluation_protocol: {}` | Invalid (missing protocol_id). |
| `source_revision` absent | Fallback to metadata source_revision. |
| `source_revision: null` | Recorded UNKNOWN; no fallback. |
| `environment` absent | Fallback to metadata environment. |
| `environment: null` | Recorded UNKNOWN; no fallback. |
| `environment: {}` | Present-empty; **INPUT_SHAPE_UNSUPPORTED** (Stage 2 empty-environment rule); no fallback. |
| `evaluation_dataset_artifact_id` absent | Fallback to Stage 2 single-attached-dataset rule. |
| `evaluation_dataset_artifact_id: null` | **Invalid selector** (schema error); no fallback. |
| `evaluation_dataset_artifact_id: "id"` | Select that artifact; must be attached by input_to, kind dataset, file; no fallback if invalid. |
| `model_checkpoint_artifact_id` absent | Fallback to Stage 2 single-attached-checkpoint rule. |
| `model_checkpoint_artifact_id: null` | **Invalid selector**; no fallback. |
| `model_checkpoint_artifact_id: "id"` | Select that artifact; must be attached by input_to, kind checkpoint, file; no fallback if invalid. |
| `result.metric_definition` absent | Fallback to result metadata metric_definition. |
| `result.metric_definition: null` | Recorded UNKNOWN; no fallback. |
| `result.metric_definition: {}` | Invalid (missing definition_id). |

If a first-class dataset selector suppresses the dataset dimension, preprocessing derived
from that dataset is also absent; preprocessing is never derived from a different,
unselected dataset. Other attached datasets remain ignored only as comparison candidates;
their metadata may still be scanned and relevant graph structure validated.

Dump/reload preserves omitted vs null vs empty for every row above. No blanket
`exclude_none`/`exclude_defaults`.

## 5. Resolution, conflict and canonical equality

Per dimension:
1. If the first-class field is present, use it. Explicit null means UNKNOWN.
2. If a valid legacy counterpart is also present, require canonical equality; equal
   duplicates collapse to one observation; unequal or malformed duplicates produce
   `INPUT_SHAPE_UNSUPPORTED`.
3. If first-class is absent, use the Stage 2 legacy fallback.
4. No source -> MISSING.

Protocol, metric definition and environment resolve as whole values; never merge members.
Canonical equality reuses the core's rules:
- object key order irrelevant;
- array order significant;
- booleans distinct from numbers;
- strings exact (no case/whitespace normalization);
- int and float equal only when their exact values are equal (`1 == 1.0`), using exact
  rational comparison, not Python's bool/int conflation.

Executable failure order (preserves 0.1 behavior):
1. Selection / selector syntax / duplicate IDs / relevant edges.
2. Shape (types, keys, nonblank, nested null), per fixed dimension order.
3. Bounds for **both** candidates of each dimension (so an oversized value cannot be
   canonicalized unbounded).
4. Duplicate conflict equality (same dimension, after both candidates are bounded).
5. Materialize.
6. Baseline side completes steps 1-5 before target side begins. A baseline bound therefore
   beats a target shape/selector failure, matching existing 0.1 tests. Mixed 0.1/0.2 operands
   follow the same baseline-first order.
7. A bound failure on either duplicate candidate wins over a conflict error. A conflict in a
   later dimension cannot mask an earlier dimension's shape/bound failure.
8. Conflict uses `INPUT_SHAPE_UNSUPPORTED`; no new command-catalogue code in this gate.

## 6. References and selection

- `evaluation_dataset_artifact_id` / `model_checkpoint_artifact_id` reference `Artifact.id`
  in the same manifest, not semantic `Artifact.dataset_id`.
- A matching `input_to` edge is independently required; selectors never synthesize edges.
  `Artifact.run_id` remains produced-by.
- Dataset selector requires `kind == "dataset"`; checkpoint requires `kind == "checkpoint"`.
  No broader classification and no `dataset_id`-only eligibility. Contradictory kinds fail.
- Invalid explicit selectors fail; never fall back. Absent selectors preserve Stage 2 exact
  candidate rules (zero -> MISSING, one -> selected, multiple -> unsupported).
- Volatile/unhashed selected files preserve Stage 2: version MISSING; directory artifacts
  are `INPUT_SHAPE_UNSUPPORTED`.
- One evaluation context per run: all results inherit the run's conditions. Different
  conditions require separate evaluation runs; do not infer from result names.
- Reference validation ownership: 3b adds a bounded conditions validator used by
  `validate`/`inspect` for every declared selector, including runs not selected for
  comparison: syntax, existence, object kind, input_to attachment, contradictions. The
  bridge re-checks defensively. Findings use fixed codes and sanitize message, subject,
  path and hint; they do not echo raw IDs/values.
- The no-ID output promise is scoped to comparability and the new sanitized findings.
  Ordinary `validate`/`inspect` already expose other manifest details.

## 7. Serialization, versioning and schema publication

- `Manifest.schema_version: Literal["0.1", "0.2"]`; omitted version = 0.1.
- `load_manifest` accepts both. A 0.1 manifest containing any 0.2-only key (including null)
  is a schema error.
- Version-aware `dump_manifest` and `artifact_to_dict`:
  - 0.1 output omits `conditions`, `dataset_id`, `metric_definition` and every other
    0.2-only key, including null/default forms. Preserve existing writer formatting/default
    behavior. Prove frozen old-reader acceptance and compare old-writer vs new-writer output
    for equivalent 0.1 inputs; do not promise arbitrary original-input byte preservation.
  - `artifact_to_dict` takes an explicit containing-manifest version context; update its
    callers (`edit.py`, `directory_authoring.py`, `dump_manifest`).
  - 0.2 output uses recursive `model_fields_set` to omit unset optional members; explicit
    null and explicit empty are preserved. Nested protocol/definition dumps must not add
    `version: null`, `averaging: null` or `higher_is_better: null`.
- `init`/adapters write 0.1 through 3b/3c. 0.2 default is a 3d decision.
- `fingerprint` v1 keeps rejecting 0.2 unless separately designed; 0.1 fingerprints
  unchanged.
- `init --force` inventory remains incomplete for new fields until 3d; document that.

Schema publication:
- Preserve `schema/runevidence.schema.json` as the 0.1 artifact and `json_schema()`/the
  `schema` command as 0.1. Add a distinctly named 0.2 schema with a distinct `$id` and an
  explicit `json_schema_0_2()`/version-addressed generator. The existing 0.1 behavior is not
  silently replaced.
- Omitted version behaves as 0.1 and cannot admit 0.2 keys. 0.2 keys under 0.1 (including
  null) and future versions are invalid.
- New nested optional properties are published as **optional property, nonnull when
  present** (no null branch), matching runtime rejection. Model validators alone do not
  change generated schema; post-process or generate explicit schema branches, and test with
  a standards-compliant JSON Schema validator.
- Standard JSON Schema number semantics may treat mathematically integral numbers such as
  `1.0` as integers; schema/runtime agreement tests must not claim identical lexical-number
  handling. Runtime canonical equality follows section 5.
- Existence, object-kind resolution and `input_to` attachment remain runtime graph checks.
- Test Pydantic 2.6 and a current version; exact committed regeneration uses a designated
  generator version. Packaging does not ship `schema/`; do not claim otherwise.

## 8. Privacy and qualification

- First-class fields organize recorded assertions; they do not verify bytes, protocols,
  checkpoints, environment, reproducibility or scientific validity.
- Extend processing disclosure: whole-manifest loading, first-class fields, selector
  resolution, duplicate/conflict checks, and metadata scanning of attached objects.
- Output omits raw values, IDs, names, paths, hashes and metadata keys. New validator
  failures use fixed codes/messages and sanitize subject/path/hint. Conflict errors reveal
  inconsistent recording; treat them as sensitive.
- No reference triggers artifact access, repository inspection, URL resolution, environment
  discovery, network or writes.
- ID-listing preflight conflicts with the no-ID output contract; it needs its own privacy
  decision and is not part of this gate. Presence-only preflight does not solve exact-selector
  discovery.

## 9. Gates

- 3a: this RFC and focused re-review.
- 3b+3c: one public gate. Models/schema 0.2, version-aware serialization, schema
  publication, reference validator, bridge resolution/conflict, CLI/example tests.
  0.2 must not be exposed to the legacy bridge: no independent public 0.2 release. Internal
  sequencing is allowed; public entry points expose 0.2 only when the new resolver is active.
- 3d: authoring/migration/preflight. `init` default, optional `conditions` command, explicit
  migration, docs, discovery helper (separate privacy decision).

3b+3c proof:
- Python and published schema agree on version/key boundary, omitted version, null keys and
  nested nonnull-when-present properties.
- Frozen old reader accepts new-writer 0.1; old-writer/new-writer equivalent output; 0.1
  fingerprints unchanged.
- Dump/reload preserves omitted/null/empty and comparison interpretation.
- Exact types, selector-ID scope (0.1 references unchanged), bounds, references, conflicts,
  baseline-first precedence.
- 0.2 cannot receive a legacy-only comparison; test a 0.2 manifest whose first-class fields
  contradict matching legacy metadata, via bridge and CLI.
- Installed package, Pydantic 2.6/current, separate 0.2 fixtures; preserve 0.1 examples.

## 10. Test plan

- Compatibility: frozen old reader; old/new writer equivalent 0.1; old-reader rejects 0.2;
  new reader accepts 0.2; omitted version 0.1; 0.1 + null 0.2 keys invalid; future versions
  rejected; schema `$id`/claims; dump/reload presence matrix; artifact_to_dict callers.
- Types: strict bool/int/str, selected subclass policy, SelectorId pattern, existing 0.1
  unpatterned references still load, nonblank rules, version forms, nested null rejection,
  environment bounds/depth/encoding/nonfinite.
- Presence: full matrix from section 4 before and after dump/reload; conditions
  omitted/null/empty; selector omitted/null/present; environment omitted/null/empty.
- References/selection: duplicates, wrong kind, unattached, produced-by-only, repeated edges,
  multiple datasets/checkpoints, invalid selector no fallback, volatile/unhashed/directory,
  unselected-run selectors validated, contradictions.
- Conflict/precedence: equal/unequal duplicates, malformed fallback, oversized unequal
  duplicates, different dimensions, baseline bound vs target shape/selector, mixed versions,
  numeric equality (1 vs 1.0), bool vs number, object/array ordering.
- Decisions/CLI: 0.2 comparable/different/insufficient, mixed 0.1/0.2, envelope/exits
  unchanged, canaries/no-I/O, exactly two loads, sanitized new findings.
- Packaging/other: fingerprint 0.2 rejection, init 0.1, installed wheel, Pydantic 2.6,
  separate multi-dataset 0.2 fixture with explicit selection.

## 11. Resolved decisions

1. Adopt 0.2 for first-class fields; no unrelated larger release.
2. Keep `conditions`; explicit artifact-reference names; semantic `Artifact.dataset_id`;
   defer general roles.
3. Environment: bounded nested JSON; omitted/null/empty matrix above.
4. Mixing: per-dimension fallback; equal duplicates collapse; malformed/unequal rejected.
5. Init: 0.1 through 3c; decide in 3d.
6. Preflight: separate gate with its own ID/privacy decision.
7. Claims: no broadening; extend processing/inference disclosure.
8. Compatibility: statement in section 2, with command-specific limits.
9. Parent `conditions:null` and selector null are invalid; explicit null dimensions are
   UNKNOWN; empty environment is rejected.
10. Strict IDs are scoped to new selectors; existing `Id` behavior is preserved.
11. Nested optional properties are optional and nonnull when present in both runtime and
    published schema.
12. Canonical equality reuses core numeric semantics; failure order is baseline-first, with
    bounds before conflict.

## 12. Recommendation

The design is ready for a bounded 3b+3c implementation gate after the team records the
v0.3 decisions. Do not implement authoring/migration/preflight, do not rewrite 0.1 evidence,
and do not expose 0.2 comparisons before the new resolver is active. The Stage 2 CLI remains
the accepted current capability.
