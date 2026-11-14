# SQLite authority boundary and cutover RFC

Status: decision recommendation, documentation-only R12. **S3P3P7 PERSISTENCE SAFETY HOLD remains in force.** No public SQLite persistence, migration, writer changes or cutover is authorized by this RFC.

## Decision and evidence boundary

Recommend retaining JSON as the sole production manifest authority. Private SQLite is a disposable synthetic shadow. On divergence, refuse the private staged read; never silently fall back to a hidden second authority or present dual-write as atomic. Discarding a disposable shadow is a prospective operational choice, not permission to delete any current evidence. This decision preserves current authority; it does not certify JSON as atomic CAS or durable, nor resolve lost updates, symlinks, permissions or external writers.

Evidence labels below distinguish historical controlled observations from static source review. R12 ran no tests, CLI routes or writers. Historical results are reported from the pinned R7–R11 records and handoff, not independently replayed here. Synthetic single-process observations are not proof of hostile concurrency, crash recovery, power-loss durability or ACL equivalence. R11's 65/65 focused cases passed twice but remain **functional/pass with execution-process deviation and Git-metadata/persistence qualifications**, not a green authorization gate.

Source identities for this inventory: manifest `ad065582c2dac53785fdd239218f25662d41826209747eafea057caeca92123c` (M); CLI `e45b9d696d1fa63730b223bb6c7d43d5093f9aee42fb84cb498dad4112096fe1` (C); directory apply `76ac35bd917ea01f30f2be2bc9deaae9c3e3211f458491177c65bba58b7fc7d0` (D); cooperative apply `bd72c2a8395c5101e36dff89969dc14c46ba6eeceb80241425c2cf66fc578b4d` (L); stage `4296131d0d2b9412e70f53d302abcb347ad0675559872e036092cb81b201ef91` (S); reader `70f43b6d7df05c214e715b7b055f5dad464fe2a6012c44ffd1802c593cff81dd` (R); facade `59eb63020be479a09f6068bd33dd2496d1848e328841b3571bcd346c858aedd9` (F). These refer to the live pinned files, not a released version. Historical details are in `sqlite-readonly-reader-seam.md`, `sqlite-readonly-validation-facade.md`, `sqlite-authority-conflicts-and-cutover-criteria.md`, `sqlite-legacy-writer-execution-boundaries.md`, and `sqlite-existing-writer-authority-and-rollback-decision.md`.

## Bounded route inventory

All rows describe source review unless an observation is explicitly named. No route supplies a common JSON/SQLite transaction. No automatic cross-authority fallback is permitted; recovery requires investigation and a separately approved procedure, not overwriting one side based on an unverified assumption.

