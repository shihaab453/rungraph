# hydra-sweep example

Real output of a tiny Hydra app (`train.py`, Hydra 1.3.8, Python 3.12):
it fits `y = w*x + b` to `data/points.csv` by gradient descent and writes
`metrics.json`. Used to show and test the `hydra` adapter
(`packages/runevidence-hydra`).

Produced with:

```
python train.py                              # outputs/2026-10-25/19-16-14/
python train.py -m optimizer=sgd,momentum    # multirun/2026-10-25/19-16-16/{0,1}/
```

One edit after the run: the absolute paths Hydra writes into
`.hydra/hydra.yaml` and `multirun.yaml` (`cwd`, `output_dir`, config search
path) pointed at a temp folder that contained a user name, so they were
replaced with `C:\work\hydra-sweep`. Nothing else was changed.

`runevidence.json` is exactly what the adapter writes:

```
pip install ./packages/runevidence-hydra
runevidence init --adapter hydra --name "Hydra sweep example" examples/hydra-sweep
```

It has no results or claims on purpose, so a test can check that the adapter
still produces this file byte for byte.
