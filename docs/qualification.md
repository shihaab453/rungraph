# RunGraph qualification record

This page records the evidence behind the statuses in
[docs/product-completion.md](product-completion.md). It is an engineering record, not a
release announcement. RunGraph remains an alpha: nothing here is published to a package
index, tagged or version-bumped.

## Capability status

Every primary capability in the completion contract is `COMPLETE` except two deliberate
platform bounds, F3 and B6, listed under Limitations.

| Area | Status | Evidence |
| --- | --- | --- |
| A1 frozen versioned schemas, migration | COMPLETE | schemas 0.1–0.11; `tests/test_schema_freeze.py`, `test_cli_migrate.py` |
| A2 generic ingestion | COMPLETE | generic adapter; empty scans refuse with exit 2 |
| A3 Lightning/Keras/Sacred/Hydra/MLflow/TensorBoard adapters | COMPLETE | adapter packages and tests; Sacred JSON-pointer metrics, TF2 media 0.10 |
| A4 result/claim/evidence/relationship authoring | COMPLETE | `runevidence result/evidence/relationship`; `tests/test_authoring.py` |
| A5 condition authoring 0.2–0.11 | COMPLETE | `conditions`; `tests/test_cli_conditions.py` |
| A6 source/environment capture | COMPLETE | `capture`; `tests/test_cli_capture.py` |
| A7 safe mutation | COMPLETE | backups, stale-byte checks, POSIX admission lock |
| B1–B5 file, value, graph, batch, unreadable input | COMPLETE | core checks; full suite |
| B6 directory artifacts | BOUNDED (Linux only) | guarded recording and re-verification; non-Linux filesystems fail closed |
| C1–C5 detail, lineage, impact, coverage, comparison | COMPLETE | analysis routes and tests |
| C6 recorded-condition comparability 0.1–0.11 | COMPLETE (experimental verdict) | `comparability`, preflight; tests |
| C7 evidence-aware comparison | COMPLETE | `assess`; `tests/test_cli_assess.py` |
| C8 change analysis between snapshots | COMPLETE | fingerprint/diff/review-gate routes |
| D0–D6 workspace composition | COMPLETE | `rungraph` CLI; workspace tests, examples |
| E1 every route documented | COMPLETE | README reference; docs-contract route test |
| E2 TensorBoard and workspace examples | COMPLETE | `examples/tensorboard-scalars`, `examples/workspace-study` |
| E3 packaging and fresh install | COMPLETE | packaging advisory + `wheel-smoke` job |
| E4 platform behaviour stated | COMPLETE | README Platform behaviour |
| E5 machine-readable output documented | COMPLETE | README Machine-readable output |
| E6 end-to-end acceptance | COMPLETE | `tests/test_e2e_qualification.py` |
| F1 MLflow summary freshness | COMPLETE | schema 0.1/0.2 verification and refresh (RE-10) |
| F2 MLflow metric memory | COMPLETE | 50 MiB / 1M rows / 64 KiB line caps (ML-1) |
| F3 writer concurrency | BOUNDED | POSIX directory flock; Windows keeps the unlocked writer behavior |
| F4 resource and depth limits | COMPLETE | bounded readers and killing tests |

## Verification evidence

- **Full suite**: 6,285 passed, 138 skipped on Python 3.12; 6,282 passed, 141 skipped on
  Python 3.10 with pydantic 2.6.4.
- **Monorepo CI**: Ubuntu and Windows × Python 3.10 and 3.14, plus minimum dependencies on
  3.10; the packaging advisory and the install-and-run wheel smoke run on every pull request.
- **Wheel smoke**: all seven wheels build, install into a clean venv, and run
  `runevidence init/validate` and `rungraph discover/init/validate`.
- **Schema 0.11 (P3)**: structured TF2 `[k,3]` histograms, covered by
  `tests/test_schema_0_11.py`, `test_schema_0_11_migration.py`, `test_cli_tf2_histograms.py`
  and `test_tensorboard_tf2_histograms.py`.
- **Targeted proof**: RE-10/ML-1 have 12 mutation probes that each kill a named test, plus an
  end-to-end MLflow lifecycle; the workspace layer has document, discovery, graph, claim,
  report, impact, compare, authoring and baseline tests, including explicit tests for impact
  through a run input, multiple producers, whitespace-only authoring text and ambiguous
  member endpoints; the examples are kept clean by `tests/test_examples.py`.
- **Realistic experiments**: `examples/dogfood-sklearn` (a real scikit-learn training run with
  two models) and `examples/dogfood-mlp-sweep` (a real preprocessing stage plus a three-job
  learning-rate sweep) are committed with their reports; `examples/tensorboard-scalars` holds
  real event bytes; `examples/workspace-study` composes two members.

