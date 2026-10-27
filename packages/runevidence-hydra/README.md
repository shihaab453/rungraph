# runevidence-hydra

A [RunEvidence](../../README.md) adapter for experiment folders written by
[Hydra](https://hydra.cc). It turns Hydra's output folders into a starter
`runevidence.json`, with one run per Hydra job and each job's config recorded
as that run's params.

Hydra itself does not need to be installed: the adapter only reads the files
Hydra left on disk. Its one extra dependency is PyYAML.

## Install

From the root of a clone of this repository (neither package is on PyPI),
install core and the adapter together:

```
pip install -e . -e ./packages/runevidence-hydra
runevidence adapters        # should now list "hydra"
```

The adapter requires `runevidence>=0.1.1,<0.2.0`: it needs the updated core
adapter API and stays within the same minor release while core is pre-1.0.
Installing both local paths lets pip satisfy this requirement from your clone.
If core is already installed in that range,
`pip install -e ./packages/runevidence-hydra` is sufficient. For regular installs,
use `pip install . ./packages/runevidence-hydra`. CI installs those local paths
in two steps, core first.

## Use

Point `init` at the folder that holds Hydra's `outputs/` or `multirun/`
folders (usually your project folder):

```
runevidence init --adapter hydra .
runevidence validate .
```

Any folder that contains a `.hydra/` subfolder is treated as a run, at any
depth, so custom `hydra.run.dir` / `hydra.sweep.dir` locations work too.
For each run the manifest records:

| Field | Taken from |
| --- | --- |
| `params` | `.hydra/config.yaml` (the composed config) |
| `metadata.hydra.overrides` | `.hydra/overrides.yaml` |
| `metadata.hydra.choices` | config-group picks from `.hydra/hydra.yaml` (Hydra's own `hydra/*` groups left out) |
| `metadata.hydra.job_name`, `job_num`, `mode`, `version`, `override_dirname` | `.hydra/hydra.yaml` |
| `metadata.hydra.sweep_dir` | the enclosing folder with `multirun.yaml` (sweep jobs only) |
| `started_at` | Hydra's default `<date>/<time>` folder names, when used (for sweep jobs: the sweep start) |

Every file is hashed. Files under a run folder get that run's `run_id`;
`.hydra/*` and `multirun.yaml` get `kind: "config"`, logs in a run folder
get `kind: "log"`.

Not done on purpose: no results (Hydra records no metrics; add the numbers
you report yourself), no claims, and no absolute paths copied from
`hydra.yaml`, so the folder can be moved and still validate.

## Limits

- Runs started with `hydra.output_subdir=null` have no `.hydra/` folder and
  are not recognized. If no run is found at all, `init` exits 2 and suggests
  `--adapter generic`.
- A `.hydra/` file that cannot be parsed is still hashed; its path is listed
  in `metadata.hydra.unreadable`, and the reason in
  `metadata.hydra.unreadable_reasons`. YAML is read with `yaml.safe_load`, so
  tags like `!!python/object` are refused, never executed.
- Each `.hydra/` file is converted under fixed limits counted after YAML
  aliases are expanded: 100,000 values, 2,000,000 characters of text and 64
  nesting levels. A file past a limit (for example a small "alias bomb"), a
  self-referencing alias, or nesting too deep to parse is treated as
  unreadable instead of producing a huge manifest or a `RecursionError`.

## Development

```
pip install -e . -e "./packages/runevidence-hydra[dev]"
python -m pytest
```

The tests use `examples/hydra-sweep/` in the repository root, which was
produced by real Hydra 1.3.8.

License: Apache-2.0.
