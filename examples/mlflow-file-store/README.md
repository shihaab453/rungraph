# MLflow file-store example

A small synthetic MLflow file store used to exercise the `runevidence-mlflow`
adapter. It contains one experiment (`Default`, id `0`) with two finished runs:

```
mlruns/
  0/
    meta.yaml
    a1b2c3d4e5f60718293a4b5c6d7e8f90/     baseline
      meta.yaml
      params/{learning_rate,seed,use_batchnorm}
      params/optimizer/name
      metrics/train/loss
      metrics/val/accuracy
      tags/model_family
      artifacts/{model.txt,summary.json}
    0f1e2d3c4b5a69788796a5b4c3d2e1f0/     tuned
      meta.yaml
      params/{learning_rate,seed}
      params/optimizer/name
      metrics/train/loss
      metrics/eval/f1
      tags/model_family
      artifacts/predictions.csv
```

The data is invented for documentation and tests. It was not produced by a
real training job. The `artifact_location`, `artifact_uri`, source name, and
user fields in `meta.yaml` use synthetic values under `file:///tmp/...` and
`synthetic-user`. Run metadata uses ordinary MLflow fields (`run_id`,
`run_uuid`, `tags: []`, and integer `status: 3` for FINISHED). The adapter
deliberately never opens the artifact URIs and never copies them into the
manifest.

The metric histories are intentionally non-trivial:

- `train/loss` stores step 2 below step 1 in the file, so a naive "last line"
  reader would record the wrong value. The adapter selects the maximum
  `(step, timestamp, value)`.
- `val/accuracy` and `eval/f1` use the older two-column form
  `timestamp value`, which means step 0.
- Nested names such as `train/loss` and `optimizer/name` are preserved.

## Try it

Install core and the adapter from the repository root:

```
pip install -e . -e ./packages/runevidence-mlflow
```

Then scan the project folder or the store folder:

```
runevidence init --adapter mlflow examples/mlflow-file-store
runevidence validate --strict examples/mlflow-file-store
runevidence inspect examples/mlflow-file-store
```

Scanning the project folder also hashes this README. Scanning `mlruns/`
directly tracks only the store. No manifest is committed here; `init` writes
one from the current bytes.

See `packages/runevidence-mlflow/README.md` for what is recorded and for the
local-only limits.
