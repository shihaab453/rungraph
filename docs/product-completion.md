# RunGraph product completion contract

Status: **living document, first issue 2026-11-16.** This is the authoritative record of what
"complete" means for the primary RunGraph product, what is implemented today, and what
evidence is required before a capability is called done. It records engineering facts only.

The repository ships **RunEvidence** as the evidence-verification layer and grows it into
**RunGraph**, the offline-first ML experiment evidence, provenance and analysis product.
RunEvidence being feature-rich does not make RunGraph complete: the product is complete only
when its capabilities work together through coherent interfaces, not when each part exists in
isolation.

---

## 1. Product definition and boundary

RunGraph answers questions about a body of recorded ML experiments, offline, using files as
the only authority:

1. What did each run record, and do those files still match it?
2. Where did each recorded value, artifact and result come from?
3. Which claims does the recorded evidence structure support or contradict, and which are
   unsupported?
4. What changed, what is missing, and which conclusions are affected?
5. May these runs be compared at all under their recorded conditions?

**RunEvidence V1 (the existing layer)** covers questions 1 and, within one manifest, 2–4. It
records one `runevidence.json` per experiment folder and verifies it. It also ships
**pairwise** cross-manifest checks: batch validation, semantic fingerprint comparison, the
coverage/impact/review diff gates and condition comparability all take manifests from more
than one experiment (comparability takes exactly one named pair).

**RunGraph (the product)** additionally composes a whole body of experiments: discovery of
many manifests as one workspace, stable member identities, a cross-manifest provenance graph,
cross-manifest claims and evidence, impact that crosses manifest boundaries, cross-manifest
comparison, and one consolidated report. It remains offline, file-based and CLI-first. No
server, hosted service or graphical application is required.

Pairwise checks are not composition. A diff between two named manifests is not a workspace,
and it does not give a project-level answer.

The boundary is deliberate. A single manifest's checks will not be described as a
project-level guarantee, and a project-level report will not imply artifact integrity it did
not verify. Recorded assertion, verified artifact integrity, verified source value,
provenance, recorded-condition comparability and scientific validity stay distinct in every
surface.

---

## 2. Capability matrix

Status vocabulary: `NOT_STARTED`, `IN_PROGRESS`, `IMPLEMENTED` (code and focused tests
exist), `VERIFIED` (focused plus full-suite evidence, with the proving tests named),
`BLOCKED` (named external constraint), `COMPLETE` (verified and demonstrated end to end,
with the qualification record). Two qualifiers are defined as follows:

- `PARTIAL`: the capability works on a defined subset and the excluded subset is named.
- `BOUNDED`: a limitation is deliberately accepted, is visible in the command output and
  the docs, and does not change a claimed guarantee.

"Evidence" names the artefact or test that justifies the status, not a promise.

All statuses below were reviewed together in the final qualification pass recorded in
[docs/qualification.md](qualification.md). Capabilities that had reached `VERIFIED` on the
full suite were promoted to `COMPLETE` once the workflow-level evidence existed: the
committed examples and the scripted lifecycles in `tests/test_e2e_qualification.py`.

### 2.1 Recording and authoring

