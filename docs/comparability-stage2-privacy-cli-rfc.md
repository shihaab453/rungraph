# Stage 2 comparability privacy and CLI contract (v0.3)

Status: IMPLEMENTED AND ACCEPTED WITH LIMITATIONS (contract text unchanged). Stage 2a and
Stage 2b acceptance evidence is recorded in
`docs/comparability-stage2b-pin-refresh.md`.
Date: 2026-11-11.
Supersedes the Stage 2 sketch in `docs/comparability-integration-rfc.md` v0.2.
Panel: two independent GPT-6.1 Sol reviews (Claude usage exhausted). Both returned
REQUIRE CHANGES; this document incorporates every blocking condition.

## 1. Decision

The PC26 core and Stage 1 bridge are authorized for **real-manifest use only under the
trusted-local contract below**. A read-only CLI may be added, but only after:

1. the privacy-safe command module, envelope, renderer and failure boundary are implemented
   and accepted (Stage 2a);
2. the 24th parser route is added with a reviewed pin refresh (Stage 2b).

Until both stages close, the feature remains library-only and synthetic-only in public
claims.

## 2. Actual processing inventory (not the marketing inventory)

The bridge is output-minimized, but it is not read-minimized. The disclosed facts are:

- The CLI loads both **entire manifests** through `load_manifest`, including all metadata,
  names, paths, locators, artifact IDs and source bytes. Field selection applies to
  comparison mapping, not file acquisition.
- The bridge reads and maps: `artifact.metadata["dataset_id"]`; dataset/checkpoint `kind`,
  `sha256`, `volatile`, `artifact_type`; `run.metadata["evaluation"]["split"]` and
  `["protocol"]`; `result.metadata["metric_definition"]`; `run.metadata["source_revision"]`
  and `["environment"]`; run/result/artifact IDs; and `input_to`, `derived_from`,
  `result_of` edges.
- It also reads `Result.name` and puts it in the private core input. The core can expose a
  `METRIC_NAME_DIFFERS` reason; the name itself is not printed.
- It recursively scans **all metadata on the selected result, selected run and every
  attached artifact** for nonfinite numbers. An unrelated NaN/Infinity can therefore cause
  `INPUT_SHAPE_UNSUPPORTED`.
- It indexes IDs across the **whole manifest**, including project and evidence objects.
  Duplicate IDs outside the selected subgraph can cause rejection.
- It scans manifest collections and relationships beyond the selected evidence for the
  relevant edges.

No claim may say "unknown metadata is never read" or "only the selected subgraph is
inspected".

## 3. Authorization and trust boundary

Authorized use:

- The caller is authorized to read both whole manifests and to disclose the comparison
  result to every recipient.
- Trusted local process, interpreter, installed packages and filesystem. Shared hosts need
  account/file/process isolation; Unix permissions alone are not organizational consent.
- Ordinary `load_manifest`-produced, unmodified `LoadedManifest` snapshots.
- Deliberate local invocation with two manifest operands and two explicit result selectors
  is sufficient opt-in. A separate `--allow-real-metadata` flag is not required and would
  not establish ownership, prevent inference, or protect command history.

Not authorized:

- A remotely callable comparison service, cross-tenant use, or attacker-controlled adaptive
  querying.
- Hostile Python objects, subclasses with hooks, `model_construct` substitutes, monkeypatched
  models, concurrent mutation, or manually mutated snapshots.
- Claims of anonymity, constant-time behavior, DoS resistance, secure erasure, debugger or
  traceback-local protection, memory/crash-dump protection, or protection from OS/timing/
  resource observations.
- Treating output redaction as protection for shell history, process arguments, terminal
  capture, CI command echoes or host audit records.

CI use requires private inputs, authorized log viewers and an explicit retention decision.

Inference disclosure: output reveals presence, matches, differences, missing/unknown states,
nulls, malformed shapes, counts, display-name difference and fixed rejection categories.
Repeated comparisons against chosen candidates can reveal low-entropy secrets (especially
an entire `environment` object). Aggregation increases disclosure. Output is sensitive.

## 4. CLI contract

Route (Stage 2b):

```text
runevidence comparability BASELINE TARGET \
  --baseline-result ID --target-result ID [--json]
```

- No `--allow-real-metadata`, no `--redact-manifest-path` (paths are never emitted), no
  `--strict`, no `--summary-only`.
- Both operands are manifest files or folders; selectors are exact IDs, not trimmed or
  case-folded.
