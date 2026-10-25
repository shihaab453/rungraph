"""Diabetes progression regression: preprocessing plus a learning-rate sweep.

A deliberately ordinary two-stage pipeline, written the way a researcher
would write it, with no knowledge of RunEvidence. Used to dogfood RunEvidence
on a layout that differs from the ``runs/<name>/`` convention: output folders
follow Hydra's multirun layout (``multirun/<date>/<time>/<job>/`` with a
hidden ``.hydra/`` folder), each job saves per-epoch checkpoints, and one job
diverges on purpose.

Usage (needs scikit-learn; the dataset ships inside scikit-learn, no download):
    python pipeline.py prepare      # data/raw/diabetes.csv
    python pipeline.py preprocess   # data/processed/{train,val,test}.csv + scaler.json
    python pipeline.py sweep        # multirun/<date>/<time>/<job>/...
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import pickle
import time
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
from sklearn.datasets import load_diabetes
from sklearn.neural_network import MLPRegressor

HERE = Path(__file__).resolve().parent
RAW = HERE / "data" / "raw"
PROCESSED = HERE / "data" / "processed"
SEED = 7

BASE_CONFIG = {
    "model": "mlp_regressor",
    "hidden_layer_sizes": [32],
    "solver": "sgd",
    "momentum": 0.9,
    "epochs": 40,
    "checkpoint_every": 10,
    "seed": SEED,
}
SWEEP = {"learning_rate_init": [0.0003, 0.001, 0.01]}


def write_csv(path: Path, header: list[str], rows) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        for row in rows:
            w.writerow([f"{v:.8g}" for v in row])


def read_csv(path: Path) -> tuple[np.ndarray, np.ndarray]:
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))[1:]
    data = np.array([[float(v) for v in r] for r in rows])
    return data[:, :-1], data[:, -1]


def prepare() -> None:
    ds = load_diabetes(scaled=False)
    RAW.mkdir(parents=True, exist_ok=True)
    write_csv(RAW / "diabetes.csv", [*ds.feature_names, "progression"], np.column_stack([ds.data, ds.target]))
    print(f"wrote {len(ds.target)} rows to {RAW / 'diabetes.csv'}")


def preprocess() -> None:
    X, y = read_csv(RAW / "diabetes.csv")
    rng = np.random.default_rng(SEED)
    idx = rng.permutation(len(y))
    n_test, n_val = len(y) // 5, len(y) // 10
    test, val, train = idx[:n_test], idx[n_test : n_test + n_val], idx[n_test + n_val :]
    mean, std = X[train].mean(axis=0), X[train].std(axis=0)

    PROCESSED.mkdir(parents=True, exist_ok=True)
    with open(RAW / "diabetes.csv", encoding="utf-8") as fh:
        header = fh.readline().strip().split(",")
    for name, part in (("train", train), ("val", val), ("test", test)):
        write_csv(PROCESSED / f"{name}.csv", header, np.column_stack([(X[part] - mean) / std, y[part]]))
    scaler = {"features": header[:-1], "mean": mean.round(8).tolist(), "std": std.round(8).tolist()}
    (PROCESSED / "scaler.json").write_text(json.dumps(scaler, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"split {len(train)} train / {len(val)} val / {len(test)} test")


def rmse(model, X, y) -> float:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        pred = model.predict(X)
    return float(np.sqrt(np.mean((pred - y) ** 2)))


def train_job(out: Path, cfg: dict, overrides: list[str]) -> dict:
    (out / ".hydra").mkdir(parents=True, exist_ok=True)
    (out / "checkpoints").mkdir(exist_ok=True)
    (out / ".hydra" / "config.json").write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8", newline="\n")
    (out / ".hydra" / "overrides.json").write_text(json.dumps(overrides) + "\n", encoding="utf-8", newline="\n")

    X_tr, y_tr = read_csv(PROCESSED / "train.csv")
    X_va, y_va = read_csv(PROCESSED / "val.csv")
    X_te, y_te = read_csv(PROCESSED / "test.csv")
    model = MLPRegressor(
        hidden_layer_sizes=tuple(cfg["hidden_layer_sizes"]),
        solver=cfg["solver"],
        momentum=cfg["momentum"],
        learning_rate_init=cfg["learning_rate_init"],
        random_state=cfg["seed"],
    )

    best = {"epoch": None, "val_rmse": math.inf}
    status = "completed"
    log = open(out / "train.log", "w", encoding="utf-8", newline="\n")
    log.write(f"{datetime.now().isoformat(timespec='seconds')} start {' '.join(overrides)}\n")
    with open(out / "history.jsonl", "w", encoding="utf-8", newline="\n") as hist:
        for epoch in range(1, cfg["epochs"] + 1):
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    model.partial_fit(X_tr, y_tr)
            except ValueError as exc:  # sklearn refuses to continue once weights are non-finite
                status = "diverged"
                log.write(f"{datetime.now().isoformat(timespec='seconds')} epoch {epoch} diverged: {exc}\n")
                hist.write(json.dumps({"epoch": epoch, "train_loss": float("nan"), "val_rmse": float("nan")}) + "\n")
                break
            val = rmse(model, X_va, y_va)
            hist.write(json.dumps({"epoch": epoch, "train_loss": round(model.loss_, 6), "val_rmse": round(val, 6)}) + "\n")
            if val < best["val_rmse"]:
                best = {"epoch": epoch, "val_rmse": val}
                (out / "checkpoints" / "best.pkl").write_bytes(pickle.dumps(model))
            if epoch % cfg["checkpoint_every"] == 0:
                (out / "checkpoints" / f"epoch_{epoch:03d}.pkl").write_bytes(pickle.dumps(model))
                log.write(f"{datetime.now().isoformat(timespec='seconds')} epoch {epoch} val_rmse {val:.4f}\n")
    log.close()

    if best["epoch"] is not None:
        best_model = pickle.loads((out / "checkpoints" / "best.pkl").read_bytes())
        test_rmse = round(rmse(best_model, X_te, y_te), 4)
    else:
        test_rmse = float("nan")
    metrics = {
        "status": status,
        "best_epoch": best["epoch"],
        "val": {"rmse": round(best["val_rmse"], 4)},
        "test": {"rmse": test_rmse, "n": int(len(y_te))},
        "final_train_loss": round(model.loss_, 4) if status == "completed" else float("nan"),
    }
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8", newline="\n")
    return metrics


def sweep() -> None:
    now = datetime.now()
    root = HERE / "multirun" / now.strftime("%Y-%m-%d") / now.strftime("%H-%M-%S")
    jobs = []
    for job, lr in enumerate(SWEEP["learning_rate_init"]):
        cfg = {**BASE_CONFIG, "learning_rate_init": lr}
        started = time.time()
        metrics = train_job(root / str(job), cfg, [f"learning_rate_init={lr}"])
        jobs.append({"job": job, "learning_rate_init": lr, **metrics})
        print(f"job {job} lr={lr}: {metrics['status']}, test rmse {metrics['test']['rmse']} ({time.time() - started:.1f}s)")
    finished = [j for j in jobs if j["status"] == "completed"]
    best = min(finished, key=lambda j: j["val"]["rmse"])
    summary = {"sweep": SWEEP, "best_job": best["job"], "jobs": jobs}
    (root / "multirun.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"best job {best['job']} -> {root.relative_to(HERE).as_posix()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("stage", choices=["prepare", "preprocess", "sweep"])
    {"prepare": prepare, "preprocess": preprocess, "sweep": sweep}[parser.parse_args().stage]()