| ID | Capability | Status | Acceptance criterion | Evidence |
| --- | --- | --- | --- | --- |
| A1 | Versioned manifest, frozen schemas, migration | COMPLETE | every frozen schema has a model, committed schema, freeze pin and docs page; migration is explicit and never infers | schemas 0.1–0.10 are frozen and pinned; `test_schema_freeze.py`, `test_cli_migrate.py` |
| A2 | Generic folder ingestion | COMPLETE | a plain folder becomes a valid manifest with hashed artifacts and detected runs; a scan that finds nothing refuses with exit 2 instead of writing an empty manifest | generic adapter; dogfood examples; `tests/test_adapters.py::test_init_generic_refuses_an_empty_folder_and_writes_nothing` |
| A3 | Tool adapters (Hydra, MLflow file store, TensorBoard) | COMPLETE | each adapter discovers its real layout and records hashes; a scan that finds no files aborts with exit 2 and writes nothing; unreadable or unsafe *contents* inside an accepted layout are recorded as hashed artifacts with the extracted value omitted and a note, never silently dropped. A TensorBoard folder with files but no event files records them as artifacts and reports zero runs by design | adapter packages; `test_adapter_unreadable_integration.py`, `test_hydra_adapter.py`, `test_tensorboard_adapter.py`; `examples/hydra-sweep`, `examples/mlflow-file-store`, `examples/tensorboard-scalars` |
| A4 | Safe authoring of results, claims, evidence and relationships | COMPLETE | a user can add, edit and remove results, claims, evidence references and relationships from the CLI with validation, dry-run, stale-write protection and no hand-edited JSON | `runevidence result/evidence/relationship`; `tests/test_authoring.py` |
| A5 | Run-condition authoring | COMPLETE | strict patch, presence rules, dry-run, refusal writes nothing; works on every schema generation that carries the condition fields | `conditions` accepts schemas 0.2–0.10; `tests/test_cli_conditions.py` |
| A6 | Code and environment provenance capture | COMPLETE | one command records the source revision (commit id) and an environment snapshot into the existing condition fields, offline. `source_revision` keeps its meaning; dirty state lives inside the environment snapshot. No environment variables or credentials are captured | `runevidence capture`; `tests/test_cli_capture.py` |
| A7 | Safe mutation of an existing manifest | COMPLETE | byte-level stale-write detection on `add`, `rehash`, `migrate` and `conditions`, with atomic replacement; backup generations on `init --force`, `migrate` and `conditions`; cooperative admission lock where the host supports it; declared Windows limit. `init --force` regenerates with a backup but is not a compare-and-swap, and plain `add`/`rehash` do **not** keep a previous generation | `cli.py` `_finish_edit`, `_author_manifest`, `save_raw`, `_backup_existing`; `manifest.py` `save_manifest`; `test_edit.py`, `test_cli_writer_admission.py` |

### 2.2 Evidence integrity

| ID | Capability | Status | Acceptance criterion | Evidence |
| --- | --- | --- | --- | --- |
| B1 | File integrity and relocation | COMPLETE | missing, changed, moved, absolute, escaping and untracked paths are reported with stable codes and exit semantics | `tests/test_acceptance.py`, `tests/test_artifact_path_errors.py` |
| B2 | Source-value verification | COMPLETE | supported locator kinds re-read the source under strict profiles with fixed refusals (schema 0.3 and later); the frozen 0.1/0.2 JSON-pointer path keeps its legacy `json.loads` behaviour, including its documented duplicate-key tolerance | `checks/results.py`, `locator_profiles.py`, `test_csv_checker.py`, schema locator tests |
| B3 | Claim and evidence graph checks | COMPLETE | duplicate IDs, dangling edges, cycles, contradictions and claim-at-risk are reported | `checks/graph.py`, `tests/test_acceptance.py`, claim route tests |
| B4 | Batch validation | COMPLETE | several manifests validate in order with per-manifest and aggregate counts | `validate-batch`; `tests/test_cli_batch_validation.py` |
| B5 | Unreadable input handling | COMPLETE | unreadable adapter input is recorded, hashes retained, and never silently drops evidence | `tests/test_adapter_unreadable_integration.py` |
| B6 | Directory artifacts | BOUNDED | a directory is recorded and re-verified as one artifact with a declared platform scope and guard rails; unsupported filesystems fail closed | read-only checking on Linux; guarded authoring on Linux/ext4; `--directory-artifacts` exists on `validate`, `validate-batch` and `inspect`; the declared platform bound is listed in `docs/qualification.md` |

### 2.3 Analysis over one or two manifests

