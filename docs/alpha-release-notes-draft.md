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
  readable metric history. On schema 0.1/0.2 it verifies and refreshes those
  summaries against the metric files.

## Limited-alpha scope

This describes a limited alpha, not a general availability release. It does
not claim scientific validity or a fair comparison. RE-10 and ML-1 are fixed
in this draft; the remaining limitations are listed below.

## Fixed in this draft (unreleased)

- **RE-10, MLflow summary freshness:** on schema 0.1/0.2, `validate` re-reads a
  locator-less metric summary and compares value, step and timestamp exactly;
  `rehash` refreshes it from the accepted bytes and repairs a summary that
  already disagrees with an unchanged file. A missing adapter, a volatile
  metric file, an unreadable history and an ambiguous owner are reported
  instead of passing silently. Schemas 0.3–0.10 keep their existing
  `result-unverified` warning.
- **ML-1, MLflow metric memory:** metric histories are read under 50 MiB,
  1,000,000 lines and 64 KiB per line. Over-limit or malformed histories are
  recorded with a reason and produce a validation warning.
- **R5-01:** comparing a recorded value with an integer too large to convert
  to a float no longer stops with an `OverflowError`; such integers are
  compared exactly. Other numbers keep the existing tolerance.
- **Manifest parsing:** a manifest number longer than Python's integer digit
  limit is reported as `manifest-unreadable` (exit 2) instead of a traceback.
- **CLI wording:** `rehash` prints a `NOTE` for each locator-less MLflow
  summary it leaves unrefreshed, and `--help` states that a clean result shows
  consistency with the manifest, not truth, validity or freshness.

These are on the development branch only; nothing here is released.

## Known limitations

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
- **Migrated 0.3+ MLflow manifests:** a locator-less metric result keeps its
  existing `result-unverified` warning and is not refreshed by `rehash`; use
  `init --force` or the metric's source file to re-record it.
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
