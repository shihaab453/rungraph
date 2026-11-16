# Writing a RunEvidence adapter

An adapter teaches `runevidence init` how to read a folder that some tool
(or some lab convention) wrote, and turn it into a manifest. The checks
themselves never change per tool: adapters only produce manifests.

## The interface

```python
from pathlib import Path
from runevidence.adapters import Adapter, Discovery
from runevidence.models import Manifest


class MyToolAdapter(Adapter):
    name = "mytool"                    # used as: runevidence init --adapter mytool
    description = "Folders written by MyTool >= 2.0"

    def discover(self, root: Path) -> Discovery:
        """Look, don't touch. Return the files that matter and anything you
        recognized (run folders, tool version) in `details`."""

    def extract(self, root: Path, discovery: Discovery, *, project_id: str, project_name: str) -> Manifest:
        """Build the manifest: artifacts with sha256, runs, and (if the tool
        records them in plain JSON/CSV) results with locators."""
```

`Discovery` is a small dataclass: `root`, `files` (POSIX paths relative to
root) and a free-form `details` dict passed from `discover` to `extract`.

The reference implementation is `src/runevidence/adapters/generic.py`
(about 60 lines). For a complete separate package (its own `pyproject.toml`,
entry point, tests and example), copy the layout of
[`packages/runevidence-hydra`](../packages/runevidence-hydra).

### When the folder is not yours to read

If `root` holds nothing your adapter recognizes (for example, none of the
run folders it looks for), raise `runevidence.adapters.AdapterError` with a
message saying what was expected and what to try instead. `runevidence init`
prints `ERROR: adapter '<name>' cannot read <folder>: <message>`, exits 2 and
writes nothing. Do not silently return an empty manifest: the user most
likely pointed at the wrong folder or picked the wrong adapter.

## Rules

1. **Read-only.** Never write into `root`. The CLI decides where the manifest goes.
2. **Offline.** No network calls.
3. **Never execute or deserialize experiment files.** No `pickle.load`,
   `torch.load`, `joblib.load`, `yaml.load` without `SafeLoader`, `exec`, or
   importing code from the folder. Hash raw bytes with
   `runevidence.hashing.sha256_file`; parse only plain data formats (JSON,
   CSV, safely loaded YAML, or text logs), using safe parsers.
4. **Relative POSIX paths** in every `Artifact.path`.
5. **Unique IDs** across runs, artifacts, results and evidence (one namespace).
6. **Tool-specific detail goes in `metadata` / `params`**, never in new
   top-level fields. The core schema stays domain-agnostic.
7. **Do not invent claims.** Adapters may fill results that the tool recorded;
   `evidence` is for people to write.
