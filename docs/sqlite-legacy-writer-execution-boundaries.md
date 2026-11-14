# Synthetic legacy writer execution boundaries

Status: work in progress; S3P3P7 persistence safety hold remains active. No production write cutover is authorized.

## Pre-execution source review

Pinned `src/runevidence/manifest.py` SHA-256 ad065582c2dac53785fdd239218f25662d41826209747eafea057caeca92123c. `save_raw(data, path, *, expected_bytes)` (line 149) serializes the supplied dict with `dump_raw`, creates a named temporary sibling, writes, flushes and fsyncs, reads the target and compares its bytes, then uses `os.replace`; its finally block unlinks the staged name. On Windows the `os.name != "nt"` chmod branch is not entered. The comparison and replacement are separate operations; no stage CAS or lock is consulted. Windows `tempfile` and guard behavior must be demonstrated by guarded execution. Failure in staging, comparison or replacement can leave different observable scratch effects; this is not crash recovery evidence.

`save_manifest(manifest, path)` (line 113) serializes the model and for a new path calls `_write` (line 178), opening it in text write mode. On a write exception it unlinks the new path. Existing paths follow the sibling/fsync/replace route and have the same conditional chmod; that branch is not admitted here. The new-path check and open are not exclusive as product operations, so only an exclusively owned synthetic directory is suitable.

`cli._finish_edit(args, raw, loaded, summary)` (line 171) calls `save_raw` for non-dry-run, but CLI edit command closure, inputs and possible other filesystem operations are not fully reviewed: NOT EXECUTED / CODE REVIEW ONLY. No backup, directory scanner, reparse traversal, external process, lock cleanup, permission changes or native calls are admitted for these writer tests. The Python audit and wrapper guard cannot sandbox arbitrary native code or the parent shell. SQLite is native and remains trusted, limited to disposable synthetic stores.

Planned deterministic cases: W1 actual JSON change after staging and reader stale refusal; W2 stage CAS followed by actual JSON write; W3 sequential stale expected bytes rejection and refreshed expected bytes overwrite; W5 missing/invalid load refusal; new-path model round trip. W4 instrumented comparison barrier is not admitted. These are single-process schedules, not concurrency or durability tests. Execution results and limitations must be recorded after verification.

## Observed guarded execution

On Windows, the four R10 tests and 58 R7/R8/R9 tests passed together: 62 passed. JUnit, node phases and fixtures were reconciled with zero unexpected guard denials. An earlier run had 60 passes and two failures because the scratch parent name did not meet the private stage's fixed naming rule; it was corrected in the R10 test and the failed evidence retained. The guard self-test passed 54 checks, including 48 expected denials; supplement probes passed nine denials and four positives. Direct parameter canaries passed and indirect/unreviewed fixture admission was denied.

| Route / schedule | Result |
| --- | --- |
| `save_raw`, synthetic existing JSON after stage (W1) | Executed successfully. JSON changed from SHA-256 `b9125f7babf0ae8f35a7feab19bc39c7efb25e3118fd37c466f48f0c6cd3d4e1` to `90f8830452d053b49c475cd1448477e58e4c793a05e40accadb091e7b752d45d`; stage bytes remained equal and the private reader refused `stale-source`. |
| Stage CAS then `save_raw` (W2) | CAS applied and the JSON writer also completed, demonstrating no common arbitration in this controlled schedule. |
| Two precomputed candidates (W3) | The second call with original expected bytes was rejected. Supplying the intermediate bytes as expected allowed the second candidate to overwrite the first; this is a sequential single-process observation, not a concurrent race. |
| `save_manifest`, new synthetic path | Completed and the model round-tripped. |
| Missing and invalid JSON | Load raised `ManifestLoadError`; this is an application error, not a guard denial. |
| Existing-path `save_manifest`, CLI edit, barrier (W4) | NOT EXECUTED / CODE REVIEW ONLY. |

Writer staging, flush, fsync, comparison, replace and cleanup are inferred from the pinned source and successful completion; individual OS events and source metadata before/after were not independently traced in these tests. The guard records admissible operations only for some routes and does not establish complete native confinement or Windows DACL/owner preservation. Real-world writer closure, atomic JSON/SQLite authority, hostile process concurrency, interruption and durability remain unproven. The public authority remains JSON and the persistence safety hold stays active.

The final R9 source bytes were compared live with both the final R9 ZIP members and the later `ev/postrun02.json`; this does not retroactively establish a final-source R9 post-package preservation comparison. Historical permission effects, 3,102 directory-size observations and hardlink alias qualifications remain unresolved.

### D17-S1R6 source-line reconciliation, 2026-11-08 (append-only)
The `cli._finish_edit` reference at historical line 171 now resolves to `src/runevidence/cli.py:631-642` in the current 22-route CLI (LF SHA-256 `a4cd86ac0903b4827bf7a846949a253d3d8e1aff9a02602e12ea95512d3a03e6`). This is a static location correction, not a re-execution of legacy writer procedures. P7 HOLD.

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
