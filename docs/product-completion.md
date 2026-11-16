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
3. Which claims does the evidence support or contradict, and which are unsupported?
4. What changed, what is missing, and which conclusions are affected?
5. May these runs be compared at all under their recorded conditions?

**RunEvidence V1 (the existing layer)** covers questions 1 and, within one manifest, 2–4. It
records one `runevidence.json` per experiment folder and verifies it.

**RunGraph (the product)** additionally composes experiments: several manifests in one
project or study, cross-manifest provenance, cross-manifest claims, impact that crosses
manifest boundaries, and one consolidated report. It remains offline, file-based and
CLI-first. No server, hosted service or graphical application is required.

The boundary is deliberate. A single manifest's checks will not be described as a
project-level guarantee, and a project-level report will not imply artifact integrity it did
not verify. Recorded assertion, verified artifact integrity, verified source value,
provenance, recorded-condition comparability and scientific validity stay distinct in every
surface.

---

## 2. Capability matrix

Status vocabulary: `NOT_STARTED`, `IN_PROGRESS`, `IMPLEMENTED` (code and focused tests
exist), `VERIFIED` (focused plus full-suite evidence), `BLOCKED` (named external constraint),
`COMPLETE` (verified and demonstrated end to end). "Evidence" names the artefact that
justifies the status, not a promise.

### 2.1 Recording and authoring

| ID | Capability | Status | Acceptance criterion | Evidence |
| --- | --- | --- | --- | --- |
| A1 | Versioned manifest, frozen schemas, migration | VERIFIED | every released schema has model, committed schema, freeze pin and docs page; migration is explicit and never infers | 0.1–0.9 released; schema 0.10 implemented on a branch |
| A2 | Generic folder ingestion | VERIFIED | a plain folder becomes a valid manifest with hashed artifacts and detected runs | generic adapter; dogfood examples |
| A3 | Tool adapters (Hydra, MLflow file store, TensorBoard) | VERIFIED | each adapter discovers its real layout, records hashes and supported values, and refuses unsupported input with exit 2 without writing | adapter packages and tests |
| A4 | Safe authoring of results, claims, evidence and relationships | NOT_STARTED | a user can add, edit and remove these objects from the CLI with validation, dry-run, stale-write protection and no hand-edited JSON | only artifacts are authorable today |
| A5 | Run-condition authoring | VERIFIED | strict patch, presence rules, dry-run, refusal writes nothing | `conditions` |
| A6 | Code and environment provenance capture | NOT_STARTED | one command records source revision, dirty state and an environment snapshot into the existing condition fields, offline | fields exist; capture does not |
| A7 | Safe mutation of an existing manifest | VERIFIED | backups, byte-level stale-write checks, cooperative admission lock where the host supports it; declared Windows limit | `init --force`, `add`, `rehash`, `migrate`, `conditions` |

### 2.2 Evidence integrity

| ID | Capability | Status | Acceptance criterion | Evidence |
| --- | --- | --- | --- | --- |
| B1 | File integrity and relocation | VERIFIED | missing, changed, moved, absolute, escaping and untracked paths are reported with stable codes and exit semantics | core checks |
| B2 | Source-value verification | VERIFIED | supported locator kinds re-read the source under strict profiles and fixed refusals | JSON/JSONL/CSV/TensorBoard |
| B3 | Claim and evidence graph checks | VERIFIED | duplicate IDs, dangling edges, cycles, contradictions and claim-at-risk are reported | graph checks |
| B4 | Batch validation | VERIFIED | several manifests validate in order with per-manifest and aggregate counts | `validate-batch` |
| B5 | Unreadable input handling | VERIFIED | unreadable adapter input is recorded, hashes retained, and never silently drops evidence | adapter tests |

### 2.3 Single-manifest analysis

