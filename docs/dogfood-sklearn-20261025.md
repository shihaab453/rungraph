# Dogfood: a real scikit-learn run (2026-10-25)

First use of RunEvidence on a real training run instead of a hand-made
fixture. Folder: `examples/dogfood-sklearn/`.

## The run

- **Data:** the Wisconsin diagnostic breast-cancer dataset bundled with
  scikit-learn (569 rows, 30 features), exported to `data/breast_cancer.csv`,
  with a stratified 75/25 split (seed 42) saved as `data/split.json`.
- **Models:** standardized logistic regression (C=1.0) and a random forest
  (50 trees, depth 4), both trained by an ordinary script, `train.py`, that
  knows nothing about RunEvidence.
- **Per run:** `config.json`, `model.pkl` (pickle; RunEvidence only hashes
  it and never loads it), `metrics.json`, `predictions.csv`, `train.log`.
- **Results:** logistic regression 0.986 test accuracy / 0.9977 ROC AUC,
  random forest 0.958 / 0.9948, on 143 test rows.
- **Environment:** scikit-learn 1.9.1, numpy 2.5.4, Python 3.14.6, Windows 11.

To reproduce the run (needs scikit-learn, kept in a separate venv):

```powershell
cd examples\dogfood-sklearn
python train.py prepare
python train.py fit logreg
python train.py fit forest
```

## How the manifest was made

1. `runevidence init examples/dogfood-sklearn --name "Breast cancer classifier comparison"`:
   2 runs and 13 artifacts detected, validated clean on the first try.
2. Wrote `REPORT.md` with two claims; `inspect` flagged it as an untracked file.
3. Hand-edited the manifest: added `REPORT.md` (digest pasted from
   PowerShell `Get-FileHash`), 4 results with JSON Pointer locators into the
   metrics files, 2 claims, and 6 `input_to` links (dataset, split and
   script into both runs).
4. `runevidence validate`: 0 errors, 0 warnings, all 4 recorded values match
   their metrics files.

## Deliberate failures

| Scenario | Outcome | Verdict |
| --- | --- | --- |
| S1: rerun identical logreg training | Only `train.log` changed (timestamp); model pickle and predictions byte-identical. 1 error, exit 1. | Correct, and useful: it proves the retrain is reproducible. But volatile files fail the check (gap G1). |
| S2: retrain logreg with C=0.01 | 6 hash mismatches, accuracy 0.986 -> 0.9371 and AUC 0.9977 -> 0.9952 reported, both claims at risk. 10 errors, exit 1. | Correct. |
| S3: delete `runs/forest/predictions.csv` | `missing-file`. | Correct after fix B2. |
| S4: change one byte of the dataset | Dataset hash mismatch, both claims at risk via `input_to`. 3 errors, exit 1. | Correct. |
| S5: move the whole folder | 0 errors, exit 0. | Correct. |

## Bugs found and fixed

- **B1, uppercase digests rejected.** PowerShell `Get-FileHash` prints
  uppercase hex; the schema only accepted lowercase, so a pasted digest gave
  `schema-invalid`. Now either case is accepted and stored lowercase.
- **B2, misleading "was it moved?" hint.** A missing `runs/forest/predictions.csv`
  suggested `runs/logreg/predictions.csv` because the name matched. In ML
  folders every run shares file names, so a name match means little. Now,
  when a digest is recorded, only byte-identical files are suggested (the
  same rule applies to relocated absolute paths).
- **B3, `init --force` silently discarded hand-written work.** Re-running
  init (the only way to re-record digests today) dropped 4 results, 2
  claims and 6 relationships without a word. It now saves
  `runevidence.json.bak` and lists what the new manifest lacks. The backup is
  ignored by later scans.
- **B4, schema errors were hard to locate.** `artifacts[13].sha256: String
  should match pattern ...` now reads `artifacts[13] (id 'report').sha256`.
- **B5, project IDs kept capitals** (`Breast-cancer-...`). `init` now
  lowercases the derived ID.

Each has a regression test in `tests/test_dogfood_regressions.py`.

## Gaps (not fixed, ranked by pain)

- **G1, no way to accept intended changes.** After S1 or S2, the user must
  hand-edit up to 7 digests or re-init and copy annotations back. Needs
  `runevidence rehash [IDS...]` that re-records digests and sizes while
  keeping results, claims and links.
- **G2, volatile files.** Logs with timestamps change on every rerun. Needs
  a way to mark an artifact as "track existence only" without a `no-digest`
  warning (which fails `--strict`).
- **G3, results must be written by hand.** Every run here had a
  `metrics.json`; `init` could offer to record its numeric values as results
  with locators (opt-in, for example `--metrics-file metrics.json`).
- **G4, inputs are not linked.** The generic adapter cannot tell inputs from
  outputs, so `input_to` links (which make S4 work) were hand-written. Config
  files under `runs/<name>/` are recorded as outputs of the run even though
  they are really inputs.
- **G5, adding one file is clumsy.** Adding `REPORT.md` needed a manual
  digest. An `add PATH` command (or `rehash --new`) would fix it.
- **G6, generated IDs are long** (`runs-logreg-metrics.json`). Readable but
  noisy in output; consider `logreg/metrics` style IDs.
- **G7, one finding per changed file.** S2 printed 6 near-identical
  `hash-mismatch` lines for one retrained run; a per-run grouping or summary
  line would read better.
