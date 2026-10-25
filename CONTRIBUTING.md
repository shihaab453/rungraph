# Contributing

Thanks for helping. RunEvidence aims to stay small, offline and
domain-agnostic, so a few ground rules keep it that way.

## Set up

```bash
python -m venv .venv
# activate it (Windows: .venv\Scripts\Activate.ps1, macOS/Linux: source .venv/bin/activate)
pip install -e ".[dev]"
pip install -e ./packages/runevidence-hydra   # adapter package; its tests skip without it
pip install -e ./packages/runevidence-mlflow  # local MLflow file-store adapter
python -m pytest
```

## Continuous integration

Every push and pull request runs the test suite on GitHub Actions
([.github/workflows/ci.yml](.github/workflows/ci.yml)): Linux and Windows,
each on Python 3.10 (the oldest version we support) and 3.14. A pull request
should be green on all four before it is merged.

CI does a regular (not editable) install into a fresh environment. To check
a change the same way before pushing:

```bash
python -m venv /tmp/ci-venv          # any new, empty folder
/tmp/ci-venv/bin/python -m pip install ".[dev]"   # Windows: \tmp\ci-venv\Scripts\python.exe
/tmp/ci-venv/bin/python -m pip install ./packages/runevidence-hydra ./packages/runevidence-mlflow
/tmp/ci-venv/bin/python -m pytest
```

On Linux and macOS one test is skipped: it checks a wrong-case path, which
only resolves on a case-insensitive filesystem such as Windows.

## Ground rules

- **No domain vocabulary in core.** `src/runevidence` must work for any ML
  field. Tool- or domain-specific logic belongs in an adapter package
  ([docs/adapters.md](docs/adapters.md)).
- **Never execute or deserialize experiment files.** Hash bytes, parse JSON.
- **Offline.** No network calls anywhere, including tests.
- **Finding codes are a public API.** Never rename or repurpose one; add a new
  code instead, and document it in the README table.
- **Schema changes** go through `src/runevidence/models.py`; then regenerate
  the committed schema with `runevidence schema -o schema/runevidence.schema.json`
  (a test fails if you forget).
- **Fixtures are byte-exact.** Files under `examples/` and `tests/data/` are
  marked `-text` in `.gitattributes` so line endings never change their hashes.
  If you edit one, re-record it with `runevidence rehash examples/<name> <artifact-id>`.
- **Tests must pass on Windows, macOS and Linux.** Use `pathlib`, and
  `tmp_path` for anything you write.

## Good first contributions

- An adapter for a tool you use (as a separate package).
- Directory artifacts (hash a folder as a whole).
- A `remove` command, so `rehash --all` is not blocked by a file you deleted
  on purpose.