| ID | Capability | Status | Acceptance criterion | Evidence |
| --- | --- | --- | --- | --- |
| C1 | Run and claim detail | IMPLEMENTED | one run or claim can be inspected as text and machine-readable output | `run`, `claim` |
| C2 | Provenance lineage | IMPLEMENTED | a claim's recorded upstream dependency graph is returned with unresolved nodes and cycles named | `lineage` |
| C3 | Impact analysis | IMPLEMENTED | the claims potentially affected by a recorded object are returned with the dependency path | `impact` |
| C4 | Structural coverage review | IMPLEMENTED | unsupported, contradicted and uncited claims are flagged with explainable reasons | `coverage`, `coverage-gate` |
| C5 | Cross-run comparison, matrix and leaderboard | IMPLEMENTED | recorded values align by name with explicit non-numeric/missing accounting and no fairness claim | `compare`, `matrix`, `leaderboard` |
| C6 | Recorded-condition comparability | IN_PROGRESS | five mandatory recorded conditions are compared, with a stable preflight and a clearly experimental verdict | `comparability` |
| C7 | Evidence-aware comparison | NOT_STARTED | a comparison states whether recorded conditions permit it and refuses to present an unjustified ranking as meaningful | not started |

### 2.4 Cross-experiment composition (the RunGraph layer)

| ID | Capability | Status | Acceptance criterion | Evidence |
| --- | --- | --- | --- | --- |
| D1 | Workspace discovery over several manifests | NOT_STARTED | one command finds and identifies every manifest under a root, reports duplicates and unreadable members, and writes nothing | not started |
| D2 | Cross-manifest provenance graph | NOT_STARTED | recorded relationships resolve across members; unresolved cross-member references are named, not dropped; cycles are detected across members | not started |
| D3 | Cross-manifest claims and evidence | NOT_STARTED | a claim can cite evidence in another member; validation reports present, unresolved and at-risk references across members | not started |
| D4 | Cross-manifest impact | NOT_STARTED | a change in one member lists the affected claims in every member with the path that carries the impact | not started |
| D5 | Consolidated report | NOT_STARTED | one command answers "which claims still hold, what changed, and what else is affected" for a whole workspace, in text and machine-readable form | not started |

### 2.5 Delivery, usability and qualification

| ID | Capability | Status | Acceptance criterion | Evidence |
| --- | --- | --- | --- | --- |
| E1 | Discoverability for every public route | PARTIAL | every route appears in the README reference with purpose, inputs, outputs, exit codes and experimental status; a docs-contract test enforces it | 15 of 26 routes absent from the reference |
| E2 | Examples covering every adapter and the composition layer | PARTIAL | each adapter and each product workflow has a runnable example, and a test keeps it clean | no TensorBoard or workspace example |
| E3 | Packaging and installation | VERIFIED | wheel and sdist build, contents checked, entry points resolve, install works in a fresh environment | advisory packaging job |
| E4 | Declared platform behaviour | PARTIAL | every platform-specific guarantee and its exclusion is stated; Windows CI stays green | POSIX locks; Linux/ext4 directory artifacts |
| E5 | Machine-readable output | PARTIAL | every analysis route has a documented JSON form with a stable envelope | several routes are text-only |
| E6 | End-to-end acceptance harness | PARTIAL | a scripted, reproducible scenario exercises record → verify → analyse → compare → change → propagate → accept → revalidate → fresh install | dogfood regression tests only |
| F1 | MLflow summary freshness (RE-10) | BOUNDED | accepting changed MLflow bytes must not leave a recorded summary silently stale without an explicit, honest outcome | documented limitation |
| F2 | MLflow metric memory (ML-1) | BOUNDED | metric histories are read under a declared cap and an oversized history has a defined outcome | documented limitation |
| F3 | Writer concurrency | BOUNDED | cooperative serialization where supported, and the exclusion is explicit everywhere else | documented limitation |
| F4 | Resource and depth limits | VERIFIED | every new scanner has a cap and a killing test | caps and fuzz tests |

---

## 3. Priority and sequencing

Priority classes are a scheduling rule, not a permanent exclusion. P3 work may continue
when it does not displace unresolved P0 or P1 work, and may be promoted when a P0/P1
capability depends on it.

### P0 — core correctness and evidence integrity

1. **RE-10**: make the accepted-change path honest. Preferred outcome: refresh the MLflow
   metric summary through the owning adapter during `rehash`, so an accepted metric file and
   its recorded value stay consistent. Fallback: refuse the acceptance until the summary is
   refreshed, with a fixed message. Either way, a `validate --strict` exit 0 may not coexist
   with a knowingly stale summary.
