# Comparability Stage 2b pin refresh

Status: implemented, pending acceptance.
Date: 2026-11-10.
Milestone: M9-COMPARABILITY-STAGE2B-R1.
Contract: `docs/comparability-stage2-privacy-cli-rfc.md` v0.3 (status line only
changed to implemented-pending-acceptance; the contract text is unchanged).

This record is the proof packet required by section 5 of the contract for adding
the 24th public parser route. It records byte pins, the exact route order, the
change inventory, the proof obligations and the acceptance evidence observed in
this workspace. It is static parser/boundary evidence, not runtime, SQLite,
plugin-safety or scientific-validity qualification.

## 1. CLI byte pins

`src/runevidence/cli.py` is LF-only in this checkout. The CRLF pin is derived by
asserting no existing `\r\n`, replacing every `b"\n"` with `b"\r\n"`, and
hashing the result. No other normalization is applied.

| Form | Historical 23-route pin (old) | Current 24-route pin (new) |
| --- | --- | --- |
| LF | `500148e36b7b08f2e24910c7dce6df8d6704322dff511d44618d8dbc60521ba2` | `a4a4b921921f5e8fe1b04ca79d40e5dcffb2678e28ad5a0313ff6c3f25c3f147` |
| CRLF | `63886e751054cb49e92341bed976e1b4ac684f95f98e6166081eaf407a83b7a5` | `83c2dbfa6cccbd9e5c505d2cef6d244901179caf67ba70054dc2dfde6b2d2d3a` |

`HISTORICAL_23_ROUTE_CLI_HASH` now holds the old pair and
`HISTORICAL_23_ROUTES` the old sequence. `CLI_HASH` remains a `frozenset` of
exactly two digest strings, checked by `source()` and the constant test. Old
pins are disjoint from current pins.

## 2. Reader test pin

| Form | Old | New |
| --- | --- | --- |
| `READER_HASH` (single LF digest) | `cb614ec86203f3845fb54e0c1f92f5f2af0d0e3ea1b294d77392b49284e0dde3` | `582a27bca1dfe64e1d7aaf2c18376044511431b2c08cc270bbb6581cea604ecc` |

`READER_HASH` remains exactly one string, contains no collection, CRLF or
historical alternative, and matches the edited
`tests/test_experimental_staged_reader.py` bytes. The archived reader digests
are preserved in `HISTORICAL_READER_HASHES`.

## 3. Route order

Runtime `build_parser()` subparser order, observed directly:

```text
validate, validate-batch, inspect, compare, comparability, leaderboard, matrix,
run, claim, lineage, impact, coverage, coverage-gate, fingerprint,
fingerprint-compare, coverage-diff, review-diff, impact-diff, review-gate,
init, rehash, add, schema, adapters
```

- Exactly 24 unique routes.
- `comparability` is registered directly after `compare`.
- Removing `comparability` recovers the exact historical 23-route sequence.
- Removing `validate-batch` and `comparability` recovers the exact historical
  22-route sequence.
- The historical seven-route baseline remains a subset.

## 4. Change inventory

| File | Change |
| --- | --- |
| `src/runevidence/cli.py` | Imports `io`, `redirect_stderr` and `run_comparability`. Adds `cmd_comparability`, the 24th subparser directly after `compare`, a sanitized `_comparability_main(raw)` boundary in `main`, and the early raw-argv branch. Existing command handlers and the non-comparability parse path are unchanged. New LF pin above. |
| `tests/test_cli_comparability.py` | New 39-case CLI boundary suite (new file). |
| `tests/test_experimental_cli_parser_contract.py` | Adds `HISTORICAL_23_ROUTE_CLI_HASH`/`HISTORICAL_23_ROUTES`; refreshes `CLI_HASH`, `READER_HASH` and `ROUTES`; extends the historical-disjointness set; updates route-count/order assertions; adds a grammar-only `comparability` parse test. |
| `tests/test_experimental_staged_reader.py` | Route-set assertion only: adds `comparability`, count 23 -> 24, comment. Its argv tripwire and injection seam are untouched. New hash `582a27bc...`. |
| `tests/test_experimental_versioned_manifest.py` | Route-set assertion only: adds `comparability`, count 23 -> 24, comment. |
| `tests/test_experimental_comparability_bridge.py` | Import-boundary test now allows exactly the two package-root importers, `run_comparability.py` and `cli.py`; all other (including nested) modules stay forbidden. Also asserts `cli.py` does not import the bridge directly. |
| `README.md` | One command-table row and a `### Comparability (experimental)` section (usage, privacy link, exits 0/1/2, disclaimer). |
| `docs/comparability-stage2-privacy-cli-rfc.md` | Status line only. |
| `docs/comparability-stage2b-pin-refresh.md` | This record (new). |

