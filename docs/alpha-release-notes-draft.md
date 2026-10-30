# RunEvidence alpha release notes (draft)

**DRAFT. NOT PUBLISHED. NO VERSION OR RELEASE DECISION MADE.**

This describes a limited alpha, not a general availability release. It does
not claim fully verified metric freshness, scientific validity or a fair
comparison.

## What this limited alpha provides

- **Core:** manifest-based checks for missing, changed and moved files using
  raw-byte SHA-256; JSON locator checks for supported scalar values; graph
  checks for duplicate IDs, provenance, claims and relationships; and the
  `init`, `validate`, `inspect`, `rehash` and `add` commands. `rehash` and `add`
  preserve other hand-written manifest content and use a final compare/replace
  stale-write check. `init --force` regenerates rather than merging content.
- **Hydra adapter:** discovers runs, params, overrides and configuration under
  `.hydra/`. Hydra stores no metrics, so the adapter produces no metric
  results.
- **MLflow adapter:** reads a local MLflow file store only. It records
  experiments, runs, string params, hashed artifacts and one summary result per
  readable metric history. It does not provide full metric freshness.

## Owner acceptance

The owner accepts only the disclosed RE-10 limited-alpha limitation below.
That acceptance does not authorize publication, a version or tag, a package
index release, or any claim that metric freshness is fully verified.

## RE-10: MLflow metric summaries can stay stale

For MLflow text metric results, `rehash` accepts the current source bytes and
digest, but the recorded summary **value, step and timestamp may stay old**. A
later `validate --strict` can exit 0 after that acceptance. Changed bytes do
not always mean the summary is stale.

To refresh, run `init PATH --adapter mlflow --force` and follow the
[central safe refresh procedure](adapters.md#refreshing-mlflow-results-safely-re-10).
Forced regeneration keeps backup generations (`.bak`, `.bak.2`, `.bak.3`),
but it does not merge hand-written claims, relationships, annotations, volatile
flags, or project fields and settings. Compare the backup with the regenerated
manifest, retain newly extracted summaries, and manually restore only reviewed
annotations and settings after verifying IDs and claims. Do not copy stale
metric results back. Version control and separate backups are recommended.

## Fixed in this draft (unreleased)

- **R5-01:** comparing a recorded value with an integer too large to convert
  to a float no longer stops with an `OverflowError`; such integers are
  compared exactly. Other numbers keep the existing tolerance.
- **Manifest parsing:** a manifest number longer than Python's integer digit
  limit is reported as `manifest-unreadable` (exit 2) instead of a traceback.
- **CLI wording:** `rehash` prints a `NOTE` for each locator-less MLflow
  summary whose file it re-records or whose file is volatile, and `--help` states
  that a clean result shows consistency with the manifest, not truth, validity
  or freshness.

These are on the development branch only; nothing here is released.

## Known limitations

- **RE-10:** locator-less MLflow metric summaries are not refreshed by
  `rehash`, and strict validation does not check their freshness. The
  `rehash` NOTE says a summary was not re-read; it does not show that the
  summary is stale.
- **ML-1, MLflow metric memory:** the MLflow adapter reads each metric history
  file completely into memory, with no size cap, so a very large metric log
  can use a lot of memory during `init`.
- **Integer digit limit:** Python's configurable integer digit limit applies
  to JSON numbers. A longer number makes a manifest `manifest-unreadable`, and
  makes a locator-backed result `result-unverifiable` (a warning). Whether the
  schema accepts very large integers as result values on every supported
  Pydantic version has not been separately verified.
- **One writer at a time:** the final manifest compare/replace is a
  stale-write check, not a lock. Another writer can race between the final
  comparison and replacement. `init --force` has no such stale-write check
  either. Use one writer per folder for every writing command.
- **Deeply nested JSON:** a sufficiently deep JSON manifest or metrics file
  can still raise `RecursionError` instead of producing a controlled
  diagnostic.
- **Unverified environments:** Python 3.14 with pinned minimum dependencies,
  Python 3.11 to 3.13, and macOS have not been directly verified. They are not
  declared unsupported.
- **Validation scope:** `validate` checks consistency subject to the configured
  digests, volatile flags, locators and links. `--strict` only makes warnings
  fail. Neither mode establishes truth, scientific validity, a fair comparison,
  or freshness for locator-less results.

Compatibility evidence covers six CI jobs: Ubuntu and Windows, each with
Python 3.10 plus latest dependencies, Python 3.14 plus latest dependencies,
and Python 3.10 plus pinned minimum dependencies (Pydantic 2.6.0, PyYAML 6.0).
It does not establish the unverified combinations listed above.

## Not decided here

No version, tag, release date, package-index availability or publication
decision is made by this draft.
