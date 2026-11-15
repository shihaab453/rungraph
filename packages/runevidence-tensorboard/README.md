# runevidence-tensorboard

A [RunEvidence](../../README.md) adapter for [TensorBoard](https://www.tensorflow.org/tensorboard)
event files. It turns a folder of `.tfevents` files into a schema 0.5
`runevidence.json`: one run per directory that directly contains event files,
one result per selected scalar, each result carrying an exact `tensorboard`
locator.

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

Each directory that directly contains event files becomes one run. Event files
in nested directories belong to their own directory's run, and the experiment
root is the run `.`:

| Field | Taken from |
| --- | --- |
| one run per directory | id `tb-run-<16 hex SHA-256(os.fsencode(dir_rel))>`, name the relative directory |
| artifact `tb-art-…` | the file bytes, hash, size, and that directory's run |
| one result per selected scalar | `tb-res-…` built from run id, artifact id, tag, step and occurrence; the float value and a `tensorboard` locator |

Results follow discovery order (event files sorted by path), then record order,
then `Summary.Value` order. The occurrence is the zero-based count of supported
scalars with the same exact `(tag, step)` in that file. Names keep cross-run
alignment where it is unambiguous: a single-file `(tag, step)` group is
`tag @ step` (plus ` #occurrence` for repeats); once the group spans files every
member is `tag @ step [basename] #<16-hex digest>`. Nothing is merged: values
across files stay separate results.

## Tag filtering

`init --tags PATTERN` (repeatable) selects scalars by a full-string glob:
`*` matches any run of code points, `?` one code point, and `\` escapes the next
character; character classes are literal, unlike `--volatile`. Every pattern is
evaluated against every distinct tag, so the warning pass can report a pattern
that matches nothing. Patterns are capped at 1,024 code points and share a
50,000,000-unit matching budget; exhausting it refuses the run with a fixed
message. Warnings are printed only after a successful write.

The adapter writes schema 0.5 itself, so `validate` verifies each scalar
against the event file directly; no migration step is needed.

## Limits

- Event files are read once, through the same protected snapshot used for
  verification, and hashed and parsed from the same bytes. The cap is 128 MiB.
- A truncated, malformed, changed, unreadable or over-cap event file refuses
  the whole `init`; nothing is written.
- A tag must be non-empty, valid UTF-8 and at most 4,096 UTF-8 bytes. Without
  `--tags` such a tag refuses the whole `init`; with a filter it is skipped.
- One manifest may hold at most 10,000 selected TensorBoard results and 16 MiB
  of selected tag bytes across all event files; past either limit nothing is
  written. Filtering therefore brings a large log under the cap.
- A regular `.tfevents` file can be an artifact but never a volatile one:
  `init --volatile` refuses a glob that targets it, because a locator needs
  stable bytes. Directories are not event artifacts.
- Only scalar values are results. Histograms, images, audio, text and graphs
  are still hashed as file content but are not selected.

Not done on purpose: no value merging or run-level occurrence, no plugin
filtering, no result for non-event files, and no TensorBoard UI or database
access.

## Development

```
pip install -e . -e "./packages/runevidence-tensorboard[dev]"
python -m pytest
```

The tests generate event files themselves and need nothing beyond pytest.

License: Apache-2.0.
