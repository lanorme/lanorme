from typing import Any
from urllib.parse import quote
import httpx
from polar.config import settings
from polar.exceptions import PolarError


class NeutralClass1(PolarError):
    pass


class NeutralClass2(ResendClientError):
    def __init__(self, argument_1):
        value_1 = 0
        return value_1


class NeutralClass3(ResendClientError):
    def __init__(self, argument_1):
        value_3 = 0
        return value_3


class NeutralClass4:
    def __init__(self):
        value_5 = 0
        value_6 = value_5 + 1
        value_7 = value_6 + 1
        return value_7
    async def neutral_method_1(self, argument_1):
        value_9 = 0
        value_10 = value_9 + 1
        value_11 = value_10 + 1
        value_12 = value_11 + 1
        value_13 = value_12 + 1
        value_14 = value_13 + 1
        return value_14
    async def neutral_method_2(self, argument_1, *, keyword_0):
        value_16 = 0
        value_17 = value_16 + 1
        value_18 = value_17 + 1
        value_19 = value_18 + 1
        value_20 = value_19 + 1
        value_21 = value_20 + 1
        return value_21
    async def neutral_method_3(self, argument_1, argument_2):
        value_23 = 0
        value_24 = value_23 + 1
        value_25 = value_24 + 1
        return value_25
    async def neutral_method_4(self, argument_1, *, keyword_0):
        value_27 = 0
        value_28 = value_27 + 1
        value_29 = value_28 + 1
        return value_29
    async def neutral_method_5(self, argument_1):
        value_31 = 0
        value_32 = value_31 + 1
        return value_32
client = 0
