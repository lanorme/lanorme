# featurestore, turn 1

Build a feature store as a Python package `featurestore/`.

- Declare a feature: name, entity key, dtype, and the transform that computes
  it from a source table.
- Materialise features from source tables into a feature table.
- Point-in-time correct joins: given an entity key and an event timestamp,
  return the feature values as they were known at that timestamp, never later.
- A registry listing features, their owners, and their freshness.
- Reject: a feature whose transform reads a column absent from the source, a
  point-in-time query with no timestamp, a duplicate feature name.

Tables are lists of dicts. Standard library only.
