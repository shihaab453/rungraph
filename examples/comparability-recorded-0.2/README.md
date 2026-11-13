# Comparability 0.2 recorded-condition example

Four small schema 0.2 manifests over the same one-run experiment. Each run
consumes three dataset artifacts (`train-data`, `validation-data`,
`test-data`) through `input_to` edges and explicitly selects `test-data` with
`conditions.evaluation_dataset_artifact_id`. The other two datasets stay
attached but are not comparison candidates. No training, network access or
artifact dereferencing is involved.

| File | Recorded difference | Expected result |
| --- | --- | --- |
| `baseline.json` | `evaluation_split: test` | reference manifest |
| `target-comparable.json` | none | exit `0`, `COMPARABLE_RECORDED_CONDITIONS` |
| `target-drifted.json` | `evaluation_split: validation` | exit `1`, `NOT_COMPARABLE_RECORDED_CONDITIONS` |
| `target-insufficient.json` | `evaluation_split` is absent | exit `2`, `INSUFFICIENT_EVIDENCE` |

Every manifest records one run and one result (selectors are exact result
IDs). Conditions are first-class 0.2 fields: evaluation split, protocol
`{protocol_id: holdout-v1, version: 1}`, source revision `rev-1`, environment
`{python: 3.12}`, and the explicit dataset selector. The result records a
first-class metric definition `{definition_id: accuracy-top1,
higher_is_better: true}`. `target-insufficient.json` omits only
`evaluation_split` from `conditions`; with no legacy
`metadata.evaluation.split` either, the split dimension is recorded MISSING.

The selected test artifact is a non-volatile file with a recorded sha256, so
its `dataset_version` observation is the digest. The semantic
`Artifact.dataset_id` values are first-class (`toy-train-v1`,
`toy-validation-v1`, `toy-test-v1`). The manifests were written with the
project's `dump_manifest` helper; digests and sizes come from the real CSV
bytes.

This example is separate from `examples/comparability-recorded/`, which keeps
the 0.1 metadata-convention layout unchanged.

## Commands

From the repository root:

```bash
runevidence comparability examples/comparability-recorded-0.2/baseline.json examples/comparability-recorded-0.2/target-comparable.json --baseline-result baseline-run-result --target-result target-comparable-run-result
# exit 0: COMPARABLE_RECORDED_CONDITIONS

runevidence comparability examples/comparability-recorded-0.2/baseline.json examples/comparability-recorded-0.2/target-drifted.json --baseline-result baseline-run-result --target-result target-drifted-run-result
# exit 1: NOT_COMPARABLE_RECORDED_CONDITIONS (evaluation_split differs)

runevidence comparability examples/comparability-recorded-0.2/baseline.json examples/comparability-recorded-0.2/target-insufficient.json --baseline-result baseline-run-result --target-result target-insufficient-run-result
# exit 2: INSUFFICIENT_EVIDENCE (target evaluation_split is missing)
```

Add `--json` to any command for the versioned envelope described in the
[Stage 2 privacy and CLI contract](../../docs/comparability-stage2-privacy-cli-rfc.md).

**Artifacts are not read by comparability.** The command loads both complete
manifests but does not open, hash or validate any CSV. `validate` reads the
CSVs above only because they are ordinary artifact checks.

## Limitations

- `COMPARABLE_RECORDED_CONDITIONS` does not establish artifact integrity,
  causal equivalence, reproducibility, statistical equivalence or correct
  recording.
- Manifest run IDs must differ across the two operands; identical run-ID
  strings are rejected.
- First-class conditions organize recorded assertions. They do not verify
  bytes, protocols, checkpoints, environment or scientific validity.
- The command is experimental and trusted-local only. See
  [Comparability (verdict CLI experimental)](../../README.md#comparability-verdict-cli-experimental).
