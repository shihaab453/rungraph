# Diabetes progression: SGD learning-rate sweep

Small MLP (one hidden layer of 32 units, SGD with momentum 0.9, 40 epochs)
predicting one-year disease progression from 10 baseline features. Features
standardized with training-split statistics; 310 train / 44 val / 88 test.

| Job | learning_rate_init | Status | Best epoch | Val RMSE | Test RMSE |
| --- | --- | --- | --- | --- | --- |
| 0 | 0.0003 | completed | 24 | 44.86 | 51.18 |
| 1 | 0.001 | completed | 40 | 44.62 | 51.00 |
| 2 | 0.01 | diverged at epoch 5 | 1 | 243.98 | 213.81 |

## Claims

1. **lr=0.001 is the best setting in the sweep:** lowest validation RMSE
   (44.62), test RMSE 51.00 on 88 held-out patients.
2. **lr=0.01 diverges:** training loss reaches infinity by epoch 4 and NaN by
   epoch 5, so plain SGD needs a learning rate at or below 0.001 on this data.
