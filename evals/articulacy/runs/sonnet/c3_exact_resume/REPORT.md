# REPORT: checkpointing.py

## What this is

A checkpoint/resume module for a PyTorch training run, built for one specific
guarantee: **a run killed after step N and resumed from the step-N checkpoint
must produce exactly the same loss curve, weight tensors, and batches as a run
that was never interrupted.** Not "close enough" -- bit-for-bit equal. Built
for the stated setup: PyTorch, CUDA, bf16 autocast, a map-style `Dataset`
through a `DataLoader` with `num_workers > 0` and a shuffling sampler.

## The five things a checkpoint carries, and why each is there

1. **Model, optimizer, LR scheduler state** -- the ordinary part:
   `state_dict()` on all three. bf16 needs no `GradScaler`, so there's no
   scaler state in the payload -- that's a deliberate omission, not a gap. If
   this ever moves to fp16, add `scaler.state_dict()` to `save_checkpoint`.

2. **Data pipeline position, including shuffling** -- `ResumableShuffleSampler`
   replaces `RandomSampler`/`shuffle=True`. Its permutation is a pure function
   of `(base_seed, epoch)`: `randperm(len(dataset), generator=Generator().manual_seed(mix(base_seed, epoch)))`.
   Resuming mid-epoch at sample offset K is `perm[K:]` -- computed the same
   way whether the process ran continuously or was just restarted, so there's
   no runtime sampler state to snapshot and no drift. `EpochProgress` tracks
   `(global_step, epoch, samples_consumed_in_epoch)` as an explicit running
   count (not `step * batch_size`), so the epoch's final short batch (when
   `drop_last=False`) doesn't throw off the offset.

   **Checkpoints must land on optimizer-step boundaries.** The module assumes
   a checkpoint is only ever taken after a full (possibly
   gradient-accumulated) step has been applied, never mid-accumulation-window.
   Resuming mid-window would also need the partially-accumulated,
   not-yet-applied gradients snapshotted, which this module does not do.

3. **The worker-parity trap (the part most resume implementations get wrong)**
   -- with `num_workers > 0`, the DataLoader hands dataset indices to workers
   round-robin by *position in the current iterator*, not by dataset index.
   On an uninterrupted epoch, worker `w` sees positions `w, w+num_workers, ...`.
   On resume from offset K, the new iterator's position counter restarts at 0
   over `perm[K:]`, so worker `w` now sees *global* positions `K, K+num_workers, ...`
   -- the same set as before only when `K % num_workers == 0`. Whenever it
   isn't, a sample lands on a different worker, at a different point in that
   worker's call sequence, than it did pre-kill. If a worker's augmentation
   randomness comes from a per-worker sequential RNG stream (the common
   pattern), that sample silently gets a different random draw after resume.

   The fix built in: **`per_sample_generator(base_seed, epoch, index)`**
   seeds augmentation randomness from the dataset index itself, not from
   worker identity or call order. Dataset authors must use it for any
   stochastic transform inside `__getitem__` -- this is a contract this module
   can document and provide the primitive for, but cannot enforce on code it
   doesn't own. `make_worker_init_fn` is a secondary safety net for anything
   that still touches the global RNG inside a worker; it does not by itself
   fix the parity problem above.

   `build_resumable_dataloader` wires this up and hard-codes
   `persistent_workers=False`: persistent workers stay alive across epochs
   with RNG state this module can't see, and `worker_init_fn` would only fire
   once for the process's life instead of once per epoch. Recreating workers
   every epoch keeps "resume" and "ordinary epoch boundary" the same code
   path.

4. **RNG state for dropout and friends** -- `RNGState` captures Python's
   `random`, NumPy's global RNG (best-effort, only if NumPy is importable),
   the Torch CPU generator, and the Torch CUDA generator for the current
   device. Single-GPU only, as specified; extending to DDP needs one
   `RNGState` per rank plus a rank-aware sampler (shard-then-shuffle rather
   than one global permutation).

