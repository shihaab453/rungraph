# Dogfood 2: a preprocessing stage plus a Hydra-style sweep (2026-10-25)

Second real-run dogfood, chosen to stress what the first one
(`docs/dogfood-sklearn-20261025.md`) did not: a multi-stage pipeline, a folder
layout other than `runs/<name>/`, nested per-epoch checkpoints, and a run
that diverges. Folder: `examples/dogfood-mlp-sweep/`.

## The run

- **Data:** the diabetes progression dataset bundled with scikit-learn (442
  patients, 10 baseline features, regression target).
- **Stage 1, preprocess:** `data/raw/diabetes.csv` is split 310 / 44 / 88
  (train / val / test, seed 7) and standardized with training-split
  statistics into `data/processed/{train,val,test}.csv` plus `scaler.json`.
- **Stage 2, sweep:** a one-hidden-layer MLP (`MLPRegressor`, 32 units, SGD
  with momentum 0.9, 40 epochs via `partial_fit`) for three learning rates.
  Output follows Hydra's multirun layout without needing Hydra:
  `multirun/<date>/<time>/<job>/` with `.hydra/config.json`,
  `.hydra/overrides.json`, `checkpoints/epoch_0{10,20,30,40}.pkl`,
  `checkpoints/best.pkl` (best validation RMSE), `history.jsonl` (one line
  per epoch), `metrics.json` and `train.log`. The sweep writes
  `multirun.json` with the best job.
- **Results:**

  | Job | learning_rate_init | Status | Best epoch | Val RMSE | Test RMSE |
  | --- | --- | --- | --- | --- | --- |
  | 0 | 0.0003 | completed | 24 | 44.856 | 51.1836 |
  | 1 | 0.001 | completed | 40 | 44.62 | 50.9965 |
  | 2 | 0.01 | diverged at epoch 5 | 1 | 243.9775 | 213.8094 |

  Job 2's loss goes to `Infinity` at epoch 4 and `NaN` at epoch 5; its
  `metrics.json` records `"final_train_loss": NaN` (Python's `json` module
  writes the bare token).
- **Environment:** scikit-learn 1.9.1, numpy 2.5.4, Python 3.14.6, Windows 11.

To reproduce (needs scikit-learn, kept in the separate `.venv-dogfood`):

```powershell
cd examples\dogfood-mlp-sweep
python pipeline.py prepare
python pipeline.py preprocess
python pipeline.py sweep
```

A rerun is byte-identical except for `train.log` (wall-clock timestamps), but
lands in a new `multirun/<date>/<time>/` folder.

## How the manifest was made

1. Wrote `REPORT.md` with a results table and two claims.
2. `runevidence init examples/dogfood-mlp-sweep --name "Diabetes MLP learning-rate sweep"`:
   **0 runs**, 34 artifacts. The generic adapter only recognises `runs/<name>/`.
