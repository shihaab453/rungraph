"""RunEvidence adapter for local MLflow file stores.

Installed, it registers itself as ``mlflow``:

    runevidence init --adapter mlflow <folder>
"""

from .adapter import MlflowAdapter

__all__ = ["MlflowAdapter"]
__version__ = "0.1.1"
