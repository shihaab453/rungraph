# Logistic regression vs random forest on Wisconsin breast cancer

Dataset: scikit-learn's bundled Wisconsin diagnostic breast-cancer data
(569 rows, 30 features). One stratified 75/25 split, seed 42, shared by
both models. Produced by `train.py` in this folder.

| Model | Test accuracy | Test ROC AUC |
| --- | --- | --- |
| Logistic regression (standardized, C=1.0) | 0.986 | 0.9977 |
| Random forest (50 trees, depth 4) | 0.958 | 0.9948 |

C1: Logistic regression beats the random forest on test accuracy.

C2: Both models exceed 0.99 test ROC AUC.

Caveat: a single split of 143 test rows; differences of one or two rows are
within noise. This is a dogfooding example, not a model comparison study.