## False-clean probe

Every capability class was exercised through its documented success path with a condition that
should have failed. The probe worked on copies of the examples and fresh temporary folders and
checked both the exit code and the visible output:

| Class | Condition that should fail | Observed |
| --- | --- | --- |
| `init` (generic, Lightning, Keras, Sacred, Hydra, MLflow, TensorBoard) | empty folder | exit 2, no manifest written |
| `validate` / `inspect` | changed value in a tracked file | hash mismatch, value mismatch and the affected claims at risk; exit 1 |
| `validate-batch` | one changed manifest | that manifest fails, aggregate exit 1 |
| `rehash` / `add` | unknown target, duplicate ID, already-tracked path | exit 2, nothing written |
| `migrate` | downgrade or unavailable target | exit 2, nothing written |
| `conditions` | malformed patch | exit 2, manifest byte-identical |
| `capture` | folder outside a git repository | exit 2, nothing written |
| `result` / `evidence` / `relationship` authoring | dangling reference, duplicate ID, malformed or invalid-UTF-8 file, missing or non-string `id` | exit 2, nothing written, no traceback |
| `run` / `claim` / `lineage` / `impact` | unknown ID | exit 2 |
| `coverage` / `coverage-gate` | selected structural flag | gate exit 1; coverage labels the claim |
| `fingerprint-compare` / `review-diff` / `impact-diff` / `review-gate` | changed file bytes | not their domain: they state that recorded assertions only were compared and point at `validate` |
| `comparability` | no recorded conditions | exit 2 (unavailable); preflight with the same pair exits 1 (not ready) |
| `assess` | changed artifact or value | target evidence errors, `NOT PERMITTED`, exit 1 |
| `discover` / `init` (workspace) | empty root; existing document without `--force` | exit 2 |
| `validate` / `report` (workspace) | member evidence error | exit 1; unresolved references and cycles are named claim states, and `graph --fail-on-unresolved` exits 1 |
| workspace authoring | path escape, duplicate member path, stale SHA, malformed or non-object file, wrong patch envelope, operation without its payload, missing claim `id` | exit 2, no traceback, document byte-identical |
| workspace strictness | uncited claim warning with `--strict` | exit 1 and `"exit_code": 1` in JSON, for `validate` and `report` |
| workspace graph | unknown member or unknown workspace-claim reference | named unresolved node; `graph --fail-on-unresolved` exits 1 |
| workspace `impact` | unknown node | exit 2 |
| `inspect` | untracked file in the folder | reported as an info line, exit 0 (the documented meaning of `inspect`) |

The probe found and fixed these defects, each with a regression test:

- `result`/`evidence`/`relationship --file` and workspace `claim`/`relationship --file`
  raised a traceback on malformed JSON, invalid UTF-8 or a missing `id`; `patch` accepted a
  non-object file and discarded the supplied format/version. All now refuse with exit 2,
  write nothing and print no traceback, and `patch` validates its `rungraph-patch` 0.1
  envelope (`tests/test_authoring.py`, `tests/test_rungraph_authoring.py`).
- A patch operation missing its payload raised `KeyError`; it now refuses with a named
  message (`tests/test_rungraph_authoring.py`).
- A result ID that was not a string raised `TypeError`; required IDs and reference names are
  now checked as non-empty strings (`tests/test_authoring.py`).
- Workspace and member claims with an unresolved support reference were not `at-risk`, and a
  member claim did not follow relationship and dependency-closure errors, including the
  inputs of a cited result's run. Claim states and `report`/`impact` now share one
  dependency-direction closure, with `input_to` traversed from the run to its artifacts
  (`tests/test_workspace_graph.py`).
- An unknown member, unknown workspace-claim or ambiguous duplicate-ID member reference
  disappeared from `graph`'s unresolved list, raised `KeyError`, or was silently dropped;
  all are now named unresolved nodes, so `graph --fail-on-unresolved` exits 1, and new
  workspace claims/relationships into a member with duplicate IDs are refused
  (`tests/test_workspace_graph.py`, `tests/test_rungraph_authoring.py`).
- `report --strict --json` ignored strictness, and `validate --strict --json` printed
  `"exit_code": 0` before returning 1. Both now apply strictness before printing
  (`tests/test_rungraph_cli.py`).
- `result remove` and `evidence remove` wrote a document whose remaining claims still cited
  the removed object; they now refuse and point at the citing references
  (`tests/test_authoring.py`).
- The manifest authoring routes did not take the folder admission lock; they now participate
  in the same POSIX lock as the other writers (`tests/test_cli_writer_admission.py`).