Supporting current hashes (recorded, not pins):

| File | SHA-256 |
| --- | --- |
| `tests/test_cli_comparability.py` | `a3b28fbaf28eae18a1f25fbb9f342bd3d04d361874635d742c0410d04981dffe` |
| `tests/test_experimental_cli_parser_contract.py` | `bf063e711fe244bd51d2759468f97c4751aa44c5cd6648d720440a3057d5453b` |
| `tests/test_experimental_staged_reader.py` | `582a27bca1dfe64e1d7aaf2c18376044511431b2c08cc270bbb6581cea604ecc` |
| `tests/test_experimental_versioned_manifest.py` | `dda06f2f7cab9004f45e87cef4d495a2fc8395a7703a9f3db8f72a44eaa90e75` |
| `tests/test_experimental_comparability_bridge.py` | `b674051b0dd8dd48c8ce380a6b2c0440bf12e15207898c04c50d888075a2957d` |

## 5. Proof obligations

| Contract requirement | Evidence |
| --- | --- |
| Old/new byte hashes and exact change inventory | Sections 1, 2 and 4. |
| Exactly 24 unique routes; removing `comparability` recovers the prior 23 | Section 3; `test_current_contract_preserves_historical_baselines` and `test_pinned_parser_structure`. |
| All registrations, dispatch paths and shared parser behavior reviewed | Only one registration block was added, after `compare`. `main` gains one early branch keyed on `raw[0] == "comparability"`; every other command takes the original parse path. The new handler calls only `run_comparability.compare_paths` and prints. `_load_or_report` is not reused. |
| No new connection to staged readers, SQLite, private validation facades, adapters, scanners, subprocess, network or writers | `cli.py` adds `io`, `contextlib.redirect_stderr` and `.run_comparability` only. It does not import the bridge directly. `run_comparability` already imported only bridge, comparability and manifest (Stage 2a, hash unchanged). |
| Exactly the intended LF/CRLF CLI representations, no widened acceptance | `CLI_HASH` is still exactly two strings; `source()` fails closed on any other kind or count. |
| Recomputed `READER_HASH` for the changed reader test | Section 2; one LF digest. |
| Historical records and dated notes preserved | Seven-, 22- and 23-route constants and reader archives preserved. No historical doc was rewritten beyond the RFC status line; no new dated note was added (follow-up). |
| Bridge blanket prohibition replaced by a narrow allowlist | Package-root `cli.py` and `run_comparability.py` only; nested impostors and every other module stay forbidden. |
| Full acceptance counts, protected hashes/modes, negative controls | Sections 6 and 7. |

## 6. Acceptance evidence

Commands run in this workspace with `.venv/bin/python`:

| Check | Result |
| --- | --- |
| `pytest -q tests/test_cli_comparability.py` | 39 passed |
| `pytest -q tests/test_experimental_cli_parser_contract.py` | 6 passed |
| `pytest -q` (full suite) | 2115 passed, 533 skipped, 7 subtests passed, exit 0 |

The pre-change baseline was 2075 passed, 533 skipped, 7 subtests passed. The 40
new tests are the 39 CLI cases plus one grammar-only parser case; 2075 + 40 =
2115.

Protected files (not edited by this milestone):

| File | Mode | SHA-256 |
| --- | --- | --- |
| `src/runevidence/comparability.py` | `0444` | `cd245e99016bd0652ca661e1cc34c40f85b16646d557fb889d06ac0ca7e3b254` |
| `tests/test_comparability_core.py` | `0444` | `7b8f6485a4b21863be2fa9cf0f2da0c5d142f54d554fe0527cf9f095b860aa4e` |
| `src/runevidence/run_comparability.py` | `0644` | `21f0765f93909f464b90d5482e7bb7c8223ca79314dc2006b8b6d1e1428adb72` |
| `src/runevidence/_experimental_comparability_bridge.py` | `0644` | `ef65e519703c2537ae4a46175f17cdde62c5df9bbe9bb030e082ffaed25ccd16` |
| `src/runevidence/models.py` | `0644` | `1f6fb49ea91e332e00182c10af2ba02293e20d72854da45161743336b7cb5903` |
| `src/runevidence/manifest.py` | `0644` | `a47494717308b5a73b65a161b81418e287b6e7c6e39c71bf4d6dc2f7b0e374fa` |

Stage 2a module and bridge bytes match the pins stated in the Stage 2a record;
the 0444 files retain mode `0444`.