8. **No absolute paths in the manifest.** Tools often record the machine's
   working folder (Hydra's `cwd`, `output_dir`). Leave those out: they leak
   user names and break the "move the folder and it still validates" promise.
9. **Keep tool dependencies out of core.** Anything that needs PyYAML or a
   tool's library lives in the adapter package
   (`tests/test_adapters.py::test_core_never_imports_tool_libraries`).

## Publishing an adapter

Ship it as its own package (for example `runevidence-mytool`) and register it
with an entry point in that package's `pyproject.toml`:

```toml
[project.entry-points."runevidence.adapters"]
mytool = "runevidence_mytool:MyToolAdapter"
```

After `pip install runevidence-mytool`, it shows up in `runevidence adapters`
and works with `runevidence init --adapter mytool`. Built-in names cannot be
overridden, and a plugin that fails to import is skipped rather than
breaking the CLI.

## Testing an adapter

Build a tiny fake folder in a pytest `tmp_path`, run `discover` and
`extract`, save the manifest with `runevidence.manifest.save_manifest`, then
assert `run_checks(load_manifest(folder)).findings == []`. That proves your
manifest is valid and consistent with the files on disk.

## Monorepo adapters and CI

Adapters that live in this repository go under `packages/<name>/` with
their tests in `packages/<name>/tests/`. Add that tests folder to
`testpaths` in the root `pyproject.toml`, start the test module with
`pytest.importorskip("<your_module>")` so a core-only setup still passes, and
add an install step plus a `get_adapter("<name>")` check to
`.github/workflows/ci.yml`, so CI fails instead of silently skipping if the
package stops installing.

## Available adapters

### `generic` (built in)

Any folder. Hashes every file; each `runs/<name>/` subfolder becomes a run.
No params, results or claims.

### `hydra` (`packages/runevidence-hydra`)

For projects run with [Hydra](https://hydra.cc). Install it next to
RunEvidence (Hydra itself is not needed, only PyYAML). From the repository
root, install the local core and adapter together:

```
pip install -e . -e ./packages/runevidence-hydra
runevidence adapters                      # lists "hydra"
runevidence init --adapter hydra path/to/project
runevidence validate path/to/project
```

How it reads the folder:

- **Runs:** every folder that holds a `.hydra/` subfolder, at any depth. So
  Hydra's defaults (`outputs/<date>/<time>/`,
  `multirun/<date>/<time>/<job number>/`) and custom `hydra.run.dir` /
  `hydra.sweep.dir` locations all work. Run IDs come from the folder path,
  e.g. `run-multirun-2026-10-25-19-16-16-1`. Pointing `init` at a single run
  folder also works (that folder becomes the one run).
- **params:** the composed config from `.hydra/config.yaml`.
- **metadata.hydra:** `overrides` (from `overrides.yaml`), `choices`
  (config-group picks such as `optimizer: adam`; Hydra's own `hydra/*` groups
  are left out), `job_name`, `job_num` (sweep jobs only), `override_dirname`,
  `mode` (`RUN` or `MULTIRUN`), `version`, and `sweep_dir` for jobs inside a
  folder with `multirun.yaml`.
- **started_at:** parsed from Hydra's default `<date>/<time>` folder names
  when present (for sweep jobs this is the sweep's start). Otherwise empty.
- **Artifacts:** every file, hashed. Files inside a run folder get its
  `run_id` (the innermost run if folders nest). `.hydra/*` and
  `multirun.yaml` get `kind: "config"`; `*.log` in a run folder gets
  `kind: "log"`.
- **Not recorded:** results (Hydra stores no metrics), claims, and the
  absolute paths in `hydra.yaml`.

Edge cases:

- **No `.hydra/` anywhere:** `init` exits 2 and writes nothing. Runs started
  with `hydra.output_subdir=null` have no `.hydra/` folder, so they cannot be
  recognized; use `--adapter generic` for those.
- **A `.hydra/` file that will not parse** (broken YAML, an unsafe tag such
  as `!!python/object`, or the wrong shape) is still hashed, its contents are
  skipped, and its path is listed in `metadata.hydra.unreadable`, with a
  short reason per path in `metadata.hydra.unreadable_reasons`. YAML is only
  read with `yaml.safe_load`, so nothing in it is ever executed.
- **YAML aliases that expand too far or refer to themselves.** Anchors and
  aliases let a file of a few hundred bytes describe billions of values, or
  a structure that contains itself. Each parsed `.hydra/` file is converted
  without recursion and under fixed limits, counted after aliases are
  expanded: 100,000 values, 2,000,000 characters of text (keys, strings and
  integer digits) and 64 nesting levels. Conversion stops as soon as a limit
  is passed, so memory stays bounded. A file over a limit, a self-referencing
  alias, or nesting too deep for the YAML/JSON parser is treated like any
  other unreadable file: hashed, contents skipped, reason recorded. Normal
  anchors and `<<` merges well under the limits are expanded as before.
- **YAML-only values** (dates, `.nan`, sets, non-string keys) are converted
  to plain JSON (`"2026-10-25"`, `"nan"`, a sorted list, string keys), so the
  manifest stays standard JSON.
- **Scripts that imitate Hydra's layout** with `config.json` /
  `overrides.json` instead of YAML (like `examples/dogfood-mlp-sweep`) are
  read too.

Example: `examples/hydra-sweep/` is real Hydra 1.3.8 output (one single run,
one two-job sweep). Its `runevidence.json` is exactly what the adapter
writes, and a test checks that it still does.

### `mlflow` (`packages/runevidence-mlflow`)

Reads an **existing local MLflow file store**. MLflow itself is not needed;
the adapter parses plain files and uses PyYAML's safe loader for metadata.
Package version `0.1.1` follows the core release line and requires
`runevidence>=0.1.1,<0.2.0`. No package is published to PyPI by this workflow.

From the repository root, install both editable packages together:

```bash
pip install -e . -e ./packages/runevidence-mlflow
runevidence adapters                          # lists "mlflow"
runevidence init --adapter mlflow path/to/mlruns
runevidence validate path/to/mlruns --strict
```

For regular installs, install the local core **first**, so the adapter's
dependency is already satisfied:

```bash
pip install .
pip install ./packages/runevidence-mlflow
```

You can instead point `init` at a project containing `mlruns/`. Paths in the
manifest are relative to whichever root you choose. The recognized layout is:

```text
mlruns/
  <experiment-id>/
    meta.yaml
    <run-id>/
      meta.yaml
      params/<parameter-name>
      metrics/<metric-name>
      tags/<tag-name>
      artifacts/<logged-files>
```

How it reads the folder:

- **Experiments and runs:** experiment/run metadata identifies the runs.
  Experiment details go in `metadata.mlflow`; run metadata records the
  experiment and original run IDs. A copied `meta.yaml` inside an artifact
  does not create another run.
- **Params:** UTF-8 text files under `params/`, kept as strings, including
  numeric-looking values. Nested keys retain `/` in their names.
- **Start time:** `start_time` is read as milliseconds since the Unix epoch
  and converted to UTC.
- **Results:** one summary per readable metric history under `metrics/`.
  Rows are `timestamp value step`, or the legacy `timestamp value` format
  with step zero. The summary uses the greatest `(step, timestamp, value)`,
  so an out-of-order append does not pick the wrong step. This follows
  [MLflow's file-store metric selection](https://github.com/mlflow/mlflow/blob/v2.22.2/mlflow/store/tracking/file_store.py).
  Results link to the hashed metric file. No claims are inferred.
- **Artifacts:** ordinary files inside the selected root are hashed,
  including metadata, params, metric histories and logged artifacts. Files
  under a recognized run belong to that run. Experiment metadata and
  project files have no producing run.

Limits and edge cases:

- This supports on-disk file stores only, with no tracking server, database,
  API calls or network access. `artifact_uri` and `artifact_location` are
  neither followed nor copied. External artifacts must be copied inside the
  selected root to be tracked. All symlinks and Windows directory junctions
  are skipped, including links whose targets are inside the root. Each
  physical folder is also entered at most once per scan, so a link loop
  cannot make discovery repeat files under ever longer paths. A root that is
  itself reached through a link or junction is read normally.
- Machine-specific metadata such as user IDs, source paths and arbitrary
  tags is not copied into the manifest. Params remain the values you logged,
  so check them before sharing a manifest if they contain private paths.
- `.trash/` is not imported as runs. Files still present there are hashed.
- No recognized runs: `init` exits 2 and writes nothing. Point it at the
  file-store root or a project containing `mlruns/`, or use `--adapter generic`.
  A single experiment folder or run folder is not a supported root.
- Invalid or unsafe metadata YAML is refused with an adapter error. Nothing
  is executed. Empty, malformed, non-finite or unsupported metric histories
  produce no result; their paths are listed in `metadata.mlflow.unreadable`
  with a reason in `metadata.mlflow.unreadable_reasons`. A bad row invalidates
  the whole summary. The file bytes are still hashed.
- Metric logs are plain text. Core value locators support JSON only, so these
  results have no locator. On schema 0.1/0.2, `validate` verifies each summary
  through the adapter and `rehash` refreshes it; on 0.3–0.10 the existing
  `result-unverified` warning still applies.

#### MLflow metric summaries (RE-10)

On schema 0.1/0.2, a locator-less metric result is verified against its file:

- `validate` re-reads the summary and compares value, step and timestamp
  exactly. A difference is `result-value-mismatch`, and claims resting on the
  result are reported at risk. The digest of the parsed bytes must match the
  recorded digest, so a file that changes mid-check is reported, not accepted.
- `rehash` refreshes a selected summary from the bytes it accepts. It also
  repairs a summary that already disagrees with an unchanged file, so a
  manifest made stale by an older RunEvidence is fixed by selecting the metric
  (or `--all`); `init --force` is not required.
- A volatile metric file is not read. `validate` warns that the summary is not
  verified; `rehash` leaves it and prints a NOTE; `--no-volatile` records the
  digest and refreshes it.
- If no installed adapter claims the result, `validate` reports
  `result-unverified` and `rehash` accepts the bytes with a NOTE. It is never
  silently clean.
- A migrated 0.3–0.10 manifest keeps its existing `result-unverified` warning
  for the locator-less result and is not refreshed this way.

Metric histories are read under a declared bound: 50 MiB, 1,000,000 lines and
64 KiB per line. A history over a bound, or one that is malformed, non-finite
or not valid UTF-8, produces no result at `init`, is listed in
`metadata.mlflow.unreadable` with a reason, makes `validate` warn, and makes
`rehash` refuse that edit. The reader refuses symlinks and non-regular files.

Example: [examples/mlflow-file-store](../examples/mlflow-file-store) is a
small synthetic store with two runs. Copy it before running `init` if you
want to keep the checked-out example untouched.

### `tensorboard` (`packages/runevidence-tensorboard`)

For folders that hold TensorBoard event files (`.tfevents`). TensorFlow and
the `protobuf` runtime are **not** needed: the adapter uses the dependency-free
reader in core. Install it next to RunEvidence:

```
pip install -e . -e ./packages/runevidence-tensorboard
runevidence adapters                      # lists "tensorboard"
runevidence init --adapter tensorboard path/to/project
runevidence validate path/to/project
```

How it reads the folder:

- **Artifacts:** every regular file except RunEvidence's own manifest and
  backups. Non-event files keep the generic adapter's IDs and have no
  `run_id`. Event files (basename contains `.tfevents`, case-insensitively)
  get `tb-art-<16 hex of SHA-256(path)>`.
- **Runs:** one per directory that directly contains event files. Nested
  directories are separate runs and the experiment root is `.`. The run ID is
  `tb-run-<16 hex SHA-256(os.fsencode(dir_rel))>` and the name is the
  directory's relative path, so two event files in one directory share a run.
- **Results:** every selected supported scalar in discovery order, then record
  order, then `Summary.Value` order. A result ID is built from the run ID,
  artifact ID, tag, step and occurrence; a single-file `(tag, step)` group is
  named `tag @ step` (with ` #occurrence` for repeats), and a group spanning
  files is `tag @ step [basename] #<16-hex digest>`. The locator is the schema
  0.5 `{"kind": "tensorboard", ...}` object.
- **Filtering:** `init --tags PATTERN` (repeatable) keeps tags matching a
  full-string glob (`*`, `?`, `\` escapes, no character classes), capped at
  1,024 code points with a shared 50,000,000-unit budget. Without a filter an
  unrepresentable tag refuses `init`; with one it is skipped.
- **Profile:** TFRecord framing and CRC, the strict protobuf subset, and
  rank-0/rank-1[1] float and double scalars; the same reader the checker uses.
  Every event file is profile-checked even when the filter selects nothing.
- **Budgets:** 10,000 selected results and 16 MiB of selected tag UTF-8 bytes
  per manifest. The event file cap is 128 MiB.
- **One read:** the artifact digest and the parsed scalars come from the same
  protected read, so a file that changes during extraction refuses instead of
  producing a digest/parse mismatch.
- **Volatile:** a regular event artifact cannot be `--volatile`; matching one
  stops `init` before any backup or save, even when it has no selected result.
- **String tensors (0.6):** `init --include-strings` records scalar-shaped
  `DT_STRING` summaries as `tensorboard-string` results in a schema 0.6
  manifest. Without the flag the adapter writes schema 0.5 and ignores them.
  The per-value cap is 64 KiB and selected string bytes share a 16 MiB budget.
- **Histograms (0.7):** `init --include-histograms` records legacy
  `HistogramProto` summaries as structured `HistogramValue` results in schema
  0.7, with 1,024 buckets and a 16 MiB histogram-JSON budget. TF2 `[k,3]`
  tensor histograms are not recorded.
- **Images and audio (0.8):** `init --include-images` and `init --include-audio`
  record legacy `Summary.Image` and `Summary.Audio` payloads as digest-only
  `ImageValue`/`AudioValue` references in schema 0.8. PNG and RIFF/WAVE only;
  the bytes stay in the event file and `validate` re-hashes them.
- **Numeric tensors (0.9):** `init --include-tensors` records float32, float64,
  int32 and int64 tensors as canonical `TensorValue` digests in schema 0.9,
  with 1-8 dimensions and at most 1,000,000 elements. The scalar/string readers
  keep their own entries; every other dtype is skipped.

Without a media or tensor flag the adapter emits `Manifest05`; the flags select
0.6 (strings), 0.7 (histograms), 0.8 (media) or 0.9 (tensors), and
`runevidence validate` verifies the resulting locators. See the
[complete TensorBoard guide](tensorboard-guide.md) for the flag table, caps and
per-kind behavior. See
[Schema 0.5 TensorBoard scalar locators](../README.md#schema-05-tensorboard-scalar-locators-slice-d).