2. **ML-1**: bound MLflow metric-history reads and define the oversized outcome.
3. **False-clean probe**: exercise every exit-0 path for a result that should have failed and
   add a regression test for each defect found.

### P1 — missing primary capabilities

4. **Composition core (D1–D5)**: workspace discovery, a global provenance graph,
   cross-manifest impact and one consolidated report. Read-only first. A workspace document
   is introduced only when a workflow needs stable cross-manifest identities or
   cross-manifest claims; it is a separate versioned format, never a change to a released
   manifest schema.
5. **Authoring (A4)**: CLI authoring for results, claims, evidence references and
   relationships, with the same safety properties as artifact authoring.
6. **Code/environment capture (A6)**: record source revision, dirty state and environment
   from the working tree and interpreter, offline.
7. **Evidence-aware comparison (C7)**: combine comparability with comparison so a ranking
   states its recorded-condition status and never implies scientific validity.

### P2 — integration, usability and release readiness

8. **Documentation (E1, E2)**: a complete command reference, the TensorBoard adapter in the
   public adapter list, a TensorBoard example, a composition example, and a docs-contract
   test that makes missing routes fail CI.
9. **Output and interface consistency (E5)**: a documented JSON envelope and a `--json` flag
   for each analysis route.
10. **Platform statements (E4)** and packaging updates for any new entry point.
11. **End-to-end harness (E6)**: one scripted scenario with recorded commands, exit codes and
    expected versus observed results, plus negative paths.

### P3 — additional sophistication (permitted, not blocking)

- TF2 media schema 0.10 (implemented; pending merge), TF2 `[k,3]` histograms, plugin-specific
  summaries, graph/run metadata, additional adapters, richer comparison statistics, SQLite
  persistence (subject to its hold), extra platform guarantees.

### Documentation and truthfulness

The README roadmap must describe the product as it is today. It currently says lineage is
"later work" while single-manifest lineage, impact, coverage, comparison and fingerprinting
already ship, and it omits the TensorBoard adapter from the adapter list. This is a P2
correctness-of-documentation defect and is tracked as E1.

---

## 4. Completion criteria

The primary product is complete only when all of the following hold, each with recorded
evidence:

1. The intended core scope is defined here and is independently defensible.
2. Every capability marked P0/P1 above is `COMPLETE`, or is `BLOCKED` with a named external
   constraint and a transparently bounded behaviour.
3. Those capabilities function together in the workflows in §5 through documented commands.
4. The evidence-integrity and provenance guarantees each have tests that fail when the
   guarantee is broken.
5. Known correctness problems affecting an essential workflow are resolved or bounded, with
   the bound visible in the command output and the docs.
6. A realistic multi-run, multi-tool experiment demonstrates the whole workflow, including
   deliberate evidence corruption and its propagation.
7. Installation from a clean clone, packaging and the supported platform matrix are verified.
8. Independent review has assessed the completion evidence and every unresolved objection is
   recorded.
9. Outstanding limitations are listed and distinguished from completed functionality.
10. No unresolved blocker contradicts this document's status claims.

Passing tests alone do not satisfy this contract, and neither does a larger test count.

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
- **W7 Report.** Produce one consolidated, reproducible report of what holds, what changed and
  what needs review, in text and machine-readable form.

---

## 6. Status discipline

- A capability moves to `IMPLEMENTED` only when code and focused tests exist.
- It moves to `VERIFIED` only after the full suite passes on the supported Python versions.
- It moves to `COMPLETE` only after the workflow-level evidence in §4 exists.
- Regressions move a status back. A status is never inferred from a merged branch alone.
- Every status change records the command or test that justifies it.
- This document is updated with each milestone; the private operating record is not part of
  the repository.

---

## 7. Explicit non-goals

No experiment tracker, dashboard, web UI or server. No training, job launching, GPU use or
network access. No model-weight loading or execution. No claim that structural consistency
establishes scientific truth. No publication, tag or release without explicit owner
approval. No change to a released schema's meaning.
