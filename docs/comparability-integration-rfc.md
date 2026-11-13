# Comparability integration RFC (v0.2, revised after panel review)

Status: ACCEPTED WITH CHANGES — Stage 1 not yet implemented.
Date: 2026-11-10.
Supersedes: v0.1 draft (same day, reviewed by two independent GPT-6.1 Sol reviewers;
Claude was rate-limited). This revision incorporates every blocking change.
Scope: RunGraph / RunEvidence, Boat Box working tree.

Related: `src/runevidence/comparability.py` (PC26 experimental core, mode 0444),
`tests/test_comparability_core.py` (mode 0444), `src/runevidence/models.py`,
`src/runevidence/checks/graph.py`, `src/runevidence/run_lineage.py`,
`examples/dogfood-mlp-sweep/runevidence.json`, README roadmap.

## 1. Problem

RunEvidence records what each run claims. It does not answer the RunGraph question:
*given two recorded runs, were the fixed mandatory recorded conditions the same?*
`compare`, `matrix` and `leaderboard` align results by display name only.
`fingerprint` explicitly disclaims reproducibility. The PC26 core implements a bounded
decision over an explicit comparability input, but it is not fed by manifests and is
labelled synthetic-only.

This RFC specifies the smallest honest bridge from the JSON manifest model to the PC26
core. Stage 1 is **synthetic-only**: it exists to prove the mapping and boundaries, not to
authorise real-metadata use.

## 2. Scope and status

Stage 1 (this RFC): a new experimental module, synthetic unit tests, no CLI, no schema
change, no real-manifest qualification claim.

Hard boundaries:
- No change to `comparability.py` or its 0444 test file.
- No CLI route, parser pin, README claim, or schema change.
- No artifact bytes, files, URLs or result locators read.
- No SQLite, directory scanning, supervisor, Job or network work.
- No production privacy, scientific-validity or runtime qualification.
- The module is not imported by `cli.py`, `checks`, or any public workflow; a test enforces
  this import boundary.

`COMPARABLE_RECORDED_CONDITIONS` means only that the five fixed mandatory recorded
dimensions matched. It is not proof of causal equivalence, reproducibility, statistical
equivalence, or correct recording. A missing mandatory dimension prevents COMPARABLE, but a
mandatory *difference* still yields NOT_COMPARABLE even when another mandatory dimension is
missing.

## 3. Current model support (verified)

| Dimension | Current representation |
|---|---|
| dataset identity | `Artifact.kind == "dataset"`, optional `Artifact.metadata["dataset_id"]`, `input_to` edges |
| dataset version | `Artifact.sha256` (non-volatile file artifacts only) |
| model checkpoint | `Artifact.kind == "checkpoint"`, `sha256`, `input_to` edges |
| preprocessing | direct `derived_from` artifact -> artifact ancestry |
| evaluation split | free-form `Run.metadata["evaluation"]["split"]` |
| metric definition | free-form `Result.metadata["metric_definition"]` |
| evaluation protocol | free-form `Run.metadata["evaluation"]["protocol"]` |
| source revision | free-form `Run.metadata["source_revision"]` |
| environment | free-form `Run.metadata["environment"]` |

`Artifact.run_id` means the run that **produced** the artifact; it is not evidence that the
run consumed it. Dataset and checkpoint input evidence must come from `input_to` edges.
For preprocessing, the existing examples write `processed_dataset derived_from raw_dataset`,
so the bridge follows edges whose **source** is the attached dataset and reads their target
artifacts. Only direct recorded ancestry is used in Stage 1.

## 4. API (Stage 1)

New file: `src/runevidence/_experimental_comparability_bridge.py`.
New test file: `tests/test_experimental_comparability_bridge.py`.

```python
class ComparabilityMappingError(Exception):
    code: str            # fixed code from the catalogue
    side: str | None     # "baseline" | "target" | None; no input-derived text

def compare_recorded_conditions(
    baseline: LoadedManifest,
    target: LoadedManifest,
    *,
    baseline_result_id: str,
    target_result_id: str,
) -> dict:
    """Return the PC26 public projection, or raise ComparabilityMappingError."""
```

