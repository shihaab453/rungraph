"""Read folders written by Hydra (https://hydra.cc) into a RunEvidence manifest.

Hydra gives every job its own output folder and saves the job's settings in
a ``.hydra/`` subfolder inside it. With Hydra's defaults that looks like::

    outputs/<date>/<time>/                 one run    (python app.py)
    multirun/<date>/<time>/multirun.yaml   one sweep  (python app.py -m ...)
    multirun/<date>/<time>/<n>/            one folder per sweep job
        .hydra/config.yaml                 the job's config after composition and overrides
        .hydra/hydra.yaml                  Hydra's own settings (job name and number, mode, version)
        .hydra/overrides.yaml              command-line overrides, e.g. ["optimizer=adam"]
        <job name>.log

Users can move these folders (``hydra.run.dir``, ``hydra.sweep.dir``), so
nothing here depends on ``outputs/`` or ``multirun/``: any folder that holds a
``.hydra/`` subfolder is a run, at any depth.

What the manifest gets:

* every file as an artifact (sha256 and size), like the generic adapter;
* one run per Hydra job folder, owning the files under it;
* ``params``: the job's composed config from ``.hydra/config.yaml``;
* ``metadata["hydra"]``: overrides, config-group choices, job name and
  number, mode, Hydra version and, for sweep jobs, the sweep folder.

What it leaves alone: Hydra records no metrics, so no results are created,
and claims are for people to write. Absolute paths from ``hydra.yaml``
(``cwd``, ``output_dir``) are never copied, so the manifest stays portable.

YAML is read with ``yaml.safe_load`` only: tags such as ``!!python/object``
are refused, never executed. A file that cannot be read is still hashed and
is listed under ``metadata["hydra"]["unreadable"]``, with a short reason in
``metadata["hydra"]["unreadable_reasons"]``.

YAML anchors and aliases let a few hundred bytes describe a structure with
billions of values, and an alias may even contain itself. Every parsed file
is therefore converted to plain JSON data without recursion and under fixed
limits (``MAX_VALUES``, ``MAX_TEXT``, ``MAX_DEPTH``), counted on the expanded
result. Conversion stops as soon as a limit is passed, before the large
structure is built, and the file is treated as unreadable. A self-referencing
alias is reported the same way instead of raising ``RecursionError``.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Optional

import yaml
from runevidence.adapters import Adapter, AdapterError, Discovery, GenericAdapter
from runevidence.adapters.generic import slug, valid_slug
from runevidence.hashing import sha256_file
from runevidence.models import Artifact, Manifest, Project, Run

HYDRA_DIR = ".hydra"
SWEEP_FILE = "multirun.yaml"
# Hydra writes YAML. The JSON names cover scripts that imitate Hydra's layout.
CONFIG_FILES = ("config.yaml", "config.json")
OVERRIDES_FILES = ("overrides.yaml", "overrides.json")
SETTINGS_FILE = "hydra.yaml"
# OmegaConf's marker for "no value", e.g. ``num: ???`` outside a sweep.
MISSING = "???"
# Hydra's default folder names: <YYYY-MM-DD>/<HH-MM-SS>.
DEFAULT_TIMESTAMP = re.compile(r"(?:^|/)(\d{4}-\d{2}-\d{2})/(\d{2}-\d{2}-\d{2})(?:/|$)")

# Limits for one parsed .hydra file, counted after YAML aliases are expanded.
# Real Hydra configs are a few hundred values; these leave a wide margin.
MAX_VALUES = 100_000  # mappings, lists and scalars
MAX_TEXT = 2_000_000  # characters in keys and string values (and digits of integers)
MAX_DEPTH = 64  # nesting levels


class HydraAdapter(Adapter):
    name = "hydra"
    description = "Hydra output folders: every folder holding .hydra/ is a run, with its config as params."

    def discover(self, root: Path) -> Discovery:
        files = GenericAdapter().discover(root).files
        runs = sorted({_run_dir_of(rel) for rel in files if HYDRA_DIR in PurePosixPath(rel).parts})
        sweeps = sorted(_parent(rel) for rel in files if PurePosixPath(rel).name == SWEEP_FILE)
        return Discovery(root=root, files=files, details={"runs": runs, "sweeps": sweeps})

    def extract(self, root: Path, discovery: Discovery, *, project_id: str, project_name: str) -> Manifest:
        run_dirs: list[str] = sorted(set(discovery.details.get("runs", [])))
        if not run_dirs:
            raise AdapterError(
                f"no Hydra run folders found (a run folder contains a {HYDRA_DIR}/ subfolder). "
                "Point init at the folder that holds outputs/ or multirun/, or use --adapter generic. "
                "Runs started with hydra.output_subdir=null have no .hydra/ folder and cannot be recognized."
            )
        sweeps: list[str] = discovery.details.get("sweeps", [])

        used: set[str] = {project_id}
        run_ids: dict[str, str] = {}
        runs = []
        for run_dir in run_dirs:
            label = run_dir or root.name
            run_ids[run_dir] = _unique(f"run-{slug(label)}", used)
            runs.append(
                Run(
                    id=run_ids[run_dir],
                    name=label,
                    started_at=_started_at(run_dir),
                    **_read_run(root, run_dir, sweeps),
                )
            )

        # Deepest first, so a file belongs to the innermost run folder holding it.
        by_depth = sorted(run_dirs, key=lambda d: len(PurePosixPath(d).parts), reverse=True)
        artifacts = []
        for rel in discovery.files:
            run_dir = next((d for d in by_depth if _is_under(rel, d)), None)
            path = root / rel
            artifacts.append(
                Artifact(
                    id=_unique(valid_slug(rel), used),
                    path=rel,
                    sha256=sha256_file(path),
                    size_bytes=path.stat().st_size,
                    kind=_kind(rel, run_dir),
                    run_id=run_ids[run_dir] if run_dir is not None else None,
                )
            )
        return Manifest(project=Project(id=project_id, name=project_name), runs=runs, artifacts=artifacts)


def _read_run(root: Path, run_dir: str, sweeps: list[str]) -> dict[str, Any]:
    """``params`` and ``metadata`` for one run folder."""
    hydra_dir = root / run_dir / HYDRA_DIR
    info: dict[str, Any] = {}
    unreadable: list[str] = []
    reasons: dict[str, str] = {}

    def refuse(name: str, reason: str) -> None:
        rel = _join(run_dir, HYDRA_DIR, name)
        unreadable.append(rel)
        reasons[rel] = reason

    def load(name: str) -> Any:
        path = hydra_dir / name
        if not path.is_file():
            return None
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            refuse(name, f"cannot be read as UTF-8 text ({type(exc).__name__})")
            return None
        try:
            parsed = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
        except (ValueError, yaml.YAMLError) as exc:
            # ValueError covers json.JSONDecodeError.
            kind = "JSON" if path.suffix == ".json" else "YAML under safe loading"
            refuse(name, f"is not valid {kind} ({type(exc).__name__})")
            return None
        except RecursionError:
            refuse(name, "is nested too deeply to parse")
            return None
        try:
            return _plain(parsed)
        except _TooComplex as exc:
            refuse(name, str(exc))
            return None

    def first(names: tuple[str, ...], expected: type) -> Any:
        for name in names:
            if (hydra_dir / name).is_file():
                value = load(name)
                if value is not None and not isinstance(value, expected):
                    refuse(name, f"is {_a(type(value))}, not {_a(expected)}")
                    return None
                return value
        return None

    params = first(CONFIG_FILES, dict) or {}
    overrides = first(OVERRIDES_FILES, list)
    if overrides is not None:
        info["overrides"] = [str(o) for o in overrides]

    settings = first((SETTINGS_FILE,), dict)
    hydra = settings.get("hydra") if isinstance(settings, dict) else None
    if isinstance(hydra, dict):
        job = _mapping(hydra.get("job"))
        runtime = _mapping(hydra.get("runtime"))
        for key, value in (
            ("job_name", job.get("name")),
            ("job_num", job.get("num")),
            ("override_dirname", job.get("override_dirname")),
            ("mode", hydra.get("mode")),
            ("version", runtime.get("version")),
        ):
            if value not in (None, "", MISSING):
                info[key] = value
        # Config-group picks such as {"optimizer": "adam"}; Hydra's own
        # hydra/* groups are the same in nearly every run, so leave them out.
        choices = {k: v for k, v in _mapping(runtime.get("choices")).items() if not str(k).startswith("hydra/")}
        if choices:
            info["choices"] = choices

    sweep = next((s for s in sorted(sweeps, key=len, reverse=True) if run_dir != s and _is_under(run_dir, s)), None)
    if sweep is not None:
        info["sweep_dir"] = sweep or "."
    if unreadable:
        info["unreadable"] = unreadable
        info["unreadable_reasons"] = reasons
    return {"params": _jsonable(params), "metadata": {"hydra": _jsonable(info)} if info else {}}


def _run_dir_of(rel: str) -> str:
    """The folder that holds the first ``.hydra`` component of ``rel``."""
    parts = PurePosixPath(rel).parts
    return "/".join(parts[: parts.index(HYDRA_DIR)])


def _parent(rel: str) -> str:
    parent = PurePosixPath(rel).parent.as_posix()
    return "" if parent == "." else parent


def _is_under(rel: str, folder: str) -> bool:
    return folder == "" or rel == folder or rel.startswith(folder + "/")


def _join(*parts: str) -> str:
    return "/".join(p for p in parts if p)


def _kind(rel: str, run_dir: Optional[str]) -> Optional[str]:
    path = PurePosixPath(rel)
    if path.name == SWEEP_FILE or (run_dir is not None and _is_under(rel, _join(run_dir, HYDRA_DIR))):
        return "config"
    if run_dir is not None and path.suffix == ".log":
        return "log"
    return None


def _started_at(run_dir: str) -> Optional[datetime]:
    """Start time from Hydra's default ``<date>/<time>`` folder names, if used.
    For sweep jobs this is when the sweep started. Local time, no timezone."""
    matches = DEFAULT_TIMESTAMP.findall(run_dir)
    if not matches:
        return None
    day, clock = matches[-1]
    try:
        return datetime.strptime(f"{day} {clock}", "%Y-%m-%d %H-%M-%S")
    except ValueError:
        return None


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _a(kind: type) -> str:
    return {dict: "a mapping", list: "a list"}.get(kind, f"a {kind.__name__} value")


class _TooComplex(Exception):
    """A parsed file is cyclic, too deep or too large to convert."""


def _plain(value: Any) -> Any:
    """``_jsonable`` for freshly parsed data that may be hostile.

    YAML aliases make shared or even self-containing structures, which a
    plain recursive walk would expand without bound or recurse on forever.
    This walk uses its own stack, counts every value it emits (so a shared
    structure is counted each time it is expanded), and raises
    ``_TooComplex`` as soon as a limit is passed or a structure contains
    itself. Memory therefore stays proportional to the limits, never to the
    expanded size. Within the limits the result equals ``_jsonable(value)``.
    Every value is counted, including one later dropped because two keys
    convert to the same string (``1`` and ``"1"``), so such a file can be
    refused slightly earlier than its final size suggests."""
    values = 0
    text = 0

    def account(n_text: int) -> None:
        nonlocal values, text
        values += 1
        text += n_text
        if values > MAX_VALUES:
            raise _TooComplex(
                f"expands to more than {MAX_VALUES:,} values "
                "(each use of a YAML alias counts again)"
            )
        if text > MAX_TEXT:
            raise _TooComplex(
                f"expands to more than {MAX_TEXT:,} characters of text "
                "(each use of a YAML alias counts again)"
            )

    def leaf(item: Any) -> Any:
        if isinstance(item, str):
            account(len(item))
        elif isinstance(item, bytes):
            account(2 * len(item))
        elif isinstance(item, int) and not isinstance(item, bool):
            account(item.bit_length() * 30103 // 100000 + 1)  # about its decimal digits
        else:
            account(32)
        return _jsonable(item)

    def is_container(item: Any) -> bool:
        return isinstance(item, (dict, list, tuple, set, frozenset))

    if not is_container(value):
        return leaf(value)

    on_path: set[int] = set()

    def open_frame(item: Any, depth: int) -> list[Any]:
        if depth > MAX_DEPTH:
            raise _TooComplex(f"is nested more than {MAX_DEPTH} levels deep")
        if id(item) in on_path:
            raise _TooComplex("contains itself through a YAML alias (a cyclic structure)")
        account(0)
        on_path.add(id(item))
        if isinstance(item, dict):
            return [item, {}, iter(item.items()), depth]
        return [item, [], iter(item), depth]

    root = open_frame(value, 1)
    stack = [root]
    while stack:
        source, out, items, depth = stack[-1]
        try:
            entry = next(items)
        except StopIteration:
            if isinstance(source, (set, frozenset)):
                out.sort(key=repr)
            on_path.discard(id(source))
            stack.pop()
            continue
        if isinstance(out, dict):
            key, child = entry
            key = str(key)
            account(len(key))
        else:
            child = entry
        if is_container(child):
            frame = open_frame(child, depth + 1)
            converted = frame[1]
            stack.append(frame)
        else:
            converted = leaf(child)
        if isinstance(out, dict):
            out[key] = converted
        else:
            out.append(converted)
    return root[1]


def _jsonable(value: Any) -> Any:
    """YAML allows values JSON does not (dates, sets, NaN, non-string keys).
    Turn them into plain JSON so the manifest stays standard and stable."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_jsonable(v) for v in value), key=repr)
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)  # "nan", "inf", "-inf"
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _unique(base: str, used: set[str]) -> str:
    candidate, n = base, 2
    while candidate in used:
        candidate, n = f"{base}-{n}", n + 1
    used.add(candidate)
    return candidate
