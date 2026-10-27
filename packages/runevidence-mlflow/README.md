# runevidence-mlflow

A [RunEvidence](../../README.md) adapter for local MLflow **file stores**. It
turns an `mlruns/` directory into a starter `runevidence.json`: one Run per
MLflow run folder, the run's params, experiment metadata, hashes for every
file, and one Result per readable metric history.

The MLflow library is not needed. This adapter reads only the plain files MLflow
writes on disk. It does not contact a tracking server, resolve a tracking URI,
open `artifact_uri`, or follow `artifact_location`.

## Install

Neither package is on PyPI. From the root of a clone of this repository,
install core and the adapter together in editable mode:

```
pip install -e . -e ./packages/runevidence-mlflow
runevidence adapters        # should now list "mlflow"
```

The adapter requires `runevidence>=0.1.1,<0.2.0`: it uses the plugin adapter API
and stays within the same minor release while core is pre-1.0. Installing both
local paths lets pip satisfy that requirement from the clone. If core in that
range is already installed,
`pip install -e ./packages/runevidence-mlflow` is enough.

For a regular (non-editable) install, install core first, then the adapter:

```
pip install .
pip install ./packages/runevidence-mlflow
```

`runevidence-mlflow` and `runevidence` share the same pre-1.0 version policy:
a breaking adapter API change bumps core's minor version, and the adapter's
declared range is updated in the same release.

## Use

Point `init` at the project folder containing `mlruns/`, or directly at the
file-store folder:

```
runevidence init --adapter mlflow .
runevidence init --adapter mlflow ./mlruns
runevidence validate --strict .
```

A run is recognized only when both levels have valid metadata:
`<experiment>/meta.yaml` and `<experiment>/<run>/meta.yaml`. The folder may be
the store root or an `mlruns/` folder inside a project. A run folder is a run
only when it sits directly under a valid experiment folder; arbitrary
`meta.yaml` files deeper in `artifacts/` are just hashed files.

For each run the manifest records:

| Field | Taken from |
| --- | --- |
| `params` | `params/` files, copied as exact UTF-8 strings; nested names such as `optimizer/name` are kept |
| `started_at` | `start_time` in run `meta.yaml`, converted from milliseconds to UTC |
| `name` | `run_name`, or a legacy string `mlflow.runName` tag in `meta.yaml`, or the run id |
| `metadata.mlflow.experiment_id`, `experiment_name` | the enclosing experiment's `meta.yaml` |
| `metadata.mlflow.run_id`, `run_name`, `lifecycle_stage`, `status` | whitelisted run metadata; `run_id` falls back to older `run_uuid` |
| `metadata.mlflow.unreadable` | metric files that could not be summarized |

MLflow persists run status as an integer enum in the file store. Known values
are recorded by name (`1` RUNNING, `2` SCHEDULED, `3` FINISHED, `4` FAILED, `5`
KILLED); an unknown integer is preserved, and a string status is kept as-is.

Project metadata lists each experiment's `experiment_id`, `name`, and
`lifecycle_stage`. Nothing else from `meta.yaml` is copied. In particular,
`user_id`, `source_name`, source versions, arbitrary tags, `artifact_uri`, and
`artifact_location` stay out of the manifest. Those URIs are never opened, even
when they point at a local folder. Only files physically inside the scanned
folder are tracked.

Every ordinary file is hashed, including experiment and run `meta.yaml`, params,
tags, artifacts, unrelated project files, and files under `.trash`. Files in
`.trash` may therefore be preserved by the manifest, but they never become runs.

### Metric histories

MLflow metric files are lines of `timestamp value step` or, in older stores,
`timestamp value` (step 0). A metric name comes from the path under
`metrics/`, so `metrics/train/loss` becomes `train/loss`.

One Result is recorded per readable metric file, linked to the metric file's
artifact id. There is no locator because RunEvidence compares locator values
only for JSON files. The recorded value is the row with the greatest
`(step, timestamp, value)`, not the last line on disk. Result metadata keeps the
chosen `step` and `timestamp_ms`.

`validate` detects changed metric bytes through their hash. It does not
compare the result value again, and `rehash` accepts new bytes without
refreshing these text-derived results. **RE-10:** the recorded summary
**value, step and timestamp can stay old** after you accept a changed metric file, and
`validate --strict` can still exit 0 after that acceptance. Changed bytes do
not always mean the summary is stale, and validation makes no scientific truth
or general freshness claim.

To refresh summaries, run `init --adapter mlflow --force` and follow the
[central safe refresh procedure](../../docs/adapters.md#refreshing-mlflow-results-safely-re-10).
The previous manifest is kept as a new backup generation
(`runevidence.json.bak`, `.bak.2`, ...; never overwritten). Forced
regeneration does not merge hand-written claims, relationships, annotations,
volatile flags, or project fields and settings. Compare the backup with the
regenerated manifest, keep the newly extracted summaries, and manually restore
only reviewed annotations and settings after verifying IDs and claims. Do not
copy old metric results back. The `init --force` warning counts every old
result as missing even when the adapter regenerates it. Version control and a
separate backup are recommended.

A metric file that is empty, malformed, non-finite (for example `nan` or
`inf`), not valid UTF-8, or uses an unsupported row shape produces no Result at
all. Its raw bytes are still hashed, and its path is listed in
`metadata.mlflow.unreadable` on that run. RunEvidence does not summarize a
partial log when one row is bad.

## Limits

- File stores only. There is no server, REST API, SQL store, tracking URI
  resolution, or network access.
- No URI following. `artifact_uri` and `artifact_location` are metadata only.
- Symlinks and Windows directory junctions are skipped, including links that
  stay inside the folder, so a scan can never wander outside the requested
  root. Each physical folder is entered at most once per scan, so a link loop
  cannot repeat files under ever longer paths.
- Broken, unsafe, or wrong-shape experiment/run `meta.yaml` is a hard error:
  `init` prints an `AdapterError` message and exits 2 without writing. YAML is
  read with `yaml.safe_load`, so tags such as `!!python/object` are refused.
- A folder with no recognized runs is refused with a message describing the
  expected layout; use `--adapter generic` if you only want every file hashed.
- Pointing at a single run folder or a single experiment folder is not
  supported. Point at the store or the project folder containing `mlruns/`.
- No Results are invented for missing metrics, and no evidence claims are
  inferred.

## Development

```
pip install -e . -e "./packages/runevidence-mlflow[dev]"
python -m pytest packages/runevidence-mlflow/tests
```

The tests use the synthetic store in `examples/mlflow-file-store/`.

License: Apache-2.0.
