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
   `runevidence.hashing.sha256_file`; parse only plain data (JSON, CSV).
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
RunEvidence (Hydra itself is not needed, only PyYAML):

```
pip install ./packages/runevidence-hydra
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
