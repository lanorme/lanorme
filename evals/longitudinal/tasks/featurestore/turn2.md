# featurestore, turn 2

Extend the existing `featurestore/` package in this directory. Do not rewrite it.

Add versioning and backfill.

- A feature definition is versioned. Changing a transform creates a new
  version rather than mutating the old one, and both stay queryable.
- Backfill a version over a historical date range, in chunks, resumable after
  an interruption, without double-writing rows that already exist.
- A point-in-time query pins the feature version that was current at the
  event timestamp, unless the caller asks for a specific version.
- Report, per feature, which versions have been backfilled over which ranges.
