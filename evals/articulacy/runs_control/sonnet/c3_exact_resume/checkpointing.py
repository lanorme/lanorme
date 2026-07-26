"""Checkpoint and resume layer for bit-exact training runs.

The guarantee this module exists to provide: a run killed at step N and
resumed from its step-N checkpoint reproduces, exactly, the loss curve,
weight tensors and sampled batches of a run that was never interrupted.
Nothing here is "close enough" -- comparisons made against this checkpoint
are meant to attribute changes in a loss curve to a deliberate intervention,
and any divergence introduced by the checkpoint/resume machinery itself would
invalidate that comparison.

Bit-exactness on CUDA with bf16 autocast requires four things to all hold at
once; this module is organised around them:

1. Model, optimiser and LR scheduler state are saved and restored verbatim
   (``save_checkpoint`` / ``load_checkpoint``). bf16 autocast needs no loss
   scaler (unlike fp16), so there is deliberately no ``GradScaler`` state.
2. The data pipeline's position in the stream -- including the shuffle order
   -- is reproducible from a seed, and a resume can skip forward to the exact
   batch it stopped at without changing anything about how the batches
   *before or after* that point are produced (``ResumableSampler``,
   ``build_epoch_dataloader``, ``iter_training_batches``). This has to
   account for ``DataLoader`` prefetching with ``num_workers > 0``: the
   sampler is drawn ahead of what the training loop has actually consumed, so
   "resume position" is tracked as *completed optimiser steps*, not as
   sampler draws, and a resume replays the dataloader's internal state by
   iterating and discarding, never by trying to serialise it directly.
3. Every RNG stream that can affect the run -- Python's, NumPy's, and torch's
   CPU and per-device CUDA generators, in both the main process and every
   DataLoader worker -- is captured and restored, or, for workers, reseeded
   deterministically rather than from OS entropy or wall-clock time
   (``RngState``, ``worker_init_fn``).
4. Non-deterministic GPU kernels are turned off process-wide before anything
   is built (``enable_deterministic_mode``). Correct RNG state alone does not
   guarantee bit-exact CUDA results: cuDNN autotuning, TF32, and several
   backward kernels are non-deterministic by default and will silently break
   the guarantee even with (1)-(3) done right.

``verify_resume`` wires all of this into a single CI check: it reruns a
training config once from scratch and once through a real save/resume cycle,
and fails loudly the moment the two diverge.

Assumptions this module makes, matching the training setup it was written
for: PyTorch, CUDA, bf16 autocast, a map-style ``Dataset`` with a shuffling
sampler, and a ``DataLoader`` with ``num_workers > 0``. It targets a single
device; extending it to multi-GPU data-parallel training would additionally
need the shuffle split to be rank-aware, which is out of scope here.
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import random
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterator, NamedTuple, Optional

import torch
from torch import nn
from torch.optim import Optimizer
from torch.utils.data import DataLoader, Dataset, Sampler

try:
    import numpy as np
except ImportError:  # NumPy is optional; RNG capture degrades gracefully.
    np = None  # type: ignore[assignment]

__all__ = [
    "RngState",
    "enable_deterministic_mode",
    "worker_init_fn",
    "ResumableSampler",
    "build_epoch_dataloader",
    "TrainingProgress",
    "iter_training_batches",
    "save_checkpoint",
    "load_checkpoint",
    "RunComponents",
    "verify_resume",
]

_FORMAT_VERSION = 1


# --------------------------------------------------------------------------
# Determinism setup
# --------------------------------------------------------------------------


def enable_deterministic_mode(seed: int) -> None:
    """Configure the process for bit-exact CUDA + bf16 training.

    Call this once, as the very first thing the training script does --
    before the model, optimiser or any ``DataLoader`` is constructed -- on
    *every* process that is meant to line up with another: the original run
    and every resume of it. Correct RNG state (see ``RngState``) is not
    sufficient by itself; several CUDA kernels pick a non-deterministic
    algorithm unless told not to.

    ``CUBLAS_WORKSPACE_CONFIG`` and ``PYTHONHASHSEED`` are read once at
    process start-up by cuBLAS and the interpreter respectively, so setting
    them here only takes effect for this process's *children* (dataloader
    workers spawned after this call, via ``fork`` or ``spawn``), not for the
    current process if it has already touched CUDA or hashed anything. In
    practice this means: call this function before touching CUDA at all.
    """
    os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
    os.environ["PYTHONHASHSEED"] = str(seed)

    random.seed(seed)
    if np is not None:
        np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)

    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.allow_tf32 = False
    torch.backends.cuda.matmul.allow_tf32 = False
    # warn_only=False: fail loudly on an op with no deterministic kernel
    # rather than silently keep a non-deterministic one, which would produce
    # a checkpoint/resume mismatch that is very hard to trace back here.
    torch.use_deterministic_algorithms(True, warn_only=False)


# --------------------------------------------------------------------------
# RNG capture / restore
# --------------------------------------------------------------------------


@dataclasses.dataclass
class RngState:
    """A snapshot of every RNG stream that can affect a training step.

    Covers Python's ``random``, NumPy's global RNG, torch's CPU generator and
    torch's per-device CUDA generators (dropout and other stochastic layers
    draw from the CUDA generator of whichever device they run on).
    """

    python_state: tuple
    numpy_state: Optional[tuple]
    torch_cpu_state: torch.Tensor
    torch_cuda_states: list[torch.Tensor]

    @classmethod
    def capture(cls) -> "RngState":
        return cls(
            python_state=random.getstate(),
            numpy_state=np.random.get_state() if np is not None else None,
            torch_cpu_state=torch.get_rng_state().clone(),
            torch_cuda_states=(
                [state.clone() for state in torch.cuda.get_rng_state_all()]
                if torch.cuda.is_available()
                else []
            ),
        )

    def restore(self) -> None:
        random.setstate(self.python_state)
        if self.numpy_state is not None:
            if np is None:
                raise RuntimeError(
                    "checkpoint was captured with NumPy RNG state but NumPy "
                    "is not importable in this process"
                )
            np.random.set_state(self.numpy_state)
        torch.set_rng_state(self.torch_cpu_state)
        if self.torch_cuda_states:
            if not torch.cuda.is_available():
                raise RuntimeError(
                    "checkpoint was captured with CUDA RNG state but no CUDA "
                    "device is available to restore it to"
                )
            torch.cuda.set_rng_state_all(self.torch_cuda_states)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, state: dict) -> "RngState":
        return cls(**state)


def worker_init_fn(worker_id: int) -> None:
    """Seed a freshly spawned DataLoader worker's Python and NumPy RNGs.

    torch itself already seeds the worker's *torch* generator deterministically
    -- as ``base_seed + worker_id``, where ``base_seed`` comes from the
    ``generator`` passed to the ``DataLoader`` -- before this hook runs, as
    long as that ``generator`` was itself seeded deterministically (see
    ``build_epoch_dataloader``). ``torch.initial_seed()`` inside the worker
    therefore already reflects that deterministic per-worker seed; this hook
    only needs to propagate it to the two RNGs torch does not seed itself, so
    that any augmentation code using ``random`` or ``numpy.random`` inside
    ``Dataset.__getitem__`` is reproducible too. Never seed from time or PID.
    """
    worker_seed = torch.initial_seed() % 2**32
    random.seed(worker_seed)
    if np is not None:
        np.random.seed(worker_seed)


# --------------------------------------------------------------------------
# Deterministic, resumable data pipeline
# --------------------------------------------------------------------------


class ResumableSampler(Sampler):
    """A shuffling sampler whose per-epoch permutation is a pure function of
    ``(seed, epoch)``.

    Unlike ``RandomSampler``, which reseeds from a mutable, ambient
    generator, this recomputes the whole permutation from scratch on every
    ``__iter__`` call from ``seed + epoch`` alone. That means a resumed
    process, which has none of the original process's live state, can
    reconstruct the exact same shuffle order for the epoch it stopped in just
    by knowing the seed and epoch number -- both of which live in the
    checkpoint. Getting to the exact *position* within that order after a
    resume is handled separately, by ``iter_training_batches`` skipping
    forward; this class only owns "what order would this epoch visit indices
    in", never "where are we in it".
    """

    def __init__(self, dataset_size: int, seed: int, shuffle: bool = True):
        if dataset_size <= 0:
            raise ValueError(f"dataset_size must be positive, got {dataset_size}")
        self.dataset_size = dataset_size
        self.seed = seed
        self.shuffle = shuffle
        self.epoch = 0

    def set_epoch(self, epoch: int) -> None:
        self.epoch = epoch

    def _permutation(self) -> torch.Tensor:
        if not self.shuffle:
            return torch.arange(self.dataset_size)
        generator = torch.Generator()
        generator.manual_seed(self.seed + self.epoch)
        return torch.randperm(self.dataset_size, generator=generator)

    def __iter__(self) -> Iterator[int]:
        yield from self._permutation().tolist()

    def __len__(self) -> int:
        return self.dataset_size


def build_epoch_dataloader(
    dataset: Dataset,
    sampler: ResumableSampler,
    *,
    batch_size: int,
    seed: int,
    epoch: int,
    num_workers: int = 4,
    **loader_kwargs: Any,
) -> DataLoader:
    """Build the ``DataLoader`` for one epoch, deterministically.

    Sets ``sampler``'s epoch, and gives the loader a ``generator`` seeded
    from ``seed + epoch`` (distinct from, and in addition to, the sampler's
    own permutation seed) so the per-worker base seed torch derives from it
    -- and therefore everything ``worker_init_fn`` seeds from it -- is the
    same deterministic value on every construction of this epoch's loader,
    whether that is the original run or a resume that reaches this epoch.

    ``persistent_workers`` is deliberately left at its default (``False``):
    a resumed process rebuilds the loader per epoch from scratch anyway, and
    persistent workers would carry mutable state across epoch boundaries
    that this module does not track or checkpoint.
    """
    sampler.set_epoch(epoch)
    generator = torch.Generator()
    generator.manual_seed(seed + epoch)
    return DataLoader(
        dataset,
        batch_size=batch_size,
        sampler=sampler,
        num_workers=num_workers,
        worker_init_fn=worker_init_fn,
        generator=generator,
        **loader_kwargs,
    )


def _skip_batches(loader_iter: Iterator, num_batches: int, *, epoch: int) -> None:
    """Advance ``loader_iter`` past ``num_batches`` already-completed steps.

    This is the crux of resuming correctly with ``num_workers > 0``: the
    ``DataLoader`` prefetches ahead of what the training loop has consumed,
    so the sampler is always drawn further than "batches actually trained
    on". There is no clean way to serialise "resume the dataloader's
    in-flight prefetch queue" across a process boundary, so instead of
    trying to, this fast-forwards a *freshly built* loader by pulling and
    discarding batches one at a time -- exactly replaying, side effect for
    side effect (worker RNG draws, round-robin worker assignment, prefetch
    depth), what the original run's loader did to reach the same point. The
    result is indistinguishable from having never stopped.
    """
    for _ in range(num_batches):
        try:
            next(loader_iter)
        except StopIteration as exc:
            raise RuntimeError(
                f"checkpoint expects {num_batches} completed batches in "
                f"epoch {epoch}, but the dataloader ran out first -- the "
                "dataset size or batch size must have changed since the "
                "checkpoint was written"
            ) from exc


# --------------------------------------------------------------------------
# Training progress and checkpoint I/O
# --------------------------------------------------------------------------


@dataclasses.dataclass
class TrainingProgress:
    """Where a training run is: which epoch, how far into it, and in total."""

    epoch: int
    batches_done: int  # completed optimiser steps since the start of `epoch`
    global_step: int  # completed optimiser steps since the start of training


def iter_training_batches(
    dataset: Dataset,
    sampler: ResumableSampler,
    *,
    seed: int,
    start_progress: TrainingProgress,
    batch_size: int,
    num_workers: int = 4,
    **loader_kwargs: Any,
) -> Iterator[tuple[TrainingProgress, Any]]:
    """Yield ``(progress, batch)`` pairs, resuming exactly at ``start_progress``.

    This is the data-pipeline half of the checkpoint/resume contract: drive
    training from this instead of a bare ``for batch in dataloader`` loop and
    both "run straight through" and "kill and resume" produce the identical
    sequence of batches. Runs forever across epoch boundaries (rebuilding
    the loader for each new epoch via ``build_epoch_dataloader``); the caller
    stops iterating once it has done enough steps.

    ``progress`` reflects the state *after* the yielded batch's step is
    counted as done -- i.e. it is exactly what should go into
    ``save_checkpoint`` if a checkpoint is written right after this batch's
    optimiser step completes.
    """
    epoch = start_progress.epoch
    batches_done = start_progress.batches_done
    global_step = start_progress.global_step

    while True:
        loader = build_epoch_dataloader(
            dataset,
            sampler,
            batch_size=batch_size,
            seed=seed,
            epoch=epoch,
            num_workers=num_workers,
            **loader_kwargs,
        )
        loader_iter = iter(loader)
        if batches_done:
            _skip_batches(loader_iter, batches_done, epoch=epoch)

        for batch in loader_iter:
            batches_done += 1
            global_step += 1
            yield (
                TrainingProgress(
                    epoch=epoch, batches_done=batches_done, global_step=global_step
                ),
                batch,
            )

        epoch += 1
        batches_done = 0


def save_checkpoint(
    path: str | Path,
    *,
    model: nn.Module,
    optimizer: Optimizer,
    scheduler: Any,
    progress: TrainingProgress,
    seed: int,
    extra: Optional[dict] = None,
) -> None:
    """Save everything needed to resume this exact run.

    ``scheduler`` may be ``None`` if the run has no LR schedule. There is
    deliberately no gradient scaler state: bf16 autocast does not use one
    (unlike fp16), so there is nothing to save for it.

    Written atomically (write to a temp file in the same directory, fsync,
    then ``os.replace``) so a process killed mid-write -- the same kind of
    interruption this module exists to survive -- never leaves behind a
    corrupt checkpoint that a resume would load.
    """
    payload = {
        "format_version": _FORMAT_VERSION,
        "seed": seed,
        "progress": dataclasses.asdict(progress),
        "model": model.state_dict(),
        "optimizer": optimizer.state_dict(),
        "scheduler": scheduler.state_dict() if scheduler is not None else None,
        "rng": RngState.capture().to_dict(),
        "extra": extra or {},
    }
    _atomic_torch_save(payload, path)


def _atomic_torch_save(payload: dict, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as tmp_file:
            torch.save(payload, tmp_file)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp_name)
        raise


def load_checkpoint(
    path: str | Path,
    *,
    model: nn.Module,
    optimizer: Optimizer,
    scheduler: Any,
    sampler: ResumableSampler,
    map_location: str | torch.device = "cpu",
) -> TrainingProgress:
    """Restore model, optimiser, scheduler and RNG state from ``path``.

    ``model`` and ``optimizer`` must already be constructed (and, for a CUDA
    resume, already moved to their target device) exactly as they were in
    the original run -- ``load_state_dict`` matches by parameter name and
    shape, and ``Optimizer.load_state_dict`` places restored state tensors
    onto each parameter's current device automatically.

    ``sampler`` must be seeded with the same ``dataset_size``/``shuffle`` as
    the original run; this function sets its epoch to match the checkpoint
    but does not reconstruct it from scratch. Its seed is cross-checked
    against the checkpoint's, since resuming with a different seed silently
    breaks the whole guarantee this module provides.

    Returns the ``TrainingProgress`` to pass to ``iter_training_batches`` as
    ``start_progress``.
    """
    payload = torch.load(path, map_location=map_location)
    if payload.get("format_version") != _FORMAT_VERSION:
        raise ValueError(
            f"unsupported checkpoint format_version={payload.get('format_version')!r} "
            f"in {path} (expected {_FORMAT_VERSION})"
        )
    if payload["seed"] != sampler.seed:
        raise ValueError(
            f"checkpoint was written with seed={payload['seed']!r} but the "
            f"sampler passed to load_checkpoint has seed={sampler.seed!r}; "
            "resuming with a different seed breaks bit-exact resume"
        )

    model.load_state_dict(payload["model"])
    optimizer.load_state_dict(payload["optimizer"])
    if scheduler is not None:
        if payload["scheduler"] is None:
            raise ValueError("checkpoint has no scheduler state but a scheduler was passed")
        scheduler.load_state_dict(payload["scheduler"])

    progress = TrainingProgress(**payload["progress"])
    sampler.set_epoch(progress.epoch)
    RngState.from_dict(payload["rng"]).restore()
    return progress


# --------------------------------------------------------------------------
# CI verification helper
# --------------------------------------------------------------------------


class RunComponents(NamedTuple):
    """Everything ``verify_resume`` needs to build one instance of the run.

    ``model`` and ``optimizer`` must already be on the device training runs
    on (``verify_resume`` does not move them, to avoid the well-known
    footgun of moving a module *after* its optimiser was constructed over
    its original, pre-move parameters).
    """

    model: nn.Module
    optimizer: Optimizer
    scheduler: Any
    dataset: Dataset


BuildComponentsFn = Callable[[], RunComponents]
TrainStepFn = Callable[[nn.Module, Optimizer, Any, Any], float]


def verify_resume(
    checkpoint_path: str | Path,
    build_components: BuildComponentsFn,
    train_step: TrainStepFn,
    *,
    num_verify_steps: int = 20,
    batch_size: int = 8,
    num_workers: int = 2,
    device: str | torch.device = "cuda",
) -> None:
    """CI check: assert resuming from ``checkpoint_path`` is bit-exact.

    Reruns the same configuration twice with the seed recorded in the
    checkpoint:

    1. From scratch (epoch 0, step 0), straight through to
       ``num_verify_steps`` steps past the checkpoint's step. Under the
       determinism guarantee this module provides, this is exactly what an
       uninterrupted run would have produced at that point, regardless of
       whether it was actually ever interrupted there -- so it serves as the
       reference for what should have happened.
    2. Loaded from ``checkpoint_path`` and continued for ``num_verify_steps``
       steps.

    Fails with a descriptive ``AssertionError`` the moment the two runs'
    per-step losses, or their final model weights, disagree at all -- exact
    equality, not a tolerance, since any drift here is exactly the kind of
    silent divergence that would invalidate a loss-curve comparison.

    ``build_components`` and ``train_step`` are supplied by the caller
    because this module has no knowledge of the concrete model or training
    step; they must be deterministic given the process-wide RNG state set by
    ``enable_deterministic_mode``, which this function calls internally
    before each of the two runs.
    """
    payload = torch.load(checkpoint_path, map_location="cpu")
    if payload.get("format_version") != _FORMAT_VERSION:
        raise ValueError(
            f"unsupported checkpoint format_version={payload.get('format_version')!r} "
            f"in {checkpoint_path} (expected {_FORMAT_VERSION})"
        )
    seed = payload["seed"]
    checkpoint_progress = TrainingProgress(**payload["progress"])
    target_step = checkpoint_progress.global_step + num_verify_steps

    def run(*, resume: bool) -> tuple[list[float], dict[str, torch.Tensor]]:
        enable_deterministic_mode(seed)
        components = build_components()
        sampler = ResumableSampler(len(components.dataset), seed=seed)

        if resume:
            start_progress = load_checkpoint(
                checkpoint_path,
                model=components.model,
                optimizer=components.optimizer,
                scheduler=components.scheduler,
                sampler=sampler,
                map_location=device,
            )
        else:
            start_progress = TrainingProgress(epoch=0, batches_done=0, global_step=0)

        losses: list[float] = []
        for progress, batch in iter_training_batches(
            components.dataset,
            sampler,
            seed=seed,
            start_progress=start_progress,
            batch_size=batch_size,
            num_workers=num_workers,
        ):
            losses.append(
                train_step(components.model, components.optimizer, components.scheduler, batch)
            )
            if progress.global_step >= target_step:
                break

        final_state = {
            name: tensor.detach().to("cpu").clone()
            for name, tensor in components.model.state_dict().items()
        }
        return losses, final_state

    reference_losses, reference_state = run(resume=False)
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    resumed_losses, resumed_state = run(resume=True)

    reference_tail = reference_losses[-num_verify_steps:]
    if reference_tail != resumed_losses:
        first_bad = next(
            i
            for i, (ref, got) in enumerate(zip(reference_tail, resumed_losses))
            if ref != got
        )
        raise AssertionError(
            "resume is not bit-exact: loss diverges at post-resume step "
            f"{first_bad + 1}/{num_verify_steps} "
            f"(global step {checkpoint_progress.global_step + first_bad + 1}): "
            f"expected {reference_tail[first_bad]!r}, got {resumed_losses[first_bad]!r}"
        )

    mismatched = sorted(
        name for name in reference_state if not torch.equal(reference_state[name], resumed_state[name])
    )
    if mismatched:
        raise AssertionError(
            "resume is not bit-exact: weight tensors differ after "
            f"{num_verify_steps} post-resume steps: {mismatched[:10]}"
            + (f" (+{len(mismatched) - 10} more)" if len(mismatched) > 10 else "")
        )
