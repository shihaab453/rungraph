# runevidence-keras

A [RunEvidence](../../README.md) adapter for a Keras training-history CSV, typically written by
`pd.DataFrame(history.history).to_csv("history.csv")`.

```bash
pip install -e . -e ./packages/runevidence-keras
runevidence init runs/ --adapter keras
runevidence validate runs/
```

The adapter finds one history CSV (`history.csv` by preference, otherwise a single `*.csv` with an
`epoch` header), records every file as a hashed artifact, and records every numeric metric cell as
a schema 0.4 result with a `csv-cell` locator. Keras' default `to_csv` writes an empty index
header; save it with `index_label="epoch"` so the strict CSV profile can address the column. Names are `<metric> @ <epoch>`;
non-numeric cells are skipped. The CSV is parsed with the same strict profile that verification
uses, so a malformed file is refused rather than partially imported.
