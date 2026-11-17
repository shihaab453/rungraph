# PyTorch Lightning CSVLogger example

A `CSVLogger` run with one version:

```bash
runevidence init examples/lightning-logs --adapter lightning \
  --name "Lightning metrics" --id lightning-metrics
runevidence validate examples/lightning-logs --strict
```

Each numeric cell becomes a schema 0.4 `csv-cell` result named `<metric> @ <step>`;
sparse cells are skipped.