- Both result IDs are required. There is no name-based or automatic selection in Stage 1;
  no `**kwargs`, no mixed selectors, no trimming or case folding of selectors.
- A blank or non-string selector is `INVALID_SELECTOR`.
- Each selector must match exactly one result in its own manifest; zero matches is
  `RESULT_NOT_FOUND`, more than one is `RESULT_SELECTION_AMBIGUOUS`.
- Each selected result's `run_id` must resolve to exactly one run; zero is `RUN_NOT_FOUND`,
  more than one is `RUN_SELECTION_AMBIGUOUS`.
- The two manifests are independent snapshots. Same result/run IDs across them are allowed;
  the core itself rejects equal run-ID strings across sides (`SAME_RUN_ID`) and the bridge
  preserves that rejection without renaming IDs.
- `_build_input(...)` is private, test-facing, and returns raw synthetic input. It must not
  be logged, rendered, or exposed as a user API.

## 5. Structural validation before mapping

`LoadedManifest` proves model validation, not graph validity. Before mapping, the bridge
performs its own bounded checks and returns fixed codes only; it never calls or returns
graph findings whose messages contain input data.

Required checks:
- IDs are unique across the shared object namespace (project, runs, artifacts, results,
  evidence). Cross-kind collisions are rejected.
- The selected results and their owning runs exist exactly once.
- Relevant `input_to` / `derived_from` / `result_of` edges have known endpoints of the
  expected kinds. Edges involving unrelated objects may be ignored, but any edge the bridge
  relies on must be valid.
- A result's recorded `run_id` agrees with any `result_of` edge for that result; conflicting
  ownership is `CONFLICTING_OWNERSHIP`.
- Repeated identical attachment edges are deduplicated. Distinct artifacts remain distinct
  candidates.
- `Result.value` is irrelevant to condition comparison and is never copied. Nonfinite
  result values (permitted by M1 at `results[*].value`) do not affect mapping.
- Nonfinite numbers anywhere in selected condition metadata are rejected, because they
  cannot be represented as strict JSON comparability evidence.

Catalogue (fixed):
`INVALID_SELECTOR`, `RESULT_NOT_FOUND`, `RESULT_SELECTION_AMBIGUOUS`, `RUN_NOT_FOUND`,
`RUN_SELECTION_AMBIGUOUS`, `DUPLICATE_ID`, `DANGLING_REFERENCE`,
`CONFLICTING_OWNERSHIP`, `INPUT_SHAPE_UNSUPPORTED`, `INPUT_BOUND_EXCEEDED`,
`INTERNAL_MAPPING_ERROR`.
When both sides fail, `side` reports the first in order baseline, target; code precedence is
the catalogue order above unless a test pins a more specific rule.

## 6. Mapping rules (Stage 1)

Every observation has exactly `value`, `field`, `basis`. `field` is a static descriptor with
no IDs, names, paths or values.

### 6.1 Dataset

- Find artifacts with `kind == "dataset"` attached to the selected run by `input_to`.
- After deduplicating repeated edges, Stage 1 supports **at most one distinct dataset
  artifact**. Zero -> dataset dimensions MISSING. More than one ->
  `INPUT_SHAPE_UNSUPPORTED` ("multiple datasets").
- A directory artifact is `INPUT_SHAPE_UNSUPPORTED`; its digest domain is not qualified.
- dataset_identity:
  - `artifact.metadata["dataset_id"]` must be a non-blank string when present.
  - Absent/`null` -> no identity observation (MISSING). Do **not** fall back to
    `artifact.id`: a manifest-local ID is not a cross-manifest semantic identity and can
    create false equality.
  - Wrong type or blank string -> `INPUT_SHAPE_UNSUPPORTED`.
  - basis `proposed_extension`; field `artifact.metadata.dataset_id`.
- dataset_version:
  - Uses `artifact.sha256` only when the artifact is non-volatile.
  - Volatile -> no version observation (MISSING); its hash is explicitly ignored by the
    model.
  - Absent hash -> no version observation.
  - basis `existing_derived`; field `artifact.sha256 (kind=dataset, input_to run)`.

### 6.2 Model checkpoint

