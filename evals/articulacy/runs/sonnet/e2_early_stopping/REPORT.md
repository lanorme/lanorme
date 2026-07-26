# Early stopping

## What changed

Added three new `TrainConfig` fields (all overridable from the CLI the same
way as every other field, e.g. `--early-stopping true`):

- `early_stopping: bool = False` -- master switch, off by default.
- `early_stopping_patience: int = 5` -- number of consecutive evaluations
  allowed with no sufficient improvement before stopping.
- `early_stopping_min_delta: float = 0.0` -- the eval loss must drop by at
  least this much versus the current best to count as an improvement.

In the eval branch of the training loop (still gated on `eval_interval`, so
"consecutive evaluations" means consecutive eval events, not steps):

- If `eval_loss < best_val_loss - early_stopping_min_delta`, it's an
  improvement: `best_val_loss` updates, the no-improvement counter resets to
  0, and `ckpt_best.pt` is (re)written, exactly as before.
- Otherwise the no-improvement counter increments.
- If `early_stopping` is on and the counter reaches
  `early_stopping_patience`, the run breaks out of the training loop
  immediately (no further steps run).

When the loop exits -- whether by early stopping or by reaching `max_steps` --
a final rotating checkpoint is written via `checkpoint_now(step)` as before.
The two cases are then logged/printed differently:

- Early stop: a `{"kind": "early_stop", "step": ..., "reason": ..., "best_val_loss": ...}`
  record goes to `train_log.jsonl`, and the console prints the step, the
  human-readable reason (e.g. "eval loss did not improve by at least 0.0 for
  5 consecutive evaluations"), the best eval loss, and a reminder of where the
  best checkpoint lives.
- Normal completion: unchanged `"kind": "done"` record and message.

## Best checkpoint guarantee

`ckpt_best.pt` is only ever written inside the "improved" branch, so it is
untouched by early stopping and by the regular `keep_last_k` rotation of
`ckpt_step*.pt` files. Whenever the run halts -- early or at `max_steps` --
the model weights in `ckpt_best.pt` are the ones with the lowest eval loss
seen so far, distinct from whatever the last rotating `ckpt_step*.pt` (the
step-at-stop checkpoint) contains.

## Things the operator should know

- **Patience resets on `--resume`.** The no-improvement counter is in-memory
  only (not part of the checkpoint payload), so resuming a run always
  restarts the counter at 0, even though `best_val_loss` itself is restored
  correctly from the checkpoint. In practice this means a resumed run gets a
  fresh full `patience` window before it can early-stop again. If tighter
  continuity across resumes is needed later, the counter would need to be
  added to `build_checkpoint`/`load_checkpoint`.
- **Default is off.** Nothing changes for existing invocations unless
  `--early-stopping true` (or `early_stopping=True` in code) is passed.
- **Units are evaluations, not steps.** With `eval_interval=2000` and
  `early_stopping_patience=5`, the earliest an early stop can trigger is
  10,000 steps after the current best.
