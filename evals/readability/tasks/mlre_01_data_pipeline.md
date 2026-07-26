# mlre_01_data_pipeline

Write `prepare_pretrain_corpus.py`.

We have a directory of JSONL shards, each line `{"id":..., "url":..., "text":...}`.
Build the preprocessing pipeline that turns them into training-ready packed
sequences.

Steps:

- Quality filtering: drop documents shorter than a token threshold, with a mean
  line length below a threshold, with a symbol-to-word ratio above a threshold,
  or whose fraction of lines ending in an ellipsis is too high.
- Language filter: keep documents whose stopword hit rate looks like English.
- Near-duplicate removal across shards with MinHash + LSH banding (implement it,
  do not pull in datasketch).
- Tokenise with a tokeniser passed in by the caller (assume a
  `.encode(str) -> list[int]` interface), then pack into fixed-length sequences
  with an EOS separator, no padding except in the final sequence.
- Deterministic train/val split by document id hash.
- Write output shards as `.npy` int32 arrays plus a JSON manifest recording
  counts, drop reasons and the config used.

Multiprocessing over shards. NumPy is available; nothing else beyond the
standard library.