- Same attachment and multiplicity rule: at most one distinct checkpoint artifact via
  `input_to`; zero -> MISSING; more than one -> `INPUT_SHAPE_UNSUPPORTED`.
- Value is `sha256`, non-volatile only. Directory -> `INPUT_SHAPE_UNSUPPORTED`.
- basis `existing_derived`; field `artifact.sha256 (kind=checkpoint, input_to run)`.
- Checkpoint is optional in the default policy: a difference or missing checkpoint cannot
  by itself veto comparability. Tests must show this explicitly.

### 6.3 Preprocessing (direct ancestry)

- Start from the attached dataset artifact. Follow edges with `source == dataset.id`,
  `type == derived_from`, to their target artifacts. This is the processed -> raw direction
  used by the existing examples.
- Deduplicate targets; sort by artifact id.
- Value is a sorted list of `{"id": ...}`; if a target is a non-volatile file artifact with
  `sha256`, include `"sha256"`. Do not include paths, names or metadata.
- Absent edges -> no observation.
- basis `existing_derived`; field `relationships[derived_from] dataset->parents`.
- This records direct ancestry only; it does not identify the transformation algorithm.

### 6.4 Metadata-backed dimensions

Only the exact paths below are read. All other metadata keys are ignored and must not become
dimensions.

| Dimension | Path | Admitted shape | Basis |
|---|---|---|---|
| evaluation_split | `run.metadata["evaluation"]["split"]` | string (blank is passed through and became AMBIGUOUS); `null` -> UNKNOWN observation | `proposed_extension` |
| metric_definition | `result.metadata["metric_definition"]` | see 6.4.1 | `proposed_extension` |
| evaluation_protocol | `run.metadata["evaluation"]["protocol"]` | see 6.4.2 | `proposed_extension` |
| source_revision | `run.metadata["source_revision"]` | string (blank is passed through and became AMBIGUOUS); `null` -> UNKNOWN observation | `proposed_extension` |
| environment | `run.metadata["environment"]` | non-empty JSON object of bounded values; `null` -> UNKNOWN observation | `proposed_extension` |

Absent path -> no observation (MISSING). Present `null` -> observation with value `null`
(the core reports UNKNOWN). Present blank string in split/revision -> pass through; the core
reports AMBIGUOUS -> INSUFFICIENT. Wrong container/type -> `INPUT_SHAPE_UNSUPPORTED`.

`run.metadata["evaluation"]` must be a JSON object when present; a non-object intermediate
is `INPUT_SHAPE_UNSUPPORTED`.

#### 6.4.1 Metric definition

The core only rejects non-dictionaries and dictionaries whose key set is a subset of
`{name, display_name, label}`. That check is shallow: arbitrary extra keys, null IDs and
blank IDs can produce COMPARABLE. Stage 1 therefore admits only:

- absent -> no observation;
- `null` -> UNKNOWN observation;
- `{name|display_name|label: ...}` name-only -> pass through unchanged, so the core reports
  AMBIGUOUS -> INSUFFICIENT (a diagnostic, not a definition);
- empty object `{}` -> pass through, core AMBIGUOUS;
- otherwise a dictionary with a required non-blank string `definition_id` and optional
  `higher_is_better` (bool), `averaging` (non-blank string), `version` (integer or non-blank
  string). Any other key, a missing/blank/non-string `definition_id`, or a wrong optional
  type -> `INPUT_SHAPE_UNSUPPORTED`.

#### 6.4.2 Evaluation protocol

The core treats any non-empty container as KNOWN, so Stage 1 requires:

- absent -> no observation;
- `null` -> UNKNOWN observation;
- otherwise a dictionary with a required non-blank string `protocol_id` and optional
  `version` (integer or non-blank string). Any other shape or key -> 
  `INPUT_SHAPE_UNSUPPORTED`. `false`, `0`, `[null]`, `{"protocol_id": ""}` are rejected.

#### 6.4.3 Environment

Use the recorded JSON object as the value. It must be a non-empty object whose values are
strictly JSON-representable (no NaN/Infinity, no Python-only objects), depth <= 8 and
serialized size <= 16 KiB. Otherwise `INPUT_SHAPE_UNSUPPORTED` or `INPUT_BOUND_EXCEEDED`.
Environment is optional in the default policy; a difference cannot veto comparability.

