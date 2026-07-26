"""Checkpoint and resume layer for bit-exact training resume.

The guarantee this module implements: a run killed after completing step N and
resumed from the step-N checkpoint produces the same loss curve, the same
weight tensors, and the same sampled batches as a run that was never
interrupted. Concretely, "same" means bit-for-bit equal tensors, not
"statistically similar" -- there is no tolerance built in anywhere below.

Assumptions this design is built around (stated once, relied on everywhere):

  * PyTorch, CUDA, bf16 autocast. bf16 needs no loss-scaling, so there is no
    `GradScaler` state to checkpoint. (If this is ever changed to fp16, add
    `scaler.state_dict()` to the payload -- it is intentionally absent here,
    not forgotten.)
  * A map-style `Dataset` read through a `DataLoader` with `num_workers > 0`
    and a shuffling sampler.
  * Checkpoints are only ever taken at an optimizer-step boundary -- i.e.
    after a full (possibly gradient-accumulated) step has been applied, never
    mid-accumulation-window. Resuming mid-accumulation would require also
    snapshotting the partially-accumulated, not-yet-applied gradients, which
    this module does not do.
  * Single GPU. Multi-GPU/DDP needs the same ideas per rank (each rank
    checkpoints its own RNG state and its own shard of the data stream) plus
    a rank-aware sampler; see the note at the bottom of the module docstring
    in REPORT.md.

Bit-exactness on GPU is a property of the *environment* as much as the code:
cuDNN algorithm selection, `torch.backends.cudnn.benchmark`, and several CUDA
kernels are non-deterministic by default. This module does not silently flip
those global flags for you (that is a training-script concern, done once at
startup), but it records them in every checkpoint's manifest and
`verify_resume` will flag a mismatch between the environment that produced a
checkpoint and the environment trying to resume from it.

The five moving parts, and where they live below:

  1. Model / optimizer / scheduler state       -> `save_checkpoint` / `load_checkpoint`
  2. Data pipeline position (incl. shuffling)   -> `ResumableShuffleSampler`, `EpochProgress`
  3. Per-sample stochastic augmentation         -> `per_sample_generator` (a contract for Dataset authors)
  4. Global RNG state (dropout, etc.)           -> `RNGState`
  5. CI guarantee check                         -> `verify_resume`
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import platform
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, Union

import torch
from torch.utils.data import DataLoader, Dataset, Sampler

try:
    import numpy as np
except ImportError:  # numpy is optional; only used if the caller's code uses it
    np = None  # type: ignore[assignment]


CHECKPOINT_FORMAT_VERSION = 1

_REQUIRED_PAYLOAD_KEYS = (
    "format_version",
    "step",
    "epoch",
    "samples_consumed_in_epoch",
    "base_seed",
    "model_state_dict",
    "optimizer_state_dict",
    "sampler_state_dict",
    "rng_state",
)

# Environment fields whose mismatch between save-time and resume-time breaks
# the bit-exact guarantee outright (as opposed to fields kept only for
# provenance/debugging).
_DETERMINISM_CRITICAL_ENV_FIELDS = (
    "torch_version",
    "cuda_version",
    "cudnn_deterministic",
    "deterministic_algorithms",
    "cublas_workspace_config",
    "gpu_name",
    "gpu_capability",
)


# --------------------------------------------------------------------------
# Seed mixing
# --------------------------------------------------------------------------


def _mix_seed(*parts: Any) -> int:
    """Combine arbitrary parts into a well-distributed non-negative 63-bit int.

    Plain addition (e.g. `base_seed + epoch * 1000 + index`) can collide
    across different (epoch, index) pairs and, for some generator algorithms,
    correlate neighbouring seeds. Hashing the parts avoids both problems and
    keeps the mixing a pure, order-sensitive function of its inputs -- the
    same inputs always produce the same seed, on any machine, in any process.
    """
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "little") & ((1 << 63) - 1)


def per_sample_seed(base_seed: int, epoch: int, index: int) -> int:
    """Deterministic seed for dataset item `index` in a given epoch.

    Contract for Dataset authors: any stochastic augmentation inside
    `Dataset.__getitem__` MUST derive its randomness from a generator seeded
    with `per_sample_seed(base_seed, epoch, index)` (see
    `per_sample_generator`), not from the global `random` / `np.random` /
    `torch` RNG.

    Why this is mandatory, not a style preference: with `num_workers > 0`,
    the DataLoader assigns dataset indices to worker processes round-robin
    based on each index's *position in the current iterator's sequence*
    (`position % num_workers`), not the index itself. On a fresh, uninterrupted
    epoch, worker `w` sees positions `w, w + num_workers, w + 2*num_workers, ...`.
    On resume from a mid-epoch checkpoint at sample offset K, the resumed run
    builds a *new* iterator starting its own position counter at 0 over the
    remaining `perm[K:]` slice -- so worker `w` in the resumed run now sees
    the *global* positions `K, K + num_workers, ...`, which is the same set
    only when `K % num_workers == 0`. Whenever it isn't, a given dataset item
    lands on a different worker, at a different local call index, than it did
    in the uninterrupted run. If that worker's augmentation randomness comes
    from a per-worker sequential stream (e.g. seeded once at worker startup),
    the same item gets a *different* random draw after resume than it got
    before the kill, silently breaking bit-exactness for every augmented
    sample from the resume point onward.

    Seeding per *index* instead of per *worker-and-call-order* makes an
    item's randomness independent of which worker fetches it and independent
    of where in the epoch resume happens, so this failure mode cannot occur.
    """
    return _mix_seed(base_seed, epoch, index)


def per_sample_generator(
    base_seed: int, epoch: int, index: int, device: Union[str, torch.device] = "cpu"
) -> torch.Generator:
    """A `torch.Generator` seeded deterministically for dataset item `index`.

    Pass this as the `generator=` argument to any randomness-consuming
    transform/augmentation inside `Dataset.__getitem__`.
    """
    generator = torch.Generator(device=device)
    generator.manual_seed(per_sample_seed(base_seed, epoch, index))
    return generator


def make_worker_init_fn(base_seed: int, epoch: int) -> Callable[[int], None]:
    """Seed each DataLoader worker's global RNGs deterministically.

    This is a *secondary* safety net, not the primary mechanism: it makes any
    incidental use of the global `random` / `np.random` / `torch` RNG inside a
    worker reproducible run-to-run for a *fresh, uninterrupted* epoch, and it
    keeps worker startup itself deterministic. It does NOT by itself fix the
    worker-parity problem described in `per_sample_seed` above -- augmentation
    code must still use `per_sample_generator` keyed on the dataset index for
    resume-safety with `num_workers > 0`.
    """

    def _worker_init_fn(worker_id: int) -> None:
        seed = _mix_seed(base_seed, epoch, "worker", worker_id)
        random.seed(seed)
        if np is not None:
            np.random.seed(seed % (2**32 - 1))
        torch.manual_seed(seed)

    return _worker_init_fn


# --------------------------------------------------------------------------
# Resumable, deterministic shuffling sampler
# --------------------------------------------------------------------------


class ResumableShuffleSampler(Sampler[int]):
    """A shuffling `Sampler` whose order is a pure function of (seed, epoch)
    and which can fast-forward to a mid-epoch offset without touching the
    dataset.

    `torch.utils.data.RandomSampler` is not resume-safe: its shuffle draws
    from a `Generator` whose *runtime* state you would have to snapshot and
    restore exactly, including however far into the epoch it had already
    advanced -- fragile, and it still does not survive being wrapped by
    `DataLoader`'s own iterator machinery cleanly across a process restart.

    Instead, this sampler recomputes the *entire* epoch's permutation
    directly from `(base_seed, epoch)` every time it is iterated:

        perm = randperm(len(dataset), generator=Generator().manual_seed(mix(base_seed, epoch)))

    That permutation is bytewise identical whenever it is recomputed, in any
    process, given the same two integers. Resuming mid-epoch is then just
    `perm[start_index:]` -- no different, numerically, from an uninterrupted
    run reaching the same point and continuing. `set_start_index` is O(1); it
    does not touch the dataset, so no compute is wasted re-deriving items
    that were already consumed before the kill.

    Cost note: recomputing `randperm` over the full dataset on every epoch
    (and every resume) is O(N). For datasets in the tens of millions of items
    this is sub-second; for datasets orders of magnitude larger, consider a
    block/shard-shuffle scheme instead of a single global permutation.
    """

    def __init__(
        self,
        dataset_len: int,
        base_seed: int,
        epoch: int = 0,
        start_index: int = 0,
    ) -> None:
        if dataset_len <= 0:
            raise ValueError(f"dataset_len must be positive, got {dataset_len}")
        if not 0 <= start_index <= dataset_len:
            raise ValueError(
                f"start_index {start_index} out of range for dataset_len {dataset_len}"
            )
        self.dataset_len = dataset_len
        self.base_seed = base_seed
        self.epoch = epoch
        self.start_index = start_index

    def set_epoch(self, epoch: int) -> None:
        """Call at the start of each new epoch (resets the offset to 0)."""
        self.epoch = epoch
        self.start_index = 0

    def set_start_index(self, start_index: int) -> None:
        """Fast-forward within the current epoch, e.g. after loading a checkpoint."""
        if not 0 <= start_index <= self.dataset_len:
            raise ValueError(
                f"start_index {start_index} out of range for dataset_len {self.dataset_len}"
            )
        self.start_index = start_index

    def _permutation(self) -> torch.Tensor:
        generator = torch.Generator()
        generator.manual_seed(_mix_seed(self.base_seed, self.epoch))
        return torch.randperm(self.dataset_len, generator=generator)

    def __iter__(self) -> Iterator[int]:
        yield from self._permutation()[self.start_index :].tolist()

    def __len__(self) -> int:
        return self.dataset_len - self.start_index

    def state_dict(self) -> dict:
        return {
            "base_seed": self.base_seed,
            "epoch": self.epoch,
            "start_index": self.start_index,
            "dataset_len": self.dataset_len,
        }

    def load_state_dict(self, state: dict) -> None:
        if state["dataset_len"] != self.dataset_len:
            raise ValueError(
                "checkpoint's dataset length "
                f"({state['dataset_len']}) does not match the current dataset "
                f"length ({self.dataset_len}); batch order cannot be "
                "guaranteed identical after a dataset change"
            )
        self.base_seed = state["base_seed"]
        self.epoch = state["epoch"]
        self.start_index = state["start_index"]


def build_resumable_dataloader(
    dataset: Dataset,
    *,
    batch_size: int,
    base_seed: int,
    epoch: int,
    start_index: int = 0,
    num_workers: int = 4,
    drop_last: bool = True,
    pin_memory: bool = True,
    prefetch_factor: Optional[int] = 2,
    collate_fn: Optional[Callable] = None,
) -> tuple[DataLoader, ResumableShuffleSampler]:
    """Build a `DataLoader` + `ResumableShuffleSampler` pair wired correctly
    for bit-exact resume.

    `persistent_workers=False` is intentional and not configurable here: with
    persistent workers, worker processes (and any residual global RNG state
    inside them) stay alive and mutate across epoch boundaries in a way this
    module has no visibility into, and `worker_init_fn` would only fire once
    for the process's lifetime instead of once per epoch/resume. Recreating
    workers every epoch is exactly what a fresh resume already does, so this
    keeps "resume" and "ordinary epoch boundary" the same code path.
    """
    sampler = ResumableShuffleSampler(
        dataset_len=len(dataset),  # type: ignore[arg-type]
        base_seed=base_seed,
        epoch=epoch,
        start_index=start_index,
    )
    loader = DataLoader(
        dataset,
        batch_size=batch_size,
        sampler=sampler,
        drop_last=drop_last,
        num_workers=num_workers,
        pin_memory=pin_memory,
        persistent_workers=False,
        prefetch_factor=prefetch_factor if num_workers > 0 else None,
        worker_init_fn=make_worker_init_fn(base_seed, epoch) if num_workers > 0 else None,
        collate_fn=collate_fn,
    )
    return loader, sampler


# --------------------------------------------------------------------------
# Epoch / step progress tracker
# --------------------------------------------------------------------------


@dataclasses.dataclass
class EpochProgress:
    """Tracks where training is in (global step, epoch, position-in-epoch).

    `samples_consumed_in_epoch` is tracked as an explicit running count of
    actual batch sizes seen, not derived as `step_in_epoch * batch_size`, so
    it stays correct across an epoch's final, possibly shorter, batch.
    """

    global_step: int = 0
    epoch: int = 0
    samples_consumed_in_epoch: int = 0

    def record_batch(self, batch_size: int) -> None:
        self.global_step += 1
        self.samples_consumed_in_epoch += batch_size

    def start_new_epoch(self) -> None:
        self.epoch += 1
        self.samples_consumed_in_epoch = 0

    def state_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_state_dict(cls, state: dict) -> "EpochProgress":
        return cls(**state)


# --------------------------------------------------------------------------
# RNG state (dropout and any other stochastic layer)
# --------------------------------------------------------------------------


@dataclasses.dataclass
class RNGState:
    """Every RNG stream that can affect a training step's numerics.

    Covers Python's `random`, NumPy's global RNG (if NumPy is importable --
    captured on a best-effort basis since not all codebases use it), the
    Torch CPU RNG (used for CPU-side ops and as the seed source for DataLoader
    worker base seeds when `worker_init_fn` is not overridden), and the Torch
    CUDA RNG for the current device (dropout, or any other CUDA op that draws
    randomness, reads from this stream).

    Single-GPU only: for DDP, capture/restore one `RNGState` per rank, each
    tied to that rank's device, alongside a rank-aware data shard/sampler.
    """

    python_state: tuple
    numpy_state: Optional[tuple]
    torch_cpu_state: torch.Tensor
    torch_cuda_state: Optional[torch.Tensor]
    cuda_device_index: Optional[int]

    @classmethod
    def capture(cls) -> "RNGState":
        numpy_state = np.random.get_state() if np is not None else None
        cuda_state: Optional[torch.Tensor] = None
        cuda_device_index: Optional[int] = None
        if torch.cuda.is_available():
            cuda_device_index = torch.cuda.current_device()
            cuda_state = torch.cuda.get_rng_state(cuda_device_index)
        return cls(
            python_state=random.getstate(),
            numpy_state=numpy_state,
            torch_cpu_state=torch.get_rng_state(),
            torch_cuda_state=cuda_state,
            cuda_device_index=cuda_device_index,
        )

    def restore(self) -> None:
        random.setstate(self.python_state)
        if self.numpy_state is not None and np is not None:
            np.random.set_state(self.numpy_state)
        torch.set_rng_state(self.torch_cpu_state)
        if self.torch_cuda_state is not None and torch.cuda.is_available():
            torch.cuda.set_rng_state(self.torch_cuda_state, self.cuda_device_index)

    def to_dict(self) -> dict:
        return {
            "python_state": self.python_state,
            "numpy_state": self.numpy_state,
            "torch_cpu_state": self.torch_cpu_state,
            "torch_cuda_state": self.torch_cuda_state,
            "cuda_device_index": self.cuda_device_index,
        }

    @classmethod
    def from_dict(cls, state: dict) -> "RNGState":
        return cls(**state)


# --------------------------------------------------------------------------
# Environment / determinism fingerprint
# --------------------------------------------------------------------------


def capture_environment_fingerprint() -> dict:
    """Record everything that affects whether *this* environment can
    reproduce a checkpoint bit-for-bit, separate from the checkpoint's own
    saved state.

    `save_checkpoint` stores this at save time; `verify_resume` recomputes it
    at resume time and diffs the two. This module deliberately does not flip
    any of these flags itself (e.g. it will not silently set
    `cudnn.deterministic = True`) -- that is a training-script startup
    concern, done once, since it has a real performance cost. This function
    only ever observes and records.
    """
    cudnn_available = torch.backends.cudnn.is_available()
    cuda_available = torch.cuda.is_available()
    return {
        "python_version": platform.python_version(),
        "torch_version": torch.__version__,
        "cuda_version": torch.version.cuda,
        "cudnn_version": torch.backends.cudnn.version() if cudnn_available else None,
        "cudnn_deterministic": bool(torch.backends.cudnn.deterministic),
        "cudnn_benchmark": bool(torch.backends.cudnn.benchmark),
        "deterministic_algorithms": torch.are_deterministic_algorithms_enabled(),
        "cublas_workspace_config": os.environ.get("CUBLAS_WORKSPACE_CONFIG"),
        "pythonhashseed": os.environ.get("PYTHONHASHSEED"),
        "gpu_name": torch.cuda.get_device_name(0) if cuda_available else None,
        "gpu_capability": (
            list(torch.cuda.get_device_capability(0)) if cuda_available else None
        ),
    }


def seed_everything(seed: int) -> None:
    """One-time process-startup seeding for a fresh (non-resumed) run.

    On resume, do not call this -- `load_checkpoint` restores the exact RNG
    state that was captured at save time instead, which is what actually
    matters for bit-exactness. This helper is only for establishing the
    starting point of a run that was never interrupted, so that its very
    first checkpoint has a well-defined `base_seed` provenance.
    """
    random.seed(seed)
    if np is not None:
        np.random.seed(seed % (2**32 - 1))
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


# --------------------------------------------------------------------------
# Save / load
# --------------------------------------------------------------------------


def _sha256_of_file(path: Union[str, Path]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_path(checkpoint_path: Union[str, Path]) -> Path:
    return Path(str(checkpoint_path) + ".manifest.json")


def _atomic_write_bytes(path: Path, write_fn: Callable[[Any], None]) -> None:
    """Write via a temp file in the same directory + `os.replace`, so a kill
    mid-write can never leave a truncated or half-written file at `path`.
    `os.replace` is atomic as long as source and destination are on the same
    filesystem, which the shared parent directory guarantees.
    """
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        with open(tmp_path, "wb") as f:
            write_fn(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)


def save_checkpoint(
    checkpoint_path: Union[str, Path],
    *,
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: Optional[Any],
    sampler: ResumableShuffleSampler,
    progress: EpochProgress,
    base_seed: int,
    extra: Optional[dict] = None,
) -> None:
    """Atomically write a checkpoint plus a small JSON manifest alongside it.

    The checkpoint file (`checkpoint_path`) is a normal `torch.save` payload,
    loadable with plain `torch.load` by anyone. The manifest
    (`checkpoint_path + ".manifest.json"`) is a cheap-to-read sidecar with a
    checksum and the determinism-critical environment fingerprint, so
    `verify_resume` (and a human) can sanity-check a checkpoint without
    deserializing the full tensor payload.

    Both files are written via write-to-temp-then-`os.replace`, so a process
    killed mid-write leaves either the previous good checkpoint or nothing --
    never a corrupt one at the canonical path.
    """
    checkpoint_path = Path(checkpoint_path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "format_version": CHECKPOINT_FORMAT_VERSION,
        "step": progress.global_step,
        "epoch": progress.epoch,
        "samples_consumed_in_epoch": progress.samples_consumed_in_epoch,
        "base_seed": base_seed,
        "model_state_dict": model.state_dict(),
        "optimizer_state_dict": optimizer.state_dict(),
        "scheduler_state_dict": scheduler.state_dict() if scheduler is not None else None,
        "sampler_state_dict": sampler.state_dict(),
        "rng_state": RNGState.capture().to_dict(),
        "extra": extra or {},
    }

    _atomic_write_bytes(checkpoint_path, lambda f: torch.save(payload, f))

    manifest = {
        "format_version": CHECKPOINT_FORMAT_VERSION,
        "step": progress.global_step,
        "epoch": progress.epoch,
        "samples_consumed_in_epoch": progress.samples_consumed_in_epoch,
        "base_seed": base_seed,
        "checkpoint_file": checkpoint_path.name,
        "checkpoint_sha256": _sha256_of_file(checkpoint_path),
        "checkpoint_bytes": checkpoint_path.stat().st_size,
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        "env_fingerprint": capture_environment_fingerprint(),
    }
    manifest_path = _manifest_path(checkpoint_path)
    _atomic_write_bytes(
        manifest_path,
        lambda f: f.write(json.dumps(manifest, indent=2, default=str).encode("utf-8")),
    )


def load_checkpoint(
    checkpoint_path: Union[str, Path],
    *,
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: Optional[Any],
    sampler: ResumableShuffleSampler,
    map_location: Union[str, torch.device] = "cuda",
) -> EpochProgress:
    """Restore model, optimizer, scheduler, sampler position, and global RNG
    state from a checkpoint written by `save_checkpoint`. Returns the
    `EpochProgress` the run should continue from.

    `weights_only=False` is required (and deliberate): the payload carries
    plain-Python RNG state and dicts alongside tensors, which `weights_only`
    loading rejects. This means `torch.load` here can execute arbitrary
    pickled objects -- only ever point this at checkpoints your own training
    job wrote, never at a checkpoint from an untrusted source.
    """
    payload = torch.load(checkpoint_path, map_location=map_location, weights_only=False)

    missing = [key for key in _REQUIRED_PAYLOAD_KEYS if key not in payload]
    if missing:
        raise ValueError(f"checkpoint at {checkpoint_path} is missing keys: {missing}")

    model.load_state_dict(payload["model_state_dict"])
    optimizer.load_state_dict(payload["optimizer_state_dict"])
    if scheduler is not None and payload.get("scheduler_state_dict") is not None:
        scheduler.load_state_dict(payload["scheduler_state_dict"])
    sampler.load_state_dict(payload["sampler_state_dict"])
    RNGState.from_dict(payload["rng_state"]).restore()

    return EpochProgress(
        global_step=payload["step"],
        epoch=payload["epoch"],
        samples_consumed_in_epoch=payload["samples_consumed_in_epoch"],
    )


# --------------------------------------------------------------------------
# verify_resume: the CI guarantee check
# --------------------------------------------------------------------------


@dataclasses.dataclass
class VerifyResult:
    ok: bool
    problems: list
    warnings: list

    def raise_if_failed(self) -> None:
        if not self.ok:
            raise AssertionError(
                "checkpoint failed bit-exact resume verification:\n"
                + "\n".join(f"  - {p}" for p in self.problems)
            )


def verify_resume(
    checkpoint_path: Union[str, Path],
    *,
    dataset_len: Optional[int] = None,
    strict_env: bool = True,
    model_factory: Optional[Callable[[], torch.nn.Module]] = None,
    optimizer_factory: Optional[Callable[[torch.nn.Module], torch.optim.Optimizer]] = None,
    scheduler_factory: Optional[Callable[[torch.optim.Optimizer], Any]] = None,
    dataset: Optional[Dataset] = None,
    step_fn: Optional[Callable[[torch.nn.Module, torch.optim.Optimizer, Any, Any], float]] = None,
    num_steps: int = 3,
    replay_batch_size: int = 8,
    device: Union[str, torch.device] = "cuda",
) -> VerifyResult:
    """CI helper: check that a checkpoint satisfies the bit-exact resume
    guarantee.

    Runs in two tiers:

    Tier 1 -- structural integrity and environment match (always runs, needs
    only `checkpoint_path`):
      * The checkpoint file's SHA-256 matches the manifest recorded at save
        time (catches truncation/corruption from a kill mid-write, or a
        transfer error -- the exact failure mode implied by "killed at step
        N", since a torn write would otherwise resume silently on
        subtly-wrong state).
      * All required payload keys are present and the format version is the
        one this module knows how to read.
      * The manifest's step/epoch/samples_consumed_in_epoch/base_seed agree
        with the values inside the payload itself.
      * `samples_consumed_in_epoch` is in `[0, dataset_len]`, if `dataset_len`
        is supplied.
      * The environment fingerprint recorded at save time matches the
        environment calling `verify_resume` now, for every field in
        `_DETERMINISM_CRITICAL_ENV_FIELDS`. Under `strict_env=True` (default)
        a mismatch is a hard failure; under `strict_env=False` it is
        downgraded to a warning, for cases where the operator is knowingly
        resuming on different hardware and accepts the risk.

    Tier 2 -- actual determinism replay (only runs if `model_factory`,
    `optimizer_factory`, `dataset`, and `step_fn` are all supplied): loads the
    checkpoint into two independently constructed model/optimizer/scheduler
    replicas, advances both `num_steps` training steps via `step_fn` reading
    from the checkpoint's exact resume position, and asserts the two replicas
    produce bit-identical losses and bit-identical resulting parameters.
    A checkpoint that cannot reproduce itself deterministically cannot match
    an original uninterrupted run either, so this is a necessary condition for
    the full guarantee, checked without needing the original multi-hour run
    to diff against.

    Tier 2 replays with `num_workers=0` for speed and to isolate
    model/optimizer/scheduler/sampler/RNG determinism from the DataLoader
    worker-parity subtlety described in `per_sample_seed`; it does not
    exercise the real multi-worker path. Treat it as a fast per-commit smoke
    test, and pair it with a slower, periodic integration test that resumes
    through the real `num_workers > 0` pipeline for full end-to-end coverage.
    """
    problems: list = []
    warnings: list = []

    checkpoint_path = Path(checkpoint_path)
    manifest_path = _manifest_path(checkpoint_path)

    if not checkpoint_path.exists():
        return VerifyResult(ok=False, problems=[f"checkpoint file not found: {checkpoint_path}"], warnings=[])
    if not manifest_path.exists():
        return VerifyResult(ok=False, problems=[f"manifest file not found: {manifest_path}"], warnings=[])

    try:
        manifest = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return VerifyResult(ok=False, problems=[f"manifest file is unreadable/corrupt: {exc}"], warnings=[])

    actual_sha256 = _sha256_of_file(checkpoint_path)
    if actual_sha256 != manifest.get("checkpoint_sha256"):
        problems.append(
            "checkpoint file checksum does not match its manifest "
            f"(expected {manifest.get('checkpoint_sha256')}, got {actual_sha256}) "
            "-- the file is corrupt or was truncated, likely by a kill mid-write"
        )

    # A checksum mismatch already tells us the file is not what it claims to
    # be; deserializing it anyway is still useful (it may well fail loudly,
    # which is additional evidence), but never let that exception escape a CI
    # helper whose entire job is to turn "this checkpoint is broken" into a
    # clean, reportable result instead of a crash.
    try:
        payload = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    except Exception as exc:  # noqa: BLE001 -- deliberately broad: any load failure is a reportable integrity problem
        problems.append(f"checkpoint payload failed to deserialize: {exc!r}")
        return VerifyResult(ok=False, problems=problems, warnings=warnings)

    missing_keys = [key for key in _REQUIRED_PAYLOAD_KEYS if key not in payload]
    if missing_keys:
        problems.append(f"checkpoint payload is missing required keys: {missing_keys}")

    if payload.get("format_version") != CHECKPOINT_FORMAT_VERSION:
        problems.append(
            f"checkpoint format_version {payload.get('format_version')!r} is not the "
            f"version this module reads ({CHECKPOINT_FORMAT_VERSION})"
        )

    for field in ("step", "epoch", "samples_consumed_in_epoch", "base_seed"):
        if manifest.get(field) != payload.get(field):
            problems.append(
                f"manifest/{field}={manifest.get(field)!r} disagrees with "
                f"payload/{field}={payload.get(field)!r}"
            )

    samples_consumed = payload.get("samples_consumed_in_epoch")
    if isinstance(samples_consumed, int):
        if samples_consumed < 0:
            problems.append(f"samples_consumed_in_epoch is negative: {samples_consumed}")
        if dataset_len is not None and samples_consumed > dataset_len:
            problems.append(
                f"samples_consumed_in_epoch ({samples_consumed}) exceeds dataset_len ({dataset_len})"
            )

    saved_env = manifest.get("env_fingerprint", {})
    current_env = capture_environment_fingerprint()
    for field in _DETERMINISM_CRITICAL_ENV_FIELDS:
        saved_value = saved_env.get(field)
        current_value = current_env.get(field)
        if saved_value != current_value:
            message = (
                f"determinism-critical environment field {field!r} differs: "
                f"checkpoint was saved with {saved_value!r}, current environment "
                f"has {current_value!r}"
            )
            if strict_env:
                problems.append(message)
            else:
                warnings.append(message)

    if model_factory is not None and optimizer_factory is not None and dataset is not None and step_fn is not None:
        replay_problems = _replay_and_compare(
            checkpoint_path,
            model_factory=model_factory,
            optimizer_factory=optimizer_factory,
            scheduler_factory=scheduler_factory,
            dataset=dataset,
            step_fn=step_fn,
            num_steps=num_steps,
            replay_batch_size=replay_batch_size,
            device=device,
        )
        problems.extend(replay_problems)
    else:
        warnings.append(
            "tier-2 determinism replay skipped: supply model_factory, "
            "optimizer_factory, dataset, and step_fn to run it"
        )

    return VerifyResult(ok=len(problems) == 0, problems=problems, warnings=warnings)


def _replay_and_compare(
    checkpoint_path: Union[str, Path],
    *,
    model_factory: Callable[[], torch.nn.Module],
    optimizer_factory: Callable[[torch.nn.Module], torch.optim.Optimizer],
    scheduler_factory: Optional[Callable[[torch.optim.Optimizer], Any]],
    dataset: Dataset,
    step_fn: Callable[[torch.nn.Module, torch.optim.Optimizer, Any, Any], float],
    num_steps: int,
    replay_batch_size: int,
    device: Union[str, torch.device],
) -> list:
    """Load the checkpoint into two independent replicas and compare their
    next `num_steps` of training bit-for-bit. Returns a list of problem
    strings (empty if the replicas agree everywhere)."""
    replicas = []
    for _ in range(2):
        model = model_factory().to(device)
        optimizer = optimizer_factory(model)
        scheduler = scheduler_factory(optimizer) if scheduler_factory is not None else None
        sampler = ResumableShuffleSampler(dataset_len=len(dataset), base_seed=0)  # type: ignore[arg-type]
        load_checkpoint(
            checkpoint_path,
            model=model,
            optimizer=optimizer,
            scheduler=scheduler,
            sampler=sampler,
            map_location=device,
        )
        loader = DataLoader(dataset, batch_size=replay_batch_size, sampler=sampler, drop_last=True, num_workers=0)

        losses = []
        loader_iter = iter(loader)
        for _ in range(num_steps):
            batch = next(loader_iter)
            losses.append(float(step_fn(model, optimizer, scheduler, batch)))

        final_state = {
            key: value.detach().to("cpu").clone()
            for key, value in model.state_dict().items()
            if torch.is_tensor(value)
        }
        replicas.append({"losses": losses, "state": final_state})

    problems: list = []
    replica_a, replica_b = replicas
    if replica_a["losses"] != replica_b["losses"]:
        problems.append(
            f"loss curves diverge across two replays of the same checkpoint: "
            f"{replica_a['losses']} vs {replica_b['losses']}"
        )
    for key in replica_a["state"]:
        if not torch.equal(replica_a["state"][key], replica_b["state"][key]):
            problems.append(
                f"parameter/buffer {key!r} diverges bit-for-bit across two replays "
                "of the same checkpoint"
            )
    return problems


# --------------------------------------------------------------------------
# CI entry point
# --------------------------------------------------------------------------


def _cli() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description=(
            "Verify that a training checkpoint satisfies the bit-exact resume "
            "guarantee (structural integrity + determinism-environment match; "
            "add --dataset-len for the sample-offset range check)."
        )
    )
    parser.add_argument("checkpoint_path", help="path to the checkpoint file")
    parser.add_argument("--dataset-len", type=int, default=None)
    parser.add_argument(
        "--no-strict-env",
        action="store_true",
        help="downgrade environment mismatches to warnings instead of failures",
    )
    args = parser.parse_args()

    result = verify_resume(
        args.checkpoint_path,
        dataset_len=args.dataset_len,
        strict_env=not args.no_strict_env,
    )
    for warning in result.warnings:
        print(f"WARN: {warning}")
    for problem in result.problems:
        print(f"FAIL: {problem}")

    if result.ok:
        print("OK: checkpoint passes bit-exact resume verification.")
        sys.exit(0)
    else:
        print("FAILED: checkpoint does not satisfy the bit-exact resume guarantee.")
        sys.exit(1)


if __name__ == "__main__":
    _cli()
