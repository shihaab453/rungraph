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
step was recorded, the array index is used, and repeated names get a stable `#2`, `#3` suffix.
Non-scalar values and parseable non-finite values are skipped. A file that fails the strict
JSON locator profile (duplicate keys, underflow/overflow, oversized integers, bare CR or excessive
nesting) is retained as an artifact but listed under `metadata.sacred.unreadable`; `inspect` shows
the note. Content files are read through the protected descriptor walk with a 50 MiB cap, artifacts
are stream-hashed through the same walk, and an unreadable file or directory refuses `init` with
exit 2 and no manifest. Configuration files use the same strict profile, and an external symlink is
not read.

No Sacred installation is required, and nothing is read outside the folder.
