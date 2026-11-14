<img src="https://raw.githubusercontent.com/shihaab453/rungraph/main/assets/icon.svg" width="64" height="64" alt="">

# RunEvidence

[![CI](https://github.com/shihaab453/rungraph/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/shihaab453/rungraph/actions/workflows/ci.yml?query=branch%3Amain)

**Does the evidence in your experiment folder still hold?**

RunEvidence checks an ML experiment folder against a small manifest
(`runevidence.json`) and tells you, offline:

- which recorded files are **missing**,
- which files **changed** since you recorded them (sha256), and lets you accept the changes you meant (`rehash`),
- which references **break when the folder is moved** (absolute paths, wrong case, paths outside the folder),
- which **claims** ("tuning improves accuracy") rest on results that are missing, changed, contradictory or not cited at all,
- which recorded metric values with JSON locators **no longer match** their source file.

It never trains, never launches jobs, never phones home, and never loads model
weights: files are hashed as raw bytes. Core checks metric values in JSON;
optional adapters also read plain configuration and metric logs. It works
with any ML stack because it only looks at files.

RunEvidence is V1 of the **RunGraph** project. Schema 0.2, migration and
conditions authoring are stable; recorded-condition comparability has an
experimental verdict CLI with a read-only readiness preflight. Lineage and the
rest of RunGraph are still planned. See [Roadmap](#roadmap).

---

## 10-minute walkthrough

You need Python 3.10 or newer and a terminal. Commands are shown for
PowerShell (Windows) and bash (macOS/Linux) where they differ.

### 1. Install (2 minutes)

```bash
git clone https://github.com/shihaab453/rungraph.git rungraph
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

If you only want the command, install it straight from GitHub without cloning:

```bash
pip install "git+https://github.com/shihaab453/rungraph.git"
```

RunEvidence is licensed under [Apache-2.0](LICENSE). The optional Hydra and
MLflow file-store adapters are separate packages. From the repository root,
install core and an adapter together, for example
`pip install -e . -e ./packages/runevidence-mlflow`;
see [Adapters](#adapters) for both packages and usage.

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
The same warning and relocation hint apply to that drive spelling in a
locator-less 0.1/0.2 manifest even on Linux: it is still read relative to the
manifest folder, never the current directory. An artifact named by a 0.3 or 0.4
locator is read through the protected snapshot instead, which reports
`missing-file` and never opens a lookalike file it was not told about.

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

Each target must select the same artifacts under every interpretation that
matches. For example, if `report` is an artifact ID and also a folder holding
other tracked files, the command refuses it with an ambiguity error (exit 2).
Choose explicitly with `id:report`, `run:run-tuned`, `path:REPORT.md`,
`folder:report/` or `glob:runs/*/train.log`. Quote globs to keep the shell from
expanding them. `path:` selects exact file paths; `folder:` selects files
inside a folder. Paths and folders can be relative to the manifest or the
current folder. If those locations match different artifact sets, use an
absolute path. Multiple targets combine their selected sets, but each target
must be unambiguous on its own. Existing unambiguous targets keep working;
an ID starting with a reserved prefix needs `id:`, for example
`id:path:report` selects the artifact whose ID is `path:report`.

When a re-recorded file is the source of a result with a supported,
resolvable scalar JSON locator (`artifact_id` + `locator`) and that file's
digest changed, `rehash` also updates the recorded value from the file and
prints `old -> new`. This includes recording a digest again with
`--no-volatile` for a source that was volatile. Only JSON locators that
resolve to a single scalar are refreshed; other result metadata is not.
Results without a locator never refresh, whatever the file's bytes. It then
lists every claim that rests on what changed: **re-read those claims**, because
a claim like "tuned beats baseline" may no longer be true even though every
check now passes. Refresh requires the current digest to differ from the
recorded digest (or no digest to have been kept while volatile). A wrong
value next to an unchanged, hash-checked file stays an error.

**RE-10: MLflow metric summaries can stay stale.** For MLflow text metric
results, `rehash` accepts the current source bytes and digest, but the recorded
summary **value, step and timestamp may stay old**. A later `validate --strict`
can then exit 0 after that acceptance. Changed bytes do not always mean the
summary is stale, and validation makes no scientific truth or general
freshness claim. See [Refreshing MLflow results
safely](https://github.com/shihaab453/rungraph/blob/main/docs/adapters.md#refreshing-mlflow-results-safely-re-10) and the
[draft alpha release notes](https://github.com/shihaab453/rungraph/blob/main/docs/alpha-release-notes-draft.md).

A **volatile** artifact (`"volatile": true`) is checked for existence only. No
digest is kept for it, so `validate` never reports its bytes as changed.
Result locators into volatile JSON files are still checked against their
recorded values and can put claims at risk. Deleting it is still an error. Mark files
volatile when you run `init` (`--volatile "*.log"`), when you `add` them, or
afterwards with `rehash --volatile`. Undo with `rehash --no-volatile`.
When a volatile source has a result with a locator, `rehash` leaves that
result value as it is and prints a note; results without a locator never
refresh. Use `--no-volatile` to record the current digest and, for supported
scalar JSON-located results, the current value.

`add` takes files or folders (a folder adds every file inside that is not
tracked yet). Paths are relative to where you run the command. If the
neighbouring files all belong to one run, the new file joins that run. Use
`--run RUN_ID` or `--no-run` to choose yourself.

`rehash` and `add` rewrite only the values they change in `runevidence.json`.
Other results, claims, links and field order are preserved. Indentation is normalized.
Both commands plan from one validated manifest snapshot and compare its exact
bytes with the on-disk manifest after staging a complete replacement. If the
manifest changed, they refuse the save (exit 2) and leave the competing edit
intact. Review the current manifest and retry. `migrate` likewise re-reads the
target immediately before creating its backup, and `conditions` uses the same
stale checks on its write path; `save_raw` checks the exact
bytes again. Where the host provides POSIX directory flock (`fcntl.flock`),
these five commands also take one non-blocking admission lock for the manifest
folder before they load it (and, for `init --force`, through the backup and the
save). A second RunEvidence CLI writer refuses with exit `2` and
`ERROR (BUSY)` instead of waiting. There the lock is mandatory: if it cannot be
taken at all, the command refuses with exit `2` and `ERROR (LOCK_UNAVAILABLE)`
and writes nothing. Hosts without directory flock (for example Windows) are
outside this guarantee and keep the pre-retrofit unlocked writer behavior; they
are not serialized, and no Windows parity is claimed. This is cooperative
serialization, not compare-and-swap: editors, scripts, older RunEvidence
versions and other writers do not take the lock, so they can still race between
the final comparison and replacement.
Commit before you run them if you want an easy undo.

For a worked example on a real training run (scikit-learn, two models, real
metrics), see `examples/dogfood-sklearn/` and its write-up in
[docs/dogfood-sklearn-20261025.md](https://github.com/shihaab453/rungraph/blob/main/docs/dogfood-sklearn-20261025.md). Its
`model.pkl` files are pickles: RunEvidence only hashes them, and you should
never unpickle files you did not create yourself.

A second worked example, `examples/dogfood-mlp-sweep/`, uses a different
layout: a preprocessing stage feeding a Hydra-style learning-rate sweep
(`multirun/<date>/<time>/<job>/`) with per-epoch checkpoints and one
diverged job. Write-up: [docs/dogfood-mlp-sweep-20261025.md](https://github.com/shihaab453/rungraph/blob/main/docs/dogfood-mlp-sweep-20261025.md).

---

## Commands

| Command | What it does |
| --- | --- |
| `runevidence validate PATH` | Run every check, print only problems. |
| `runevidence validate-batch --json MANIFEST...` | Validate several manifests in order and print a counts-only summary for each, plus an aggregate (`--strict`, `--redact-manifest-path`). See [Batch summaries](#batch-summaries). |
| `runevidence inspect PATH` | Summarize the manifest, then run every check, also listing files the manifest does not track. |
| `runevidence comparability BASELINE TARGET --baseline-result ID --target-result ID [--json]` | Experimental read-only comparison of five mandatory recorded conditions across two manifests (no artifact checks; exit `0` comparable, `1` different, `2` unavailable). `--preflight` is a text-only read-only readiness listing/pair check (no JSON; exit `0` list/ready, `1` not ready, `2` load/unknown/invalid). See [Comparability (verdict CLI experimental)](#comparability-verdict-cli-experimental). |
| `runevidence init [PATH]` | Scan a folder and write a starter `runevidence.json` (`--adapter`, `--name`, `--id`, `--force`, `--volatile GLOB`). With `--force`, the old manifest is kept as a backup, because a fresh scan does not carry over hand-written results and claims. See [Backups from `init --force`](#backups-from-init---force). |
| `runevidence migrate PATH --to 0.2\|0.3\|0.4 [--dry-run] [--json]` | Declare an existing valid manifest as the chosen schema without changing any recorded value. Exits `0` on success, no-op or dry run; `2` on refusal or failure. There is no inference and no downgrade. See [Migrating a manifest to 0.2](#migrating-a-manifest-to-02) and [Schema 0.4 CSV cell locators](#schema-04-csv-cell-locators-slice-c). |
| `runevidence conditions PATH --run RUN_ID [--patch FILE\|-] [--clear FIELD]... [--dry-run] [--json]` | Set or clear recorded run conditions on a 0.2 manifest from a local JSON patch; no condition values on the command line. Exits `0` on success, no-op or dry run; `2` on refusal or failure. See [Authoring run conditions](#authoring-run-conditions). |
| `runevidence rehash PATH TARGET... \| --all` | Accept intended changes: re-record digest and size, and re-read values only for supported, resolvable scalar JSON-located results in changed files (`--volatile`, `--no-volatile`, `--dry-run`). `--directory` re-records one exact directory artifact ID. |
| `runevidence add PATH FILE...` | Track new files or folders without re-running `init` (`--id`, `--kind`, `--run`, `--no-run`, `--volatile`, `--dry-run`). `add PATH DIR --directory --id ID` records one folder as a directory artifact. |
| `runevidence schema [-o FILE]` | Print the 0.1 manifest JSON Schema (also committed at `schema/runevidence.schema.json`). The separate 0.2 schema is `schema/runevidence-0.2.schema.json`. |
| `runevidence adapters` | List installed adapters. |

`PATH` can be the experiment folder or the manifest file. `validate` and
`inspect` accept `--json` (machine-readable) and `--strict` (warnings fail too).
With `--json`, either command also accepts `--redact-manifest-path`: the
report's `manifest` field and manifest-load finding `path` fields become
`"<redacted>"`. Recognized missing and unreadable manifest load errors also
use path-independent messages and hints. Without the flag, output is unchanged.
`--redact-manifest-path` is JSON-only (it requires `--json`); text output still
prints the manifest path in its header, as before. This does not sanitize the
whole report: other diagnostics, artifact paths, identifiers, adapter
diagnostics and exception contexts may still contain sensitive information.

`validate` and `inspect` also accept `--directory-artifacts`, an experimental
read-only opt-in for checking `artifact_type: "directory"` records with the
existing directory finding codes. It scans the recorded folder without writing
anything. On Linux it supports `ext4` only; another filesystem is reported as
`directory-unsafe` (`unsupported-filesystem`). Directory findings carry the
artifact ID and the recorded artifact path (the manifest text as written). A
recorded path that does not pass the strict relative portable-path profile is
rejected before scanning and may be echoed as written; that is user manifest
text, not the resolved scan root, which is never echoed. Directory diagnostics
never include the absolute scan root, `/proc` paths, member names, OS error
text, inode/device/mount IDs or a newly computed digest. Output is not
path-free: on top of directory findings, `inspect` keeps its existing
untracked-file findings, which may list member relative paths exactly as they
do without the flag, including after a failed directory scan; those are
existing `untracked-file` findings, not directory diagnostics. Without the
flag, a directory record is still reported as `not-a-file`. The flag is not
accepted by `validate-batch` or any authoring route, and it does not add
directory writing or a new command.

`validate --json --summary-only` prints only counts, for CI logs that should
not show finding details:

```json
{
  "summary_version": 1,
  "total_findings": 2,
  "by_severity": {"error": 1, "warning": 1},
  "by_code": {"missing-file": 1, "no-digest": 1},
  "exit_code": 1
}
```

Counts match the ordinary `validate --json` report, including manifest load
errors (exit `2`), and `exit_code` follows the same default and `--strict`
rules. Keys in `by_severity` and `by_code` are sorted; both list only
severities and codes that occurred (no zero counts), so a run with no
findings gives `{}` for each. Summary-only output omits dedicated
manifest-path and finding-detail fields (paths, messages, subjects and hints).
Its `by_code` and `by_severity` keys derive from finding code and severity
values, normally the declared enums; the summary helper does not independently
validate those values or sanitize arbitrary text within them.
`--redact-manifest-path` does not change the summary. `--summary-only` requires
`--json` and is accepted only by `validate`.

### Batch summaries

`validate-batch --json MANIFEST [MANIFEST ...]` checks each input in the
order given, one at a time, and prints the same counts-only summary that
`validate MANIFEST --json --summary-only` would print for it on its own:

```json
{
  "batch_version": 1,
  "results": [
    {"manifest": "exp-a", "summary": {"summary_version": 1, "total_findings": 0,
      "by_severity": {}, "by_code": {}, "exit_code": 0}},
    {"manifest": "exp-b/runevidence.json", "summary": {"summary_version": 1,
      "total_findings": 1, "by_severity": {"error": 1},
      "by_code": {"manifest-not-found": 1}, "exit_code": 2}}
  ],
  "aggregate": {
    "total_manifests": 2,
    "total_findings": 1,
    "by_severity": {"error": 1},
    "by_code": {"manifest-not-found": 1},
    "exit_code": 2
  }
}
```

There is one result per input, so a repeated input is checked and listed
again. A manifest that is missing, unreadable or schema-invalid gets its exit
`2` summary and the batch continues with the next input. `manifest` echoes
the input as typed; with `--redact-manifest-path` it is `"<redacted>"`.
`aggregate` adds up every result: `by_severity` and `by_code` are sorted and
list only values that occurred. The process exits with the highest individual
exit code, which is also `aggregate.exit_code`. `--strict` applies to every
input. `--json` and at least one input are required. The batch prints no
finding details, runs no extra checks and writes nothing.

For schema validation findings, `issue_location` is a JSON Pointer when a
safe structural location is known, or `null` otherwise. For scalar union
branch errors it identifies the containing field, an ancestor of the branch
error. Schema validation messages use these safe locations and categories
instead of submitted identifiers, keys, or Pydantic error text. Their wording
has changed; consumers should use the code and structured fields rather than
parse messages. Manifest file paths and other diagnostics may still contain
sensitive information, so the complete output is not guaranteed private.

### Comparability (verdict CLI experimental)

```text
runevidence comparability BASELINE TARGET \
  --baseline-result ID --target-result ID [--json]
runevidence comparability BASELINE TARGET --preflight \
  [--baseline-result ID --target-result ID]
```

Schema 0.2, `migrate` and `conditions` are stable; this comparison verdict CLI
remains experimental. `--preflight` is a separate read-only readiness mode: it
loads both manifests, writes nothing and prints fixed text only. With no
selectors it lists both sides' result candidates in manifest order (20 per
side, then a fixed `N more not shown` line; duplicate-ID sides are shown as
`unavailable: DUPLICATE_ID` and still exit `0`). With both selectors it reports
exactly that pair using the bridge's own mapping/bounds and the core's own
validation/projection; exactly one selector is `INVALID_ARGUMENTS`. Readiness
requires all five mandatory dimensions to be `present` and usable (not
`recorded-unknown`, `missing`, ambiguous or conflicting), the dataset
attachment to be mappable and
the pair checks to pass (including `SAME_RUN_ID` and core `NUMBER_LIMIT`).
Exit `0` means the list worked or the pair is ready; `1` means not ready or a
bridge mapping/bound refusal; `2` means a load failure, unknown result ID or
invalid arguments. `--preflight --json` is a fixed text refusal. Result IDs
come only from the loaded manifests (a >1024-character ID is replaced by a
fixed placeholder and counted) and are never echoed from the command line. A
ready pair can still compare as `INSUFFICIENT_EVIDENCE` for a case/whitespace
difference; the preflight footer says so. Readiness does not establish matching
values, artifact integrity or a successful comparison, and does not assess
omitted candidates or pairwise compatibility.

Compares five mandatory recorded conditions (dataset identity and version,
evaluation split, metric definition and evaluation protocol) and four optional
ones (preprocessing, source revision, model checkpoint and environment) across
two manifests. Both operands are manifest files or folders. Both selectors are
exact result IDs; they are not trimmed or case-folded. No artifact file is
opened, hashed or validated, and no manifest is written. This is not artifact
integrity, causal equivalence, reproducibility, statistical equivalence or
scientific validation.

Privacy: read [the Stage 2 privacy and CLI
contract](https://github.com/shihaab453/rungraph/blob/main/docs/comparability-stage2-privacy-cli-rfc.md)
before using real manifests. The command loads both complete manifests locally.
The comparison verdict output omits raw evidence values, IDs, names, paths,
hashes and metadata keys. The read-only preflight is the documented exception
for result IDs (see above); its error output stays ID-free. It reveals
condition presence, states, equality or difference, counts,
display-name difference and fixed rejection categories. Repeated comparisons
can reveal sensitive information, so treat output as sensitive. Trusted local
use only: this does not cover hostile Python objects, constructed or mutated
models, shared comparison services or cross-tenant use.

| Code | Meaning |
| --- | --- |
| `0` | `COMPARABLE_RECORDED_CONDITIONS`: all five mandatory recorded dimensions matched. |
| `1` | `NOT_COMPARABLE_RECORDED_CONDITIONS`: a mandatory recorded dimension differs. This is not an artifact-validation failure. |
| `2` | `INSUFFICIENT_EVIDENCE`, core-rejected input, manifest load failure, mapping failure, invalid arguments or internal error. Insufficient evidence can follow a completed evaluation. |

With `--json`, output is one versioned envelope (`format`,
`application_status`, `core`, `notice`); `core` is the frozen prototype
projection, unchanged. Validated outcomes that fail print exactly one fixed
application-error document; interpreter or resource failures outside the
validated outcome boundary (for example, process termination) may end without
that document. Parser diagnostics and submitted paths, IDs and tokens are never
echoed. For recognized invocations, `--json` is taken from the raw arguments,
so a grammar failure still prints a JSON error when requested. `--strict`,
`--redact-manifest-path` and `--summary-only` are not accepted.

> Experimental read-only CLI for a frozen prototype core. Preserved core
> integration flags are legacy core status, not current CLI availability.
> COMPARABLE_RECORDED_CONDITIONS means only that five mandatory recorded
> dimensions matched. It does not establish artifact integrity, causal
> equivalence, reproducibility, statistical equivalence or correct recording.
> Missing evidence, identical run-ID strings across manifests, unsupported
> shapes and bounds may prevent comparison. This is not production
> qualification.

### Exit codes

Exit codes are how scripts and CI (automated checks that run on every push)
know whether something passed without reading the text.

| Code | Meaning |
| --- | --- |
| `0` | No errors. Warnings may exist unless `--strict`. |
| `1` | The evidence has problems: errors, or warnings under `--strict`. |
| `2` | The check could not produce a pass/difference decision, either because it could not run or because a completed check returned an unavailable or inconclusive result: manifest missing, not valid JSON, or does not match the schema; bad arguments; `init` refused to overwrite, or could not write its backup or the new manifest (the old manifest is left as it was); `rehash` or `add` could not make the change (nothing was written; the `--directory` forms can exit `2` after a backup or a completed replacement, with a phase-specific line saying so); `migrate` refused the migration or could not write the migrated manifest (this command did not replace the manifest; another writer may have changed it); `conditions` refused the update or could not write the updated manifest (the manifest was not replaced; a completed backup may remain, and another writer may have changed it); on a host with POSIX directory flock, a built-in writer found the folder admission lock held by another RunEvidence writer (`BUSY`), or the mandatory lock could not be taken (`LOCK_UNAVAILABLE`); nothing was written in either case. |

For `comparability`, exit `2` includes `INSUFFICIENT_EVIDENCE` after a completed
evaluation, core-rejected input, manifest load or mapping failures, internal
errors and invalid arguments. It is not only a failure to check.

`validate`, `inspect` and `adapters` never write anything. `schema -o FILE`
writes the schema to that file. `init`, `migrate`, `conditions`, `rehash` and
`add` write the manifest; `init --force`, `migrate` and `conditions` also keep
the previous manifest as a backup.

### Backups from `init --force`

Every forced `init` keeps the manifest it is about to replace as a new
backup generation next to it: `runevidence.json.bak` first, then
`runevidence.json.bak.2`, `runevidence.json.bak.3`, and so on. The highest
number is the newest. An existing backup is never overwritten, and
RunEvidence never deletes backups, so repeated runs cannot destroy the only
copy of a hand-edited manifest. Delete old generations yourself when you no
longer need them. The newest backup is reused only when it is a regular file,
not the manifest or an alias of it, with identical bytes; otherwise the next
generation is created.

If `runevidence.json` is a symbolic link, `init --force` backs up the bytes the
link currently names as `runevidence.json.bak` next to the link and replaces the
link itself with the new manifest. The link target and its folder are never
written.

The backup is written and read back before the manifest is touched, and the
new manifest is staged beside the old one before replacing it. If either
step fails, `init` exits 2 and the existing manifest is left as it was; a
partly written backup is removed. Backups at the experiment root are never
tracked as artifacts or reported as untracked files. To restore one, copy
it over `runevidence.json`. `migrate` uses the same backup generation and reuse
scheme; see [Migrating a manifest to 0.2](#migrating-a-manifest-to-02).

## The manifest

| Section | Holds | Key fields |
| --- | --- | --- |
| `project` | What the folder is about | `id`, `name`, `description`, `tags` |
| `runs` | Executions of any kind | `id`, `name`, `started_at`, `params` |
| `artifacts` | Files on disk | `id`, `path` (relative, forward slashes), `sha256`, `size_bytes`, `volatile` (existence only), `kind`, `run_id` (producer) |
| `results` | Named values a run produced | `id`, `run_id`, `name`, `value`, `artifact_id`, `locator` (JSON Pointer, or a 0.3/0.4 structured locator) |
| `evidence` | Claims in plain words | `id`, `claim`, `supports`, `contradicts`, `source` |
| `relationships` | Extra typed edges | `source`, `target`, `type`: `produced_by`, `input_to`, `result_of`, `reported_in`, `derived_from`, `supports`, `contradicts` |

All IDs share one namespace. Unknown fields are rejected (so a typo like
`sha265` is caught); put anything custom in `metadata` (every entity) or
`params` (runs). The full contract is `schema/runevidence.schema.json`.

### Schema 0.2

Schema 0.2 is stable: later changes may not change the accepted manifest
interpretation, the documented outcomes or the authoring/refusal behavior
without a new schema version and an explicit migration. The comparison verdict
CLI remains experimental. Schema 0.1 is the default and existing behavior is
unchanged. A manifest opts
into 0.2 only with `"schema_version": "0.2"`; an omitted version means 0.1, a
0.1 manifest rejects every 0.2-only key (including one set to `null`), and
future versions are rejected. The new optional fields are `conditions` on runs
(split, protocol, dataset and checkpoint selectors, source revision and
environment), `metric_definition` on results and `dataset_id` on artifacts.
The two published schemas are `schema/runevidence.schema.json` (0.1, what
`runevidence schema` prints) and `schema/runevidence-0.2.schema.json` (0.2).

`validate` and `inspect` report sanitized selector-reference findings
(`condition-reference-missing`, `-wrong-kind`, `-unsupported` and
`-not-consumed`) for every declared selector, including runs you do not
compare. See `examples/comparability-recorded-0.2/` for a worked example and
[docs/comparability-schema-0.2.md](https://github.com/shihaab453/rungraph/blob/main/docs/comparability-schema-0.2.md)
for the field, reference and comparison rules. `init` and the adapters still
write 0.1, and nothing infers 0.2 from field presence. To opt in, run
`runevidence migrate PATH --to 0.2` before adding any first-class 0.2 field
(or change `schema_version` by hand for an unusual layout): a 0.2-only key in a
0.1 manifest is a schema error. `init --force` refuses a declared 0.2
manifest before backup or save; move it aside, run `init`, then migrate and
reapply the first-class values. Treat migrated 0.2 manifests as hand-authored
for `artifact.dataset_id` and `result.metric_definition`; `run.conditions` can
be authored with the `conditions` command or by hand. See the
[executable authoring example](docs/comparability-authoring-example.md) for the
full migrate, hand-edit, validate, preflight and compare sequence.

### Schema 0.3 verified locators (slice B)

Schema 0.3 supports verified string JSON pointers and structured JSONL object
locators. A targeted artifact is read once through a protected snapshot shared
by the file check and every locator; the parent path is walked one component at
a time with no-follow directory descriptors, and the strict JSONL and
whole-document JSON profiles reject invalid encodings, duplicates, overflow and
excessive nesting. `validate`/`inspect` report fixed locator codes, and an
absent locator in 0.3 is `result-unverified` (warning). CSV/`csv-cell` is not
part of slice B; schema 0.4 adds it (see below). Schemas 0.1 and 0.2 are
unchanged. See [docs/schema-0.3-locators.md](docs/schema-0.3-locators.md).

Only artifacts named by a 0.3 or 0.4 locator use the protected snapshot. All other
artifacts, including every artifact in a 0.1 or 0.2 manifest, are hashed with an
ordinary open that follows symbolic links after a separate containment check,
so a concurrent swap of a parent directory can make validate hash, and report
the digest and size of, a file outside the folder.

### Schema 0.4 CSV cell locators (slice C)

Schema 0.4 is an explicit opt-in that adds one locator kind, `csv-cell`, to the
0.3 string and `jsonl` forms. A result can locate one numeric cell in a `.csv`
artifact:

```json
{
  "id": "rmse",
  "run_id": "tuned",
  "name": "val_rmse",
  "value": 0.5,
  "artifact_id": "metrics",
  "locator": {"kind": "csv-cell", "row": 41, "column": 4, "header": "val_rmse"}
}
```

`row` is one-based over data records (row 1 is the first record after the
header; the header is never selectable), `column` is one-based over the parsed
header, and `header` is the required exact parsed-header assertion at that
column (`1` to `65,536` code points; at most `65,536` UTF-8 bytes at runtime).
`kind` and extension must agree: `csv-cell` needs a `.csv` path, case
insensitively. Any non-null 0.4 locator requires `artifact_id`. Unknown kinds,
bad ranges, an empty header, a lone surrogate, and a non-string/non-object
locator are `schema-invalid` load refusals (exit `2`).

The verifier is strict and POSIX-first: a comma-only RFC 4180-derived profile
with no sniffing, parsed records, LF/CRLF terminators (a bare CR is refused
anywhere), quoted-field rules, no blank-boundary or ragged records, UTF-8 with
one leading BOM stripped, and an ASCII-only numeric grammar. It never uses the
stdlib `csv` module, never splits on Unicode line boundaries, and reads the
artifact once through the slice-B protected snapshot. Structural caps are per
artifact: 50 MiB file, 1 MiB record, 64 KiB field, 1,024 columns, 1,000,000 data
rows and 10,000,000 cells. Two eligibility caps are per manifest and counted
without reading files: 10,000 `csv-cell` results and 16 MiB of total `header`
metadata; a capped manifest emits one `locator-unreadable` cap finding per
eligible artifact, marks it broken, and reads no cell. Outcomes reuse the
existing locator codes (`locator-kind-mismatch`, `locator-mismatch`,
`locator-unreadable`, `locator-unsupported-value`,
`result-value-mismatch`) plus `directory-locator-unsupported`,
`result-unverified` (warning), `claim-unverified` and `claim-at-risk`.

Human output shows a csv-cell as `csv-cell:<row>,<column>` and never the
recorded header; `--json` keeps the structured locator including `header`. A
finding never includes cell text, header text, raw row text, OS error text or
parser exception text. 0.3 human output is byte-identical. Use
`runevidence migrate PATH --to 0.4` to opt in: 0.1/0.2 sources run the
manifest-only locator precheck before any write
(`LOCATOR_MIGRATION_UNSUPPORTED`), and 0.3 sources get a version-only bump.
There is no downgrade (`DOWNGRADE_UNSUPPORTED`). A 0.4-capable `init --force`
refuses an existing 0.4 manifest before writing (`INIT_0_4_REFUSAL`); a
pre-0.4 binary does not know 0.4 and instead backs it up and replaces it with
0.1, so keep 0.4 manifests away from older builds. The `schema` command still
prints 0.1; the 0.4 schema is `schema/runevidence-0.4.schema.json`. Schemas
0.1/0.2/0.3 are unchanged and 0.3 still refuses `csv-cell`. See
[docs/schema-0.4-csv-cell.md](docs/schema-0.4-csv-cell.md).

### Migrating a manifest to 0.2

```bash
runevidence migrate PATH --to 0.2 [--dry-run] [--json]
```

`migrate` declares an existing valid manifest as 0.2. It is a stable authoring
path. It is explicit: nothing infers 0.2 from field presence, and `--to 0.2`,
`--to 0.3` and `--to 0.4` are the only supported targets (this section covers
0.2; 0.4 adds csv-cell and is described above).
The only value it changes is the top-level `schema_version`, which is written
first; every other decoded value, type, presence and object-key order is
preserved, apart from the canonical writer's normalization of number spelling
and formatting (for example `1e2` becomes `100.0`, an underflowing value stays
`0.0`, a float `-0.0` keeps its sign, and indentation is normalized). The
original bytes survive only in the backup generation.

On a real run, `migrate` creates `runevidence.json.bak` (then `.bak.2`,
`.bak.3`, ...; the highest number is newest) next to the manifest and never
overwrites or deletes an earlier generation. A new backup is created with the
manifest's POSIX permission bits, narrowed by the process umask; ACLs,
ownership and extended attributes are not copied or verified. The newest
generation is reused only when it is a regular non-symlink file, not the
manifest or an alias of it, with identical bytes and POSIX permission bits no
wider than the manifest's; otherwise the next generation is created. A dry
run still loads, proves preservation and performs the stale read, but writes
nothing and does not enumerate backups. A manifest that already declares the
requested target is a successful no-op (exit `0`, no backup, no write).

`init --force` refuses a manifest that declares schema 0.2, 0.3 or 0.4, valid
or not, before adapter discovery, backup or save; nothing is written. It prints
a fixed value-free message telling you to move the manifest aside, run `init`,
then `migrate --to 0.2` (or `--to 0.3` / `--to 0.4`) and reapply the
first-class values from the moved file.
A manifest whose raw JSON does not parse keeps the older backup-then-regenerate
path, because no 0.2/0.3/0.4 declaration can be read from it. A pre-0.4 binary
does not recognize 0.4 and therefore backs the manifest up and replaces it with
0.1; that older-build behavior is a documented limitation, not a 0.4 refusal.

Exit `0` means the manifest was migrated, would be migrated, or already
declared the requested target version (`--to 0.2`, `--to 0.3` or `--to 0.4`).
Exit `2` means this command did not replace the manifest; another writer may
have changed it, a source locator made 0.3 or 0.4 unsafe, or the requested
change was a downgrade. Output names one closed code:
`RESERVED_PATH`, `MANIFEST_LOAD_FAILED`, `SERIALIZATION_FAILED`,
`READ_FAILED`, `STALE_MANIFEST`, `WRITE_FAILED`,
`LOCATOR_MIGRATION_UNSUPPORTED`, `DOWNGRADE_UNSUPPORTED`, `INTERNAL_ERROR`, or
the CLI-level `INVALID_ARGUMENTS` / `UNSUPPORTED_TARGET`. Text output may show
the resolved manifest and backup paths; `--json` output is path-free and
contains no IDs, values or hashes.

An empty 0.2 manifest is only a compatibility declaration, not comparability
readiness, and it verifies no artifact. The mandatory order is:

1. `runevidence init` (or an adapter) writes a 0.1 manifest.
2. `runevidence migrate PATH --to 0.2`.
3. Author `run.conditions` with `runevidence conditions` (or by hand), then
   hand-add `artifact.dataset_id` and `result.metric_definition`.

Adding a 0.2-only field while the manifest still declares 0.1 is a schema
error. A reader that only understands 0.1 rejects every 0.2 manifest, even an
empty one. `migrate` reads no artifact, calls no adapter and uses no network
or environment state.

### Authoring run conditions

```bash
runevidence conditions PATH --run RUN_ID [--patch FILE|-] [--clear FIELD]... [--dry-run] [--json]
```

`conditions` is a stable authoring path. It sets or clears the six
`runs[i].conditions` children on a manifest that already declares schema 0.2;
run `migrate` first. A 0.1 target is
refused with instructions to migrate, and nothing is changed automatically. The
patch is one strict JSON object from a local file or `-`
for stdin; condition values never appear on the command line. At least one of
`--patch` or `--clear` is required. `--patch ./-` names a file called `-`; only
the exact token `-` means stdin.

Patch keys are exactly the six children: `evaluation_split`,
`evaluation_protocol`, `evaluation_dataset_artifact_id`,
`model_checkpoint_artifact_id`, `source_revision` and `environment`:

```json
{
  "evaluation_split": "test",
  "evaluation_dataset_artifact_id": "test-data",
  "source_revision": "9f2c1ab"
}
```

```bash
runevidence conditions path/to/experiment --run baseline-run \
  --patch conditions.json --dry-run
```

- An absent patch key changes nothing; a present key sets that child.
- `null` records an explicit UNKNOWN for `evaluation_split`,
  `evaluation_protocol`, `source_revision` and `environment`. It is invalid
  for either selector and for a nested `evaluation_protocol.version`;
  `evaluation_protocol: {}` is invalid too.
- `environment` must be a non-empty JSON object or `null`. `environment: {}`
  is refused even though the schema accepts it, because comparison rejects an
  empty environment.
- `--clear FIELD` removes that child and restores the legacy fallback. If all
  six children are absent after an operation, the `conditions` key itself is
  removed; `conditions: null` is never written. A clear of an absent child
  from an existing `conditions: {}` removes the empty parent and counts as a
  change.
- A selector must name an existing, non-directory artifact of the right kind
  (`dataset` or `checkpoint`) attached to the selected run by an `input_to`
  relationship. The command never synthesizes an edge, never falls back to
  legacy selection and reads no artifact bytes.

Every supplied value is bounded before writing:

| Bound | Limit |
| --- | --- |
| Patch raw bytes, file or stdin | 65,536 bytes |
| Container depth, including the root patch object | 9 levels |
| String values and object keys | 1,024 characters |
| `environment` container depth, including its root | 8 levels |
| `environment` compact JSON | 16,384 bytes |
| `environment` value | non-empty object |
| Integer magnitude | 9,007,199,254,740,991 |
| Float magnitude | 1e100 |
| Every supplied object or array width | 128 members |
| Nodes in each supplied value's subtree | 4,096 |

Strings and keys must be UTF-8-encodable. These limits apply to each
patch-supplied value; the comparison core's total input bytes and node count
across all dimensions and both sides remain comparison-time concerns. See
[RFC section 3.3](https://github.com/shihaab453/rungraph/blob/main/docs/comparability-stage3d3-conditions-rfc.md#33-bounds)
and
[RFC section 5.3](https://github.com/shihaab453/rungraph/blob/main/docs/comparability-stage3d3-conditions-rfc.md#53-comparison-time-bounds-at-authoring).

For a changed edit, `--dry-run` also performs the stale read; a no-op dry run
returns before it. It writes nothing. A real changed run creates or reuses one
`.bak` generation before replacing the manifest. The command performs no
inference and reads no artifact, repository, network or environment state. It
does not run `validate`, `inspect` or `comparability`, does not verify artifact
identity or reproducibility, and does not make a run comparable; use those
commands as the review path. Exit `0` means written, no-op or dry run; exit `2`
means refused or failed. Text output may show the resolved manifest and backup
paths; `--json` is path-free and value-free; its success document may repeat
only the supplied run ID and field names.

## What it checks

| Code | Severity | Meaning |
| --- | --- | --- |
| `missing-file` | error | Artifact path does not exist (with a hint if the file seems to have moved). |
| `not-a-file` | error | Path is a folder, not a file. |
| `hash-mismatch` | error | File bytes differ from the recorded sha256. |
| `size-mismatch` | error | Recorded size differs, with a matching digest or with no recorded digest. Emitted once per artifact. |
| `path-escapes-root` | error | Path leaves the experiment folder (`../`). |
| `absolute-path` | warning | Path will break when the folder moves. |
| `non-posix-path` | warning | Backslashes; use `/` so it works on every OS. |
| `path-case-mismatch` | warning | Only resolves because this filesystem ignores case. |
| `no-digest` | warning | No sha256 recorded, so changes cannot be detected. Record one with `rehash`, or mark the file `volatile` if that is intended. |
| `untracked-file` | info | In the folder but not in the manifest (`inspect` only). |
| `duplicate-id` | error | Same ID used twice. |
| `dangling-reference` | error | A link points at an ID that does not exist. |
| `invalid-relationship` | error | A link joins the wrong kinds (e.g. a run "produced by" an artifact). |
| `condition-reference-missing` | error | A run condition selector names an artifact that is not in the manifest. |
| `condition-reference-wrong-kind` | error | A run condition selector names an artifact whose kind is not the expected dataset/checkpoint kind. |
| `condition-reference-unsupported` | error | A run condition selector names a directory artifact, which comparison does not support. |
| `condition-reference-not-consumed` | error | A run condition selector's artifact has no `input_to` relationship to that run. |
| `conflicting-provenance` | error / warning | One file or result attributed to two runs; or a result read from another run's file. |
| `conflicting-evidence` | error | A claim both supports and contradicts the same thing; or one run reports two values for one metric. |
| `derivation-cycle` | error | `derived_from` loops. The search is iterative, so long valid chains (tested with 5,000 steps) are checked instead of crashing. |
| `unsupported-claim` | warning / error | A claim cites nothing; or none of its citations are valid. |
| `claim-at-risk` | error | A claim depends on a result, file or run input that is missing or changed. Lineage is followed upstream (`derived_from`, `input_to`, producing runs), so raw data two steps back counts; indirect causes are shown as `id (via ...)`. |
| `result-value-mismatch` | error | Recorded value differs from the value at its locator in the file. Numbers match within a small tolerance (relative 1e-9); an integer too large to convert to a float is compared exactly instead. `NaN` matches `NaN`, so a diverged run can be recorded faithfully. |
| `result-unverifiable` | warning | A legacy 0.1/0.2 locator cannot be checked (not JSON, cannot be parsed, not found, no artifact). |
| `locator-kind-mismatch` | error | A 0.3/0.4 locator targets the wrong artifact extension (`string` needs `.json`, `jsonl` needs `.jsonl`, `csv-cell` needs `.csv`). |
| `locator-mismatch` | error | A 0.3 line/pointer or a 0.4 row/column/header is blank, out of range, or absent. For `csv-cell`, a selected header must match the parsed header at that column. |
| `locator-unreadable` | error | The protected read failed (permission or other errno), or the snapshot/selected value does not satisfy the strict profile (encoding, duplicate keys, overflow, CSV dialect, caps, parse). |
| `locator-unreadable` (csv-cell cap) | error | A 0.4 manifest exceeds the csv-cell eligibility limits (10,000 `csv-cell` results or 16 MiB of `header` metadata). One finding per eligible artifact; no cell is read. |
| `locator-unsupported-value` | error | A 0.3 or 0.4 locator resolves to an array or object, not a scalar; or a 0.4 `csv-cell` result records a string, boolean or null instead of a number. |
| `artifact-changed` | error | The protected snapshot identity changed before or during the read, or a parent component was no longer the verified directory. |
| `locator-no-digest` | error | A result locates a value in an artifact with no recorded sha256. |
| `locator-unverified` | error | A result locates a value in a volatile artifact. |
| `result-unverified` | warning | A 0.3 or 0.4 result has no locator, so its value was not checked. |
| `claim-unverified` | warning | A claim cites only 0.3 or 0.4 results with no locator. |
| `locator-verifier-unavailable` | error | Defensive code for an unimplemented locator kind; `jsonl` and `csv-cell` are implemented. |

Directory findings appear only with `validate --directory-artifacts` or
`inspect --directory-artifacts`:

| Code | Severity | Meaning |
| --- | --- | --- |
| `missing-directory` / `not-a-directory` | error | The recorded directory root is absent or names a file. |
| `directory-hash-mismatch` / `directory-size-mismatch` | error | The recorded directory digest or size changed. |
| `directory-no-digest` | warning | No directory digest is recorded, so changes cannot be detected. |
| `directory-unsafe` | error | The root or a member breaks the scanner policy, including an unsupported platform or filesystem. |
| `directory-unreadable` / `directory-unstable` / `directory-limit-exceeded` / `directory-path-collision` | error | The scan could not read a stable, bounded tree. |
| `directory-locator-unsupported` | error | A result uses a locator on a directory artifact. |

### Recording a directory (`add --directory` / `rehash --directory`)

These two text-only forms record one folder as one `artifact_type: "directory"`
record. They are available on Linux with a release-qualified `ext4` manifest
folder; another platform or filesystem is refused before scanning. The forms
are `add PATH DIR --directory --id ID [--kind KIND] [--run RUN | --no-run]
[--dry-run]` and `rehash PATH ID --directory [--dry-run]`. Exactly one folder
and one explicit ID are required for add; rehash takes exactly one exact
directory artifact ID (bare or `id:`) and refuses `--all`, selectors, globs and
`--volatile`. No `--json` and no digest/size/metadata override exist.

`DIR` is interpreted lexically against the current working directory, without
resolving symbolic links, and must stay inside the manifest folder. An absolute
`DIR` is accepted only when that lexical path is inside. This deliberately fails
closed for a symlinked alias of the manifest folder or a symlinked directory
inside it: use the resolved/canonical path or a relative path instead. There is
no manifest-relative fallback.

A real write takes the manifest-folder admission lock, loads and checks the
manifest under it, runs the metadata/source-durability preflight, a first
native scan, a checked rebuild second scan, a private staging cell, one fresh
backup generation, an atomic replacement and a parent `fsync`. `--dry-run` takes
no lock, creates no cell/backup/temp, performs no source/parent `fsync`, requires
only read access and prints the would-be digest and member count (plus the
old and new size for `rehash`). Real and dry-run refusals both use the same
fixed reason lines; the legacy manifest-load report is never used here, so a
missing, unreadable or invalid manifest prints no path, `strerror` or parser
message. A
`rehash --directory` with an unchanged digest writes nothing. An add success
line prints the recorded artifact path, digest, member count and size; a rehash
success line prints the old and new digest/size and the member count but no
artifact path. Both print the manifest path, any backup path and the next-step
`runevidence validate PATH --directory-artifacts`. This is guarded
publication, not compare-and-swap: editors, scripts and other nonparticipating
writers are not locked out, the tree is not frozen, and the tree can change
between the last scan and replacement. No Windows/macOS, ACL, batch, tree-freeze
or CAS guarantee is claimed. See `docs/directory-authoring-s4-rfc.md`.

## Adapters

An adapter reads a folder written by some tool and produces a manifest, in two
read-only steps: `discover(root)` then `extract(root, discovery)`. The
`generic` adapter is built in. Tool adapters are separate packages, so core
stays free of tool dependencies:

- **`hydra`** ([packages/runevidence-hydra](https://github.com/shihaab453/rungraph/tree/main/packages/runevidence-hydra)):
  every folder holding a `.hydra/` subfolder becomes a run, at any depth, with
  the job's composed config as `params` and its overrides in `metadata`.
  Hydra itself is not needed.

  ```bash
  # From the repository root, install both local packages together.
  pip install -e . -e ./packages/runevidence-hydra
  runevidence init --adapter hydra path/to/project
  ```

- **`mlflow`** ([packages/runevidence-mlflow](https://github.com/shihaab453/rungraph/tree/main/packages/runevidence-mlflow)):
  reads local `mlruns/` file stores into experiments, runs, string params,
  hashed artifacts and metric results. MLflow itself is not needed.

  ```bash
  # From the repository root, install both local packages together.
  pip install -e . -e ./packages/runevidence-mlflow
  runevidence init --adapter mlflow path/to/mlruns
  runevidence validate path/to/mlruns
  ```

  File-store only: no tracking server, database or remote artifacts. Metric
  text files are hash-checked; JSON value locators do not apply to them.

See [docs/adapters.md](https://github.com/shihaab453/rungraph/blob/main/docs/adapters.md) for format details, limits and how to
write an adapter for your tool.

## Limits and guarantees

Read these before relying on a green `validate` for anything important.

- **Consistency, not truth.** A clean result means the folder still matches
  what the manifest recorded: the files exist with the same bytes, recorded
  JSON values match their files, and every claim cites valid evidence. It
  does not mean the experiment was right, the metric is meaningful or fresh,
  the comparison is fair or the claim is scientifically true. RunEvidence
  checks that your evidence trail is intact, not what it proves.
  `runevidence --help` says the same next to the exit codes.
- **One cooperating writer at a time (POSIX directory flock).** Where
  `fcntl.flock` is available, `init` (including `--force`), `add`, `rehash`,
  `migrate` and `conditions` take one non-blocking lock on the manifest folder
  before loading it and hold it through the save; a second RunEvidence CLI
  writer exits `2` with a fixed `BUSY` line (or `error.code: "BUSY"` in the
  existing migrate/conditions JSON envelope) and writes nothing. There the lock
  is mandatory: if it cannot be taken for any other reason, the command exits
  `2` with a fixed `LOCK_UNAVAILABLE` refusal and writes nothing, never an
  unlocked write. Admission resolves the target, locks its folder, and
  re-resolves the target before loading; a retarget in that gap is retried a
  bounded number of times and then refused with `LOCK_UNAVAILABLE`. Admission
  applies the loader's directory step exactly once, so `add DIR` and
  `add DIR/runevidence.json` agree with what the loader can read: an operand
  whose manifest path is still a directory keeps the legacy load refusal and
  is never written beside. The
  admitted command loads and saves the resolved manifest path, so retargeting
  the original alias afterwards cannot move the load or save outside the locked
  folder. `--dry-run` takes no lock. `init` is the exception: it writes the
  `runevidence.json` entry in the folder you name, so a symlink there is
  replaced in place with its backup in the same folder, and a directory at that
  name is refused rather than descended into. After admission, an external actor that
  replaces or renames the resolved parent directory can still redirect a
  path-based load or save outside the held lock; that is nonparticipating
  namespace mutation and is not covered, like the non-CAS limitation above.
  On hosts without POSIX directory
  flock (for example Windows), these commands keep the legacy unlocked writer
  behavior: no `BUSY`, no `LOCK_UNAVAILABLE`, and no serialization guarantee;
  no Windows parity is claimed. The guarantee also only covers this version's
  CLI: editors, scripts, older versions and other writers are not locked out.
  The stale-write checks remain but are not compare-and-swap, so a
  nonparticipant can still change the file between the final check and the
  replacement.
- **MLflow metric summaries can go stale (RE-10).** MLflow metric logs are
  plain text, so their results have no JSON locator. `validate` detects a
  changed metric file through its hash, but `rehash` accepts the new bytes
  without re-reading the summary value, step or timestamp, so the recorded
  result can keep old values. `validate --strict` can still exit 0 after that
  acceptance. Changed bytes do not always mean the summary is stale.
  `rehash` prints a `NOTE` for each such summary whose file it re-records,
  or whose file is volatile; the NOTE says the summary was not re-read, not
  that it is stale. To refresh, follow [Refreshing MLflow results
  safely](https://github.com/shihaab453/rungraph/blob/main/docs/adapters.md#refreshing-mlflow-results-safely-re-10): run
  `init PATH --adapter mlflow --force`, compare the backup with the
  regenerated manifest, keep the newly extracted summaries, and manually
  restore only reviewed claims, relationships, annotations, volatile flags and
  project settings after verifying IDs. Do not copy old metric results back.
  Version control and a separate backup are strongly recommended.
- **MLflow metric files are read whole.** When the MLflow adapter builds a
  manifest (`init --adapter mlflow`), it reads each metric history file
  completely into memory, with no size cap, so a very large metric log can
  use a lot of memory.
- **Very long integers.** Python limits how many digits it will turn into an
  integer (see `sys.set_int_max_str_digits`; the limit is configurable and
  can be disabled). A manifest containing a longer number is reported as
  `manifest-unreadable` (exit 2). A longer number in a 0.1/0.2 JSON file read
  through a locator makes that result `result-unverifiable`, a warning (exit 1
  only with `--strict`). In 0.3 the strict profile refuses it as
  `locator-unreadable` (error), and integer literals over 512 digits are
  refused. Integers within the limit but too large for a float are compared
  exactly for int-to-int. Whether the manifest schema accepts such very large
  integers as result values on every supported Pydantic version has not been
  separately verified.
- **Locator path diagnostics (schema 0.3).** A targeted artifact whose parent
  component is a symbolic link that resolves **inside** the folder is refused
  with `artifact-changed` and the fixed message `a parent directory is a
  symbolic link; use the real path` (for example an in-folder
  `latest -> run_2025` layout). A parent or leaf that physically resolves
  **outside** keeps `path-escapes-root`, even when every component is spelled
  inside; that covers an absolute or relative leaf link whose loop entry lives
  outside and a parent link to an outside folder that contains the loop. An
  artifact path that is physically outside the folder keeps `path-escapes-root`
  with or without a locator. Three `..` cases are distinct: an artifact path
  spelled with an in-root `..` (`sub/../x.jsonl`, or the same spelling made
  absolute) is valid without a locator, but the targeted reader refuses the
  spelling with the existing `locator-unreadable` code and `artifact path uses
  '..'; write it without parent references` (never a mid-read change, and no
  new finding code); a shape refusal without a `..` (doubled separator, any
  `'.'` component — including `./x.jsonl`, `sub/./x.jsonl` or a trailing
  `x.jsonl/.` — or trailing slash) keeps `locator-unreadable` but says
  `artifact path has an empty, '.' or trailing-slash component; write the
  plain file path` instead of blaming a parent reference that is not there,
  and it is reported before the extension/kind test, so no
  `locator-kind-mismatch` also names the wrong defect; a relative
  leaf alias target that stays inside (`sub/alias.jsonl ->
  ../metrics.jsonl`) is resolved and verified, exit `0`, because its `..`
  climbs a real parent directory the walk has verified — a target with `..`
  after a component of the target itself (`alias.jsonl -> "sub/../x.jsonl"`,
  relative or absolute) is refused instead with `locator-unreadable` and the
  fixed link-target message, never normalised, because that component can be a
  link; and a leaf or parent target that resolves above or outside the folder
  keeps `path-escapes-root`, not `artifact-changed`, and is never opened (even
  when it lexically re-enters). Absoluteness follows the host: on POSIX only a
  spelling starting with `/` is absolute. An **artifact path** with
  backslashes is normalised first (with `non-posix-path`), because the manifest
  may have been written on Windows; a drive-shaped spelling such as
  `C:/x.jsonl` (or `C:\x.jsonl`, normalised to `C:/x.jsonl`) is then a literal
  relative name resolved inside the manifest folder, never against the process
  cwd, and a locator-less artifact of that spelling keeps the legacy
  `absolute-path` warning and relocation hint. A drive-relative spelling
  (`C:x`, `C:data/...`, bare `C:`) or a non-letter `1:/...` is resolved as an
  ordinary relative name and does **not** get that warning, so `--strict` can
  exit `0` where older builds exited `1`; a single-letter `X:/...` spelling is
  warned as drive-shaped even when it is a literal folder name on POSIX. A
  backslash UNC artifact
  spelling such as `\\srv\share\x.jsonl` normalises to `//srv/share/x.jsonl`,
  which is host-absolute on POSIX and is refused `path-escapes-root` even when
  a file with that literal name exists; it is never read as a literal relative
  artifact name — unless a same-digest file elsewhere in the folder matches a
  suffix of the spelling, in which case the existing `missing-file` relocation
  hint is reported (as in slice A). A **link target** is never normalised: it is split on `/`
  only, backslashes stay literal, and `C:x.jsonl` or `\\srv\share\x.jsonl`
  is resolved against the link's own directory like the kernel does. An
  empty target component the kernel can resolve (a doubled separator) is
  refused as a shape error. `./x.jsonl` as a link target is shape-refused
  with the fixed link-target message, while `x.jsonl/.` or `x.jsonl/` as a
  link target is `missing-file` because the kernel pre-stat returns `ENOTDIR`
  before the shape check. A locator-less artifact keeps ordinary `Path` semantics for
  `./x.jsonl`, a `.` component or a trailing slash: it still opens and
  verifies the file and exits `0`; only the targeted artifact reader refuses
  the spelling. An in-folder `sub -> run_2025` parent link
  keeps the fixed parent message, while `root/esc -> outside` (regular outside
  leaf or outside loop) keeps `path-escapes-root`. A path spelled with `..` is
  never normalised, because that could verify a different file than the one
  the kernel opens.
  Moved-file hints are hashed through the same protected open, so an alias
  candidate behind a symlinked parent is not named.
  Legacy 0.1/0.2 artifacts still resolve containment with `Path.resolve`, so an
  in-folder symlink self-loop differs by Python version: `path-escapes-root` on
  3.10/3.12, `missing-file` on 3.13/3.14.
- **Large JSONL artifacts under a locator.** A targeted JSONL artifact is
  validated with C-level byte scans (NUL membership and CR/CRLF counts), then
  decoded once; the 1 MiB line cap still walks one C-level `find` per newline
  in a Python loop, so a newline-dense file costs about 0.4 s per 5 MiB of bare
  newlines (about 4 s at the cap) without allocating per line. The requested
  lines are indexed in one forward scan and only their offsets are kept, so
  many distinct locators do not rescan from the start. A 52,428,744-byte file
  of short lines peaks at about 114 MiB of RSS instead of the old eager
  per-line decode's 2,003,920 KB, and a CRLF-only file of the same size takes
  about 1.8 s instead of about 7 s. Memory is still proportional to one
  artifact's bytes plus its decoded text (about twice the file size) while that
  artifact is checked; snapshots are released before the next artifact is
  read.
- **CSV cells (schema 0.4).** The `csv-cell` verifier is strict and
  numeric-only: it refuses non-RFC dialects, blank-boundary and ragged
  records, bare CR, NUL, invalid UTF-8, non-numeric cells, and recorded
  string/boolean/null values. Real writers' `nan`, `inf`, `-inf` and empty
  cells therefore cannot be verified. Structural caps are 50 MiB per file,
  1 MiB per record, 64 KiB per field, 1,024 columns, 1,000,000 data rows and
  10,000,000 cells per artifact; the per-manifest eligibility caps are 10,000
  `csv-cell` results and 16 MiB of total `header` metadata. A capped artifact
  is marked broken, so `claim-at-risk` still says "missing or changed", which
  is imprecise for a resource cap. There are no column aggregates, delimiter
  sniffing, or semicolon/TAB dialects.
- **Bounded input.** Folder scans never follow symbolic links or Windows
  junctions out of the folder, and the MLflow adapter enters each physical
  folder once, so link loops cannot make a scan repeat itself. Hydra
  `.hydra/` files are converted under fixed limits after YAML aliases are
  expanded (100,000 values, 2,000,000 characters, 64 levels); a file past a
  limit or with a self-referencing alias is recorded as unreadable instead of
  producing a huge manifest. Long `derived_from` chains (tested with 5,000
  steps) are checked without recursion.
- **Deeply nested JSON.** A manifest nested beyond Python's recursion limit is
  reported as `manifest-unreadable`/`schema-invalid`, not a traceback. A JSON
  file read through a 0.1/0.2 locator maps `RecursionError` to
  `result-unverifiable`, and the strict 0.3 profiles refuse nesting beyond 64
  levels as `locator-unreadable`. Other JSON readers outside those paths are not
  separately depth-limited. Hydra's `config.json` and `overrides.json` are
  guarded as described above.
- **Supported versions.** Python 3.10 or newer. CI has six jobs: Ubuntu and
  Windows, each on Python 3.10 with the newest dependencies, Python 3.14 with
  the newest dependencies, and Python 3.10 with the lowest declared versions
  (`pydantic` 2.6.0, `PyYAML` 6.0). Python 3.14 with the pinned minimum
  dependencies is not directly verified. Python 3.11 to 3.13 and macOS are not
  directly verified either; they are not declared unsupported. `runevidence
  schema` output is spelled slightly differently before Pydantic 2.11 (it
  omits `"additionalProperties": true` on free-form fields and `"type":
  "string"` beside the version constant). Both spellings accept exactly the
  same manifests; the committed `schema/runevidence.schema.json` uses the
  2.11+ spelling.

## Non-goals (V1)

No experiment tracker, dashboard, web UI or server. No training, job
launching or GPU use. No network access. No loading of model weights.

## Roadmap

RunEvidence is the first layer of **RunGraph**. Schema 0.2, migration and
conditions authoring are stable. Recorded-condition comparability (did both
runs record the same data, split and code?) still has an experimental verdict
CLI; its read-only readiness preflight is the supported discovery/readiness
check, while the comparison verdict remains experimental.
Directory artifacts are delivered: read-only checking through
`validate`/`inspect --directory-artifacts` on Linux, and guarded authoring through
`add --directory` / `rehash --directory` on Linux/ext4. YAML manifests are
deferred for now: JSON remains the authority, and the
[decision memo](https://github.com/shihaab453/rungraph/blob/main/docs/manifest-format-yaml-decision-memo.md)
records why. [Locator slice B](https://github.com/shihaab453/rungraph/blob/main/docs/verified-value-locators-rfc.md)
is delivered (2026-11-13): schema 0.3 verifies string JSON pointers and structured JSONL
locators through one protected snapshot, with strict profiles, fixed findings
and claim propagation. Slice C is delivered (2026-11-14): schema 0.4 adds
verified numeric `csv-cell` locators under a strict CSV profile, with privacy
and a documented migration path, without changing 0.1/0.2/0.3. TensorBoard,
other adapters and cross-run lineage remain later work.

## Development

```bash
pip install -e ".[dev]" -e ./packages/runevidence-hydra -e ./packages/runevidence-mlflow
python -m pytest
```

Tests run offline in a few seconds. Contributions are welcome: see
[CONTRIBUTING.md](https://github.com/shihaab453/rungraph/blob/main/CONTRIBUTING.md).

## License

Apache-2.0. See [LICENSE](LICENSE) for the full text.
