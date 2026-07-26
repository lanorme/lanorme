"""Preprocessing pipeline for language-model pretraining corpora.

Turns a directory of JSONL "web text" shards -- each line a JSON object
``{"id": ..., "url": ..., "text": ...}`` -- into packed, tokenised int32
sequences ready to feed a training data loader.

Pipeline stages (see ``prepare_pretrain_corpus`` for the orchestration):

1. Quality filtering (length, mean line length, symbol-to-word ratio,
   ellipsis-terminated lines).
2. Language filtering (English stopword hit rate).
3. Cross-shard near-duplicate removal using a from-scratch MinHash + LSH
   banding implementation.
4. Tokenisation with a caller-supplied tokeniser and packing into
   fixed-length sequences separated by an EOS token, with padding only in
   the final, trailing sequence of each split.
5. Deterministic train/val split keyed by a hash of the document id.
6. Output as ``.npy`` int32 arrays plus a JSON manifest of counts, drop
   reasons, and the configuration used.

Stages 1-2 and the tokenisation step are parallelised across input shards
with ``multiprocessing``. Only the standard library and NumPy are used.

Library usage::

    from prepare_pretrain_corpus import PipelineConfig, prepare_pretrain_corpus

    manifest = prepare_pretrain_corpus(
        input_dir="raw_shards/",
        output_dir="packed_corpus/",
        tokenizer=my_tokenizer,   # anything with .encode(str) -> list[int]
        config=PipelineConfig(sequence_length=2048, eos_token_id=1),
    )

Command-line usage (the tokeniser is supplied as a ``module:attribute``
import spec, since it cannot be passed on the command line directly)::

    python prepare_pretrain_corpus.py \\
        --input-dir raw_shards/ --output-dir packed_corpus/ \\
        --tokenizer mypkg.tokenizer:build_tokenizer
"""

from __future__ import annotations

import argparse
import dataclasses
import functools
import hashlib
import importlib
import json
import logging
import multiprocessing as mp
import os
import re
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Protocol

import numpy as np

logger = logging.getLogger("prepare_pretrain_corpus")


# ---------------------------------------------------------------------------
# Tokeniser interface
# ---------------------------------------------------------------------------


class Tokenizer(Protocol):
    """The only interface this pipeline requires of a tokeniser."""

    def encode(self, text: str) -> list[int]: ...


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PipelineConfig:
    """All tunable thresholds and knobs, recorded verbatim into the manifest."""

    # -- quality filtering --------------------------------------------------
    min_tokens: int = 50
    min_mean_line_length: float = 3.0
    max_symbol_to_word_ratio: float = 0.1
    max_ellipsis_line_fraction: float = 0.3

    # -- language filtering --------------------------------------------------
    min_stopword_hit_rate: float = 0.06

    # -- near-duplicate removal (MinHash + LSH) ------------------------------
    shingle_size: int = 5
    num_perm: int = 128
    num_bands: int = 32
    jaccard_threshold: float = 0.8
    minhash_seed: int = 1234

    # -- train/val split ------------------------------------------------------
    val_fraction: float = 0.02

    # -- tokenisation and packing --------------------------------------------
    sequence_length: int = 2048
    eos_token_id: int = 0
    sequences_per_file: int = 1024

    # -- execution ------------------------------------------------------------
    num_workers: int = 0  # 0 => os.cpu_count()

    def rows_per_band(self) -> int:
        if self.num_perm % self.num_bands != 0:
            raise ValueError(
                f"num_perm ({self.num_perm}) must be divisible by "
                f"num_bands ({self.num_bands})"
            )
        return self.num_perm // self.num_bands


# ---------------------------------------------------------------------------
# Text statistics shared by the quality and language filters
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"\w+", re.UNICODE)
_ELLIPSIS_RE = re.compile(r"(\.\.\.|…)\s*$")


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text)


