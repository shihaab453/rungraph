<img src="assets/icon.svg" width="64" height="64" alt="">

# RunEvidence

**Does the evidence in your experiment folder still hold?**

RunEvidence checks an ML experiment folder against a small manifest
(`runevidence.json`) and tells you, offline:

- which recorded files are **missing**,
- which files **changed** since you recorded them (sha256), and lets you accept the changes you meant (`rehash`),
- which references **break when the folder is moved** (absolute paths, wrong case, paths outside the folder),
- which **claims** ("tuning improves accuracy") rest on results that are missing, changed, contradictory or not cited at all,
- which recorded metric values **no longer match** the metrics file they came from.

It never trains, never launches jobs, never phones home, and never loads model
weights: files are hashed as raw bytes, and the only thing it ever parses is
JSON metrics. It works with any ML stack because it only looks at files.

RunEvidence is V1 of the **RunGraph** project, which will add lineage and
comparability checks across runs. See [Roadmap](#roadmap).

---

## 10-minute walkthrough

You need Python 3.10 or newer and a terminal. Commands are shown for
PowerShell (Windows) and bash (macOS/Linux) where they differ.

### 1. Install (2 minutes)

```bash
git clone <this-repo-url> rungraph
cd rungraph
python -m venv .venv
```

Activate the virtual environment (a private folder of packages for this project, so nothing leaks into your system Python):

```powershell
.venv\Scripts\Activate.ps1        # PowerShell
```
```bash
source .venv/bin/activate         # bash
```

Then install RunEvidence and check it works:

```bash
pip install -e .
runevidence --version
```

`-e` ("editable") means the installed command runs the code in this folder, so edits take effect without reinstalling.

### 2. Inspect the example experiment (1 minute)

`examples/generic-experiment/` is a tiny fake experiment: a dataset, two runs
(`baseline`, `tuned`) each with a config, a placeholder model file and a
metrics file, and a short write-up making two claims.

```bash
runevidence inspect examples/generic-experiment
```

You get a summary of runs, artifacts, results and claims, ending with:

```
Findings:
OK: 0 errors, 0 warnings (exit 0)
```

Open `examples/generic-experiment/runevidence.json` alongside it. That file is
the whole idea: every artifact has a relative `path` and a `sha256`, every
result says which run produced it and where its value lives
(`"locator": "/eval/accuracy"` is a JSON Pointer into the metrics file), and
every claim lists the IDs that support it.

### 3. Break it on purpose (3 minutes)

Work on a copy so the example stays clean.

```powershell
# PowerShell
Copy-Item -Recurse examples\generic-experiment $env:TEMP\exp
Remove-Item $env:TEMP\exp\data\test.csv
(Get-Content $env:TEMP\exp\runs\tuned\metrics.json) -replace '1.0,', '0.75,' | Set-Content $env:TEMP\exp\runs\tuned\metrics.json
runevidence validate $env:TEMP\exp
```
```bash
# bash
cp -r examples/generic-experiment /tmp/exp
rm /tmp/exp/data/test.csv
sed -i.bak 's/"accuracy": 1.0/"accuracy": 0.75/' /tmp/exp/runs/tuned/metrics.json && rm /tmp/exp/runs/tuned/metrics.json.bak
runevidence validate /tmp/exp
```

Output (abridged):

```
ERROR missing-file [test-data]: Artifact 'test-data' is missing: 'data/test.csv' does not exist
ERROR hash-mismatch [tuned-metrics]: Artifact 'tuned-metrics' changed since it was recorded: sha256 33fa63b7a5ac... recorded, ... on disk.
ERROR result-value-mismatch [tuned-accuracy]: Result 'tuned-accuracy' (test_accuracy) records 1.0 but runs/tuned/metrics.json at /eval/accuracy contains 0.75
ERROR claim-at-risk [claim-tuned-perfect]: Claim 'claim-tuned-perfect' rests on evidence that is missing or changed: test-data, tuned-accuracy, tuned-metrics
      hint: Claim: 'The tuned run reaches 100% test accuracy.'
ERROR claim-at-risk [claim-tuning-helps]: ...
FAIL: 5 errors, 0 warnings (exit 1)
```

Read it top to bottom: root causes first (a file is gone, a file changed,
and the number you reported is no longer the number in the file), then their
consequences (which claims you can no longer back up). The missing test set
puts both claims at risk because both runs were evaluated on it.

### 4. Move the folder (1 minute)

Paths in the manifest are relative to the manifest's folder, so a moved,
zipped or emailed experiment still validates:

```bash
runevidence validate examples/generic-experiment     # from the repo root
cd examples && runevidence validate generic-experiment && cd ..   # from anywhere else
```

If someone writes an absolute path such as `C:/Users/me/exp/data/train.csv`,
RunEvidence warns (`absolute-path`), and once the folder moves it reports the
file as missing **and** points at where it is now:
`hint: A file matching it exists at 'data/train.csv'; update the path to that.`

### 5. Use it on your own folder (3 minutes)

```bash
runevidence init path/to/my-experiment --name "My experiment"
```

The built-in `generic` adapter hashes every file and turns each
`runs/<name>/` subfolder into a run (for Hydra's `outputs/` and `multirun/`
folders, use the [Hydra adapter](#adapters) instead). It does not guess which numbers or claims
matter. Add those yourself to the generated `runevidence.json`:

```json
"results": [
  {"id": "acc", "run_id": "run-a", "name": "accuracy", "value": 0.91,
   "artifact_id": "runs-a-metrics.json", "locator": "/accuracy"}
],
"evidence": [
  {"id": "claim-1", "claim": "Model A beats 0.9 accuracy.", "supports": ["acc"]}
]
```

Then `runevidence validate path/to/my-experiment`. Commit the manifest next to
your results; re-run validate before you publish anything.

### 6. When you change things on purpose

A failing check is not always a mistake. You retrained a model, wrote a new
report, or your training log carries a timestamp. Three commands record that
the change was intended, so you never hand-edit digests:

```bash
cd path/to/my-experiment

# You reran the "tuned" run: accept its new files (all files the run produced).
runevidence rehash . run-tuned --dry-run   # preview first
runevidence rehash . run-tuned

# Logs change on every run: check only that they exist, not their bytes.
runevidence rehash . "runs/*/train.log" --volatile

# A new file appeared: track it without re-running init.
runevidence add . REPORT.md --kind report
```

`rehash` targets can be artifact IDs, run IDs (every file that run produced),
file or folder paths, or globs over manifest paths (quote them; `*` also
crosses folders). It needs targets or `--all`, so nothing is accepted by
accident. It refuses (and writes nothing) if a target file is missing.

When a re-recorded file is the source of a result (`artifact_id` +
`locator`), `rehash` also updates the recorded value from the file and prints
`old -> new`. It then lists every claim that rests on what changed: **re-read
those claims**, because a claim like "tuned beats baseline" may no longer be
true even though every check now passes. A recorded value is only updated when
its file's bytes changed. A wrong value next to an unchanged file stays an
error.

A **volatile** artifact (`"volatile": true`) is checked for existence only. No
digest is kept for it, so `validate` never reports its bytes as changed.
Result locators into volatile JSON files are still checked against their
recorded values and can put claims at risk. Deleting it is still an error. Mark files
volatile when you run `init` (`--volatile "*.log"`), when you `add` them, or
afterwards with `rehash --volatile`. Undo with `rehash --no-volatile`.

`add` takes files or folders (a folder adds every file inside that is not
tracked yet). Paths are relative to where you run the command. If the
neighbouring files all belong to one run, the new file joins that run. Use
`--run RUN_ID` or `--no-run` to choose yourself.

`rehash` and `add` rewrite only the values they change in `runevidence.json`.
Other results, claims, links and field order are preserved. Indentation is normalized.
Commit before you run them if you want an easy undo.

For a worked example on a real training run (scikit-learn, two models, real
metrics), see `examples/dogfood-sklearn/` and its write-up in
[docs/dogfood-sklearn-20261025.md](docs/dogfood-sklearn-20261025.md). Its
`model.pkl` files are pickles: RunEvidence only hashes them, and you should
never unpickle files you did not create yourself.

A second worked example, `examples/dogfood-mlp-sweep/`, uses a different
layout: a preprocessing stage feeding a Hydra-style learning-rate sweep
(`multirun/<date>/<time>/<job>/`) with per-epoch checkpoints and one
diverged job. Write-up: [docs/dogfood-mlp-sweep-20261025.md](docs/dogfood-mlp-sweep-20261025.md).

---

## Commands

| Command | What it does |
| --- | --- |
| `runevidence validate PATH` | Run every check, print only problems. |
| `runevidence inspect PATH` | Summarize the manifest, then run every check, also listing files the manifest does not track. |
| `runevidence init [PATH]` | Scan a folder and write a starter `runevidence.json` (`--adapter`, `--name`, `--id`, `--force`, `--volatile GLOB`). With `--force`, the old manifest is kept as `runevidence.json.bak`, because a fresh scan does not carry over hand-written results and claims. |
| `runevidence rehash PATH TARGET... \| --all` | Accept intended changes: re-record digest and size, and re-read result values from changed files (`--volatile`, `--no-volatile`, `--dry-run`). |
| `runevidence add PATH FILE...` | Track new files or folders without re-running `init` (`--id`, `--kind`, `--run`, `--no-run`, `--volatile`, `--dry-run`). |
| `runevidence schema [-o FILE]` | Print the manifest JSON Schema (also committed at `schema/runevidence.schema.json`). |
| `runevidence adapters` | List installed adapters. |

`PATH` can be the experiment folder or the manifest file. `validate` and
`inspect` accept `--json` (machine-readable) and `--strict` (warnings fail too).

### Exit codes

Exit codes are how scripts and CI (automated checks that run on every push)
know whether something passed without reading the text.

| Code | Meaning |
| --- | --- |
| `0` | No errors. Warnings may exist unless `--strict`. |
| `1` | The evidence has problems: errors, or warnings under `--strict`. |
| `2` | Could not check at all: manifest missing, not valid JSON, or does not match the schema; bad arguments; `init` refused to overwrite; `rehash` or `add` could not make the change (nothing was written). |

`validate`, `inspect` and `adapters` never write anything. `schema -o FILE`
writes the schema to that file. `init`, `rehash` and `add` write the manifest;
`init --force` also keeps the previous manifest as `runevidence.json.bak`.

## The manifest

| Section | Holds | Key fields |
| --- | --- | --- |
| `project` | What the folder is about | `id`, `name`, `description`, `tags` |
| `runs` | Executions of any kind | `id`, `name`, `started_at`, `params` |
| `artifacts` | Files on disk | `id`, `path` (relative, forward slashes), `sha256`, `size_bytes`, `volatile` (existence only), `kind`, `run_id` (producer) |
| `results` | Named values a run produced | `id`, `run_id`, `name`, `value`, `artifact_id`, `locator` (JSON Pointer) |
| `evidence` | Claims in plain words | `id`, `claim`, `supports`, `contradicts`, `source` |
| `relationships` | Extra typed edges | `source`, `target`, `type`: `produced_by`, `input_to`, `result_of`, `reported_in`, `derived_from`, `supports`, `contradicts` |

All IDs share one namespace. Unknown fields are rejected (so a typo like
`sha265` is caught); put anything custom in `metadata` (every entity) or
`params` (runs). The full contract is `schema/runevidence.schema.json`.

## What it checks

| Code | Severity | Meaning |
| --- | --- | --- |
| `missing-file` | error | Artifact path does not exist (with a hint if the file seems to have moved). |
| `not-a-file` | error | Path is a folder, not a file. |
| `hash-mismatch` | error | File bytes differ from the recorded sha256. |
| `size-mismatch` | error | No digest recorded, but the size changed. |
| `path-escapes-root` | error | Path leaves the experiment folder (`../`). |
| `absolute-path` | warning | Path will break when the folder moves. |
| `non-posix-path` | warning | Backslashes; use `/` so it works on every OS. |
| `path-case-mismatch` | warning | Only resolves because this filesystem ignores case. |
| `no-digest` | warning | No sha256 recorded, so changes cannot be detected. Record one with `rehash`, or mark the file `volatile` if that is intended. |
| `untracked-file` | info | In the folder but not in the manifest (`inspect` only). |
| `duplicate-id` | error | Same ID used twice. |
| `dangling-reference` | error | A link points at an ID that does not exist. |
| `invalid-relationship` | error | A link joins the wrong kinds (e.g. a run "produced by" an artifact). |
| `conflicting-provenance` | error / warning | One file or result attributed to two runs; or a result read from another run's file. |
| `conflicting-evidence` | error | A claim both supports and contradicts the same thing; or one run reports two values for one metric. |
| `derivation-cycle` | error | `derived_from` loops. |
| `unsupported-claim` | warning / error | A claim cites nothing; or none of its citations are valid. |
| `claim-at-risk` | error | A claim depends on a result, file or run input that is missing or changed. Lineage is followed upstream (`derived_from`, `input_to`, producing runs), so raw data two steps back counts; indirect causes are shown as `id (via ...)`. |
| `result-value-mismatch` | error | Recorded value differs from the value at its locator in the file. `NaN` matches `NaN`, so a diverged run can be recorded faithfully. |
| `result-unverifiable` | warning | A locator cannot be checked (not JSON, not found, no artifact). |

## Adapters

An adapter reads a folder written by some tool and produces a manifest, in two
read-only steps: `discover(root)` then `extract(root, discovery)`. The
`generic` adapter is built in. Tool adapters are separate packages, so core
stays free of tool dependencies:

- **`hydra`** ([packages/runevidence-hydra](packages/runevidence-hydra)):
  every folder holding a `.hydra/` subfolder becomes a run, at any depth, with
  the job's composed config as `params` and its overrides in `metadata`.
  Hydra itself is not needed.

  ```bash
  pip install ./packages/runevidence-hydra
  runevidence init --adapter hydra path/to/project
  ```

See [docs/adapters.md](docs/adapters.md) to write one for your tool.

## Non-goals (V1)

No experiment tracker, dashboard, web UI or server. No training, job
launching or GPU use. No network access. No loading of model weights.

## Roadmap

RunEvidence is the first layer of **RunGraph**. Planned next, roughly in order:
directory artifacts, YAML manifests, more tool adapters (MLflow file stores,
TensorBoard logs, plain CSV logs), then RunGraph proper: lineage across runs
and comparability checks (did both runs use the same data, split and code?).

## Development

```bash
pip install -e ".[dev]" -e ./packages/runevidence-hydra
python -m pytest
```

Tests run offline in a few seconds. Contributions are welcome: see
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache-2.0. See [LICENSE](LICENSE).
