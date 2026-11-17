# runevidence-wandb

A [RunEvidence](../../README.md) adapter for local Weights & Biases run folders.

A W&B offline/synced run directory contains:

```text
wandb/offline-run-.../
  files/wandb-summary.json     scalar summary values
  files/config.yaml            configuration
  files/wandb-metadata.json    run id and display name
  files/...                    other run files
```

The adapter records one run per folder containing `wandb-summary.json` or
`files/wandb-summary.json`, the YAML configuration as params, every file as a hashed artifact,
and every finite numeric summary value as a schema 0.3 result with a JSON Pointer locator.
Keys beginning with `_` (W&B bookkeeping such as `_runtime`) are skipped. Non-numeric and
non-finite values are skipped. The adapter uses protected descriptor reads and refuses hosts
without POSIX directory-descriptor support.

```bash
pip install -e . -e ./packages/runevidence-wandb
runevidence init wandb/ --adapter wandb
runevidence validate wandb/
```
