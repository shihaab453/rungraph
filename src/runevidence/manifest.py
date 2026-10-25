"""Reading and writing manifest files."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from .findings import Code, Finding, Severity
from .models import MANIFEST_FILENAME, Manifest


class ManifestLoadError(Exception):
    """Raised when a manifest cannot be found, parsed, or schema-validated."""

    def __init__(self, findings: list[Finding]):
        self.findings = findings
        super().__init__("; ".join(f.message for f in findings))


@dataclass(frozen=True)
class LoadedManifest:
    manifest: Manifest
    path: Path  # the manifest file itself

    @property
    def root(self) -> Path:
        """Folder that artifact paths are relative to: the manifest's folder."""
        return self.path.parent


def resolve_manifest_path(target: Path) -> Path:
    """Accept either a manifest file or a folder that contains one."""
    if target.is_dir():
        return target / MANIFEST_FILENAME
    return target


def load_manifest(target: Path) -> LoadedManifest:
    path = resolve_manifest_path(Path(target)).resolve()
    if not path.is_file():
        raise ManifestLoadError(
            [
                Finding(
                    code=Code.manifest_not_found,
                    severity=Severity.error,
                    message=f"No manifest at {path}",
                    path=str(path),
                    hint=f"Create one with: runevidence init {path.parent}",
                )
            ]
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestLoadError(
            [
                Finding(
                    code=Code.manifest_unreadable,
                    severity=Severity.error,
                    message=f"Could not read manifest as UTF-8 JSON: {exc}",
                    path=str(path),
                )
            ]
        ) from exc
    try:
        manifest = Manifest.model_validate(raw)
    except ValidationError as exc:
        raise ManifestLoadError(
            [
                Finding(
                    code=Code.schema_invalid,
                    severity=Severity.error,
                    message=f"{_loc(err['loc'], raw)}: {err['msg']}",
                    path=str(path),
                )
                for err in exc.errors()
            ]
        ) from exc
    return LoadedManifest(manifest=manifest, path=path)


def dump_manifest(manifest: Manifest) -> str:
    data = manifest.model_dump(mode="json", exclude_defaults=False)
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def save_manifest(manifest: Manifest, path: Path) -> None:
    # newline="\n" keeps the file byte-identical across Windows and POSIX.
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(dump_manifest(manifest))


def _loc(loc: tuple, raw: object = None) -> str:
    """Render a Pydantic error location; name the entry by its id when the
    raw data has one, e.g. ``artifacts[13] (id 'report').sha256``."""
    out = ""
    node = raw
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
            node = node[part] if isinstance(node, list) and part < len(node) else None
            if isinstance(node, dict) and isinstance(node.get("id"), str):
                out += f" (id {node['id']!r})"
        else:
            out += f".{part}" if out else str(part)
            node = node.get(part) if isinstance(node, dict) else None
    return out or "<root>"
