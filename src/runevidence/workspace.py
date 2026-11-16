"""The RunGraph workspace: a versioned document over several manifests.

This module owns the document format, read-only discovery and the validated
member set. It never changes a member manifest and never runs checks.
"""

from __future__ import annotations

import json
import os
import re
import stat

from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .adapters.generic import valid_slug
from .manifest import ManifestLoadError, load_manifest
from .models import ID_PATTERN, MANIFEST_FILENAME, Manifest, RelationshipType

WORKSPACE_FILENAME = "rungraph.json"
WORKSPACE_FORMAT = "rungraph-workspace"
WORKSPACE_VERSION = "0.1"
MAX_WORKSPACE_BYTES = 16 * 1024 * 1024
MAX_MEMBERS = 10_000
MAX_CLAIMS = 10_000
MAX_RELATIONSHIPS = 10_000
MAX_REFS = 1_024
MAX_DISCOVERY_MANIFESTS = 10_000
MAX_DEPTH = 64
MEMBER_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"

IGNORED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        "node_modules",
        ".tox",
        "build",
        "dist",
    }
)


class WorkspaceError(ValueError):
    """The workspace document or one of its members is unusable."""


def _is_case_insensitive() -> bool:
    return os.path.normcase("A") == os.path.normcase("a")


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise WorkspaceError(f"duplicate JSON key {key!r}")
        out[key] = value
    return out


def _no_constants(text: str) -> None:
    raise WorkspaceError(f"non-finite JSON number {text!r} is not allowed")


def _check_member_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(MEMBER_ID_PATTERN, value):
        raise ValueError("member ID must start with a letter or digit and contain only A-Z a-z 0-9 . _ -")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Ref(_Strict):
    """A qualified member reference or a workspace-claim reference."""

    member: Optional[str] = None
    id: Optional[str] = None
    claim: Optional[str] = None

    @model_validator(mode="after")
    def _one_form(self) -> "Ref":
        if self.claim is not None:
            if self.member is not None or self.id is not None:
                raise ValueError("a ref is either {member, id} or {claim}, not both")
            if not re.fullmatch(MEMBER_ID_PATTERN, self.claim):
                raise ValueError("claim reference must match the claim ID pattern")
            return self
        if self.member is None or self.id is None:
            raise ValueError("a member ref needs both member and id")
        _check_member_id(self.member)
        if not re.fullmatch(ID_PATTERN, self.id):
            raise ValueError("local ID does not match the manifest ID pattern")
        return self

    def text(self) -> str:
        return f"claim:{self.claim}" if self.claim is not None else f"{self.member}#{self.id}"


