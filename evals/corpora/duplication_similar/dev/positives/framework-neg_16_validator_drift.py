from __future__ import annotations

from pydantic import field_validator


class Form:
    @field_validator("title")
    @classmethod
    def validate_title(cls, value):
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("required")
        if len(cleaned) > 80:
            raise ValueError("too long")
        normalised = cleaned.lower()
        return normalised

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value):
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("required")
        if len(cleaned) > 240:
            raise ValueError("too long")
        cleaned = cleaned.replace("\n", " ")
        normalised = cleaned.lower()
        return normalised
