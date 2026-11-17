# Platform support and adapter boundaries

RunEvidence and RunGraph are offline, file-only tools. This page records what is
verified on each platform and why some adapters fail closed elsewhere.

## Verified environment

- Python 3.10 or newer.
- CI runs Ubuntu and Windows on Python 3.10 and 3.14, Python 3.10 with the lowest declared
dependency versions, and macOS on Python 3.12 with the newest dependencies.
- macOS is verified on Python 3.12 only. Python 3.11 and 3.13 are not directly verified; they
  are not declared unsupported.
- The wheel smoke job builds all eight wheels, installs them into a clean venv
  and runs both console scripts.

## Adapter matrix

| Adapter | Linux | Windows | Notes |
| --- | --- | --- | --- |
| `generic` | yes | yes | Hashes files; no protected descriptor reads. |
| `hydra` | yes | yes | Uses the generic scanner plus YAML/JSON parsing. |
| `mlflow` | yes | yes | Local file store only; no tracking server. |
| `tensorboard` | yes | yes | Event reader is bounded; no TensorFlow needed. |
| `keras` | yes | **refused** | Needs POSIX directory-descriptor reads. |
| `lightning` | yes | **refused** | Needs POSIX directory-descriptor reads. |
| `sacred` | yes | **refused** | Needs POSIX directory-descriptor reads. |
| `wandb` | yes | **refused** | Needs POSIX directory-descriptor reads for local summary files. |

The three refusing adapters protect against a parent folder or file being
swapped for an outside link between discovery and the read. Python on Windows
does not expose the parent-relative descriptor APIs that close that race, so
the adapters refuse rather than use an unprotected fallback. This is a safety
boundary, not a missing dependency.

Alternatives on Windows:

- use `generic` when file hashes and run folders are enough;
- run the POSIX-only adapters on WSL/Linux and copy the resulting
  `runevidence.json` back;
- author results/conditions/claims with the CLI on any platform after a generic
  ingest.

`rungraph` itself works on Windows for workspace composition, validation,
reports and comparison. The CLI only rewrites the workspace document it is
given; it never rewrites a member manifest.

## Other declared bounds

- **Writer concurrency:** POSIX directory `flock` serializes built-in writers.
  Windows keeps the unlocked writer behavior with byte-level stale-write checks;
  no serialization guarantee is claimed there.
- **Directory artifacts:** read-only checking is Linux-only and authoring is
  Linux/ext4 only. Other filesystems fail closed; Windows is not claimed.
- **Memory/file bounds:** recursive scans stop at depth 64, 10,000 folders and
  1,000,000 files; structured readers have their own declared caps. Exceeding a
  cap refuses the scan instead of returning a partial result.

See [the completion contract](product-completion.md) and
[qualification record](qualification.md) for the evidence behind each bound.