class Member(_Strict):
    id: str
    path: str
    manifest: str = MANIFEST_FILENAME
    role: Optional[str] = None

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        return _check_member_id(value)

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError("member path must be non-empty")
        if value == ".":
            return value
        if "\\" in value or value.startswith("/"):
            raise ValueError("member path must be relative and use forward slashes")
        parts = PurePosixPath(value).parts
        if not parts or any(part in ("", ".", "..") for part in parts):
            raise ValueError("member path may not contain '.', '..' or empty components")
        return value

    @field_validator("manifest")
    @classmethod
    def _manifest(cls, value: str) -> str:
        if (
            not isinstance(value, str)
            or not value
            or value in (".", "..")
            or "/" in value
            or "\\" in value
            or not value.endswith(".json")
        ):
            raise ValueError("manifest must be one .json file name without separators")
        return value

    @field_validator("role")
    @classmethod
    def _role(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and len(value) > 128:
            raise ValueError("role is longer than 128 characters")
        return value


class Claim(_Strict):
    id: str
    claim: str
    supports: list[Ref] = Field(default_factory=list)
    contradicts: list[Ref] = Field(default_factory=list)
    source: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        return _check_member_id(value)

    @field_validator("claim")
    @classmethod
    def _claim(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 64 * 1024:
            raise ValueError("claim text must be non-blank and at most 64 KiB")
        return value

    @field_validator("supports", "contradicts")
    @classmethod
    def _refs(cls, value: list[Ref]) -> list[Ref]:
        if len(value) > MAX_REFS:
            raise ValueError(f"at most {MAX_REFS} refs are allowed")
        return value

    @field_validator("source")
    @classmethod
    def _source(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and len(value) > 4096:
            raise ValueError("source is longer than 4 KiB")
        return value


class Relationship(_Strict):
    id: str
    type: RelationshipType
    source: Ref
    target: Ref
    note: Optional[str] = None

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        return _check_member_id(value)

    @field_validator("type")
    @classmethod
    def _type(cls, value: RelationshipType) -> RelationshipType:
        if value in (RelationshipType.supports, RelationshipType.contradicts):
            raise ValueError("claims are the only claim-citation mechanism")
        return value

    @field_validator("note")
    @classmethod
    def _note(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and len(value) > 4096:
            raise ValueError("note is longer than 4 KiB")
        return value


class Workspace(_Strict):
    format: Literal["rungraph-workspace"]
    version: Literal["0.1"]
    id: str
    name: str
    description: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    members: list[Member]
    claims: list[Claim] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        return _check_member_id(value)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 512:
            raise ValueError("workspace name must be non-blank and at most 512 characters")
        return value

    @model_validator(mode="after")
    def _counts(self) -> "Workspace":
        if not self.members:
            raise ValueError("a workspace needs at least one member")
        if len(self.members) > MAX_MEMBERS:
            raise ValueError(f"at most {MAX_MEMBERS} members are allowed")
        if len(self.claims) > MAX_CLAIMS:
            raise ValueError(f"at most {MAX_CLAIMS} claims are allowed")
        if len(self.relationships) > MAX_RELATIONSHIPS:
            raise ValueError(f"at most {MAX_RELATIONSHIPS} relationships are allowed")
        member_ids = [m.id for m in self.members]
        if len(set(member_ids)) != len(member_ids):
            raise ValueError("member IDs must be unique")
        claim_ids = [c.id for c in self.claims]
        if len(set(claim_ids)) != len(claim_ids):
            raise ValueError("workspace claim IDs must be unique")
        rel_ids = [r.id for r in self.relationships]
        if len(set(rel_ids)) != len(rel_ids):
            raise ValueError("relationship IDs must be unique")
        return self


@dataclass
class LoadedMember:
    member: Member
    folder: Path
    manifest_path: Path
    manifest: Optional[Manifest] = None
    error: Optional[str] = None


@dataclass
class LoadedWorkspace:
    root: Path
    path: Path
    document: Workspace
    members: dict[str, LoadedMember] = field(default_factory=dict)


def _member_folder(root: Path, member: Member) -> Path:
    folder = root.joinpath(*PurePosixPath(member.path).parts)
    if not folder.is_dir():
        raise WorkspaceError(f"member {member.id!r}: folder {member.path!r} does not exist")
    return folder


def _manifest_path(folder: Path, member: Member) -> Path:
    path = folder / member.manifest
    try:
        info = os.lstat(path)
    except OSError:
        raise WorkspaceError(f"member {member.id!r}: manifest {member.manifest!r} does not exist") from None
    if stat.S_ISLNK(info.st_mode):
        raise WorkspaceError(f"member {member.id!r}: manifest {member.manifest!r} is a symlink")
    if not stat.S_ISREG(info.st_mode):
        raise WorkspaceError(f"member {member.id!r}: manifest {member.manifest!r} is not a regular file")
    return path


def _resolve_members(root: Path, document: Workspace) -> dict[str, LoadedMember]:
    loaded: dict[str, LoadedMember] = {}
    seen_files: dict[tuple[int, int], str] = {}
    case_insensitive = _is_case_insensitive()
    paths: dict[str, str] = {}
    for member in document.members:
        folder = _member_folder(root, member)
        manifest_path = _manifest_path(folder, member)
        info = os.lstat(manifest_path)
        key = (info.st_dev, info.st_ino)
        if key in seen_files:
            raise WorkspaceError(
                f"members {seen_files[key]!r} and {member.id!r} resolve to the same manifest"
            )
        seen_files[key] = member.id
        normalized = folder.resolve().as_posix()
        if case_insensitive:
            normalized = normalized.casefold()
        if normalized in paths:
            raise WorkspaceError(f"members {paths[normalized]!r} and {member.id!r} resolve to the same folder")
        paths[normalized] = member.id
        loaded[member.id] = LoadedMember(member=member, folder=folder, manifest_path=manifest_path)
    # A member nested inside another member is refused.
    for outer in document.members:
        outer_path = (root / outer.path).resolve()
        for inner in document.members:
            if inner is outer:
                continue
            inner_path = (root / inner.path).resolve()
            if inner_path != outer_path and outer_path in inner_path.parents:
                raise WorkspaceError(f"member {inner.id!r} is nested inside member {outer.id!r}")
    return loaded


def load_workspace(target: Path) -> LoadedWorkspace:
    root = target if target.is_dir() else target.parent
    path = root / WORKSPACE_FILENAME if target.is_dir() else target
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise WorkspaceError(f"cannot read {path}: {exc.strerror or exc}") from None
    if len(data) > MAX_WORKSPACE_BYTES:
        raise WorkspaceError(f"workspace document is larger than {MAX_WORKSPACE_BYTES} bytes")
    try:
        raw = json.loads(data.decode("utf-8"), object_pairs_hook=_no_duplicate_keys, parse_constant=_no_constants)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkspaceError(f"workspace document is not valid JSON: {exc}") from None
    try:
        document = Workspace.model_validate(raw)
    except Exception as exc:
        raise WorkspaceError(f"invalid workspace document: {exc}") from None
    loaded = _resolve_members(root, document)
    for item in loaded.values():
        try:
            item.manifest = load_manifest(item.folder).manifest
        except (ManifestLoadError, OSError, ValueError) as exc:
            item.error = str(exc)
    return LoadedWorkspace(root=root, path=path, document=document, members=loaded)


@dataclass
class DiscoveredMember:
    path: str
    id: str
    stable: bool
    loads: bool
    schema_version: Optional[str] = None
    error: Optional[str] = None
    duplicate: bool = False


@dataclass
class Discovery:
    root: Path
    members: list[DiscoveredMember] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return 2 if self.errors else 0


def _iter_manifest_folders(root: Path) -> list[Path]:
    found: list[Path] = []
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        folder, depth = stack.pop()
        if depth > MAX_DEPTH:
            raise WorkspaceError(f"discovery depth exceeds {MAX_DEPTH}")
        try:
            entries = sorted(os.scandir(folder), key=lambda entry: entry.name)
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir(follow_symlinks=False):
                continue
            if entry.name in IGNORED_DIRS:
                continue
            path = Path(entry.path)
            try:
                if stat.S_ISLNK(os.lstat(path).st_mode):
                    continue
            except OSError:
                continue
            stack.append((path, depth + 1))
        if (folder / MANIFEST_FILENAME).is_file():
            found.append(folder)
        if len(found) > MAX_DISCOVERY_MANIFESTS:
            raise WorkspaceError(f"more than {MAX_DISCOVERY_MANIFESTS} manifests found")
    return sorted(found, key=lambda item: item.relative_to(root).as_posix())


def discover(root: Path) -> Discovery:
    root = root.resolve()
    result = Discovery(root=root)
    if not root.is_dir():
        result.errors.append(f"{root} is not a folder")
        return result
    document: Optional[Workspace] = None
    declared: dict[str, Member] = {}
    if (root / WORKSPACE_FILENAME).exists():
        try:
            document = load_workspace(root).document
            declared = {m.path: m for m in document.members}
        except WorkspaceError as exc:
            result.errors.append(str(exc))
            return result
    try:
        folders = _iter_manifest_folders(root)
    except WorkspaceError as exc:
        result.errors.append(str(exc))
        return result
    # Include declared members even under ignored folders.
    for member in declared.values():
        folder = root / member.path
        if folder.is_dir() and folder not in folders and not _inside_ignored(folder, root):
            folders.append(folder)
        elif folder.is_dir() and _inside_ignored(folder, root):
            folders.append(folder)
    folders = sorted(set(folders), key=lambda item: item.relative_to(root).as_posix())
    if not folders:
        result.errors.append("no manifests found")
        return result
    used: set[str] = set()
    by_folder: dict[Path, str] = {}
    for folder in folders:
        rel = folder.relative_to(root).as_posix()
        member = declared.get(rel)
        if member is not None:
            member_id = member.id
            stable = True
        else:
            base = "prov-root" if rel == "." else f"prov-{valid_slug(rel)}"
            member_id = base
            suffix = 2
            while member_id in used:
                member_id = f"{base}-{suffix}"
                suffix += 1
            stable = False
        used.add(member_id)
        by_folder[folder] = member_id
        entry = DiscoveredMember(path=rel, id=member_id, stable=stable, loads=False)
        try:
            loaded = load_manifest(folder)
            entry.loads = True
            entry.schema_version = loaded.manifest.schema_version
        except (ManifestLoadError, OSError, ValueError) as exc:
            entry.error = str(exc)
            result.errors.append(f"{rel}: {exc}")
        result.members.append(entry)
    seen: dict[tuple[int, int], str] = {}
    for folder in folders:
        manifest = folder / MANIFEST_FILENAME
        try:
            info = os.lstat(manifest)
        except OSError:
            continue
        key = (info.st_dev, info.st_ino)
        if key in seen:
            by_folder[folder] = by_folder[folder]
            for entry in result.members:
                if entry.path == folder.relative_to(root).as_posix():
                    entry.duplicate = True
            result.errors.append(f"duplicate manifest: {seen[key]!r} and {folder.relative_to(root).as_posix()!r}")
        else:
            seen[key] = folder.relative_to(root).as_posix()
    # Nested member folders.
    resolved = {folder.resolve(): folder for folder in folders}
    for outer in resolved:
        for inner in resolved:
            if inner != outer and outer in inner.parents:
                result.errors.append(
                    f"nested manifest: {resolved[inner].relative_to(root).as_posix()!r} is inside "
                    f"{resolved[outer].relative_to(root).as_posix()!r}"
                )
    return result


def _inside_ignored(folder: Path, root: Path) -> bool:
    try:
        parts = folder.relative_to(root).parts
    except ValueError:
        return False
    return any(part in IGNORED_DIRS for part in parts)


# --- authoring --------------------------------------------------------------


def canonical_document(document: Workspace) -> bytes:
    return (json.dumps(document.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n").encode("utf-8")


@contextmanager
def workspace_lock(root: Path):
    try:
        import fcntl
    except ImportError:
        yield
        return
    if os.name == "nt":
        yield
        return
    try:
        fd = os.open(os.fspath(root), os.O_RDONLY)
    except OSError as exc:
        raise WorkspaceError(f"cannot lock {root}: {exc.strerror or exc}") from None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise WorkspaceError("BUSY: another writer holds the workspace") from None
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def save_workspace(document: Workspace, path: Path, expected_bytes: Optional[bytes]) -> None:
    data = canonical_document(document)
    if expected_bytes is not None:
        try:
            current = path.read_bytes()
        except OSError:
            current = b""
        if current != expected_bytes:
            raise WorkspaceError("stale workspace document; retry")
    staging = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with staging.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging, path)
    finally:
        try:
            staging.unlink()
        except OSError:
            pass


def _patch_error(message: str) -> WorkspaceError:
    return WorkspaceError(message)


def _apply_operation(raw: dict[str, Any], operation: dict[str, Any]) -> None:
    kind = operation.get("op")
    if kind == "set-workspace":
        for key in ("id", "name", "description", "metadata"):
            if key in operation:
                raw[key] = operation[key]
        return
    if kind == "add-member":
        raw.setdefault("members", []).append(operation["member"])
        return
    if kind in ("remove-member", "move-member", "rename-member"):
        members = raw.setdefault("members", [])
        index = next((i for i, member in enumerate(members) if member.get("id") == operation.get("id")), None)
        if index is None:
            raise _patch_error(f"unknown member {operation.get('id')!r}")
        if kind == "remove-member":
            members.pop(index)
        elif kind == "move-member":
            members[index]["path"] = operation["path"]
        else:
            new_id = operation["new_id"]
            old_id = members[index]["id"]
            members[index]["id"] = new_id
            for claim in raw.setdefault("claims", []):
                for ref in list(claim.get("supports", [])) + list(claim.get("contradicts", [])):
                    if ref.get("member") == old_id:
                        ref["member"] = new_id
            for relationship in raw.setdefault("relationships", []):
                for side in ("source", "target"):
                    if relationship.get(side, {}).get("member") == old_id:
                        relationship[side]["member"] = new_id
        return
    if kind == "add-claim":
        raw.setdefault("claims", []).append(operation["claim"])
        return
    if kind in ("edit-claim", "remove-claim"):
        claims = raw.setdefault("claims", [])
        index = next((i for i, claim in enumerate(claims) if claim.get("id") == operation.get("id")), None)
        if index is None:
            raise _patch_error(f"unknown claim {operation.get('id')!r}")
        if kind == "remove-claim":
            claims.pop(index)
        else:
            patch = operation["claim"]
            for key, value in patch.items():
                if key != "id":
                    claims[index][key] = value
        return
    if kind == "add-relationship":
        raw.setdefault("relationships", []).append(operation["relationship"])
        return
    if kind == "remove-relationship":
        relationships = raw.setdefault("relationships", [])
        index = next((i for i, item in enumerate(relationships) if item.get("id") == operation.get("id")), None)
        if index is None:
            raise _patch_error(f"unknown relationship {operation.get('id')!r}")
        relationships.pop(index)
        return
    raise _patch_error(f"unknown patch operation {kind!r}")


def _manifest_index(manifest: Manifest) -> set[str]:
    ids = {manifest.project.id}
    ids |= {item.id for item in manifest.runs}
    ids |= {item.id for item in manifest.artifacts}
    ids |= {item.id for item in manifest.results}
    ids |= {item.id for item in manifest.evidence}
    return ids


def _validate_refs(loaded_members: dict[str, LoadedMember], document: Workspace) -> None:
    claim_ids = {claim.id for claim in document.claims}
    indexes = {}
    for member_id, item in loaded_members.items():
        if item.manifest is None:
            raise _patch_error(f"member {member_id!r} has no readable manifest")
        indexes[member_id] = _manifest_index(item.manifest)
    for claim in document.claims:
        for ref in claim.supports + claim.contradicts:
            if ref.claim is not None:
                if ref.claim not in claim_ids:
                    raise _patch_error(f"claim {claim.id!r}: unknown workspace claim {ref.claim!r}")
            else:
                if ref.member not in indexes:
                    raise _patch_error(f"claim {claim.id!r}: unknown member {ref.member!r}")
                if ref.id not in indexes[ref.member]:
                    raise _patch_error(f"claim {claim.id!r}: {ref.member}#{ref.id} does not resolve")
    for relationship in document.relationships:
        for ref in (relationship.source, relationship.target):
            if ref.claim is not None:
                if ref.claim not in claim_ids:
                    raise _patch_error(f"relationship {relationship.id!r}: unknown claim {ref.claim!r}")
            else:
                if ref.member not in indexes:
                    raise _patch_error(f"relationship {relationship.id!r}: unknown member {ref.member!r}")
                if ref.id not in indexes[ref.member]:
                    raise _patch_error(f"relationship {relationship.id!r}: {ref.member}#{ref.id} does not resolve")


def apply_patch(
    loaded: LoadedWorkspace,
    patch: dict[str, Any],
    *,
    expected_sha256: Optional[str] = None,
    dry_run: bool = False,
) -> Workspace:
    if patch.get("format") != "rungraph-patch" or patch.get("version") != "0.1":
        raise _patch_error("patch must be a rungraph-patch 0.1 document")
    operations = patch.get("operations")
    if not isinstance(operations, list) or not operations:
        raise _patch_error("patch needs a non-empty operations list")
    current = loaded.path.read_bytes()
    expected = expected_sha256 or patch.get("expected_sha256")
    if expected is None:
        raise _patch_error("expected_sha256 is required when the workspace document exists")
    import hashlib

    if hashlib.sha256(current).hexdigest() != expected:
        raise _patch_error("stale workspace document; retry")
    raw = loaded.document.model_dump(mode="json")
    for operation in operations:
        if not isinstance(operation, dict):
            raise _patch_error("each operation must be an object")
        _apply_operation(raw, operation)
    try:
        document = Workspace.model_validate(raw)
    except Exception as exc:
        raise _patch_error(f"invalid workspace after patch: {exc}") from None
    members = _resolve_members(loaded.root, document)
    from .manifest import ManifestLoadError, load_manifest

    for member_id, item in members.items():
        try:
            item.manifest = load_manifest(item.folder).manifest
        except (ManifestLoadError, OSError, ValueError) as exc:
            raise _patch_error(f"member {member_id!r}: {exc}") from None
    _validate_refs(members, document)
    if not dry_run:
        with workspace_lock(loaded.root):
            if loaded.path.read_bytes() != current:
                raise _patch_error("stale workspace document; retry")
            save_workspace(document, loaded.path, current)
    return document
