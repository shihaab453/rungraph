"""RunEvidence adapter for experiment folders written by Hydra.

Installed, it registers itself as ``hydra``:

    runevidence init --adapter hydra <folder>
"""

from .adapter import HydraAdapter

__all__ = ["HydraAdapter"]
__version__ = "0.1.1"
