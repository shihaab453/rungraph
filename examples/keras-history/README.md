# Keras history example

A Keras training history saved with `index_label="epoch"`:

```bash
runevidence init examples/keras-history --adapter keras \
  --name "Keras history" --id keras-history
runevidence validate examples/keras-history --strict
```

Each numeric metric cell becomes a schema 0.4 result with a `csv-cell` locator.
