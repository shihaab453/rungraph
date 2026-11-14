# Comparability dogfood, 2026-11-11

Scope: the new `examples/comparability-recorded` manifests and the existing
`examples/dogfood-mlp-sweep`, `examples/hydra-sweep` and
`examples/mlflow-file-store` folders. Every command below is the accepted
experimental `runevidence comparability` route (Stage 2b) and was run
read-only: no manifest or artifact was written, rehashed or modified.

How to read this note:

- **Observed** means the command was actually run and this document quotes its
  exit code and JSON fields, or the manifest contents were read directly.
- **Inferred** means the explanation comes from reading
  `src/runevidence/_experimental_comparability_bridge.py`, the frozen core, the
  Stage 2 contract or the tests; it was not proven by a new experiment here.

## 1. New example: actual results

All four example manifests validate clean (`runevidence validate FILE --json
--summary-only`: exit `0`, `total_findings: 0`, empty `by_severity` and
`by_code`).

```text
runevidence comparability examples/comparability-recorded/baseline.json \
  examples/comparability-recorded/target-comparable.json \
  --baseline-result baseline-result --target-result target-comparable-result --json
```

- exit `0`
- `format: runevidence-comparability-result-v1`
- `application_status: EXPERIMENTAL_READ_ONLY_CLI`
- `core.decision: COMPARABLE_RECORDED_CONDITIONS`
- `core.input_rejected: false`
- reason codes include `ALL_MANDATORY_DIMENSIONS_MATCH`, plus
  `OPTIONAL_DIMENSION_INSUFFICIENT:model_checkpoint` and
  `OPTIONAL_DIMENSION_INSUFFICIENT:preprocessing` (both absent on both sides;
  optional, so they do not change the decision).

```text
runevidence comparability examples/comparability-recorded/baseline.json \
  examples/comparability-recorded/target-drifted.json \
  --baseline-result baseline-result --target-result target-drifted-result --json
```

- exit `1`
- `core.decision: NOT_COMPARABLE_RECORDED_CONDITIONS`
- `core.input_rejected: false`
- reason codes include
  `MANDATORY_DIMENSION_DIFFERENT:evaluation_split` (baseline `test`, target
  `validation`).

```text
runevidence comparability examples/comparability-recorded/baseline.json \
  examples/comparability-recorded/target-insufficient.json \
  --baseline-result baseline-result --target-result target-insufficient-result --json
```

- exit `2`
- `core.decision: INSUFFICIENT_EVIDENCE`
- `core.input_rejected: false` (this is a completed evaluation with missing
  evidence, not a core rejection)
- reason codes include
  `MANDATORY_DIMENSION_INSUFFICIENT:evaluation_split` (the target run has no
  `metadata.evaluation.split`).

Observed: exit `2` is used both for completed-but-insufficient evaluations
(above) and for application errors (§2), exactly as the contract states.

## 2. Existing dogfood folders: actual attempts

### 2.1 `examples/dogfood-mlp-sweep`

```text
runevidence comparability examples/dogfood-mlp-sweep/runevidence.json \
  examples/dogfood-mlp-sweep/runevidence.json \
  --baseline-result job0-val-rmse --target-result job1-val-rmse --json
```

Observed output (exit `2`):

```json
{
  "format": "runevidence-comparability-error-v1",
  "ok": false,
  "exit_code": 2,
  "error": {
    "code": "MAPPING_FAILED",
    "side": "baseline",
    "mapping_code": "INPUT_SHAPE_UNSUPPORTED"
  }
}
```

Observed from the manifest: `job0` and `job1` have result records
(`job0-val-rmse`, `job1-val-rmse`) and the runs are distinct, so the scenario
looks like a natural before/after comparison.

Inferred cause: each selected job run is the target of three `input_to` edges
from datasets (`data-processed-train.csv`, `-val.csv`, `-test.csv`). The
bridge rejects a side with more than one attached artifact whose
`kind == "dataset"`. The privacy-minimal error deliberately does not say
which rule failed.

Also observed: these runs have no `metadata` at all and results have no
`metadata.metric_definition`. Even if mapping succeeded, the mandatory
`evaluation_split`, `metric_definition` and `evaluation_protocol` dimensions
would be missing on both sides, so the likely decision would be
`INSUFFICIENT_EVIDENCE` (inferred from the mapping rules).

### 2.2 `examples/hydra-sweep`

```text
runevidence comparability examples/hydra-sweep/runevidence.json \
  examples/hydra-sweep/runevidence.json \
  --baseline-result run-multirun-2026-10-25-19-16-16-0 \
  --target-result run-multirun-2026-10-25-19-16-16-1 --json
```

Observed output (exit `2`):

```json
{
  "format": "runevidence-comparability-error-v1",
  "ok": false,
  "exit_code": 2,
  "error": {
    "code": "MAPPING_FAILED",
    "side": "baseline",
    "mapping_code": "RESULT_NOT_FOUND"
  }
}
```

Observed from the manifest: it has three runs and **zero** `results`. The
selectors above are run IDs used to make the attempt concrete; any selector
fails with `RESULT_NOT_FOUND` because no result IDs exist. The Hydra adapter
records runs and artifact hashes, not result records, so this layout is not
directly selectable by the comparability route.

### 2.3 `examples/mlflow-file-store`

```text
runevidence comparability examples/mlflow-file-store \
  examples/mlflow-file-store \
  --baseline-result mlflow-run-0 --target-result mlflow-run-1 --json
```

Observed output (exit `2`):

```json
{
  "format": "runevidence-comparability-error-v1",
  "ok": false,
  "exit_code": 2,
  "error": {
    "code": "MANIFEST_LOAD_FAILED",
    "side": "baseline"
  }
}
```

