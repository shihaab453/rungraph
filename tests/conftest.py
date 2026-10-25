import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "generic-experiment"


@pytest.fixture
def experiment(tmp_path: Path) -> Path:
    """A fresh, writable copy of the generic example experiment.

    Tests mutate this copy (delete files, edit bytes, move the folder) and
    never touch the committed example.
    """
    dest = tmp_path / "experiment"
    shutil.copytree(EXAMPLE, dest)
    return dest
