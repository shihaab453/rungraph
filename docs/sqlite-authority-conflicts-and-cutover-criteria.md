# Synthetic source authority conflicts and cutover criteria

Status: characterization in progress. S3P3P7 persistence safety hold remains in force. No production authority choice or activation follows from this record. The JSON path and bytes consumed by `load_manifest` and the private SQLite stage plus receipt are independent authorities; the read-only facade observes them separately, not in a shared transaction. The test schedules are deterministic interventions in one controlled process, not hostile process concurrency, crash or durability tests.

## Writer closure and boundaries

This inventory is code review only; no legacy writer is invoked by the new synthetic tests. The following source SHA-256 pins identify the reviewed version. All routes can bypass the private stage because none calls its CAS.

| Entry and callers | Source SHA-256 | Writes and controls |
| --- | --- | --- |
| `manifest.save_manifest`, called by `cli.cmd_init` | `ad065582c2dac53785fdd239218f25662d41826209747eafea057caeca92123c` | New path uses `_write` directly; existing path uses sibling temporary file, fsync and `os.replace`. No lock or CAS. On non-Windows, chmod copies mode; Windows ACL/owner equivalence is not established. |
| `manifest.save_raw`, called by `cli._finish_edit` for `cmd_rehash` and `cmd_add`, and by `directory_apply.apply_directory_proposal` | same manifest hash | Sibling temporary file, fsync, byte comparison, then replace; comparison and replace are separate, not atomic CAS. No shared SQLite lock. Non-Windows chmod as above. |
| `cli._backup_existing` / `_write_new_file`, called by forced `cmd_init` | `e45b9d696d1fa63730b223bb6c7d43d5093f9aee42fb84cb498dad4112096fe1` | Exclusive backup content write, flush/fsync; no replacement or stage CAS. Backup history is deliberately not exercised. |
| `cli.cmd_schema` | same CLI hash | Optional output file is opened for text write; it is schema JSON, not a manifest transaction; it can target a caller-selected path. No lock, replace or CAS. |
| `directory_apply.apply_directory_proposal` | `76ac35bd917ea01f30f2be2bc9deaae9c3e3211f458491177c65bba58b7fc7d0` | Rechecks source and rebuilds proposal, then delegates to `save_raw`; scanner and permission-sensitive paths are not exercised here. |
| `directory_cooperative_apply.apply_directory_proposal_cooperatively` | `bd72c2a8395c5101e36dff89969dc14c46ba6eeceb80241425c2cf66fc578b4d` | Exclusive sibling lock, owner/identity check, and delegation to directory apply. Lock only coordinates participating callers; cleanup unlinks lock. No shared stage transaction or Windows ACL preservation guarantee. |
| `edit.plan_add` / `plan_rehash`, `directory_authoring` proposals, directory review and batch | `fd0e82cda7eece6065d9405202f2989ab753396fa375abd5f3eb3993e98f74d5`, `d93e390a3dbdd3495000fa0682e37f943a1857c63cc1b09c67d51244d5ef6533`, `ba6a430a167d334917cf31d446582ae7f055d087bf15e15193028395cc06f1e0`, `1bc5fd18c579858535a7f76cf90f6e17eada3b427a5c927540efc788cdbed619` | Planning/review operations read and build in-memory proposals, not manifest writes. Apply is a separate operation. Directory scans and permission-sensitive paths are excluded from this gate. |

`save_manifest`, `save_raw`, forced-init backup, schema output and directory/cooperative write routes are **NOT PROVEN / CODE REVIEW ONLY**. Direct edits in tests model a nonparticipating JSON writer; they do not execute a production writer. No claim is made that this table covers unreviewed external processes.

## Controlled cases and limits

The tests record source, stage and artifact SHA-256, revision and intended observation in a JSON trace beneath each invented scratch directory. A stage CAS changes only the stage; a source write changes only JSON. The `facade.run_checks` test hook injects source or stage change after the first observation and before the second. The artifact case changes bytes during checks, observes changed findings, restores the bytes (ABA), and demonstrates that source/stage parity can still pass. This acceptance is an insufficiency, not a durability or security guarantee. The stale-candidate case demonstrates SQLite CAS conflict for participating candidates while an emulated ordinary JSON edit is not arbitrated. Missing, corrupt, sidecar, forged and busy cases expect refusal without JSON fallback. Actual observations depend on the guarded run and its retained traces; test source alone is not evidence of a pass.

## Decision alternatives

| Alternative | Boundary and blockers |
| --- | --- |
| JSON remains sole production authority; SQLite disposable shadow | Preserves existing writer semantics, but shadow divergence must be detected and discarded; no stage read may silently replace the JSON authority. |
| SQLite becomes prospective sole authority | Requires separately authorized migration and exact compatibility, closure and interception of every legacy writer, external process concurrency and crash recovery proof, ACL/owner handling and tested rollback. Existing APIs do not establish these properties. |
| Dual-write or mirror | Independently updating JSON and SQLite has an intermediate state and crash/lost-update windows. Without a common transaction or a proven protocol covering every writer it is not atomic or safe by default. |

Before any later production-write gate: explicit owner approval and a separate reviewed safety plan for writer closure, real-world and hostile concurrency, crash recovery/durability, Windows ACL/owner state, and rollback. R9 does not clear those blockers. Parent shell and native SQLite are trusted but not sandboxed; no Windows DACL/owner equivalence is claimed. Historical 3,102 directory-size observations and prior permission-operation uncertainties remain qualified, not retrospectively explained.

## R9 observed results

Two guarded runs each admitted and passed 13 R9 cases plus 45 R7/R8 regression cases; the second follows correction of the JSON-first case to change a value rather than whitespace. Baseline, stage-first, source-first including whitespace-only, two facade observation interventions, artifact change and restoration, stale competing stage candidates, and five failure modes were observed in their individual synthetic traces. The artifact report changed while stage and source parity still passed; restoration also returned the original report. The negative cases refused with missing, invalid-stage, or busy classification as appropriate. JSON writes in this characterization were emulated on exclusively owned synthetic files. Guard hooks and monkeypatches control this process only; they cannot constrain an adversary or native SQLite. The stage/source observations are not atomic, and neither artifact immutability nor Windows ACL/owner equivalence is established.

The guarded readback inspected 112 quiescent synthetic stores across both runs, with 18 classified negative and 62 eligible revision-one matching-source clean samples; other valid stores may deliberately diverge from their JSON source. A separate reader checked the exact SQL schema, identity, integrity and bytes of three clean samples and refused a corrupted header. These are bounded samples, not a proof of recovery or concurrency. Seven public help paths and public imports did not load the private stage, reader or facade modules.

### D17-S1R6 reconciliation, 2026-11-08 (append-only; static assessment)
The CLI source identity `e45b9d696d1fa63730b223bb6c7d43d5093f9aee42fb84cb498dad4112096fe1` used above is historical. Live CLI LF SHA-256: `a4cd86ac0903b4827bf7a846949a253d3d8e1aff9a02602e12ea95512d3a03e6`; 22 routes. The original `_backup_existing`, `_write_new_file`, and `cmd_schema` functions have matching AST structure against the reviewed seven-route source, but their surrounding callees have changed. In particular, `schema -o` still writes a caller-selected file, while the generated schema content now reflects directory-artifact model additions. The R9 statement about seven help paths is correctly scoped to its historical seven-route runtime check, not current 22-route coverage. Added routes have static-only review and do not establish the absence of installed adapter plugin side effects. P7 HOLD; production authority remains JSON.

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
