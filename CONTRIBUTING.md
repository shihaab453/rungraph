# Contributing

Thanks for helping. RunEvidence aims to stay small, offline and
domain-agnostic, so a few ground rules keep it that way.

## Set up

```bash
python -m venv .venv
# activate it (Windows: .venv\Scripts\Activate.ps1, macOS/Linux: source .venv/bin/activate)
pip install -e ".[dev]"
python -m pytest
```

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
  If you edit one, update its `sha256` and `size_bytes` in the manifest.
- **Tests must pass on Windows, macOS and Linux.** Use `pathlib`, and
  `tmp_path` for anything you write.

## Good first contributions

- An adapter for a tool you use (as a separate package).
- Directory artifacts (hash a folder as a whole).
- A `rehash` command that re-records digests for intended changes.
