"""Training entry point for a small transformer LM.

Usage:
    python train.py --max-steps 20000 --tokens-per-step 262144 --max-lr 3e-4

All configuration lives in `TrainConfig` below; every field can be overridden
from the command line as `--field-name value` (see `build_config_from_args`).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import random
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch

import data
from model import GPT

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


@dataclass
class TrainConfig:
    # Model architecture (forwarded to GPT's config object).
    vocab_size: int = 50304
    n_layer: int = 12
    n_head: int = 12
    n_embd: int = 768
    seq_len: int = 1024
    dropout: float = 0.0
    bias: bool = True

    # Batch composition. `micro_batch_size` is NOT set directly: it is derived
    # from `tokens_per_step` and `max_micro_batch_size` (a memory ceiling) so
    # that grad_accum_steps * micro_batch_size * seq_len tracks the target as
    # closely as an integer micro-batch size allows.
    tokens_per_step: int = 2**19  # ~524k tokens per optimizer step
    max_micro_batch_size: int = 64

    # Optimizer (decoupled AdamW).
    max_lr: float = 6e-4
    min_lr: float = 6e-5
    weight_decay: float = 0.1
    beta1: float = 0.9
    beta2: float = 0.95
    grad_clip: float = 1.0

    # Warmup-then-cosine LR schedule.
    warmup_steps: int = 2000
    max_steps: int = 600_000
    lr_decay_steps: int = -1  # -1 means "use max_steps"

    # Mixed precision: "bfloat16", "float16", or "float32".
    dtype: str = "bfloat16"

    # Periodic evaluation.
    eval_interval: int = 2000
    eval_iters: int = 200

    # Early stopping: halt when eval loss has gone `early_stopping_patience`
    # consecutive evaluations without improving by at least
    # `early_stopping_min_delta`. Off by default.
    early_stopping: bool = False
    early_stopping_patience: int = 5
    early_stopping_min_delta: float = 0.0

    # Logging.
    log_interval: int = 10
    log_file: str = "train_log.jsonl"

    # Checkpointing.
    out_dir: str = "out"
    ckpt_interval: int = 1000
    keep_last_k: int = 3
    resume: bool = False

    # Misc.
    seed: int = 1337
    device: str = "cuda"
    compile_model: bool = False
    peak_flops: float = 0.0  # 0 => infer from GPU name, else used as-is (FLOPs/s)


def _str2bool(value: str) -> bool:
    if value.lower() in ("1", "true", "yes", "y"):
        return True
    if value.lower() in ("0", "false", "no", "n"):
        return False
    raise argparse.ArgumentTypeError(f"expected a boolean value, got {value!r}")


def build_config_from_args(argv: list[str] | None = None) -> TrainConfig:
    """Build a TrainConfig from defaults, overridden by CLI flags.

    Every dataclass field becomes a `--field-name` flag; only flags actually
    passed on the command line override the dataclass default.
    """
    defaults = TrainConfig()
    parser = argparse.ArgumentParser(description="Train a small transformer LM.")
    for field in dataclasses.fields(TrainConfig):
        flag = f"--{field.name.replace('_', '-')}"
        default_value = getattr(defaults, field.name)
        if isinstance(default_value, bool):
            parser.add_argument(flag, type=_str2bool, default=None)
        else:
            parser.add_argument(flag, type=type(default_value), default=None)
    args = parser.parse_args(argv)
    overrides = {k: v for k, v in vars(args).items() if v is not None}
    return dataclasses.replace(defaults, **overrides)


# --------------------------------------------------------------------------
# Batch schedule: derive micro-batch size + grad accumulation from tokens/step
# --------------------------------------------------------------------------


def derive_batch_schedule(cfg: TrainConfig) -> tuple[int, int]:
    """Return (micro_batch_size, grad_accum_steps) to hit ~tokens_per_step.

    `max_micro_batch_size` is treated as a memory ceiling: we pick the fewest
    accumulation steps that keep the micro-batch within that ceiling, then
    size the micro-batch to make the product match the token target as
    closely as an integer micro-batch allows.
    """
    tokens_per_micro_ceiling = cfg.max_micro_batch_size * cfg.seq_len
    grad_accum_steps = max(1, math.ceil(cfg.tokens_per_step / tokens_per_micro_ceiling))
    micro_batch_size = max(1, round(cfg.tokens_per_step / (grad_accum_steps * cfg.seq_len)))
    micro_batch_size = min(micro_batch_size, cfg.max_micro_batch_size)
    return micro_batch_size, grad_accum_steps


# --------------------------------------------------------------------------
# Optimizer: decoupled weight decay applied only to matmul (ndim >= 2) params
# --------------------------------------------------------------------------


def configure_optimizer(model: torch.nn.Module, cfg: TrainConfig) -> torch.optim.Optimizer:
    decay, no_decay = [], []
    for param in model.parameters():
        if not param.requires_grad:
            continue
        (decay if param.dim() >= 2 else no_decay).append(param)

    param_groups = [
        {"params": decay, "weight_decay": cfg.weight_decay},
        {"params": no_decay, "weight_decay": 0.0},
    ]
    extra_kwargs: dict[str, Any] = {}
    if "cuda" in cfg.device and torch.cuda.is_available():
        try:
            extra_kwargs["fused"] = True
            return torch.optim.AdamW(
                param_groups, lr=cfg.max_lr, betas=(cfg.beta1, cfg.beta2), **extra_kwargs
            )
        except TypeError:
            pass  # older torch without `fused` support
    return torch.optim.AdamW(param_groups, lr=cfg.max_lr, betas=(cfg.beta1, cfg.beta2))


# --------------------------------------------------------------------------
# LR schedule: linear warmup, then cosine decay to a floor. Pure function of
# `step`, so persisting `step` in a checkpoint is sufficient to resume the
# schedule exactly -- no separate scheduler object/state is needed.
# --------------------------------------------------------------------------


def get_lr(step: int, cfg: TrainConfig) -> float:
    if step < cfg.warmup_steps:
        return cfg.max_lr * (step + 1) / max(1, cfg.warmup_steps)
    decay_steps = cfg.max_steps if cfg.lr_decay_steps < 0 else cfg.lr_decay_steps
    if step >= decay_steps:
        return cfg.min_lr
    decay_ratio = (step - cfg.warmup_steps) / max(1, decay_steps - cfg.warmup_steps)
    coeff = 0.5 * (1.0 + math.cos(math.pi * decay_ratio))
    return cfg.min_lr + coeff * (cfg.max_lr - cfg.min_lr)


# --------------------------------------------------------------------------
# MFU estimate (PaLM-style FLOPs-per-token approximation)
# --------------------------------------------------------------------------

_GPU_PEAK_FLOPS = {
    # Dense (non-sparse) tensor-core peak, bf16/fp16, FLOPs/s.
    "h100": 989e12,
    "a100": 312e12,
    "a10": 125e12,
    "v100": 125e12,
    "rtx 4090": 165e12,
    "rtx 3090": 71e12,
}


def infer_peak_flops(cfg: TrainConfig) -> float:
    if cfg.peak_flops > 0:
        return cfg.peak_flops
    if "cuda" not in cfg.device or not torch.cuda.is_available():
        return 0.0
    name = torch.cuda.get_device_name(cfg.device).lower()
    for key, flops in _GPU_PEAK_FLOPS.items():
        if key in name:
            return flops
    return 0.0  # unknown hardware: MFU cannot be estimated reliably


def count_non_embedding_params(model: torch.nn.Module) -> int:
    n_params = sum(p.numel() for p in model.parameters())
    wte = getattr(getattr(model, "transformer", None), "wte", None)
    if wte is not None and hasattr(wte, "weight"):
        n_params -= wte.weight.numel()
    return n_params


def estimate_mfu(
    num_params: int,
    cfg: TrainConfig,
    tokens_per_step: int,
    step_time_s: float,
    peak_flops: float,
) -> float | None:
    if peak_flops <= 0 or step_time_s <= 0:
        return None
    head_dim = cfg.n_embd // cfg.n_head
    flops_per_token = 6 * num_params + 12 * cfg.n_layer * cfg.n_head * head_dim * cfg.seq_len
    flops_per_step = flops_per_token * tokens_per_step
    flops_achieved = flops_per_step / step_time_s
    return flops_achieved / peak_flops


# --------------------------------------------------------------------------
# Structured logging: one JSON object per line, plus a console echo.
# --------------------------------------------------------------------------


class JsonlLogger:
    def __init__(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._file = open(path, "a", buffering=1)

    def log(self, record: dict[str, Any]) -> None:
        self._file.write(json.dumps(record) + "\n")

    def close(self) -> None:
        self._file.close()


def log_console_line(record: dict[str, Any]) -> None:
    if record["kind"] == "train":
        mfu = record["mfu"]
        mfu_str = f"{mfu * 100:.2f}%" if mfu is not None else "n/a"
        print(
            f"step {record['step']:7d} | loss {record['loss']:.4f} | "
            f"lr {record['lr']:.3e} | {record['tokens_per_sec']:,.0f} tok/s | "
            f"mfu {mfu_str}"
        )
    else:
        print(
            f"step {record['step']:7d} | eval loss {record['eval_loss']:.4f} | "
            f"ppl {record['perplexity']:.2f}"
        )


# --------------------------------------------------------------------------
# Checkpointing: model, optimizer, step, RNG state. `step` alone reconstructs
# scheduler state (see get_lr). Keep the last K rotating checkpoints plus a
# separate best-by-eval-loss checkpoint that is never rotated out.
# --------------------------------------------------------------------------


def _rng_state() -> dict[str, Any]:
    return {
        "python": random.getstate(),
        "torch": torch.get_rng_state(),
        "cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
    }


def _restore_rng_state(state: dict[str, Any]) -> None:
    random.setstate(state["python"])
    torch.set_rng_state(state["torch"])
    if state["cuda"] is not None and torch.cuda.is_available():
        torch.cuda.set_rng_state_all(state["cuda"])


def build_checkpoint(
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    cfg: TrainConfig,
    step: int,
    best_val_loss: float,
) -> dict[str, Any]:
    return {
        "model": model.state_dict(),
        "optimizer": optimizer.state_dict(),
        "step": step,
        "best_val_loss": best_val_loss,
        "config": dataclasses.asdict(cfg),
        "rng_state": _rng_state(),
    }


def save_checkpoint(path: Path, checkpoint: dict[str, Any]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    torch.save(checkpoint, tmp_path)
    tmp_path.replace(path)  # atomic on POSIX: no half-written checkpoint on crash


def _checkpoint_step(path: Path) -> int:
    return int(path.stem.split("step")[-1])


def rotate_checkpoints(out_dir: Path, keep_last_k: int) -> None:
    ckpts = sorted(out_dir.glob("ckpt_step*.pt"), key=_checkpoint_step)
    excess = len(ckpts) - keep_last_k
    for stale in ckpts[:excess]:
        stale.unlink(missing_ok=True)


def find_latest_checkpoint(out_dir: Path) -> Path | None:
    ckpts = sorted(out_dir.glob("ckpt_step*.pt"), key=_checkpoint_step)
    return ckpts[-1] if ckpts else None


def load_checkpoint(
    path: Path,
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    device: str,
) -> tuple[int, float]:
    checkpoint = torch.load(path, map_location=device)
    model.load_state_dict(checkpoint["model"])
    optimizer.load_state_dict(checkpoint["optimizer"])
    _restore_rng_state(checkpoint["rng_state"])
    return checkpoint["step"], checkpoint["best_val_loss"]


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------


@torch.no_grad()
def evaluate(
    model: torch.nn.Module,
    cfg: TrainConfig,
    micro_batch_size: int,
    autocast_ctx: torch.autocast,
) -> float:
    model.eval()
    losses = torch.zeros(cfg.eval_iters)
    for i in range(cfg.eval_iters):
        x, y = data.get_batch("val", micro_batch_size, cfg.seq_len, cfg.device)
        with autocast_ctx:
            _, loss = model(x, y)
        losses[i] = loss.item()
    model.train()
    return losses.mean().item()


# --------------------------------------------------------------------------
# Graceful SIGTERM handling
# --------------------------------------------------------------------------

_stop_requested = False


def _handle_sigterm(signum: int, frame: Any) -> None:
    global _stop_requested
    _stop_requested = True


# --------------------------------------------------------------------------
# Main training loop
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    cfg = build_config_from_args(argv)
    signal.signal(signal.SIGTERM, _handle_sigterm)

    out_dir = Path(cfg.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    random.seed(cfg.seed)
    torch.manual_seed(cfg.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(cfg.seed)

    device_type = "cuda" if "cuda" in cfg.device else "cpu"
    if device_type == "cpu" and cfg.dtype == "float16":
        raise ValueError("float16 mixed precision requires a CUDA device")
    ptdtype = {"float32": torch.float32, "bfloat16": torch.bfloat16, "float16": torch.float16}[
        cfg.dtype
    ]
    autocast_enabled = cfg.dtype != "float32"
    autocast_ctx = torch.autocast(device_type=device_type, dtype=ptdtype, enabled=autocast_enabled)
    scaler = torch.cuda.amp.GradScaler(enabled=(cfg.dtype == "float16"))

    micro_batch_size, grad_accum_steps = derive_batch_schedule(cfg)
    tokens_per_step = micro_batch_size * grad_accum_steps * cfg.seq_len
    print(
        f"batch schedule: micro_batch_size={micro_batch_size} "
        f"grad_accum_steps={grad_accum_steps} -> {tokens_per_step:,} tokens/step "
        f"(target was {cfg.tokens_per_step:,})"
    )

    model_config = argparse.Namespace(
        vocab_size=cfg.vocab_size,
        n_layer=cfg.n_layer,
        n_head=cfg.n_head,
        n_embd=cfg.n_embd,
        block_size=cfg.seq_len,
        dropout=cfg.dropout,
        bias=cfg.bias,
    )
    model = GPT(model_config)
    model.to(cfg.device)
    if cfg.compile_model:
        model = torch.compile(model)

    optimizer = configure_optimizer(model, cfg)

    step = 0
    best_val_loss = float("inf")
    if cfg.resume:
        latest = find_latest_checkpoint(out_dir)
        if latest is None:
            print(f"--resume set but no checkpoint found in {out_dir}; starting from scratch")
        else:
            step, best_val_loss = load_checkpoint(latest, model, optimizer, cfg.device)
            print(f"resumed from {latest} at step {step} (best_val_loss={best_val_loss:.4f})")

    num_params = count_non_embedding_params(model)
    peak_flops = infer_peak_flops(cfg)
    logger = JsonlLogger(str(out_dir / cfg.log_file))

    def checkpoint_now(current_step: int) -> None:
        ckpt = build_checkpoint(model, optimizer, cfg, current_step, best_val_loss)
        save_checkpoint(out_dir / f"ckpt_step{current_step:08d}.pt", ckpt)
        rotate_checkpoints(out_dir, cfg.keep_last_k)

    evals_without_improvement = 0
    stop_reason: str | None = None

    try:
        step_start = time.time()
        while step < cfg.max_steps:
            if _stop_requested:
                print(f"SIGTERM received at step {step}: checkpointing and exiting")
                checkpoint_now(step)
                logger.close()
                sys.exit(0)

            lr = get_lr(step, cfg)
            for group in optimizer.param_groups:
                group["lr"] = lr

            optimizer.zero_grad(set_to_none=True)
            loss_accum = 0.0
            for _ in range(grad_accum_steps):
                x, y = data.get_batch("train", micro_batch_size, cfg.seq_len, cfg.device)
                with autocast_ctx:
                    _, loss = model(x, y)
                    loss = loss / grad_accum_steps
                loss_accum += loss.item()
                scaler.scale(loss).backward()

            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), cfg.grad_clip)
            scaler.step(optimizer)
            scaler.update()

            if device_type == "cuda":
                torch.cuda.synchronize()
            now = time.time()
            step_time = now - step_start
            step_start = now
            tokens_per_sec = tokens_per_step / step_time if step_time > 0 else 0.0

            if step % cfg.log_interval == 0:
                mfu = estimate_mfu(num_params, cfg, tokens_per_step, step_time, peak_flops)
                record = {
                    "kind": "train",
                    "step": step,
                    "loss": loss_accum,
                    "lr": lr,
                    "tokens_per_sec": tokens_per_sec,
                    "mfu": mfu,
                    "time": now,
                }
                logger.log(record)
                log_console_line(record)

            if step > 0 and step % cfg.eval_interval == 0:
                eval_loss = evaluate(model, cfg, micro_batch_size, autocast_ctx)
                perplexity = math.exp(min(eval_loss, 20.0))  # guard against overflow
                record = {
                    "kind": "eval",
                    "step": step,
                    "eval_loss": eval_loss,
                    "perplexity": perplexity,
                    "tokens_per_sec": tokens_per_sec,
                }
                logger.log(record)
                log_console_line(record)

                if eval_loss < best_val_loss - cfg.early_stopping_min_delta:
                    best_val_loss = eval_loss
                    evals_without_improvement = 0
                    best_ckpt = build_checkpoint(model, optimizer, cfg, step, best_val_loss)
                    save_checkpoint(out_dir / "ckpt_best.pt", best_ckpt)
                else:
                    evals_without_improvement += 1

                if cfg.early_stopping and evals_without_improvement >= cfg.early_stopping_patience:
                    stop_reason = (
                        f"eval loss did not improve by at least {cfg.early_stopping_min_delta} "
                        f"for {cfg.early_stopping_patience} consecutive evaluations"
                    )
                    break

            if step > 0 and step % cfg.ckpt_interval == 0:
                checkpoint_now(step)

            step += 1

        checkpoint_now(step)
        if stop_reason is not None:
            logger.log(
                {
                    "kind": "early_stop",
                    "step": step,
                    "reason": stop_reason,
                    "best_val_loss": best_val_loss,
                }
            )
            print(
                f"early stopping at step {step}: {stop_reason} "
                f"(best_val_loss={best_val_loss:.4f}, kept at {out_dir / 'ckpt_best.pt'})"
            )
        else:
            logger.log({"kind": "done", "step": step, "best_val_loss": best_val_loss})
            print(f"training complete at step {step}; best_val_loss={best_val_loss:.4f}")
    finally:
        logger.close()


if __name__ == "__main__":
    main()