Runtime route check (executed, not inferred): 24 choices in the order shown in
section 3, with `routes.index("compare") + 1 == comparability` and the
comparability-removed list byte-equal to `HISTORICAL_23_ROUTES`.

## 7. Negative controls

Each named control fails a specific assertion if the property is broken:

- Token/diagnostic echo: `test_grammar_error_does_not_echo_tokens` submits a
  canary as an extra operand and requires it absent from stdout/stderr while
  the fixed `INVALID_ARGUMENTS` document is printed.
- Path echo: load-failure tests assert that neither operand path nor the canary
  filename appears in output.
- Selector/ID echo: `test_unknown_result_selector_is_fixed_mapping_failure`
  passes a canary selector and requires it absent from the fixed
  `MAPPING_FAILED` document.
- Secret echo: malformed JSON contains a secret string; it must not appear.
- Parser diagnostic suppression: real-entry grammar failure asserts empty
  stderr and no `usage:` line.
- No work on grammar failure: monkeypatched `compare_paths` and
  `load_manifest` recorders must stay empty for every grammar case.
- No artifact I/O: `Path.read_bytes` is guarded and must be called only for the
  two manifest operands; a tempting artifact file is never read.
- No partial output: serializing the result document raises before any write,
  and stdout/stderr stay empty.
- Privacy on success: a canary-bearing but comparable manifest pair produces
  no canary in text or JSON.

## 8. Deviations and limits

- Serialization failure after a successful comparison propagates instead of
  being converted to `INTERNAL_ERROR`. The contract's fixed internal-error
  document is produced by the Stage 2a module, which this milestone may not
  edit; the required property (complete document built before printing, no
  partial stdout) is tested and holds. This is the "if practical" exception
  from the task brief.
- Only static parser/boundary evidence is claimed. No runtime qualification,
  plugin safety, SQLite qualification or scientific-validity claim is made.
- No dated notes were added and no historical document was rewritten beyond
  the RFC status line, per the task boundary.
- No git operation was performed; this record is filesystem evidence only.

## 9. M10 close-out: final status and acceptance

Final status: **IMPLEMENTED AND ACCEPTED WITH LIMITATIONS**. The contract text
is unchanged; the RFC status line records this verdict.

Both Stage 2b acceptance verdicts returned **accept with limitations**:

1. Independent static route/pin review: ACCEPT WITH LIMITATIONS. The 24-route
   order and both CLI byte representations are accepted, the sanitized grammar
   boundary performs no manifest work, and the historical 23-route record is
   preserved. Limitations are the trusted-local privacy model, static-only
   route review (no plugin/runtime/SQLite qualification), and the fact that
   interpreter or resource failures outside the validated outcome boundary can
   terminate without the fixed application-error document.
2. Independent boundary/privacy re-check: ACCEPT WITH LIMITATIONS. Only the two
   manifest operands are read, failures emit closed fixed documents without
   submitted paths/IDs/tokens, and the installed wheel reproduces the same
   JSON/text outcomes. No scientific-validity, artifact-integrity or
   remote/multi-tenant claim is made.

### Close-out changes

- `tests/test_experimental_cli_parser_contract.py`: the superseded 23-route
  reader digest `cb614ec86203f3845fb54e0c1f92f5f2af0d0e3ea1b294d77392b49284e0dde3`
  is now preserved in `HISTORICAL_READER_HASHES` and named
  `HISTORICAL_READER_DIGEST_23`, with a constant-only assertion that it is in
  the historical tuple and is not the current `READER_HASH`. New file SHA-256:
  `59aab4522bff026e6e1b6637872220f5f80af0de1628c868466c07748b253cde`.
- `src/runevidence/run_comparability.py`: docstring only. The stale "no CLI
  route is registered yet" phrase now states the Stage 2a module / Stage 2b
  `cli.py` route. No behavior change. New SHA-256:
  `21dbf80ea1ba4570cd59e8cd13aa65887e6855a3fb1cc3bc40879756e44a4b5a`.
  The section 6 hash `21f0765f...` records the pre-closeout docstring bytes.
- `README.md`: final SHA-256
  `164e01cb976ca3770cd90521542e03dfb947c0fe2067288011f0d814bb606a06`
  (recorded over the file as checked out; README is CRLF-uniform, while the
  appended documentation notes are LF-only).
  The general exit-code row now covers completed checks that cannot produce a
  pass/difference decision, the comparability exit-2 scope is stated, and the
  single-error-document claim is limited to validated outcomes.
