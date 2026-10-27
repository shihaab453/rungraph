import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "generic-experiment"


def make_dir_link(link: Path, target: Path) -> bool:
    """Create a directory link: a junction on Windows (no privilege needed),
    a symbolic link elsewhere. Returns False when the platform refuses."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        return False
    return True


def is_junction(path: Path) -> bool:
    """True when ``path`` is a Windows junction, using lstat metadata so the
    assertion also works on Python versions without os.path.isjunction."""
    if os.name != "nt":
        return False
    try:
        st = os.lstat(path)
    except OSError:
        return False
    tag = getattr(st, "st_reparse_tag", None)
    if tag is not None:
        return tag == stat.IO_REPARSE_TAG_MOUNT_POINT
    return bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def remove_dir_link(link: Path) -> None:
    """Remove a directory link without touching the target it points at."""
    if os.name == "nt":
        try:
            os.rmdir(link)
            return
        except OSError:
            subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True)
            return
    try:
        link.unlink()
    except FileNotFoundError:
        pass


@pytest.fixture
def experiment(tmp_path: Path) -> Path:
    """A fresh, writable copy of the generic example experiment.

    Tests mutate this copy (delete files, edit bytes, move the folder) and
    never touch the committed example.
    """
    dest = tmp_path / "experiment"
    shutil.copytree(EXAMPLE, dest)
    return dest