- The README command reference did not name the workspace routes, and the workspace guide
  claimed `--json` for `graph`/`impact`; both are corrected and the docs-contract test now
  covers the workspace parser (`tests/test_docs_contract.py`).

No command reported a false success: every failing condition either changed the exit code or
was named in the output as a state or warning. Where a route deliberately compares recorded
assertions rather than bytes, its output says so and directs the user to `validate`.

## End-to-end scenarios

The qualification harness records two experiments from scratch with `init`, authors a
result and a claim in each, composes them with `rungraph init` plus a cross-member claim, and
runs the lifecycle through the documented commands:

1. Record and validate both experiments (`--strict`).
2. Compose the workspace; validate it and compare one metric across members.
3. Change a recorded metric value; member and workspace validation fail, `report` labels the
   affected claims `at-risk` (never `supported`), and `impact` names the cross-member claim.
4. Accept the intended change with `rehash`; member and workspace validation are clean and
   the comparison shows the new value.
5. Negative paths: a malformed patch operation is refused with exit 2, no traceback and an
   unchanged document; removing a cited result is refused.

Negative paths are covered throughout the suite: missing/changed bytes, dangling and
ambiguous references, duplicate IDs, path escapes, unresolved cross-member references, claim
cycles, over-cap readers, malformed hooks, stale documents, refused writes and empty scans.

## Completion criteria

| Criterion (§4) | Evidence |
| --- | --- |
| 1. Scope defined and defensible | contract §1–§3; the delivered surface is the one the Phase A audit identified |
| 2. Every P0/P1 `COMPLETE` or bounded | capability table above; F3 and B6 are `BOUNDED`, with their named constraints in Limitations |
| 3. Capabilities work together | `tests/test_e2e_qualification.py`; README command reference; committed examples |
| 4. Guarantees fail when broken | 12 mutation probes for RE-10/ML-1; negative-path suites; the false-clean probe above |
| 5. Known correctness problems resolved or bounded | RE-10 and ML-1 fixed; F3 and directory artifacts bounded as stated |
| 6. Realistic multi-run, multi-tool experiment with corruption and propagation | `examples/dogfood-sklearn` (two real models), `examples/dogfood-mlp-sweep` (three-job Hydra sweep with two claims), `examples/workspace-study` (composition); the end-to-end harness corrupts a value and follows the claim-at-risk propagation |
| 7. Clean install, packaging and platform matrix | `wheel-smoke` (clean venv, seven wheels, both console scripts); six required CI jobs on Ubuntu and Windows, Python 3.10 and 3.14, minimum dependencies on 3.10 |
| 8. Evidence recorded and reproducible | this page, the exact commands below, and the full suite counts |
| 9. Limitations distinguished | the Limitations section, the README platform section, and the experimental comparability verdict |
| 10. No blocker contradicts the statuses | no unresolved blocker is recorded; the false-clean probe is closed |

## Limitations

- **F3, writer concurrency**: the built-in writers take a cooperative POSIX directory lock
  where the host provides it and exit `BUSY`/`LOCK_UNAVAILABLE` otherwise. Windows keeps the
  unlocked writer behavior with byte-level stale checks only. Editors and older versions are
  never locked out.
- **Comparability verdict**: the recorded-condition comparison remains experimental. It
  establishes only that five recorded dimensions matched; it is not artifact integrity,
  reproducibility, statistical equivalence or scientific truth.
- **Directory artifacts**: read-only checking is Linux-only and authoring is Linux/ext4 only;
  other filesystems fail closed, and Windows is not claimed.
- **Keras adapter**: requires POSIX directory-descriptor support for protected reads; hosts
  without it refuse `--adapter keras` rather than fall back to an unlocked read. The generic
  adapter remains available.
- **Adapters**: the MLflow adapter reads a local file store only; no tracking server, remote
  URI or network access.
- **Accepted changes**: `rehash` records the new bytes and value; it does not re-adjudicate
  whether a claim's wording is still true. `supported` remains an evidence state, not a truth
  judgement.
- **Release**: no tag, version bump or package-index publication. Installation is from the
  repository or a locally built wheel.

## Reproducing the baseline

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" -e ./packages/runevidence-tensorboard \
  -e ./packages/runevidence-mlflow -e ./packages/runevidence-hydra \
  -e ./packages/runevidence-sacred -e ./packages/runevidence-keras \
  -e ./packages/runevidence-lightning
python -m pytest
runevidence validate examples/tensorboard-scalars --strict
rungraph validate examples/workspace-study --strict
```

The full command reference is in the README; the workspace guide is
[docs/rungraph-workspace.md](rungraph-workspace.md).