| Route and source | Authority, control and evidence | Fallback / rollback obligation |
| --- | --- | --- |
| `save_manifest` new path (M); CLI `init` (C) | JSON. Existence check then ordinary text open, no exclusive creation, expected bytes or lock. R10 observed new-path execution on synthetic data. CLI route itself is source review only. | On ordinary write exception source attempts unlink; no proven crash rollback. Inspect source and backups before any recovery. |
| `save_manifest` existing path (M); `init` (C) | JSON. Sibling staging, flush/fsync and replace; no expected-byte check or lock. R11 traced synthetic existing-path execution and changed source identity; stage remained byte-identical. | No SQLite fallback. Replace is not a rollback protocol; inspect the surviving JSON and verified backup. |
| `save_raw` (M), CLI `rehash` and `add` (C) | JSON. Stages and fsyncs, compares expected source bytes, then replaces; no lock and comparison is separate from replace. R10 observed synthetic direct calls, including sequential stale refusal and refreshed overwrite. CLI actual manifest writer execution is unproven here. | Refuse stale input and reload; no automatic reconciliation. Interrupted replacement and restoration require future proof. |
| CLI `rehash --dry-run` and `add --dry-run` (C) | **UNAUTHORIZED R11 DRY-RUN EXECUTION** in inherited R7 test, baseline and injected; these are route invocations, distinct from actual manifest writer execution. Dry-run source returns before `save_raw`; guard sentinels and absence of observed writer effects do not authorize the calls. | Do not repeat under R12; preserve deviation. No claim that dry-run establishes rollback or writer safety. |
| CLI schema output (C) | Caller-selected schema output file opened for text write; not a manifest transaction, but may target a chosen path. No lock, expected bytes or stage CAS. Source review only. | No backup/rollback guarantee; do not point it at evidence. |
| Forced-init backup (C) | Exclusive backup creation with write, flush/fsync and readback; collision refuses instead of replacing that backup. Separate from subsequent JSON replacement; no common lock or CAS. Source review only. | Preserve backup and source; backup collision and restoration require investigation and separately tested procedure. |
| Directory apply (D, M) | Rechecks source and rebuilds proposal, then delegates to `save_raw`; expected bytes are not atomic CAS. Source review only for this gate. | Refusal is safer than overwrite; scanner, artifact state and recovery are separate dependencies. |
| Cooperative apply (L, D, M) | Exclusive sibling lock and ownership checks coordinate participating callers, then delegate to directory apply. Existing lock is refused and left alone. Nonparticipants and SQLite do not share lock. Source review only for this gate. | Do not remove a possibly live or unowned lock; investigate ownership and recovery separately. |
| Private stage CAS (S) | Synthetic SQLite stage revision/receipt CAS coordinates participating stage candidates only. R9 controlled candidate conflict and stage/source divergence were observed. It does not update or lock JSON. | Refuse conflicts; stage cannot decide JSON rollback or become production authority. |
| R7 staged reader (R, S, M) | Private receipt-based read checks layout, stage and source identity/bytes before and after; requires revision/generation one. R7 historical synthetic observations only. No lock freezes JSON or artifacts. | Refuses missing, stale, invalid, busy, sidecar or changed state; no JSON fallback. No recovery is implied. |
| R8 validation facade (F, R) | Private scanner-free checks between two staged observations, then compares views. R8/R9 historical synthetic observations only; no atomic snapshot. | Propagate refusal, do not select a second authority; a passing observation cannot roll back later change. |

Planning/review routes build in-memory proposals; external processes remain outside this bounded inventory. Adapters and public reader compatibility need independent verification before any change in authority.

## Non-atomic boundaries

`save_raw` performs read–compare–replace, leaving a race between comparison and replacement. `save_manifest` has no comparable expected-byte check. Neither participates in the private SQLite CAS. R7 checks source and stage at observations; R8 repeats observations around checks, but neither binds them into one transaction or freezes the physical artifacts. R9's controlled artifact mutation changed findings and restoration produced an ABA case while source/stage parity still passed. This is evidence of an insufficient snapshot, not proof of all possible schedules.

In R11, replacement changed source identity and the reader refused `stale-source / source / identity-before`; identity is checked before stage readback and source-byte comparison. It is therefore incorrect to claim that the first-stage JSON bytes were separately rejected in that run. File fsync and replace do not establish directory durability, crash recovery or Windows ACL/owner preservation. Unexpected SQLite sidecars are refused by the reader; native SQLite behavior, journal recovery and the parent shell are not sandboxed by Python guard hooks.

## Failure and rollback matrix

For every row, the only presently safe known response is to stop automatic writes, retain evidence and refuse a staged result when its checks detect divergence. No destructive repair is authorized. The additional notes identify unresolved dependencies and future tests, not a recovery prescription.

