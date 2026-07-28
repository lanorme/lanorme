# featurestore, turn 3

Extend the existing `featurestore/` package in this directory. Do not rewrite it.

Add lineage and training-set snapshots.

- Track lineage: which source tables and which upstream features each feature
  version derives from, and expose the transitive closure.
- Detect a cycle in the lineage graph and refuse it with a useful message.
- Snapshot a training set: given a list of entity keys with event timestamps
  and a list of features, produce a frozen, content-addressed dataset plus a
  manifest recording every feature version and source used.
- Given a snapshot, say whether it can still be reproduced from current state,
  and if not, exactly which input changed.
