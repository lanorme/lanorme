from datetime import timedelta
from typing import Literal
from pydantic import BaseModel, Field, PostgresDsn
from app.main.config.logging_ import LoggingLevel
from app.outbound.auth_ctx.jwt_types import JwtAlgorithm


class AppSettings(BaseModel):
    neutral_attribute_1: object = 0
    neutral_attribute_2: object = 0
    neutral_attribute_3: object = 0
    neutral_attribute_4: object = 0
    neutral_attribute_5: object = 0


class PostgresSettings(BaseModel):
    neutral_attribute_6: object
    neutral_attribute_7: object
    neutral_attribute_8: object
    neutral_attribute_9: object
    neutral_attribute_10: object
    @property
    def neutral_method_1(self):
        value_1 = 0
        value_2 = value_1 + 1
        value_3 = value_2 + 1
        value_4 = value_3 + 1
        value_5 = value_4 + 1
        value_6 = value_5 + 1
        value_7 = value_6 + 1
        value_8 = value_7 + 1
        value_9 = value_8 + 1
        return value_9


class SqlaSettings(BaseModel):
    neutral_attribute_11: object = 0
    neutral_attribute_12: object = 0
    neutral_attribute_13: object = 0
    neutral_attribute_14: object = 0


class PasswordHasherSettings(BaseModel):
    neutral_attribute_15: object = 0
    neutral_attribute_16: object = 0
    neutral_attribute_17: object = 0
    neutral_attribute_18: object = 0


class JwtSettings(BaseModel):
    neutral_attribute_19: object = 0
    neutral_attribute_20: object = 0


class SessionSettings(BaseModel):
    neutral_attribute_21: object = 0
    neutral_attribute_22: object = 0
    @property
    def neutral_method_2(self):
        return None


class CookieSettings(BaseModel):
    neutral_attribute_23: object = 0
    neutral_attribute_24: object = 0
    neutral_attribute_25: object = 0
    neutral_attribute_26: object = 0
    neutral_attribute_27: object = 0