- `--json` is recognized from the raw argv even when parsing later fails.

Exit codes:

- `0` — `COMPARABLE_RECORDED_CONDITIONS`
- `1` — `NOT_COMPARABLE_RECORDED_CONDITIONS`
- `2` — `INSUFFICIENT_EVIDENCE`, core-rejected input, load failure, mapping failure, invalid
  arguments, or internal error.

`1` means recorded mandatory conditions differ; it is not an artifact-validation failure.
`2` can follow a completed evaluation (insufficient evidence), not only a failure to check.

### 4.1 Success / core-rejected JSON envelope

The core projection must not be presented bare, because its frozen
`integrated_into_rungraph: false` would contradict a live CLI. Use a versioned application
envelope:

```json
{
  "format": "runevidence-comparability-result-v1",
  "application_status": "EXPERIMENTAL_READ_ONLY_CLI",
  "core": { "...": "the exact core public projection, unchanged" },
  "notice": "Embedded prototype and integration flags describe the frozen PC26 core, not this CLI."
}
```

- `core` is exactly the object returned by `comparability.evaluate`; no fields added or
  removed inside it.
- `notice` is the exact fixed string above.
- Exit code is 2 when `core.input_rejected` is true, even though the envelope is a result.
- The envelope key set is closed and validated before rendering.

### 4.2 Application error JSON

Exactly one complete document, no partial result:

```json
{
  "format": "runevidence-comparability-error-v1",
  "ok": false,
  "exit_code": 2,
  "error": {
    "code": "MANIFEST_LOAD_FAILED",
    "side": "baseline"
  }
}
```

For mapping failures:

```json
{"code": "MAPPING_FAILED", "side": "baseline", "mapping_code": "INPUT_SHAPE_UNSUPPORTED"}
```

Closed codes: `MANIFEST_LOAD_FAILED`, `MAPPING_FAILED`, `INVALID_ARGUMENTS`,
`INTERNAL_ERROR`. `side` is `baseline`, `target` or null. `mapping_code` is one of the
bridge's fixed catalogue values. No message, location, hint, selector, path, ID or value.
All fields are validated against closed vocabularies before output.

### 4.3 Failure precedence

1. Argument grammar (no manifest load).
2. Baseline load.
3. Target load.
4. Mapping (baseline/target as reported by the bridge).
5. Unexpected error.

For recognized `comparability` invocations, parser diagnostics are suppressed before
argparse writes them: capture/redirect stderr during parse, discard the captured text, and
emit the fixed `INVALID_ARGUMENTS` shape. `--help` and `--version` keep normal behavior.
This covers missing operands, missing option values, unknown options, extra operands,
option-like selectors and misplaced flags.

### 4.4 Text output

Fixed renderer built only from the validated envelope/projection vocabulary:
decision, application status, per-dimension designation/status, observation counts, reason
codes, core input-error codes, and fixed notice/disclaimer. No raw metadata values, names,
IDs, hashes, locators, paths or free-form exception text.

### 4.5 Privacy wording (user-facing)

> Experimental recorded-condition comparison. Use only manifests you are authorized to
> read, and disclose results only to authorized recipients. The CLI loads both complete
> manifests locally. Comparison examines the documented condition fields, selected result
> names, manifest-wide IDs and relevant relationships, and metadata on selected objects for
> nonfinite numbers. It does not dereference artifact paths, locators or URLs.
>
> Output omits raw evidence values, IDs, names, paths, hashes and metadata keys. It reveals
> condition presence, states, equality or difference, counts, display-name difference and
> fixed rejection categories. Repeated comparisons can reveal sensitive information. Output
> is not anonymous or safe for unrestricted publication.
>
> Trusted local use only. This does not cover hostile Python objects, constructed or mutated
> models, shared comparison services or cross-tenant use. Debuggers, traceback locals,
> process memory, command history, terminal capture, OS observations, timing and resource
> usage are outside the output privacy guarantee.

### 4.6 Disclaimer (user-facing)

> Experimental read-only CLI for a frozen prototype core. Preserved core integration flags
> are legacy core status, not current CLI availability. COMPARABLE_RECORDED_CONDITIONS means
> only that five mandatory recorded dimensions matched. It does not establish artifact
> integrity, causal equivalence, reproducibility, statistical equivalence or correct
> recording. Missing evidence, identical run-ID strings across manifests, unsupported shapes
> and bounds may prevent comparison. This is not production qualification.

## 5. Parser-pin refresh (Stage 2b only)