| State | Known bounded behavior; unknown recovery and dependencies; future evidence required |
| --- | --- |
| JSON updated, SQLite stale | R10/R11 synthetic changes caused staged refusal; JSON remains canonical. Which JSON version is correct and whether a shadow may be rebuilt require owner decision and source/artifact provenance. Test interruption and nonparticipating writers. |
| SQLite advanced, JSON unchanged | Stage CAS can advance independently; R7 refuses revisions beyond one. No stage-to-JSON promotion or rollback is proven. Test stage conflict, restart and disposal under an approved protocol. |
| Both changed differently | Do not choose a winner automatically. Reconciliation and rollback depend on authoritative history, backups and all writers. Test divergent interleavings and independently verified restore. |
| Artifacts mutate while both agree | Parity can pass despite changed findings, including ABA. Preserve observations; the correct artifact version is unknown. Define a source/artifact consistency model and test mutation during and after reads. |
| Interrupted before replace | Source normally remains the prior path in the reviewed staging sequence, but a real crash, buffers and temp state are unproven. Inspect rather than assume; test controlled interruption and restart on each supported filesystem. |
| Interrupted after replace | New path may be visible, but durability and prior version recovery are unproven. Preserve source and backup; test restart, directory durability and verified restore. |
| Temporary file left | Ordinary `finally` attempts cleanup; abrupt termination may bypass it. Do not blindly remove or use it; identify ownership/content and test interrupted cleanup. |
| Stale cooperative lock | Source refuses an existing lock and leaves it; a live owner cannot be inferred from a filename. Recovery depends on independently establishing owner and liveness; test crash and lock identity under a separate gate. |
| Forced-init backup collision | Exclusive creation refuses collision. Preserve both files; backup naming and restore semantics require review and collision/restore tests. |
| ACL/owner mismatch | No equivalence is established; stop and preserve state. Windows DACL/owner, aliases and filesystem policy require specialist assessment and separately authorized native tests. |

## Options and cutover decision

1. **JSON canonical, disposable SQLite shadow — recommended now.** Existing public routes continue to read JSON; divergence makes the private reader refuse. Reversible in principle by ceasing use of a disposable shadow, subject to evidence-preservation restrictions. JSON writer races and permissions remain open.
2. **SQLite sole authority — future option only.** Requires migration, closure of every legacy and independent writer, independent reader parity, public compatibility, artifact consistency, crash/restart and rollback proof. Reversal depends on a tested export/restore and format compatibility. No migration or activation is authorized.
3. **Dual-write/mirror — no current approval.** Two independent updates necessarily expose an intermediate state unless a proven protocol coordinates all writers and recovery. Reversal requires reconciling divergent histories; no such protocol is established. It cannot be represented as atomic on current evidence.

## Production cutover checklist

Every unproven item is **NO-GO**, including all items below; no checkbox is discharged by this RFC.

- Explicit owner approval of the exact authority, scope, migration and rollback.
- Complete route closure for legacy writers, backups, schema output and external/nonparticipating writers; verified coordination independent of cooperative callers.
- Defined and tested atomic source/artifact consistency model, including mutation and ABA.
- Crash/restart, interrupted replace, sidecars, fsync and durability evidence on supported systems.
- Windows ACL/DACL/owner and hardlink alias assessment, including preservation under replacement.
- Verified backups, restore and rollback, and on-disk format/version compatibility.
- Independently verified public CLI and adapters, reader parity and broad regression with safely reviewed omitted cases.
- Performance limits, observability, refusal diagnostics and operational ownership.

## Execution correction and next gate

R11 recorded 65/65 focused testcases twice, with the historical qualification above. The inherited `tests/test_experimental_staged_reader.py::test_cli_with_test_only_injection_matches_unmodified_json` ran `rehash --dry-run` and `add --dry-run` in both baseline and injected arrays: four route invocations per run, eight across two runs. Both initial startup/canary guard denials and both dry-run violations remain recorded. Guard writer sentinels do not cure the authorization deviation. The original guard record was 54 checks / 48 expected denials, plus nine supplementary denials and four positives; these are historical reports, not R12 reruns.

A future safe regression gate needs explicit owner authorization and either an exact reviewed test-only isolation change preserving remaining safe coverage, or explicit deselection of this exact node and both prohibited native scanner nodes. The latter must report exclusions and must not claim a full 65/65 regression. Any test edit, dry-run CLI execution, cross-process hostile concurrency, native ACL work or public persistence requires new owner authorization. R12 does none of these.

Propose first a **test-only isolation gate**, with a reviewed exact diff, fixture/parameter admission and a node-level inventory; acceptance requires zero prohibited route/scanner starts, reconciled collected and executed node counts, preserved safe coverage, retained deviations and unchanged pinned production bytes. This has lower scope and cost than a writer-coordination prototype, but cannot establish production concurrency or durability. An independently approved prototype investigation could instead enumerate every writer and external participant, demonstrate specified conflict/refusal and restart cases with independent evidence, and report unhandled schedules; it carries higher isolation, native-code and recovery risk and still would not authorize public persistence. Neither gate is opened here.