def _non_empty_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Quality filtering
# ---------------------------------------------------------------------------


def quality_filter_reason(text: str, config: PipelineConfig) -> str | None:
    """Return the drop reason if ``text`` fails a quality gate, else None."""
    words = _words(text)
    if len(words) < config.min_tokens:
        return "too_short"

    lines = _non_empty_lines(text)
    if not lines:
        return "too_short"

    mean_line_length = sum(len(_WORD_RE.findall(line)) for line in lines) / len(lines)
    if mean_line_length < config.min_mean_line_length:
        return "low_mean_line_length"

    symbol_count = text.count("#") + text.count("…") + text.count("...")
    word_count = max(len(words), 1)
    if symbol_count / word_count > config.max_symbol_to_word_ratio:
        return "high_symbol_ratio"

    ellipsis_lines = sum(1 for line in lines if _ELLIPSIS_RE.search(line.strip()))
    if ellipsis_lines / len(lines) > config.max_ellipsis_line_fraction:
        return "high_ellipsis_line_fraction"

    return None


# ---------------------------------------------------------------------------
# Language filtering
# ---------------------------------------------------------------------------

# A compact list of very high-frequency English function words. Genuine
# English prose hits this list often; text in other languages, boilerplate,
# or token soup does not.
ENGLISH_STOPWORDS = frozenset(
    {
        "a", "about", "after", "again", "all", "also", "an", "and", "any",
        "are", "as", "at", "be", "because", "been", "before", "being",
        "between", "both", "but", "by", "can", "could", "did", "do", "does",
        "down", "during", "each", "few", "for", "from", "further", "had",
        "has", "have", "having", "he", "her", "here", "hers", "herself",
        "him", "himself", "his", "how", "i", "if", "in", "into", "is", "it",
        "its", "itself", "just", "me", "more", "most", "my", "myself", "no",
        "nor", "not", "now", "of", "off", "on", "once", "only", "or", "other",
        "our", "ours", "ourselves", "out", "over", "own", "same", "she",
        "should", "so", "some", "such", "than", "that", "the", "their",
        "theirs", "them", "themselves", "then", "there", "these", "they",
        "this", "those", "through", "to", "too", "under", "until", "up",
        "very", "was", "we", "were", "what", "when", "where", "which",
        "while", "who", "whom", "why", "will", "with", "would", "you",
        "your", "yours", "yourself", "yourselves",
    }
)


def stopword_hit_rate(words: Iterable[str]) -> float:
    words = list(words)
    if not words:
        return 0.0
    hits = sum(1 for w in words if w.lower() in ENGLISH_STOPWORDS)
    return hits / len(words)


def passes_language_filter(words: Iterable[str], config: PipelineConfig) -> bool:
    return stopword_hit_rate(words) >= config.min_stopword_hit_rate


# ---------------------------------------------------------------------------
# MinHash + LSH near-duplicate detection (implemented from scratch)
# ---------------------------------------------------------------------------

_MERSENNE_PRIME = (1 << 61) - 1


def _stable_hash(shingle: str) -> int:
    """A hash that is stable across processes and Python invocations.

    ``hash()`` on str/bytes is salted per-process (PYTHONHASHSEED), which
    would make MinHash signatures computed in different worker processes
    incomparable. blake2b is unsalted and deterministic.
    """
    digest = hashlib.blake2b(shingle.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def word_shingles(words: list[str], k: int) -> set[str]:
    """k-word shingles of a document, used as the MinHash item set."""
    if not words:
        return set()
    if len(words) < k:
        return {" ".join(words)}
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}