| ID | Capability | Status | Acceptance criterion | Evidence |
| --- | --- | --- | --- | --- |
| C1 | Run and claim detail | COMPLETE | one run or claim can be inspected as text and machine-readable output | `run`, `claim`; route tests |
| C2 | Provenance lineage | COMPLETE | a claim's recorded upstream dependency graph is returned with unresolved nodes and cycles named | `lineage`; route tests |
| C3 | Impact analysis | COMPLETE | the claims potentially affected by a recorded object are returned with the dependency path | `impact`; route tests |
| C4 | Structural coverage review | COMPLETE | unsupported, contradicted and uncited claims are flagged with explainable reasons | `coverage`, `coverage-gate`; route tests |
| C5 | Cross-run comparison, matrix and leaderboard | COMPLETE | recorded values align by name with explicit non-numeric/missing accounting and no fairness claim; single manifest only | `compare`, `matrix`, `leaderboard`; route tests |
| C6 | Recorded-condition comparability | COMPLETE | five mandatory recorded conditions are compared for a named pair, with a stable preflight and a clearly experimental verdict, across 0.1–0.10 (0.1 through the legacy metadata projection, 0.2–0.10 through the first-class fields) | `comparability` and preflight accept 0.1–0.10; the verdict remains experimental; `tests/test_run_comparability.py`, `tests/test_cli_comparability_preflight.py` |
| C7 | Evidence-aware comparison | COMPLETE | a comparison states whether recorded conditions permit it *and* whether the compared results are backed by intact, verified evidence; missing or changed artifacts, failed source-value checks and unverifiable values change the outcome instead of being ignored | `runevidence assess`; `tests/test_cli_assess.py` |
| C8 | Change analysis between recorded snapshots | COMPLETE | two recorded manifests can be compared for semantic equality (`fingerprint`, `fingerprint-compare`) and for review-relevant differences (`coverage-diff`, `impact-diff`, `review-diff`, `review-gate`), with exit codes and machine-readable output; documented in the README command reference | `run_fingerprint*.py`, `run_*_diff.py`, `run_review_gate.py`; route tests |

### 2.4 Cross-experiment composition (the RunGraph layer)

| ID | Capability | Status | Acceptance criterion | Evidence |
| --- | --- | --- | --- | --- |
| D0 | Workspace document | COMPLETE | a versioned `rungraph.json` document names the members, assigns each a stable ID and path, and is created, validated and edited by documented commands with the same safety properties as manifest authoring. Discovery works without it and reports provisional IDs, but cross-manifest references require it | `rungraph init` plus `member`/`claim`/`relationship`/`patch` authoring; `tests/test_rungraph_authoring.py` |
| D1 | Workspace discovery over several manifests | COMPLETE | one command finds every manifest under a root and reports each member's stable ID (from the workspace document, otherwise a deterministic provisional ID from its relative path), duplicates, nested/unreadable members and caps; it writes nothing, and `--check --directory-artifacts` runs the same check set as `validate` | `rungraph discover`; `tests/test_workspace.py`, `tests/test_rungraph_cli.py` |
| D2 | Cross-manifest provenance graph | COMPLETE | a cross-manifest identity is a typed `(member-id, local-id)` reference; recorded relationships, member evidence and workspace claims resolve across members, unresolved references are named rather than dropped, and cycles are detected with a bounded example list | `rungraph graph`; `tests/test_workspace_graph.py` |
| D3 | Cross-manifest claims and evidence | COMPLETE | a claim can cite a qualified `(member-id, local-id)` evidence reference in another member, and workspace-level claims can be authored, edited and removed; validation reports present, unresolved and at-risk references across members without flagging a valid cross-member reference as dangling | citation, resolution, claim states and claim/relationship authoring; `tests/test_workspace_graph.py`, `tests/test_rungraph_authoring.py` |
| D4 | Cross-manifest impact | COMPLETE | a change in one member lists the affected claims in every member with the path that carries the impact | `rungraph impact`; `tests/test_rungraph_cli.py` |
| D5 | Consolidated report | COMPLETE | one command over a workspace reports, with an explicit baseline named for each section: (a) current recorded-versus-filesystem findings; (b) optional assertion changes against a named prior snapshot; (c) per-claim evidence state; (d) cross-member impact | `rungraph report` with current evidence, claim states, impact and `--baseline`; `tests/test_rungraph_authoring.py` |
| D6 | Cross-manifest comparison and ranking | COMPLETE | `compare`, `matrix` and `leaderboard` compare recorded values across members with explicit missing/non-numeric/ambiguous accounting and ties; `compare` states each pair's recorded-condition status through the experimental comparability surface | `rungraph compare/matrix/leaderboard`; `tests/test_workspace_compare.py` |