R9 lacks final-source/postpackage preservation proof; R10 lacks raw postpackage evidence; R10R1 bounded Git-object and 3,102-record checks were incomplete. R11's bounded 1,675-row match and 49 pins do not settle those gaps. Historical permission effects, hardlink alias and ACL/owner equivalence, and the 3,102 directory-size rows remain unknown. Python audit hooks do not sandbox native SQLite or the parent shell. No claim of full historical preservation or removal of P7 hold follows.

### D17-S1R6 reconciliation, 2026-11-08 (append-only; static assessment)
The earlier CLI pin `e45b9d696d1fa63730b223bb6c7d43d5093f9aee42fb84cb498dad4112096fe1` denotes the CRLF form of the historical seven-route CLI. The currently reviewed 22-route `src/runevidence/cli.py` is LF SHA-256 `a4cd86ac0903b4827bf7a846949a253d3d8e1aff9a02602e12ea95512d3a03e6` (CRLF SHA-256 `ba6bd580723f1b1ad7bccda0a0379a09d210d5cf46e43b9800e54383f56338a6`). The seven original handlers retain their prior writer sites per D17-S1R5 static inspection; current locations: `cmd_init` 543-596, `_finish_edit` 631-642, `cmd_rehash` 645-694, `cmd_add` 697-732, `_backup_existing` 735-765, `_write_new_file` 785-799 and `cmd_schema` 846-854. The fifteen additional CLI handlers were statically traced to JSON reads and in-memory projections, with no new built-in persistence route identified. Historical observations and pinned gates above remain intact. This is not a runtime, plugin-safety, SMB, OS, durability or SQLite cutover assessment. JSON remains production authority; P7 HOLD.

## Dated note — 2026-11-11 (overnight session): current source identities

- Current `src/runevidence/cli.py` LF SHA-256 is `500148e36b7b08f2e24910c7dce6df8d6704322dff511d44618d8dbc60521ba2` with 23 parser routes including `validate-batch`; the exact tested bytes are covered by `tests/test_experimental_cli_parser_contract.py`.
- Current `src/runevidence/manifest.py` SHA-256 is `a47494717308b5a73b65a161b81418e287b6e7c6e39c71bf4d6dc2f7b0e374fa`; duplicate-key and numeric-overflow rejection are retained, legacy `NaN`/`Infinity`/`-Infinity` are accepted only at `results[*].value`, and schema findings use privacy-safe pointers.
- `src/runevidence/report.py` remains `1eb3fdc9236018c3d2c4ff02ee9c968cfbd602b085bdfe4592eb5cb6fa0df063` and `README.md` remains `692c007d83219aaee49048d10b2f17793560bf4379475f0dbcde8f6c32c655eb`.
- This is an append-only dated current-state note, not a pin refresh; all historical text and prior gates remain as written, and no SQLite activation, B4 qualification, CLI runtime/plugin qualification or cutover is implied.

## Dated note - 2026-11-11 (Stage 2b): comparability CLI and pins

- Current `src/runevidence/cli.py` LF SHA-256 is `a4a4b921921f5e8fe1b04ca79d40e5dcffb2678e28ad5a0313ff6c3f25c3f147` (CRLF `83c2dbfa6cccbd9e5c505d2cef6d244901179caf67ba70054dc2dfde6b2d2d3a`) with 24 parser routes; `comparability` is registered directly after `compare`, and removing it recovers the exact prior 23-route sequence.
- Reader test pin: `READER_HASH` is `582a27bca1dfe64e1d7aaf2c18376044511431b2c08cc270bbb6581cea604ecc`; the superseded 23-route reader digest `cb614ec86203f3845fb54e0c1f92f5f2af0d0e3ea1b294d77392b49284e0dde3` is preserved in the historical tuple.
- `README.md` SHA-256 is `164e01cb976ca3770cd90521542e03dfb947c0fe2067288011f0d814bb606a06`. The controlled refresh and its proof packet are documented in `docs/comparability-stage2b-pin-refresh.md`.
- Append-only note. No SQLite activation, B4 qualification, plugin/runtime qualification or cutover is implied; JSON remains production authority and P7 HOLD remains.