class MinHasher:
    """Computes MinHash signatures via a fixed, seeded universal hash family.

    Uses the classic ``h(x) = (a*x + b) mod p`` family over the Mersenne
    prime 2**61 - 1. Arithmetic is kept in plain Python ints (not NumPy's
    fixed-width integers) to avoid silent overflow, since intermediate
    products can exceed 2**120.
    """

    def __init__(self, num_perm: int, seed: int = 1234) -> None:
        rng = np.random.default_rng(seed)
        self.num_perm = num_perm
        self.a = [int(x) for x in rng.integers(1, _MERSENNE_PRIME - 1, size=num_perm)]
        self.b = [int(x) for x in rng.integers(0, _MERSENNE_PRIME - 1, size=num_perm)]

    def signature(self, shingles: set[str]) -> np.ndarray:
        sig = [_MERSENNE_PRIME] * self.num_perm
        for shingle in shingles:
            h = _stable_hash(shingle) % _MERSENNE_PRIME
            for i in range(self.num_perm):
                value = (self.a[i] * h + self.b[i]) % _MERSENNE_PRIME
                if value < sig[i]:
                    sig[i] = value
        return np.array(sig, dtype=np.uint64)


def lsh_band_keys(signature: np.ndarray, num_bands: int, rows_per_band: int) -> list[bytes]:
    """Split a signature into bands, each usable as an LSH bucket key.

    Two documents that share a bucket key in *any* band are candidate
    near-duplicates. The band index is folded into the key so identical
    row values occurring in different bands never collide with each other.
    """
    keys = []
    for band in range(num_bands):
        start = band * rows_per_band
        chunk = signature[start : start + rows_per_band].tobytes()
        keys.append(struct.pack(">I", band) + chunk)
    return keys