### 2.5 Delivery, usability and qualification

| ID | Capability | Status | Acceptance criterion | Evidence |
| --- | --- | --- | --- | --- |
| E1 | Discoverability for every public route | COMPLETE | every route appears in the README reference with purpose, inputs, outputs, exit codes and experimental status; a docs-contract test enforces it | every route is named in the README reference; `test_docs_contract.py::test_readme_names_every_public_cli_route`, `::test_readme_names_every_workspace_route` |
| E2 | Examples covering every adapter and the composition layer | COMPLETE | each adapter and each product workflow has a runnable example, and a test keeps it clean | `examples/tensorboard-scalars`, `examples/workspace-study`; `tests/test_examples.py` |
| E3 | Packaging | COMPLETE | distributions build, contents and metadata are checked, and entry points resolve; a fresh-install run of the CLI and adapters is part of E6 | packaging advisory plus `wheel-smoke` (clean venv, installed wheels, both console scripts) |
| E4 | Declared platform behaviour | COMPLETE | every platform-specific guarantee and its exclusion is stated; Windows CI stays green | README Platform behaviour; Windows/Ubuntu CI |
| E5 | Machine-readable output | COMPLETE | every analysis route that offers JSON documents the envelope and keeps it stable; the sole text-only analysis mode, `comparability --preflight`, stays text-only by its frozen contract and must not gain JSON; write and diagnostic commands state whether they have a machine-readable form | README Machine-readable output; every route that emits JSON documents its envelope (`graph` through `--format`, `impact` always JSON); preflight stays text-only |
| E6 | End-to-end acceptance harness | COMPLETE | a scripted, reproducible scenario exercises record → verify → analyse → compare → change → propagate → accept → revalidate → fresh install, with recorded commands, exit codes and expected versus observed results, plus negative paths | `tests/test_e2e_qualification.py` (single experiment and workspace lifecycle) plus the per-slice clean-clone wheel smokes |
| F1 | MLflow summary freshness (RE-10) | COMPLETE | accepting changed MLflow bytes must not leave a recorded summary silently stale: on schema 0.1/0.2, `validate` verifies the summary against the file and `rehash` refreshes it, including repairing a stale summary with an unchanged digest; absent/ambiguous adapters, volatile files and unreadable histories warn instead of passing | `tests/test_mlflow_summary_verification.py`, `tests/test_mlflow_end_to_end.py`, full suite |
| F2 | MLflow metric memory (ML-1) | COMPLETE | metric histories are read under declared byte, row and line caps, and an oversized history has a defined, visible outcome | `packages/runevidence-mlflow/tests/test_metric_reader.py`, `tests/test_summaries.py` |
| F3 | Writer concurrency | BOUNDED | cooperative serialization where supported, and the exclusion is explicit everywhere else | documented limitation |
| F4 | Resource and depth limits | COMPLETE | every scanner except the MLflow metric reader (F2) has a declared cap, and removing the cap makes a named test fail | `tests/test_tensorboard_tensors.py::test_one_million_elements_is_the_boundary`, `::test_sixteen_mib_summary_of_millions_of_empty_tensor_spans_stays_bounded`, `tests/test_csv_profile.py::test_columns_at_cap_and_one_over`, `::test_rows_at_cap_and_one_over` (the 16 MiB memory test is skipped on non-Linux hosts) |

---

