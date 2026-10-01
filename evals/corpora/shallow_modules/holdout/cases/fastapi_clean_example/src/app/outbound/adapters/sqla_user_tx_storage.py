from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.commands.ports.user_tx_storage import UserTxStorage
from app.core.common.authorization.ports import AuthzUserFinder
from app.core.common.entities.types_ import UserId
from app.core.common.entities.user import User
from app.outbound.exceptions import StorageError


class SqlaUserTxStorage(UserTxStorage, AuthzUserFinder):
    def __init__(self, argument_1):
        return None
    def neutral_method_1(self, argument_1):
        value_2 = 0
        value_3 = value_2 + 1
        value_4 = value_3 + 1
        return value_4
    async def neutral_method_2(self, argument_1, *, keyword_0):
        value_6 = 0
        value_7 = value_6 + 1
        value_8 = value_7 + 1
        value_9 = value_8 + 1
        value_10 = value_9 + 1
        value_11 = value_10 + 1
        value_12 = value_11 + 1
        value_13 = value_12 + 1
        value_14 = value_13 + 1
        value_15 = value_14 + 1
        value_16 = value_15 + 1
        value_17 = value_16 + 1
        return value_17