def find_near_duplicates(signatures: list[np.ndarray], config: PipelineConfig) -> set[int]:
    """Return the indices to drop as near-duplicates of an earlier document.

    LSH banding proposes candidate pairs (those sharing a bucket in at
    least one band); each candidate is then verified by the MinHash-
    estimated Jaccard similarity of its full signature before being
    merged, to guard against false positives from banding alone.
    Near-duplicate documents are grouped by connected components and only
    the lowest-indexed (i.e. first-encountered) document in each group is
    kept.
    """
    if len(signatures) < 2:
        return set()

    rows_per_band = config.rows_per_band()
    buckets: dict[bytes, list[int]] = {}
    for idx, sig in enumerate(signatures):
        for key in lsh_band_keys(sig, config.num_bands, rows_per_band):
            buckets.setdefault(key, []).append(idx)

    parent = list(range(len(signatures)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        root_x, root_y = find(x), find(y)
        if root_x != root_y:
            parent[max(root_x, root_y)] = min(root_x, root_y)

    checked_pairs: set[tuple[int, int]] = set()
    for members in buckets.values():
        if len(members) < 2:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                left, right = members[i], members[j]
                pair = (left, right) if left < right else (right, left)
                if pair in checked_pairs or find(pair[0]) == find(pair[1]):
                    continue
                checked_pairs.add(pair)
                similarity = float(np.mean(signatures[pair[0]] == signatures[pair[1]]))
                if similarity >= config.jaccard_threshold:
                    union(pair[0], pair[1])

    components: dict[int, list[int]] = {}
    for idx in range(len(signatures)):
        components.setdefault(find(idx), []).append(idx)

    drop: set[int] = set()
    for members in components.values():
        if len(members) < 2:
            continue
        keeper = min(members)
        drop.update(m for m in members if m != keeper)
    return drop


# ---------------------------------------------------------------------------
# Deterministic train/val split
# ---------------------------------------------------------------------------


def assign_split(doc_id: str, val_fraction: float) -> str:
    """Deterministically bucket a document id into "train" or "val".

    Uses ``hashlib`` (not the built-in, per-process-salted ``hash()``) so
    the split is identical across runs, processes, and machines.
    """
    digest = hashlib.md5(doc_id.encode("utf-8")).hexdigest()
    fraction = int(digest[:8], 16) / 0xFFFFFFFF
    return "val" if fraction < val_fraction else "train"


# ---------------------------------------------------------------------------
# Shard reading + per-document filtering (phase 1, parallel over shards)
# ---------------------------------------------------------------------------


@dataclass
class DocRecord:
    doc_id: str
    url: str
    text: str
    signature: np.ndarray
    shard_path: Path


@dataclass
class ShardFilterResult:
    shard_path: Path
    surviving: list[DocRecord]
    drop_counts: dict[str, int]
    total_lines: int


def _read_jsonl(path: Path) -> Iterator[dict[str, Any] | None]:
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                yield None


def _filter_and_hash_shard(shard_path: Path, config: PipelineConfig) -> ShardFilterResult:
    """Phase-1 worker: quality/language filter one shard and MinHash survivors."""
    hasher = MinHasher(config.num_perm, seed=config.minhash_seed)
    surviving: list[DocRecord] = []
    drop_counts: dict[str, int] = {}
    total = 0

    def record_drop(reason: str) -> None:
        drop_counts[reason] = drop_counts.get(reason, 0) + 1

    for record in _read_jsonl(shard_path):
        total += 1
        if record is None:
            record_drop("invalid_json")
            continue

        doc_id = record.get("id")
        text = record.get("text")
        url = record.get("url")
        if not isinstance(doc_id, (str, int)) or not isinstance(text, str) or not text:
            record_drop("missing_field")
            continue
        doc_id = str(doc_id)

        reason = quality_filter_reason(text, config)
        if reason is not None:
            record_drop(reason)
            continue

        words = _words(text)
        if not passes_language_filter(words, config):
            record_drop("not_english")
            continue

        shingles = word_shingles(words, config.shingle_size)
        signature = hasher.signature(shingles)
        surviving.append(
            DocRecord(
                doc_id=doc_id,
                url=str(url) if url is not None else "",
                text=text,
                signature=signature,
                shard_path=shard_path,
            )
        )

    return ShardFilterResult(
        shard_path=shard_path,
        surviving=surviving,
        drop_counts=drop_counts,
        total_lines=total,
    )


def _flatten_phase1(results: list[ShardFilterResult]) -> tuple[list[DocRecord], dict[str, int], int]:
    docs: list[DocRecord] = []
    drop_counts: dict[str, int] = {}
    total_lines = 0
    for result in results:
        total_lines += result.total_lines
        for reason, count in result.drop_counts.items():
            drop_counts[reason] = drop_counts.get(reason, 0) + count
        docs.extend(result.surviving)
    return docs, drop_counts, total_lines


# ---------------------------------------------------------------------------
# Tokenisation (phase 2, parallel over shards)
# ---------------------------------------------------------------------------

_worker_tokenizer: Tokenizer | None = None


def _init_tokenizer_worker(tokenizer: Tokenizer) -> None:
    global _worker_tokenizer
    _worker_tokenizer = tokenizer


def _tokenize_group(items: list[tuple[str, str]]) -> list[tuple[str, list[int]]]:
    assert _worker_tokenizer is not None, "tokenizer worker was not initialised"
    return [(doc_id, _worker_tokenizer.encode(text)) for doc_id, text in items]


# ---------------------------------------------------------------------------
# Packing into fixed-length sequences
# ---------------------------------------------------------------------------


def _token_stream_for_split(
    ordered_docs: list[tuple[str, str]],
    token_map: dict[str, list[int]],
    split: str,
    eos_token_id: int,
) -> Iterator[int]:
    for doc_id, doc_split in ordered_docs:
        if doc_split != split:
            continue
        yield from token_map[doc_id]
        yield eos_token_id


def pack_sequences(
    token_stream: Iterable[int], sequence_length: int
) -> tuple[list[np.ndarray], np.ndarray | None]:
    """Pack a flat token stream into fixed-length sequences.

    Every full ``sequence_length`` chunk is emitted with no padding. Any
    leftover tokens at the end of the stream (fewer than
    ``sequence_length``) are returned separately as the unpadded "tail" --
    the only sequence in the split allowed to be shorter than
    ``sequence_length``.
    """
    full_sequences: list[np.ndarray] = []
    buffer: list[int] = []
    for token in token_stream:
        buffer.append(token)
        if len(buffer) == sequence_length:
            full_sequences.append(np.array(buffer, dtype=np.int32))
            buffer = []
    tail = np.array(buffer, dtype=np.int32) if buffer else None
    return full_sequences, tail


def _write_split(
    output_dir: Path,
    split: str,
    full_sequences: list[np.ndarray],
    tail: np.ndarray | None,
    sequences_per_file: int,
) -> list[str]:
    output_files: list[str] = []
    for start in range(0, len(full_sequences), sequences_per_file):
        chunk = full_sequences[start : start + sequences_per_file]
        array = np.stack(chunk).astype(np.int32)
        filename = f"{split}_{start // sequences_per_file:05d}.npy"
        np.save(output_dir / filename, array)
        output_files.append(filename)

    if tail is not None:
        filename = f"{split}_tail.npy"
        np.save(output_dir / filename, tail.astype(np.int32))
        output_files.append(filename)

    return output_files


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def prepare_pretrain_corpus(
    input_dir: str | Path,
    output_dir: str | Path,
    tokenizer: Tokenizer,
    config: PipelineConfig | None = None,
) -> dict[str, Any]:
    """Run the full pipeline end to end and write outputs + manifest.

    Args:
        input_dir: directory containing ``*.jsonl`` shards.
        output_dir: directory to write ``.npy`` sequence shards and
            ``manifest.json`` into (created if missing).
        tokenizer: any object with an ``.encode(text: str) -> list[int]``
            method. When ``config.num_workers`` implies multiprocessing,
            it is sent to worker processes once via a pool initializer,
            so it must be picklable.
        config: pipeline configuration; defaults to ``PipelineConfig()``.

    Returns:
        The manifest dict that was also written to ``output_dir /
        "manifest.json"``.
    """
    config = config or PipelineConfig()
    config.rows_per_band()  # validate num_perm / num_bands early

    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    shard_paths = sorted(input_dir.glob("*.jsonl"))
    if not shard_paths:
        raise FileNotFoundError(f"no .jsonl shards found in {input_dir}")

    num_workers = config.num_workers or (os.cpu_count() or 1)
    use_mp = num_workers > 1 and len(shard_paths) > 1

    logger.info("phase 1/3: filtering + minhash over %d shard(s)", len(shard_paths))
    if use_mp:
        worker = functools.partial(_filter_and_hash_shard, config=config)
        with mp.Pool(processes=min(num_workers, len(shard_paths))) as pool:
            phase1_results = pool.map(worker, shard_paths)
    else:
        phase1_results = [_filter_and_hash_shard(p, config) for p in shard_paths]

    docs, drop_counts, total_docs_read = _flatten_phase1(phase1_results)

    logger.info("phase 2/3: near-duplicate detection over %d surviving doc(s)", len(docs))
    dup_indices = find_near_duplicates([d.signature for d in docs], config)
    drop_counts["near_duplicate"] = len(dup_indices)
    kept = [d for i, d in enumerate(docs) if i not in dup_indices]

    groups: dict[Path, list[tuple[str, str]]] = {}
    for doc in kept:
        groups.setdefault(doc.shard_path, []).append((doc.doc_id, doc.text))
    ordered_groups = [groups[p] for p in shard_paths if p in groups]

    logger.info("phase 3/3: tokenising %d document(s)", len(kept))
    if use_mp:
        with mp.Pool(
            processes=min(num_workers, max(len(ordered_groups), 1)),
            initializer=_init_tokenizer_worker,
            initargs=(tokenizer,),
        ) as pool:
            tokenized_groups = pool.map(_tokenize_group, ordered_groups)
    else:
        _init_tokenizer_worker(tokenizer)
        tokenized_groups = [_tokenize_group(group) for group in ordered_groups]

    token_map: dict[str, list[int]] = {}
    for group in tokenized_groups:
        for doc_id, token_ids in group:
            token_map[doc_id] = token_ids

    ordered = [(doc.doc_id, assign_split(doc.doc_id, config.val_fraction)) for doc in kept]

    manifest_splits: dict[str, Any] = {}
    for split in ("train", "val"):
        stream = _token_stream_for_split(ordered, token_map, split, config.eos_token_id)
        full_sequences, tail = pack_sequences(stream, config.sequence_length)
        output_files = _write_split(output_dir, split, full_sequences, tail, config.sequences_per_file)
        manifest_splits[split] = {
            "documents": sum(1 for _, s in ordered if s == split),
            "full_sequences": len(full_sequences),
            "tail_tokens": int(tail.shape[0]) if tail is not None else 0,
            "output_files": output_files,
        }

    manifest: dict[str, Any] = {
        "config": asdict(config),
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "shards_processed": [str(p) for p in shard_paths],
        "documents_read": total_docs_read,
        "documents_kept": len(kept),
        "drop_counts": drop_counts,
        "splits": manifest_splits,
    }

    with (output_dir / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)

    logger.info(
        "done: kept %d/%d documents (%d dropped, %d near-duplicates)",
        len(kept),
        total_docs_read,
        total_docs_read - len(kept),
        len(dup_indices),
    )
    return manifest


# ---------------------------------------------------------------------------
# Command-line interface
# ---------------------------------------------------------------------------


def _load_tokenizer(spec: str) -> Tokenizer:
    """Resolve a ``module.submodule:attribute`` spec into a tokeniser.

    If the resolved attribute is callable and does not itself expose
    ``.encode``, it is treated as a zero-argument factory and called to
    produce the tokeniser instance.
    """
    module_name, _, attr_path = spec.partition(":")
    if not attr_path:
        raise ValueError(
            f"invalid --tokenizer spec {spec!r}; expected 'module:attribute' "
            "(e.g. 'mypkg.tokenizer:build_tokenizer')"
        )
    obj: Any = importlib.import_module(module_name)
    for part in attr_path.split("."):
        obj = getattr(obj, part)

    tokenizer = obj() if callable(obj) and not hasattr(obj, "encode") else obj
    if not hasattr(tokenizer, "encode"):
        raise TypeError(f"{spec!r} did not resolve to an object with an .encode() method")
    return tokenizer


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare a packed, tokenised pretraining corpus from JSONL shards."
    )
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--tokenizer",
        required=True,
        help="'module:attribute' import spec for an object (or zero-arg factory) "
        "exposing .encode(str) -> list[int]",
    )
    parser.add_argument("--log-level", default="INFO")

    defaults = PipelineConfig()
    for f in dataclasses.fields(PipelineConfig):
        flag = "--" + f.name.replace("_", "-")
        parser.add_argument(
            flag,
            type=type(getattr(defaults, f.name)),
            default=None,
            help=f"override {f.name} (default: {getattr(defaults, f.name)})",
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(message)s")

    overrides = {}
    for f in dataclasses.fields(PipelineConfig):
        value = getattr(args, f.name, None)
        if value is not None:
            overrides[f.name] = value
    config = dataclasses.replace(PipelineConfig(), **overrides)

    tokenizer = _load_tokenizer(args.tokenizer)
    manifest = prepare_pretrain_corpus(args.input_dir, args.output_dir, tokenizer, config)
    print(
        json.dumps(
            {
                "documents_read": manifest["documents_read"],
                "documents_kept": manifest["documents_kept"],
                "drop_counts": manifest["drop_counts"],
                "splits": {
                    split: {k: v for k, v in info.items() if k != "output_files"}
                    for split, info in manifest["splits"].items()
                },
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
