"""Names that point at a secret (its env var, id, file or scheme), not the secret."""

from __future__ import annotations

PASSWORD_ENV = "APP_DB_PASSWORD"
api_key_env_var = "STRIPE_API_KEY"
token_var = "GITHUB_TOKEN_VALUE"
secret_id = "arn:aws:secretsmanager:eu-west-1:123456789012:secret:prod/db"
password_file = "/run/secrets/db_password"
private_key_file = "/etc/ssl/private/server.key"
PASSWORD_ALGORITHM = "pbkdf2_sha256"
TOKEN_BACKEND = "rest_framework.authtoken"
secret_store = "vault-kv-v2-prod"
token_handler = "app.auth.handlers.TokenHandler"