## 7. Policy

Stage 1 omits `policy`; the core default applies:
- mandatory: dataset_identity, dataset_version, evaluation_split, metric_definition,
  evaluation_protocol;
- optional: preprocessing, model_checkpoint, source_revision, environment.

Tests pin all nine designations so default drift fails acceptance. No caller-supplied policy
in Stage 1.

## 8. Bounds, determinism and immutability

- Enforce core bounds before copying or sorting: max 16 observations per dimension, strings
  <= 1024 characters, JSON depth <= 16, serialized input <= 65,536 bytes. Violations are
  `INPUT_BOUND_EXCEEDED`, never truncation.
- Observations are emitted in a deterministic order: dimension order fixed by the table,
  then `(field, canonical value)`. Repeated calls and permuted manifest collections or dict
  insertion orders produce byte-identical `json.dumps` output.
- Copy selected values into the input; never alias mutable manifest values. The manifest
  models must be unchanged after a call.
- Do not reopen paths or reconcile `source_bytes`; the in-memory models are the snapshot.
- Bound traversal: only objects reachable from the two selected results/runs/artifacts and
  the relevant edges are inspected.

## 9. Output and privacy

`compare_recorded_conditions` returns exactly the object from `comparability.evaluate`, the
core's evidence-minimising public projection (`pc27-comparability-public-v1`): fixed
vocabulary, dimension states, counts, anonymised unknown references, no raw values,
identifiers, field descriptors or error paths. The bridge does not augment it with manifest
paths, names, values, metadata, or exception text. `prototype: true` and
`integrated_into_rungraph: false` remain the core's own status flags.

Privacy promise: the projection and the fixed mapping errors do not disclose raw evidence.
This excludes debugger/process-memory inspection and traceback rendering with captured
locals, which can expose caller-held manifests. Tests must put unique canaries in IDs,
names, metadata **keys and values**, hashes, paths, locators, relationship endpoints/notes,
the `LoadedManifest` path and `source_bytes`, then check the projection, `json.dumps`, core
rendering, stdout/stderr, logs, and exception `str`/`repr`/`args`/ordinary formatted chain.

## 10. Failure contract

- Mapping failures raise `ComparabilityMappingError` with a fixed code and side. No
  fabricated scientific decision for a mapping bug.
- Unexpected mapping exceptions become `INTERNAL_MAPPING_ERROR`. Construct and raise the
  fixed error **outside** the handler so the original exception is not retained as
  `__cause__`, `__context__`, argument or attribute. Do not use `from exc`; `from None`
  alone is insufficient because `__context__` remains accessible.
- Do not catch `BaseException`.
- The core's own sanitised rejected-input projection is preserved unchanged.

## 11. Staged gates

Stage 1 (this RFC): synthetic-only module + tests.
Acceptance:
- Mapping and failure rules above implemented exactly.
- Decisions COMPARABLE / NOT_COMPARABLE / INSUFFICIENT each reached for the intended
  reason on synthetic manifests.
- Every blocking counterexample from review is a regression test (see 12).
- Privacy canaries and the independent no-I/O counter pass.
- Full suite: 0 failed, 0 errors. The current lane has 1709 passed, 533 skipped (414
  native-scanner capability skips + 119 explicitly skipped R5 prerequisites), so the suite
  exits 0. Exact counts are reported, not just "green".
- 0444 hashes/modes and the full edit allowlist are unchanged; CLI/schema/docs unchanged.
- An import-boundary test proves no production module imports the bridge.
- Module docstring and this RFC state experimental, synthetic-only, non-production status.

Stage 2 (separate gate): real-metadata privacy review, then a CLI route/renderer. Requires
parser-pin and route-count reconciliation, README truth, and a separate acceptance.
Stage 3 (separate gate): schema fields for the conventions, only if free-form metadata
proves insufficient; requires schema versioning and migration.

## 12. Test plan (Stage 1)

