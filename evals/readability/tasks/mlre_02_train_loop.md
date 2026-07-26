# mlre_02_train_loop

Write `train.py`, the training entry point for a small transformer LM.

It needs:

- Config from a dataclass with CLI overrides.
- Gradient accumulation to hit a target tokens-per-step, with the micro-batch
  size derived from it.
- AdamW with decoupled weight decay applied only to matmul parameters (not
  biases or norm gains), a warmup-then-cosine LR schedule with a floor, and
  gradient clipping.
- Mixed precision with bf16 autocast, and a loss scaler path for fp16.
- Periodic evaluation on a held-out split, with the eval loss and perplexity
  logged alongside throughput in tokens/second and an MFU estimate.
- Checkpointing every N steps: model, optimiser, scheduler, step, RNG state.
  Keep the last K checkpoints and the best-by-eval-loss one. Resume must be
  exact.
- Structured logging to JSONL and a console line per log interval.
- Graceful handling of a SIGTERM: checkpoint, then exit.

PyTorch is available. Assume `model.py` exposes `GPT(config)` and the data
loader is `data.get_batch(split, batch_size, seq_len, device)`.
