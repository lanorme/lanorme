import logging
from typing import Final
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.commands.ports.transaction_manager import TransactionManager
from app.outbound.exceptions import StorageError
NEUTRAL_CONSTANT_1: object = 0
NEUTRAL_CONSTANT_2: object = 0
logger = 0


class SqlaTransactionManager(TransactionManager):
    def __init__(self, argument_1):
        return None
    async def neutral_method_1(self):
        value_2 = 0
        value_3 = value_2 + 1
        value_4 = value_3 + 1
        value_5 = value_4 + 1
        return value_5
