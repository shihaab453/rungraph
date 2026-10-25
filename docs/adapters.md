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
(about 60 lines).

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
