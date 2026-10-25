# RunEvidence weekend MVP (2026-10-25)

## What shipped

RunEvidence 0.1.0, the first layer of RunGraph: an offline, read-only checker
that answers "does the evidence in this experiment folder still hold?"

- **Package:** `runevidence` (src layout, Python >= 3.10, Apache-2.0). Only
  runtime dependency: `pydantic` v2.
- **Schema:** domain-agnostic Pydantic models (`Project`, `Run`, `Artifact`
  with sha256, `Result` with optional JSON Pointer locator,
  `EvidenceReference` with supports/contradicts, typed `Relationship`),
  exported to `schema/runevidence.schema.json`. A test keeps the two in sync.
- **CLI:** `validate`, `inspect`, `init`, `schema`, `adapters`, with `--json`
  and `--strict`. Exit codes: `0` ok, `1` evidence problems, `2` could not
  check.
- **File checks:** missing file, not a file, sha256 and size mismatch, missing
  digest, absolute paths (with relocation hint), backslash paths, paths
  escaping the folder, case-only matches, untracked files.
- **Evidence checks:** duplicate IDs, dangling references, wrong edge
  endpoint kinds, conflicting provenance, contradictory evidence, conflicting
  metric values, derivation cycles, uncited claims, claims resting on
  missing/changed files or run inputs, recorded value vs value in the JSON
  metrics file.
- **Adapters:** `Adapter` ABC (`discover` / `extract`), entry-point registry
  (`runevidence.adapters`), built-in `generic` adapter. Contributor guide in
  `docs/adapters.md`.
- **Fixture:** `examples/generic-experiment/`, a fake two-run text
  classifier. Byte-exact via `.gitattributes`.
- **Docs:** README with a 10-minute walkthrough, CONTRIBUTING.md.

## Acceptance

| Owner criterion | Evidence |
| --- | --- |
| `pytest` green offline | `python -m pytest`: 71 passed in about 1.2 s on Windows 11 / Python 3.14.6, no network. Clean install into a fresh venv also verified. |
| Missing file + hash change reported clearly | `tests/test_acceptance.py::test_missing_file_and_hash_change_are_both_reported`; README step 3 output reproduced verbatim on Windows |
| README walkthrough with install + one inspect command | `README.md`, sections 1 to 5 |
| No Celeste/Steam/rooms/PPO/pilot strings in core | `tests/test_acceptance.py::test_core_has_no_domain_specific_vocabulary` scans `src/`, `schema/`, `examples/` |

## Decisions made during the build (reversible)

- argparse instead of Typer (one dependency fewer, exit codes fully ours).
- JSON-only manifests; YAML later.
- One shared ID namespace; unknown fields rejected (`extra="forbid"`), custom
  data in `metadata` / `params`.
- Claims depend on: the cited result, the file it was read from, and the
  inputs of the run that produced it.

## Deferred to later RunEvidence releases

- Directory artifacts and glob patterns.
- `rehash` command to re-record intended changes.
- YAML manifests.
- Tool adapters (MLflow file store, TensorBoard, CSV logs). An optional
  Celeste adapter may come later as a separate package, never in core.
- Dogfooding on a real, non-Celeste ML project.

## Deferred to RunGraph

- Lineage across runs and experiments (graph queries, not just checks).
- Comparability checks: same dataset digest, same eval split, same code
  version, same seeds before two results may be compared in a claim.
- Code provenance (git commit, dirty tree) and environment capture.
- Cross-folder / multi-project manifests.
