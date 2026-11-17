# runevidence-lightning

A [RunEvidence](../../README.md) adapter for PyTorch Lightning `CSVLogger` logs.

```
lightning_logs/
  version_0/metrics.csv
  version_0/hparams.yaml
  version_1/metrics.csv
```

```bash
pip install -e . -e ./packages/runevidence-lightning
runevidence init lightning_logs/ --adapter lightning
runevidence validate lightning_logs/
```

One run is recorded per folder containing `metrics.csv` (`CSVLogger`'s `version_*` folders are the common layout); every file is hashed, and
every numeric CSV cell is a schema 0.4 result with a `csv-cell` locator. Names are
`<metric> @ <step>`; when no step is recorded, the epoch or row index is used. Sparse/empty cells
are skipped. The adapter uses the protected descriptor walk and refuses on hosts without POSIX
directory-descriptor support; use `--adapter generic` there.
