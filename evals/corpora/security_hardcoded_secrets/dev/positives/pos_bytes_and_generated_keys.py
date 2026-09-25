"""Bytes literals bound to credential names, and Django's generated dev key."""

from __future__ import annotations

import os

hmac_secret = b"k9Q2vX7mP4sT1wZ8r5N3b6C0"
signing_key = b"\x8f\x12\xa4\xd3\x9c\x4b\x7e\x21\x33\x5d\x6a\x90\xbb\xcd\xef\x01"
SECRET_KEY = os.environ.get(
    "SECRET_KEY",
    "django-insecure-k9q2vx7mp4st1wz8r5n3b6c0d2f4g8h1j3l5m7n9p1r3t5v7x9",
)
SESSION_SECRET = "django-insecure-a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0u1v2w3x4y5"
