# TensorBoard scalars example

Two runs (`baseline`, `tuned`) written as TensorBoard event files. The event bytes are
committed fixtures produced by the repository's test-only writer; no TensorFlow is needed.

```bash
pip install -e . -e ./packages/runevidence-tensorboard
runevidence validate examples/tensorboard-scalars --strict
runevidence inspect examples/tensorboard-scalars
runevidence matrix examples/tensorboard-scalars
```

The committed `runevidence.json` is schema 0.5: one `tensorboard` locator per scalar with
`tag`, `step` and `occurrence`. `validate` re-reads the event files and checks each recorded
value.
