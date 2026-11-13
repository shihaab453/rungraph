# Private staged read-only seam

This experimental seam is private and has no public CLI or adapter route. JSON remains the production authority. A staging database is a disposable, non-authoritative synthetic test artifact; this seam does not migrate, update, or fall back to JSON.

## API and preconditions

`runevidence._experimental_staged_reader.load_staged_for_read(receipt)` accepts the `StagingReceipt` produced by the private validated staging API. It returns `StagedLoadedRead`, containing an ordinary `LoadedManifest`, the stage revision, generation and SHA-256, the original source path, and the label `verified-at-observation-only`. The stage must remain revision and generation 1: subsequent stage updates do not update JSON and are refused even if their bytes happen to match.

The original absolute JSON path must remain inside the same approved scratch parent and outside the database folder. Its ordinary file identity and exact bytes must match the receipt and the real `read_staged_manifest` result before and after construction. The database folder must contain only its expected database; unexpected entries, including SQLite sidecars, are refused. The loaded manifest retains the original JSON path, so relative artifact paths resolve from the original JSON root. Exact UTF-8 source bytes, including CRLF, whitespace, omitted defaults and opaque metadata, remain available in `LoadedManifest.source_bytes`; the model is validated again against the readback.

`StagedReadError` provides fixed diagnostics: `reason`, `subject`, `check`, and an optional underlying `stage_reason`. Reasons include `stale-source`, `invalid-stage`, `missing`, `busy`, and `fatal`. A mismatch, missing or corrupt source or database, foreign or forged receipt, changed identity, invalid model, revision divergence, or unexpected sidecar refuses rather than selecting another authority. These checks are advisory observations, not authenticated path seals or hostile-filesystem containment.

## Verification and limits

The guarded focused suite executed 33 cases against the actual private reader. It covers exact source bytes and original root, real `load_raw`, scanner-free checks, graph and report operations, in-memory edit plans, empty directory review, test-only in-process CLI injection, public-route refusals, writer sentinels, missing/corrupt/busy databases and sidecars, source and stage divergence, forged receipts, a source change during read, and schema/model mismatch. The empty directory review is limited: its batch implementation independently re-reads JSON. Test-only CLI injection does not provide public SQLite support. Public core, Hydra and MLflow import smoke checks retained the seven existing CLI commands without importing either private staging or reader module.

A separate guarded process opened disposable quiescent databases read-only and checked SQLite integrity, schema, identity, state bytes and receipts. It classified intentional negative fixtures and compared the disposable tree before and after. This is not a hot-journal recovery claim. Python audit hooks and reviewed wrappers do not sandbox arbitrary native code or the outer shell. No test proves production compare-and-swap, true concurrent process behavior, crash durability, or adversarial filesystem containment.

The JSON and SQLite observations are not atomic. A concurrent JSON modification can occur between checks or immediately after the function returns. The reader cannot prevent that race, and callers must not treat its result as a durable source identity guarantee.

## Later opt-in integration

A separate authorization and design review would be required for a narrow opt-in production reader. That work must define a single authority and safe refusal when legacy JSON and a stage diverge, address the dual-source race and real-world filesystem behavior, specify compatibility and rollback, and add independent integration and concurrency evidence. This seam does not enable such a route. The P7 persistence safety hold remains in force.

### D17-S1R6 public-command reconciliation, 2026-11-08 (append-only; static assessment)
References to seven public commands or seven observed help outputs describe the earlier seven-route CLI only. The current `src/runevidence/cli.py` has 22 parser routes (LF SHA-256 `a4cd86ac0903b4827bf7a846949a253d3d8e1aff9a02602e12ea95512d3a03e6`). The fifteen new routes have been traced statically to in-memory JSON projections; R5 did not repeat help-path runtime isolation for all routes or review external adapter plugin side effects. This note does not grant SQLite authority: P7 HOLD, JSON production authority.

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