Adding the 24th route changes `cli.py`, the parser pin test, the staged-reader and
versioned-manifest route-set assertions, `READER_HASH`, README and dated docs notes. This is
an authorization change, not clerical repair. Proof required before refresh:

- old/new byte hashes and exact change inventory;
- exactly 24 unique routes in fixed order; removing `comparability` recovers the exact prior
  23-route sequence;
- all route registrations, dispatch paths and changed shared parser behavior reviewed;
- no new connection to staged readers, SQLite, private validation facades, adapters,
  scanners, subprocess, network or writers;
- exactly the intended LF/CRLF CLI representations, no widened acceptance;
- recomputed `READER_HASH` for the changed staged-reader **test** file;
- historical records and dated notes preserved;
- the bridge's blanket production-import prohibition replaced by a narrow allowlist
  (`run_comparability.py` in Stage 2a; `cli.py` in Stage 2b) with all other src modules
  forbidden;
- full acceptance counts, protected hashes/modes and negative-control evidence.

## 6. Test plan (Stage 2a module; Stage 2b adds parser/CLI cases)

Library/command module:

- Exact JSON key sets, nested shapes, enum vocabularies, ordering and strict serialization
  for success, core-rejected, every application error and text output.
- Privacy canaries in selected/ignored metadata keys and values, names, every ID kind,
  hashes, artifact paths, locators, URLs, relationship notes/endpoints, claims,
  `LoadedManifest.path` and `source_bytes`; Unicode, quotes, newlines and terminal control
  characters. Check stdout/stderr/logs/serialized/rendered/mapping-error/traceback.
- Load failures: missing, unreadable, directory operand, symlink loop, bad UTF-8, bad JSON
  with secret text, duplicate keys, schema errors with secret keys/values, deep nesting,
  injected unexpected exceptions. No raw loader text reaches output.
- Outcomes: comparable, different, insufficient, core-rejected, every mapping code,
  baseline/target precedence, both-fail precedence.
- Information-flow pins: display-name difference, unrelated nonfinite metadata, duplicate
  IDs outside the selected subgraph, whole-manifest loading.
- Identity/numbers: NaN/Infinity spellings/overflow, nonfinite result values irrelevant,
  large integers/floats around core limits, same run IDs -> sanitized rejection.
- Selection/semantics: multiple runs/results, exact selectors, duplicates, ownership
  conflicts, dataset/checkpoint multiplicity, optional vs mandatory differences.
- Library no-I/O: traps installed after imports/fixtures for file access, discovery,
  network/DNS/URL, subprocess, manifest reload, checks, writers; independent attempt counter
  must be zero; trapped positive case returns the exact expected envelope. Traps record
  before raising.
- Module boundary I/O: only the two manifest loads and necessary resolution; no artifact/
  locator access, directory scan, network, plugin, caches or writes. State that Python
  bytecode/atime effects are outside "no application writes".
- Mutation/determinism: unchanged inputs, repeated byte-identical output, permutations, no
  aliasing, fixed precedence.
- Negative controls: selected-value echo, ignored-value echo, ID/path echo, `str(load_error)`,
  raw parser diagnostic, detailed-result rendering, artifact read/stat; each must fail a
  named assertion.

Stage 2b adds parser/CLI cases:

- `main(argv)` and the real entry point; `--json` before/after operands and alongside invalid
  tokens; missing selectors/operands, unknown options, extra operands, option-like IDs,
  `--redact-manifest-path`, `--strict`, help/version; grammar failures must not load.
- Exactly one JSON document, no partial output; strict shape/exit codes; text vocabulary.
- Parser token-echoing negative control.
- Full pin-refresh proof from section 5.

## 7. Implementation staging

Stage 2a (next):
- New `src/runevidence/run_comparability.py` with the command outcome, sanitized loading,
  envelope, error shapes, text renderer and precedence. No `cli.py` or parser change.
- New `docs/comparability-privacy-contract.md` (this document's contract wording, or this
  file as the contract).
- Update the bridge import-boundary test to allow only `run_comparability.py`.
- Tests as section 6; no parser changes.

Stage 2b (after 2a acceptance):
- Register the route, sanitized argparse handling, pin refresh, README section, dated docs
  notes, parser/CLI tests, full acceptance.

## 8. Non-goals

- No 0444 core edits.
- No SQLite, directory scanner, adapters, network, service, multi-user API or remote use.
- No schema change.
- No production qualification or scientific-validity claim.
