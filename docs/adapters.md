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
  skipped, and its path is listed in `metadata.hydra.unreadable`. YAML is only
  read with `yaml.safe_load`, so nothing in it is ever executed.
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
  produce no result; their paths are listed in `metadata.mlflow.unreadable`.
  A bad row invalidates the whole summary. The file bytes are still hashed.
- Metric logs are plain text. Core value locators support JSON only, so
  these results have no locator. `validate` detects a changed metric file
  through its hash, but does not re-read its scalar value. `rehash` accepts
  the new bytes without refreshing these results. To refresh them, back up
  your manifest and run `init --adapter mlflow --force`, then restore your
  hand-written claims and links from the backup.

Example: [examples/mlflow-file-store](../examples/mlflow-file-store) is a
small synthetic store with two runs. Copy it before running `init` if you
want to keep the checked-out example untouched.
