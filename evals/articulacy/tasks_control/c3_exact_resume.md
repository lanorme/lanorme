# c3_exact_resume

Write `checkpointing.py`, the checkpoint and resume layer for our training run.

The requirement from the research team is bit-exact resume: a run that is
killed at step N and resumed from its step-N checkpoint must produce **exactly**
the same loss curve, weight tensors and sampled batches as a run that was never
interrupted. They compare loss curves across runs to attribute changes to the
intervention being tested, so any divergence invalidates the comparison.

Cover:

- Model, optimiser and LR scheduler state.
- The data pipeline's position in the stream, including shuffling, so resumed
  training sees the same batches in the same order it would have.
- All RNG state needed for dropout and any other stochastic layer.
- A `verify_resume(checkpoint_path)` helper the team can run in CI to confirm
  the guarantee holds.

Assume PyTorch, a `DataLoader` with `num_workers > 0` over a map-style dataset
with a shuffling sampler, and training on CUDA with bf16 autocast.
