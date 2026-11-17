# runevidence-sacred

A [RunEvidence](../../README.md) adapter for
[Sacred](https://sacred.readthedocs.io/) `FileStorageObserver` run folders.

A Sacred base directory contains one numeric folder per run:

```
runs/1/run.json       experiment, status, command, start/stop times
runs/1/config.json    the flattened configuration
runs/1/metrics.json   {metric: {values, steps, timestamps}}
runs/1/cout.txt       captured output
runs/1/<artifact>     files saved by the run
```

## Install and use

```bash
pip install -e . -e ./packages/runevidence-sacred
runevidence init runs/ --adapter sacred
runevidence validate runs/
```

The adapter records every file as a hashed artifact, one run per folder containing
`run.json`, the configuration as run params, and every scalar metric value as a schema 0.3 result
with a JSON Pointer locator into `metrics.json`. Result names are `<metric> @ <step>`; when no
step was recorded, the array index is used. Non-scalar and non-finite values are skipped. A
malformed `metrics.json` leaves its artifact and is listed under
`metadata.sacred.unreadable`.

No Sacred installation is required, and nothing is read outside the folder.