- `docs/comparability-stage2-privacy-cli-rfc.md`: status line only, now
  IMPLEMENTED AND ACCEPTED WITH LIMITATIONS.
- The seven SQLite docs carrying the 2026-11-11 identity note each received an
  append-only "Dated note - 2026-11-11 (Stage 2b): comparability CLI and pins"
  section: `sqlite-authority-boundary-and-cutover-rfc.md`,
  `sqlite-authority-conflicts-and-cutover-criteria.md`,
  `sqlite-existing-writer-authority-and-rollback-decision.md`,
  `sqlite-legacy-writer-execution-boundaries.md`,
  `sqlite-persistence-integration-gates.md`,
  `sqlite-readonly-reader-seam.md`,
  `sqlite-shadow-witness-acquisition-rfc.md`. No SQLite/B4/plugin/runtime
  qualification is implied.

### Acceptance counts

- Full suite: **2115 passed, 533 skipped, 7 subtests passed**, exit 0.
- Parser contract: **6 passed**; runtime route count **24**, with
  `comparability` immediately after `compare`.
- CLI suite: **39 passed**.
- Adapter suites against installed wheels: **61 passed** (hydra **31**,
  mlflow **30**).

### Packaging re-verification (M10)

Built from `/tmp/m10pkg/project` with `/tmp/m5pkg/buildvenv`; no build artifact
was written into the project.

| Artifact | SHA-256 | Members |
| --- | --- | --- |
| core wheel `runevidence-0.1.1-py3-none-any.whl` | `13a0103804fb17f1a984a874d6da520980b99e2e08250b791f42f3942a047cd1` | 58 |
| core sdist `runevidence-0.1.1.tar.gz` | `f7e89434a3375ba3c4122d26c958fc3ff4b0b383d7bfbcbd781a2605f83a80ae` | 59 |
| hydra wheel `runevidence_hydra-0.1.1-py3-none-any.whl` | `925ca1358465df20b07afe6e334f3a29fc7514af6383ea89c46ca5fc2d23d6cf` | 7 |
| hydra sdist `runevidence_hydra-0.1.1.tar.gz` | `8174821a13266904746b3cbfecb44d41a2fbce1bb625db54311b52a3922f8ab0` | 8 |
| mlflow wheel `runevidence_mlflow-0.1.1-py3-none-any.whl` | `250127530352ad4b8aeea46b30ef4262750d0bbe8460334260d76c242294de70` | 7 |
| mlflow sdist `runevidence_mlflow-0.1.1.tar.gz` | `81d62647839df91b56988a3c06e035edad6431401198278b8c2f5e93e99090b4` | 8 |

- All **53** `src/runevidence/**/*.py` files are byte-identical across the
  project, the copy and the built wheel, including `run_comparability.py`
  (`21dbf80e...`); every packaged member hash matches, and the packaged
  `cli.py` matches the LF pin `a4a4b921...c147` exactly.
- Wheel/sdist members contain no `__pycache__`, `.pyc`, `.pytest_cache`,
  git metadata, tests, docs or examples. The sdist retains the repository
  root `.gitignore` (cosmetic, as in the prior package) and its declared
  `README.md`, `LICENSE` and `CITATION.cff`.
- Clean core venv: wheel + dependency install, `pip check` clean, installed
  `comparability --help` exit 0, and a real temp-manifest comparison returned
  exit 0 with the expected JSON envelope and text, stderr empty, no canary or
  operand path in output.
- Adapter venv: core + hydra + mlflow wheels installed with `pip check` clean;
  entry points `hydra`/`mlflow` present; both adapter test directories pass
  from the copy against installed packages.
- Adapter wheel and sdist hashes are identical to the prior M6 packaging
  record, confirming those packages are unchanged.

### Current pins at close-out

- CLI LF: `a4a4b921921f5e8fe1b04ca79d40e5dcffb2678e28ad5a0313ff6c3f25c3f147`
- CLI CRLF: `83c2dbfa6cccbd9e5c505d2cef6d244901179caf67ba70054dc2dfde6b2d2d3a`
- `READER_HASH`: `582a27bca1dfe64e1d7aaf2c18376044511431b2c08cc270bbb6581cea604ecc`
- Historical reader digest: `cb614ec86203f3845fb54e0c1f92f5f2af0d0e3ea1b294d77392b49284e0dde3`
- Route order: 24 routes, `comparability` after `compare`.

### Residual limitations

Static parser/boundary and package-install evidence only. No SQLite activation,
B4 qualification, plugin-safety proof, runtime privacy qualification,
scientific-validity claim or remote/multi-tenant authorization is made. JSON
remains production authority and P7 HOLD remains.