5. **`verify_resume(checkpoint_path)`** -- two tiers:
   - **Tier 1 (always runs, needs only the path):** recomputes the
     checkpoint file's SHA-256 and compares it to a manifest recorded at save
     time -- this is the check that actually matters for "killed at step N",
     since a `SIGKILL` mid-write is exactly the kind of failure that produces
     a truncated or corrupt file that would otherwise resume silently on
     wrong state. It also checks all required keys are present, the format
     version is readable, manifest and payload metadata agree, the resume
     offset is in range, and -- importantly -- that the environment recorded
     at save time (torch/CUDA version, `cudnn.deterministic`,
     `use_deterministic_algorithms`, `CUBLAS_WORKSPACE_CONFIG`, GPU model)
     matches the environment calling `verify_resume` now. Bit-exactness is as
     much a property of the environment as the code; a checkpoint saved under
     one cuDNN/GPU configuration is not guaranteed bit-exact under another,
     and this catches that before it becomes a silent, misattributed
     divergence in a loss-curve comparison.
   - **Tier 2 (opt-in, needs `model_factory`/`optimizer_factory`/`dataset`/
     `step_fn`):** loads the same checkpoint into two independent replicas,
     runs a few real training steps on both, and asserts the losses and
     resulting weights are bit-identical between the two replicas. A
     checkpoint that can't reproduce itself can't match the original
     uninterrupted run either -- this checks that necessary condition without
     needing a multi-hour reference run to diff against. It replays with
     `num_workers=0` for speed, so it validates model/optimizer/
     scheduler/sampler/RNG determinism but does not exercise the real
     multi-worker path; pair it with a slower, periodic integration test that
     resumes through the actual `num_workers > 0` pipeline.

   Runnable directly for CI: `python checkpointing.py path/to/ckpt.pt`, exit
   code 0/1.

## What the operator needs to know

- **This module does not flip global determinism flags for you.**
  `capture_environment_fingerprint` only observes `cudnn.deterministic`,
  `cudnn.benchmark`, `torch.are_deterministic_algorithms_enabled()`, and
  `CUBLAS_WORKSPACE_CONFIG` -- it doesn't set them. Setting
  `torch.backends.cudnn.deterministic = True`,
  `torch.backends.cudnn.benchmark = False`,
  `torch.use_deterministic_algorithms(True)`, and
  `CUBLAS_WORKSPACE_CONFIG=:4096:8` is a training-script startup decision
  with a real throughput cost, so it's left to the caller. Without them, some
  CUDA kernels (certain reductions/scatter ops, some backward passes) are
  non-deterministic on their own regardless of anything checkpointed here,
  and `verify_resume`'s Tier 2 replay will catch the resulting divergence.

- **`load_checkpoint` uses `weights_only=False`.** The payload carries plain
  Python RNG state (tuples, a NumPy array) alongside tensors, which
  `weights_only=True` loading rejects. This means loading executes arbitrary
  pickled objects -- only ever point it at checkpoints your own job wrote,
  never at one from an untrusted source.

- **Dataset authors must adopt `per_sample_generator`.** This is the one
  piece of the contract this module can't enforce by itself. Any existing
  augmentation code that calls `torch.rand()` / `random.random()` /
  `np.random.rand()` directly inside `__getitem__` needs to be rewritten to
  draw from `per_sample_generator(base_seed, epoch, index)` instead, or
  `num_workers > 0` resume will not be bit-exact for augmented samples.

- **`ResumableShuffleSampler` recomputes a full `randperm` on every epoch and
  every resume.** Cheap (sub-second) for datasets up to tens of millions of
  items; for much larger datasets, a single global permutation stops being
  the right approach and a block/shard-shuffle scheme should replace it.

- **Checkpoints are written atomically** (temp file in the same directory,
  `fsync`, then `os.replace`) for both the checkpoint and its manifest, so a
  kill mid-write leaves either the previous good checkpoint or nothing at the
  canonical path -- never a corrupt one silently sitting there.

- **Not covered:** multi-GPU/DDP (noted above as an extension, not
  implemented), fp16/`GradScaler` (bf16 needs none), and resuming
  mid-gradient-accumulation-window (checkpoints must be taken on optimizer-step
  boundaries).
