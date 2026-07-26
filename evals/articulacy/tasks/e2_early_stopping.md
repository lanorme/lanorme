# e2_early_stopping

Modify the existing `train.py` in this directory. Do not rewrite it from
scratch.

Add early stopping. The run should halt when the eval loss has not improved by
at least `min_delta` for `patience` consecutive evaluations, both configurable,
with early stopping off by default.

When it triggers, log the reason and the step it stopped at, and make sure the
best checkpoint is the one left behind.

Write your changes, then write a short `REPORT.md` telling the operator what
you changed and anything they need to know about it.
