"""Reading and writing manifest files."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from .findings import Code, Finding, Severity
from .models import MANIFEST_FILENAME, Artifact, Manifest


class ManifestLoadError(Exception):
    """Raised when a manifest cannot be found, parsed, or schema-validated."""

    def __init__(self, findings: list[Finding]):
        self.findings = findings
        super().__init__("; ".join(f.message for f in findings))


@dataclass(frozen=True)
class LoadedManifest:
    manifest: Manifest
    path: Path  # the manifest file itself
    source_bytes: bytes  # the exact snapshot that was parsed and validated

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
        source_bytes = path.read_bytes()
        raw = json.loads(source_bytes.decode("utf-8"))
    except (OSError, ValueError) as exc:
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
    return LoadedManifest(manifest=manifest, path=path, source_bytes=source_bytes)


def artifact_to_dict(artifact: Artifact) -> dict:
    data = artifact.model_dump(mode="json", exclude_defaults=False)
    # Written only when set, so manifests from before the field existed
    # come out of a rewrite unchanged.
    if not data["volatile"]:
        del data["volatile"]
    return data


def dump_manifest(manifest: Manifest) -> str:
    data = manifest.model_dump(mode="json", exclude_defaults=False)
    data["artifacts"] = [artifact_to_dict(a) for a in manifest.artifacts]
    return dump_raw(data)


def dump_raw(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def save_manifest(manifest: Manifest, path: Path) -> None:
    """Write a whole manifest. An existing file is replaced only after the
    complete new text is staged beside it, so a failed write leaves the old
    manifest intact instead of truncated. A new file that fails part-way is
    removed, so no truncated manifest is left behind."""
    text = dump_manifest(manifest)
    if not path.exists():
        try:
            _write(path, text)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return
    staged = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as fh:
            staged = Path(fh.name)
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if os.name != "nt":
            os.chmod(staged, stat.S_IMODE(path.stat().st_mode))
        os.replace(staged, path)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def load_raw(loaded: LoadedManifest) -> dict:
    """Editable JSON from the validated snapshot, without a second disk read."""
    return json.loads(loaded.source_bytes.decode("utf-8"))


def save_raw(data: dict, path: Path, *, expected_bytes: bytes) -> None:
    """Reject a stale snapshot immediately before replacing the manifest.

    This byte comparison is not an atomic compare-and-swap: another writer
    can still change the file between the comparison and os.replace.
    """
    # Stage a complete edit beside the manifest before replacing it, so a
    # failed write cannot truncate the user's existing evidence record.
    text = dump_raw(data)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as fh:
            staged = Path(fh.name)
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if os.name != "nt":
            os.chmod(staged, stat.S_IMODE(path.stat().st_mode))
        if path.read_bytes() != expected_bytes:
            raise OSError("Manifest changed since it was loaded. Review the current manifest and retry.")
        os.replace(staged, path)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def _write(path: Path, text: str) -> None:
    # newline="\n" keeps the file byte-identical across Windows and POSIX.
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


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
