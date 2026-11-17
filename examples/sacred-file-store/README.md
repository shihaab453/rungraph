# Sacred file-store example

A Sacred `FileStorageObserver` base directory with one completed run:

```
runs/1/run.json
runs/1/config.json
runs/1/metrics.json
runs/1/cout.txt
runs/1/model.txt
runs/runevidence.json
```

The committed manifest was produced by:

```bash
runevidence init examples/sacred-file-store/runs --adapter sacred \
  --name "Sacred diabetes SGD" --id sacred-diabetes
runevidence validate examples/sacred-file-store/runs --strict
```

The adapter records the configuration as run params and every scalar metric value as a schema
0.3 result with a JSON Pointer into `metrics.json`.
