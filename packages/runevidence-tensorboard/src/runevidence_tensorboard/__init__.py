"""RunEvidence adapter for TensorBoard event files.

Installed, it registers itself as ``tensorboard``:

    runevidence init --adapter tensorboard <folder>
"""

from .adapter import TensorboardAdapter

__all__ = ["TensorboardAdapter"]
__version__ = "0.1.1"
