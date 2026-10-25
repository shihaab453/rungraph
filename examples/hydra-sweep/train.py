"""Fit y = w*x + b by gradient descent. A deliberately tiny Hydra app.

Used to produce the hydra-sweep example with real Hydra output folders
(``.hydra/config.yaml``, ``hydra.yaml``, ``overrides.yaml``). Pure Python,
no numpy, finishes in well under a second.

Usage (needs hydra-core):
    python train.py                                  # one run in outputs/<date>/<time>/
    python train.py -m optimizer=sgd,momentum        # a sweep in multirun/<date>/<time>/<n>/
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

import hydra
from hydra.core.hydra_config import HydraConfig
from hydra.utils import to_absolute_path
from omegaconf import DictConfig

log = logging.getLogger(__name__)


def load_points(path: str) -> list[tuple[float, float]]:
    with open(to_absolute_path(path), newline="", encoding="utf-8") as fh:
        return [(float(r["x"]), float(r["y"])) for r in csv.DictReader(fh)]


@hydra.main(version_base="1.3", config_path="conf", config_name="config")
def main(cfg: DictConfig) -> None:
    out = Path(HydraConfig.get().runtime.output_dir)
    points = load_points(cfg.data.path)
    w = b = vw = vb = 0.0
    lr, beta = cfg.optimizer.lr, cfg.optimizer.momentum
    for epoch in range(1, cfg.epochs + 1):
        gw = sum(2 * (w * x + b - y) * x for x, y in points) / len(points)
        gb = sum(2 * (w * x + b - y) for x, y in points) / len(points)
        vw, vb = beta * vw + gw, beta * vb + gb
        w, b = w - lr * vw, b - lr * vb
        if epoch % 10 == 0:
            mse = sum((w * x + b - y) ** 2 for x, y in points) / len(points)
            log.info("epoch %d mse %.6f", epoch, mse)
    mse = sum((w * x + b - y) ** 2 for x, y in points) / len(points)
    metrics = {"w": round(w, 6), "b": round(b, 6), "mse": round(mse, 6), "epochs": cfg.epochs}
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8", newline="\n")
    log.info("final mse %.6f", mse)


if __name__ == "__main__":
    main()
