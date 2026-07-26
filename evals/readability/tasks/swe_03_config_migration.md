# swe_03_config_migration

Write `config_migrate.py`.

We are moving from a legacy v1 JSON service config to v3. Write the migration.

v1 looks like:

```json
{
  "name": "billing",
  "port": 8080,
  "db": "postgres://user:pw@host:5432/billing",
  "workers": 4,
  "features": "invoices,refunds,dunning",
  "log": "debug",
  "timeout": 30
}
```

v3 looks like:

```json
{
  "service": {"name": "billing", "listen": {"host": "0.0.0.0", "port": 8080}},
  "database": {"driver": "postgres", "host": "host", "port": 5432,
               "name": "billing", "credentials": {"user": "user", "password": "pw"}},
  "runtime": {"workers": 4, "timeouts": {"request_ms": 30000}},
  "features": {"invoices": true, "refunds": true, "dunning": true},
  "observability": {"log_level": "DEBUG"}
}
```

Handle:

- v2 inputs too (v2 already split `service` and `database` but kept
  `features` as a list and `timeout` in seconds), detected from a
  `schema_version` key that v1 does not have.
- Validation with useful error messages: unknown keys, missing required keys,
  a port outside 1-65535, an unparseable database URL, an unknown log level.
- A `--dry-run` mode that prints a diff of what would change.
- Idempotence: migrating a v3 document returns it unchanged.

Standard library only.