Selection and structure:
- selected result belongs to the second run; missing run; duplicate run/result/artifact
  IDs including cross-kind; dangling/incorrectly typed relevant edges; conflicting
  `result_of`; repeated attachment edges; producer-only attachment; same run IDs across
  manifests (core rejects); blank/unknown selector; one-sided selector.

Dataset and checkpoint:
- semantic-ID and local-ID collision (no fallback); missing `dataset_id`; blank/wrong type;
  extra dataset with missing hash; two datasets with equal IDs; two with different IDs;
  reversed order; repeated edges to one artifact; volatile hash ignored; directory artifact
  rejected; missing hash; checkpoint conflict (optional -> not a veto); checkpoint missing.

Metadata:
- absent vs null vs blank vs empty container vs wrong type for split, protocol, revision,
  environment; the bypass values `{"name":"accuracy","note":""}`, `{"unit":null}`,
  `{"irrelevant":false}`, `{"definition_id":""}`, `{"definition_id":null}`, protocol
  `false`/`0`/`[null]`/`{"protocol_id":""}`; wrong intermediate `evaluation` container;
  nonfinite metadata; oversized string/depth/observations/input; unknown keys ignored.

Decisions:
- each mandatory dimension missing on one/both sides; missing plus another mandatory
  difference (NOT_COMPARABLE); optional preprocessing/revision/environment/checkpoint
  differences (still COMPARABLE); metric display names equal/different/missing; case and
  whitespace top-level vs nested; identical padded strings; dictionary key permutations;
  list order significance.

Privacy and I/O:
- canaries as in section 9 across success, insufficient, different, malformed,
  invalid-selector and injected-exception paths.
- Traps installed after imports and fixture construction for `builtins.open`, `io.open`,
  `os.open`, `stat`, `lstat`, `scandir`, `listdir`, `readlink`, socket create/connect/DNS,
  URL access, subprocess and manifest-loading/check-running entry points; an independently
  recorded attempt counter must be zero. The trapped positive case must return the exact
  expected projection.

Determinism and mutation:
- permute run/artifact/result/relationship collections and dict insertion order; repeat
  calls; compare full JSON; verify both manifests unchanged; mutate the private input and
  verify no manifest aliasing.

Negative controls (each must fail at least one test when reintroduced):
missing-equals-missing, name-as-definition, arbitrary-extra-key acceptance, first-match
selection, incomplete-dataset dropping, producer-as-input, reversed preprocessing,
volatile-hash use, whitespace normalisation, metadata echoing, exception chaining, one I/O
attempt, default-policy drift.

## 13. Resolved open questions

1. Dataset identity fallback: **no**; require `metadata.dataset_id`, else MISSING.
2. Multiple datasets/checkpoints: **unsupported** in Stage 1; one dataset, at most one
   checkpoint after deduplication.
3. Real metadata before privacy review: **not permitted**; Stage 1 is synthetic-only,
   including library use.
4. Exceptions: **fixed `ComparabilityMappingError`**, no fabricated decision, no retained
   cause/context.
5. Policy: **fixed core defaults**, pinned in tests.
6. `proposed_extension` honesty: **yes** for semantic conventions stored in an existing
   free-form container; it implies no schema enforcement or qualification.

## 14. Alternatives considered

- CLI-first: rejected for Stage 1; couples mapping to parser pins and invites premature
  production claims.
- Schema-first: rejected; adds versioning/migration before conventions are validated.
- External script: rejected; repeats the lost harness pattern and adds no product capability.
- Artifact-ID identity fallback: rejected after a demonstrated false-COMPARABLE
  counterexample.
- Independent observations as collection membership: rejected; they are competing evidence,
  not a set.

## 15. Risks

- Convention drift: users may record split/protocol/definition elsewhere and get
  INSUFFICIENT. This is the intended failure mode, not a wrong answer.
- Free-form metadata is unbounded and sensitive; the bridge bounds and never echoes it, but
  real-data use still awaits the Stage 2 privacy review.
- The feature answers a narrow recorded-conditions question, not scientific validity.
- Multi-dataset and multi-checkpoint runs are unsupported until a collection design exists.
- Directory digests are a separate digest domain and remain unsupported.