## 3. Priority and sequencing

Priority classes are a scheduling rule, not a permanent exclusion. P3 work may continue
when it does not displace unresolved P0 or P1 work, and may be promoted when a P0/P1
capability depends on it.

### P0 — core correctness and evidence integrity

1. **RE-10** (delivered): make the accepted-change path honest. `validate` verifies a
   recorded MLflow summary against the metric file, and `rehash` refreshes it, through the
   owning adapter. Accepting bytes while keeping a summary that was not re-read is not
   sufficient: refusing acceptance would only force `init --force`, which drops hand-written
   claims. Refusal is correct only when the metric file itself is unreadable or over its cap.
   See F1 for the evidence.
2. **ML-1** (delivered): bound MLflow metric-history reads (declared byte, row and line caps)
   and define the oversized outcome. See F2 for the evidence.
3. **False-clean probe** (delivered): each capability class was exercised through its
   documented success path with a condition that should have failed. The initial survey found
   RE-10 and the empty scan; the final pass over the delivered surface found three more
   defects in authoring file handling and claim-state labelling. All five are fixed and have
   regression tests. The probe classes and their failing conditions are recorded in
   [docs/qualification.md](qualification.md#false-clean-probe).
4. **Empty scans report success (A2, A3)** (delivered): `init` on an empty folder refuses
   with exit 2 and writes nothing, for the generic, Hydra, MLflow and TensorBoard adapters. A
   TensorBoard folder that holds files but no event files keeps its tested behaviour
   (artifacts recorded, zero runs) and reports that fact.

### P1 — missing primary capabilities

5. **Composition core (D0–D6)** (delivered in two slices): a versioned workspace document with stable member IDs,
   workspace discovery, a global provenance graph, cross-manifest claims and evidence,
   cross-manifest impact, cross-manifest `compare`/`matrix`/`leaderboard`, and one
   consolidated report. Read-only first; authoring of the document and of workspace claims
   follows. The workspace document is required from the start because a cross-member
   reference has no valid meaning without a qualified identity; it is a separate versioned
   format, never a change to a frozen manifest schema.
6. **Condition and comparability coverage (A5, C6)** (delivered): condition authoring and the
   comparability preflight/verdict cover every schema generation that carries the condition
   fields (0.2–0.10; 0.1 through the legacy metadata projection). This removed the gap that
   kept the product's own adapters outside its comparison surface. No frozen reading rule or
   existing output changed.
7. **Authoring (A4, D3)** (delivered): CLI authoring for results, claims, evidence references
   and relationships in a manifest, and for workspace-level claims, with the same safety
   properties as artifact authoring.
8. **Code/environment capture (A6)** (delivered): `capture` records the source revision and
   an opt-in environment snapshot from the working tree and interpreter, offline, without
   changing what `source_revision` means and without capturing credentials.
9. **Evidence-aware comparison (C7)** (delivered): `assess` combines comparability with the
   artifact and source-value verification outcome, so a comparison states both whether
   recorded conditions permit it and whether the evidence behind each value is intact; a
   missing or changed artifact, a failed value check or an unverifiable result changes the
   outcome and is named. It never implies scientific validity.
10. **Documentation of the whole surface (E1) and the end-to-end harness (E6)** (delivered):
    these are P1 because `COMPLETE` requires capability evidence through documented
    commands; both are complete, and every route is in the README reference.

### P2 — integration, usability and release readiness

11. **Examples (E2)**: a TensorBoard example and a composition example, kept clean by tests.
12. **Output and interface consistency (E5)**: document and stabilize the JSON envelopes; do
    not add JSON to `comparability --preflight`, which is text-only by its frozen contract.
13. **Platform statements (E4)** and packaging updates for any new entry point (E3). Both are
    completion blockers under criterion 7, with the same status as E1 and E6.

### P3 — additional sophistication (permitted, not blocking)

- TF2 `[k,3]` histograms, plugin-specific summaries, graph/run metadata, additional
  adapters, richer comparison statistics, SQLite persistence (subject to its hold), extra
  platform guarantees. TF2 media schema 0.10 is delivered.

### Documentation and truthfulness

The README roadmap lists the delivered capabilities, which states availability: they exist
and are usable. Availability is not a completion claim; this document records the evidence
that justifies each status. The remaining limitations (writer concurrency without a POSIX
directory lock, the experimental comparability verdict, directory artifacts on non-Linux
filesystems, the local-only MLflow adapter) are stated in the README and in
[docs/qualification.md](qualification.md), and every public route is in the README command
reference. Documentation must not describe a capability as complete while its acceptance
criteria here are unmet.

---

## 4. Completion criteria

The primary product is complete only when all of the following hold, each with recorded
evidence:

1. The intended core scope is defined here and is defensible from the repository's own design
   history.
2. Every capability marked P0/P1 above is `COMPLETE`, or is `BOUNDED`/`BLOCKED` with a named
   constraint, a visible bound and an entry in §2 that records it. A P0 item may not be
   closed as `BOUNDED` unless the bound is visible in the command output and the docs, and
   RE-10 and ML-1 must reach `COMPLETE`.
3. Those capabilities function together in the workflows in §5 through documented commands.
4. The evidence-integrity and provenance guarantees each have tests that fail when the
   guarantee is broken.
5. Known correctness problems affecting an essential workflow are resolved or bounded
   consistently with criterion 2.
6. A realistic multi-run, multi-tool experiment demonstrates the whole workflow, including
   deliberate evidence corruption and its propagation.
7. Installation from a clean clone, packaging and the supported platform matrix are verified.
8. The completion evidence is recorded, reproducible, and available for independent
   assessment; every unresolved objection is recorded.
9. Outstanding limitations are listed and distinguished from completed functionality.
10. No unresolved blocker contradicts this document's status claims.

Passing tests alone do not satisfy this contract, and neither does a larger test count.

The recorded evidence for criteria 1–10 is in
[docs/qualification.md](qualification.md): the capability table, the verification commands and
their results, the end-to-end scenarios, and the outstanding limitations.

---

## 5. Intended user workflows

These are the workflows acceptance tests must exercise together.

- **W1 Record.** Point RunGraph at a folder or a tool's output; get a valid manifest; author
  the numbers, claims and links that matter; accept intended changes safely.
- **W2 Verify.** Ask whether the evidence still holds: files, values, graph, claims.
- **W3 Explain.** Ask why a claim is supported, contradicted, at risk or unsupported, and
  which recorded objects it depends on.
- **W4 Compare.** Ask which runs may be compared under recorded conditions and see their
  recorded values side by side, with the limits of that comparison stated.
- **W5 Compose.** Ask project-level questions across several experiments: what exists, how it
  is connected, and where the same evidence or claim is shared.
- **W6 Attribute impact.** Change one piece of evidence and ask which claims, in which
  experiments, are now affected, with the dependency path.
- **W7 Report.** Produce one consolidated, reproducible report stating, for each claim, whether
  its cited evidence resolves and whether any error-level finding touches it, alongside what
  changed against a named baseline and what needs review, in text and machine-readable form.
  This is an evidence state, not a statement that the claim is scientifically true.

---

## 6. Status discipline

- A capability moves to `IMPLEMENTED` only when code and focused tests exist.
- It moves to `VERIFIED` only after the full suite passes on the supported Python versions.
- It moves to `COMPLETE` only after the workflow-level evidence in §4 exists.
- Regressions move a status back. A status is never inferred from code existing alone.
- Every status change records the command or test that justifies it.
- This document is updated with each milestone.

---

## 7. Explicit non-goals

No experiment tracker, dashboard, web UI or server. No training, job launching, GPU use or
network access. No model-weight loading or execution. No claim that structural consistency
establishes scientific truth. Publication, tagging and version bumps are outside this
contract. No change to a frozen, pinned schema's meaning.