3. Annotated by script, as a user would for 34 files: 5 runs (`preprocess`,
   `sweep`, `job0`-`job2`), `run_id` and `kind` on 32 artifacts, 6 results
   with JSON Pointer locators (including job 2's `NaN` loss and `"diverged"`
   status), 2 claims, and 20 relationships (raw data and script `input_to`
   preprocess; processed splits `derived_from` raw; splits and script
   `input_to` every job; each job's metrics `input_to` the sweep).
4. `runevidence validate`: 2 errors, both caused by bug B6 below. After the
   fix: 0 errors, 0 warnings, also with `--strict`.

## Deliberate failures

Each scenario ran on a copy in a temp folder.

| Scenario | Outcome | Verdict |
| --- | --- | --- |
| S1: change one value in `data/raw/diabetes.csv` | Before fix: 1 `hash-mismatch`, no claim flagged. After: 3 errors, both claims at risk, e.g. `data-raw-diabetes.csv (via job0-val-rmse -> data-processed-test.csv)`. | Bug B7, fixed. |
| S2: delete job 1's `checkpoints/best.pkl` | 1 `missing-file`, exit 1. No claim flagged (the claims cite metrics, not the checkpoint). No "moved?" hint: job 0's `best.pkl` has the same name but different bytes. | Correct. |
| S3: edit job 1's val RMSE 44.62 -> 43.1 in `metrics.json` | `hash-mismatch`, `result-value-mismatch` (44.62 vs 43.1), `claim-best-lr` at risk. 3 errors, exit 1. | Correct. |
| S4: rerun the sweep (new timestamped folder) | 0 errors; `inspect` lists 27 untracked files. | Correct. |
| S5: then delete the old sweep folder | 27 `missing-file` + 2 `claim-at-risk`, exit 1. 24 of 27 carry "an identical file exists at `multirun/.../18-32-08/...`. Was it moved?"; the 3 `train.log` files differ (timestamps). | Correct, but noisy (gap G9). |
| S6: `init --force` to re-record digests | Before fix: warning listed 6 results, 2 claims, 20 relationships, but 5 runs, `run_id`/`kind` on 32 artifacts and the project description were also dropped silently. | Bug B8, fixed. |
| S7: result locator into `history.jsonl` | `result-unverifiable`: locators only support `.json`. Warning, exit 0. | Correct message; gap G11. |

## Bugs found and fixed

- **B6, NaN never matched NaN.** IEEE floats define `NaN != NaN`, so a
  diverged run whose `NaN` loss was faithfully recorded got
  `result-value-mismatch ... records nan but ... contains nan` and its claim
  was flagged at risk. `values_match` now treats two NaNs as equal (NaN
  against a number is still a mismatch; infinities already compared
  correctly).
- **B7, lineage was only followed one step.** A claim depended on a result,
  the file it was read from and the result's run inputs, but not on what
  those inputs were derived from or which run produced them. Editing the raw
  data behind a preprocessing step therefore put no claim at risk, even
  though the manifest recorded the whole chain. `claim-at-risk` now walks
  `reported_in`, producing runs, `input_to` and `derived_from` (artifact and
  run) transitively. Dependencies the old rule already caught print exactly
  as before; newly reached ones print as `id (via chain)`. The walk keeps a
  visited set, so `derived_from` cycles cannot hang it.
- **B8, `init --force` under-reported what it drops.** The warning now
  compares the old manifest with the regenerated one and lists runs that
  disappear, edited run names/params/metadata, artifact `run_id`/`kind`/
  `metadata`, and project description/tags/metadata. It also notes
  artifacts whose ID changes (re-checking dogfood 1 found
  `'report' is now 'REPORT.md'`, which would leave copied-back claims
  dangling).

Each has a regression test in `tests/test_dogfood_mlp_regressions.py`
(17 tests; 8 fail on the pre-fix code, the other 9 guard behaviour that was
already right). Full suite: 95 passed.

## Gaps (not fixed, ranked by pain)

Numbering continues from dogfood 1 (G1-G7).

- **G8, runs are invisible outside `runs/<name>/`.** Hydra (`outputs/` and
  `multirun/`), Lightning (`lightning_logs/version_N/`) and many custom
  layouts give 0 runs, so every `run_id` must be added by hand. Candidates:
  an `init --runs GLOB` option (e.g. `multirun/*/*/*`) or a Hydra adapter
  that reads `.hydra/overrides.json` into `params`.
- **G9, a moved run folder prints one error per file.** S5 printed 27
  near-identical `missing-file` lines whose hints all say the same folder
  moved. A folder-level hint ("everything under `.../18-12-38/` is in
  `.../18-32-08/`") would be one line. Extends G7.
- **G10, numbers in prose are not checked.** While writing REPORT.md the
  dogfooder (acting as the user) mistyped job 0's best epoch and val RMSE
  (40 / 45.17 instead of 24 / 44.86). RunEvidence could not notice: it only
  checks values recorded as results. Options: record every reported number
  as a result, or a later "report lint" that finds numbers in a Markdown
  table and matches them to results.
- **G11, no locators for JSONL or CSV.** Per-epoch histories are usually
  JSONL or CSV, so "loss is infinite at epoch 4" could only be supported by
  citing the whole file, not checked by value.
- **G12, non-finite values make the manifest non-standard JSON.** Recording
  job 2's loss writes a bare `NaN` token into `runevidence.json`. Python
  reads it, but strict parsers (JavaScript `JSON.parse`, `jq`, most JSON
  Schema validators) reject the file. Consider accepting the strings
  `"NaN"`, `"Infinity"`, `"-Infinity"` as equivalent.
- **G13, checkpoints are not claim foundations.** S2 (deleting the
  checkpoint the test RMSE was computed from) put no claim at risk, because
  the manifest has no way to say "this result was computed from that
  file" other than `reported_in`. A `computed_from` edge (result ->
  artifact) would close it.
- Confirmed again: G1 (no `rehash`), G2 (timestamped logs), G5 (re-recording
  one file needed a hand-computed digest after fixing REPORT.md), G6 (IDs
  like `multirun-2026-10-25-18-12-38-0-checkpoints-epoch_010.pkl`).

## Design change flagged for owner review

B7 changes what `claim-at-risk` means: from "one step upstream" to "the
whole recorded lineage". It is reversible (one function in
`checks/graph.py`), and the old one-step output is unchanged, but it means a
claim can now be flagged by a file several steps away. The trade-off: more
claims flagged after any upstream change (for example editing a shared
script listed as `input_to` every run), in exchange for never missing a
broken data chain.
