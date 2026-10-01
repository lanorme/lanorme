from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class NeutralClass1(Exception):
    pass


async def db_check(argument_0):
    value_1 = 0
    value_2 = value_1 + 1
    value_3 = value_2 + 1
    return value_3
