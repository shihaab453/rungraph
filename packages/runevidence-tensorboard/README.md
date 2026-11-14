# runevidence-tensorboard

A [RunEvidence](../../README.md) adapter for [TensorBoard](https://www.tensorflow.org/tensorboard)
event files. It turns a folder of `.tfevents` files into a schema 0.5
`runevidence.json`: one run per event file, one result per supported scalar,
each result carrying an exact `tensorboard` locator.

TensorBoard does not need to be installed. The adapter reads the event files
with the dependency-free reader in core; TensorFlow, protobuf and
google-crc32c are never imported, and no extra dependency is added.

## Install

From the root of a clone of this repository (neither package is on PyPI),
install core and the adapter together:

```
pip install -e . -e ./packages/runevidence-tensorboard
runevidence adapters        # should now list "tensorboard"
```

The adapter requires `runevidence>=0.1.1,<0.2.0`: it needs the schema 0.5
models and the strict event reader, and stays within the same minor release
while core is pre-1.0. Installing both local paths lets pip satisfy this
requirement from your clone. If core is already installed in that range,
`pip install -e ./packages/runevidence-tensorboard` is sufficient. For regular
installs, use `pip install . ./packages/runevidence-tensorboard`. CI installs
those local paths in two steps, core first.

## Use

```
runevidence init --adapter tensorboard .
runevidence validate .
```

Every regular file under the folder is recorded as an artifact with its sha256
and size, except RunEvidence's own manifest and backups. Files whose basename
contains `.tfevents` (case-insensitive) are event files; other files keep the
generic adapter's artifact IDs and have no run. A folder named `.tfevents` is
not an event file and never makes its children eligible.

For each event file the manifest records:

| Field | Taken from |
| --- | --- |
| one run | named by the recorded relative path, e.g. `logs/events.out.tfevents.1` |
| artifact `tb-art-…` | the file bytes, hash, size, and that run |
| one result per supported scalar | `tb-res-…`, name `tag @ step` (plus ` #occurrence` for repeats), the float value and a `tensorboard` locator |

Results follow record order, then `Summary.Value` order. The occurrence is the
zero-based count of supported scalars with the same exact `(tag, step)` in
that file. Nothing is guessed or merged: two event files in one folder are
two runs, even when their tags overlap, and plugin pseudo-scalars are selected
without filtering. There is no `--tags` flag in this version.

The adapter writes schema 0.5 itself, so `validate` verifies each scalar
against the event file directly; no migration step is needed.

## Limits

- Event files are read once, through the same protected snapshot used for
  verification, and hashed and parsed from the same bytes. The cap is 128 MiB.
- A truncated, malformed, changed, unreadable or over-cap event file refuses
  the whole `init`; nothing is written.
- A tag must be non-empty, valid UTF-8 and at most 4,096 UTF-8 bytes. Scalar
  tags that cannot be represented in schema 0.5 refuse the whole `init`.
- One manifest may hold at most 10,000 TensorBoard results and 16 MiB of tag
  bytes across all event files; past either limit nothing is written.
- A `.tfevents` file can be an artifact but never a volatile one: `init
  --volatile` refuses a glob that targets it, because a locator needs stable
  bytes.
- Only scalar values are results. Histograms, images, audio, text and graphs
  are still hashed as file content but are not selected.

Not done on purpose: no folder runs, no multi-file runs, no result for
non-event files, no tag filtering, and no TensorBoard UI or database access.

## Development

```
pip install -e . -e "./packages/runevidence-tensorboard[dev]"
python -m pytest
```

The tests generate event files themselves and need nothing beyond pytest.

License: Apache-2.0.