Observed: the folder contains an `mlruns/` tree and a `README.md`, but no
`runevidence.json`. The comparability command accepts manifest files or
folders containing a manifest; it does not run the MLflow adapter or build a
manifest. Run IDs supplied above are placeholders.

### 2.4 Summary

| Example | Exit | Observed outcome | Main blocker (observed / inferred) |
| --- | --- | --- | --- |
| new `target-comparable` | 0 | comparable | none |
| new `target-drifted` | 1 | mandatory split differs | none |
| new `target-insufficient` | 2 | insufficient | split omitted |
| `dogfood-mlp-sweep` | 2 | `INPUT_SHAPE_UNSUPPORTED` | three datasets attached to one run (inferred); no condition metadata (observed) |
| `hydra-sweep` | 2 | `RESULT_NOT_FOUND` | no result records (observed) |
| `mlflow-file-store` | 2 | `MANIFEST_LOAD_FAILED` | no manifest (observed) |

No existing dogfood folder produced `SAME_RUN_ID` in these attempts because
the route failed earlier. `SAME_RUN_ID` is still observable in the test suite
(`tests/test_cli_comparability.py::test_core_rejected_json_envelope_exit_two`)
and is a contract-level rejection (inferred here, not re-run against these
folders).

## 3. Usability gaps

1. **Exact result IDs, with no discovery or preflight helper.** The selectors
   are exact, untrimmed and case-sensitive. There is no `comparability`
   subcommand to list candidate results or to report which recorded conditions
   are ready before the comparison. Error documents intentionally omit IDs,
   paths and selectors, so a wrong selector yields only `RESULT_NOT_FOUND` and
   the user must inspect the manifest manually. (Observed: error output; gap
   inferred from the command set.)

2. **Two manifests are mandatory, and identical run IDs are rejected.** The
   route cannot compare two results belonging to the same run, even when both
   results live in one manifest: the core rejects identical `run_id` strings
   with `SAME_RUN_ID`. This blocks common questions such as "same run,
   validation vs test metric". (Observed in the contract and tests; inferred
   for these dogfood folders, which failed earlier.)

3. **Recorded conditions are hand-written free-form metadata.** Mandatory
   dimensions such as `evaluation.split`, `evaluation.protocol` and
   `metric_definition`, and optional `source_revision` / `environment`, are
   conventions inside `Run.metadata` and `Result.metadata`. Schema `0.1` has no
   fields for them, so `runevidence validate` cannot catch a typo,
   inconsistent shape or missing convention. Our own
   `target-insufficient.json` validates clean while comparing as
   `INSUFFICIENT_EVIDENCE`. (Observed.)

4. **Dataset attachment is convention-based and can be silently absent.**
   Dataset identity/version come from an artifact with `kind: "dataset"` that
   is the source of an `input_to` edge to the selected run. `Artifact.run_id`
   means produced-by and is ignored for this purpose. A missing or
   misdirected edge silently drops the dataset dimensions, producing
   `INSUFFICIENT_EVIDENCE` rather than a validation error. (Observed in code
   and contract; inferred for user behavior.)

5. **Multiple attached datasets are rejected.** A run that consumes train,
   validation and test files as separate dataset artifacts cannot be mapped,
   even though that is a normal layout (as in `dogfood-mlp-sweep`). The error
   is a fixed `INPUT_SHAPE_UNSUPPORTED` with no side detail beyond
   `baseline`/`target`. (Observed failure; inferred cause.)

6. **The error vocabulary is privacy-minimal by design.** This is a feature,
   but it means `MAPPING_FAILED` tells the user only a fixed category and
   side. There is no safe, optional verbose mode, no finding location and no
   hint about which condition needs recording. (Observed.)

7. **Clean validation does not imply comparability readiness.** The four new
   manifests all pass `validate`, yet one is intentionally not comparable and
   one is insufficient. Artifact integrity and recorded-condition comparability
   are independent checks, and the README says so, but there is no command that
   reports both states together. (Observed.)

## 4. Recommendation

**A small CLI helper is justified now; Stage 3 schema fields are justified
next, but they solve different problems.**

- **CLI helper (read-only, privacy-minimal):** add a `comparability` preflight
  or `--list-candidates` mode that reports, per selected result, which recorded
  conditions are present/missing and whether the manifest-level shape is
  mappable, without printing raw metadata values or artifact paths. This
  directly addresses gaps 1, 5 and 6. It is a low-risk addition because it
  reads the same whole manifests and can reuse the existing closed
  vocabulary. Do not add a free-form verbose error mode without another
  privacy review.
- **Stage 3 schema fields:** add first-class fields for the three mandatory
  conditions currently marked `not_represented` (`evaluation_split`,
  `evaluation_protocol`, `metric_definition`) and consider `source_revision`
  and `environment`. This addresses gaps 3 and 4 by making conventions
  validatable and typo-resistant. It is a schema change and therefore needs
  the usual authorization, compatibility and pin-refresh work; it should not
  be smuggled into Stage 2.
- **Do not weaken the same-run-ID rejection** as a convenience fix. If
  same-run comparisons are needed, model that as an explicit Stage 3 decision
  (for example, a distinct comparison mode with clearly documented semantics),
  not as a silent relaxation of the current contract.
- **Document adapter limits.** `hydra-sweep` and `mlflow-file-store` show that
  adapter output may not contain result records or a manifest at all. A short
  note in the comparability docs would set expectations; changing adapters is
  out of scope for this task.

Overall: the new example proves the accepted route works end-to-end for a
clean recorded-condition setup, and the dogfood attempts show that the main
barrier to real use is preparation/discovery rather than a CLI defect. A
read-only helper would deliver the most usability per unit of risk; schema
fields remain the durable fix for the free-form condition conventions.
