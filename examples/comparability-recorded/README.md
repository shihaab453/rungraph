# Comparability recorded-condition example

Four small manifests over the same one-run, one-result experiment. They show
the experimental read-only comparability command deciding whether recorded
conditions match. No training, network access or artifact dereferencing is
involved.

| File | Recorded difference | Expected result |
| --- | --- | --- |
| `baseline.json` | `evaluation.split: test` | reference manifest |
| `target-comparable.json` | none | exit `0`, `COMPARABLE_RECORDED_CONDITIONS` |
| `target-drifted.json` | `evaluation.split: validation` | exit `1`, `NOT_COMPARABLE_RECORDED_CONDITIONS` |
| `target-insufficient.json` | `evaluation.split` is absent | exit `2`, `INSUFFICIENT_EVIDENCE` |

Every manifest records one run and one result (selectors are exact result IDs),
the same `data/toy.csv` dataset artifact (`kind: dataset`,
`metadata.dataset_id: toy-v1`), an `input_to` edge from the dataset to the
run, plus the protocol `{protocol_id: holdout-v1, version: 1}`, source
revision `rev-1`, environment `{python: 3.12}` and metric definition
`{definition_id: accuracy-top1, higher_is_better: true}`. The manifests were
written with the project's `dump_manifest` helper; the artifact digest and
size come from the real CSV bytes.

`data/toy.csv` is a small eight-row CSV. Run `runevidence validate` on any
manifest to check that the recorded artifact still matches its bytes.

## Commands

From the repository root:

```bash
runevidence comparability examples/comparability-recorded/baseline.json examples/comparability-recorded/target-comparable.json --baseline-result baseline-result --target-result target-comparable-result
# exit 0: COMPARABLE_RECORDED_CONDITIONS

runevidence comparability examples/comparability-recorded/baseline.json examples/comparability-recorded/target-drifted.json --baseline-result baseline-result --target-result target-drifted-result
# exit 1: NOT_COMPARABLE_RECORDED_CONDITIONS (evaluation_split differs)

runevidence comparability examples/comparability-recorded/baseline.json examples/comparability-recorded/target-insufficient.json --baseline-result baseline-result --target-result target-insufficient-result
# exit 2: INSUFFICIENT_EVIDENCE (target evaluation_split is missing)
```

Add `--json` to any command for the versioned envelope described in the
[Stage 2 privacy and CLI contract](../../docs/comparability-stage2-privacy-cli-rfc.md).
Read that contract before using real manifests; output reveals condition
presence, states and equality/difference and is not anonymous.

**Artifacts are not read by comparability.** The command loads both complete
manifests but does not open, hash or validate `data/toy.csv` or any other
artifact path. `validate` reads the CSV above only because it is an ordinary
artifact check.

## Limitations

The comparison is limited to recorded conditions:

- `COMPARABLE_RECORDED_CONDITIONS` does not establish artifact integrity,
  causal equivalence, reproducibility, statistical equivalence or correct
  recording.
- Manifest run IDs must differ across the two operands; identical run-ID
  strings are rejected.
- Several recorded dimensions (evaluation split, metric definition, evaluation
  protocol, source revision and environment) are free-form `metadata`
  conventions, not enforced schema fields.
- The command is experimental and trusted-local only. See
  [Comparability (verdict CLI experimental)](../../README.md#comparability-verdict-cli-experimental).

A dated walkthrough of this example and the existing dogfood layouts is in
[`docs/comparability-dogfood-2026-11-11.md`](../../docs/comparability-dogfood-2026-11-11.md).
