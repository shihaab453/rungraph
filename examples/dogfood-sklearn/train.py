"""Train one classifier on the Wisconsin breast-cancer dataset.

A deliberately ordinary training script, written the way a researcher would
write it, with no knowledge of RunEvidence. Used to dogfood RunEvidence on a
real run folder.

Usage (needs scikit-learn; the dataset ships inside scikit-learn, no download):
    python train.py prepare             # writes data/breast_cancer.csv and data/split.json
    python train.py fit logreg          # writes runs/logreg/...
    python train.py fit forest          # writes runs/forest/...
"""

from __future__ import annotations

import argparse
import csv
import json
import pickle
import time
from pathlib import Path

import numpy as np
from sklearn.datasets import load_breast_cancer
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
SEED = 42

CONFIGS = {
    "logreg": {"model": "logistic_regression", "C": 1.0, "max_iter": 1000, "seed": SEED},
    "forest": {"model": "random_forest", "n_estimators": 50, "max_depth": 4, "seed": SEED},
}


def prepare() -> None:
    ds = load_breast_cancer()
    DATA.mkdir(exist_ok=True)
    with open(DATA / "breast_cancer.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow([*ds.feature_names, "target"])
        for row, y in zip(ds.data, ds.target):
            w.writerow([*(f"{v:.6g}" for v in row), int(y)])
    idx = np.arange(len(ds.target))
    train_idx, test_idx = train_test_split(idx, test_size=0.25, random_state=SEED, stratify=ds.target)
    split = {"seed": SEED, "train": sorted(int(i) for i in train_idx), "test": sorted(int(i) for i in test_idx)}
    (DATA / "split.json").write_text(json.dumps(split) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {len(ds.target)} rows, {len(split['train'])} train / {len(split['test'])} test")


def load_data():
    with open(DATA / "breast_cancer.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))[1:]
    X = np.array([[float(v) for v in r[:-1]] for r in rows])
    y = np.array([int(r[-1]) for r in rows])
    split = json.loads((DATA / "split.json").read_text(encoding="utf-8"))
    return X, y, split["train"], split["test"]


def build(cfg: dict):
    if cfg["model"] == "logistic_regression":
        return make_pipeline(StandardScaler(), LogisticRegression(C=cfg["C"], max_iter=cfg["max_iter"]))
    return RandomForestClassifier(
        n_estimators=cfg["n_estimators"], max_depth=cfg["max_depth"], random_state=cfg["seed"]
    )


def fit(name: str) -> None:
    cfg = CONFIGS[name]
    out = HERE / "runs" / name
    out.mkdir(parents=True, exist_ok=True)
    (out / "config.json").write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8", newline="\n")

    X, y, train, test = load_data()
    started = time.time()
    model = build(cfg).fit(X[train], y[train])
    seconds = time.time() - started

    proba = model.predict_proba(X[test])[:, 1]
    pred = (proba >= 0.5).astype(int)
    metrics = {
        "split": "test",
        "n_test": len(test),
        "accuracy": round(float(accuracy_score(y[test], pred)), 4),
        "f1": round(float(f1_score(y[test], pred)), 4),
        "roc_auc": round(float(roc_auc_score(y[test], proba)), 4),
    }
    (out / "metrics.json").write_text(json.dumps({"test": metrics}, indent=2) + "\n", encoding="utf-8", newline="\n")
    with open(out / "predictions.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["row", "label", "proba"])
        for i, label, p in zip(test, y[test], proba):
            w.writerow([i, int(label), f"{p:.6f}"])
    with open(out / "model.pkl", "wb") as fh:
        pickle.dump(model, fh)
    with open(out / "train.log", "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} fit {name} in {seconds:.3f}s\n")
        fh.write(f"metrics {json.dumps(metrics)}\n")
    print(name, metrics)


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare")
    f = sub.add_parser("fit")
    f.add_argument("name", choices=sorted(CONFIGS))
    args = p.parse_args()
    prepare() if args.cmd == "prepare" else fit(args.name)


if __name__ == "__main__":
    main()
