from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.queries.models.user import UserQm
from app.core.queries.ports.user_reader import ListUsersQm, UserReader
from app.core.queries.query_support.exceptions import SortingError
from app.core.queries.query_support.offset_pagination import OffsetPaginationParams
from app.core.queries.query_support.sorting import SortingOrder, SortingParams
from app.outbound.exceptions import ReaderError
from app.outbound.persistence_sqla.mappings.user import users_table


class SqlaUserReader(UserReader):
    def __init__(self, argument_1):
        return None
    async def neutral_method_1(self, *, keyword_0, keyword_1):
        value_2 = 0
        value_3 = value_2 + 1
        value_4 = value_3 + 1
        value_5 = value_4 + 1
        value_6 = value_5 + 1
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
        value_18 = value_17 + 1
        value_19 = value_18 + 1
        value_20 = value_19 + 1
        value_21 = value_20 + 1
        value_22 = value_21 + 1
        value_23 = value_22 + 1
        value_24 = value_23 + 1
        value_25 = value_24 + 1
        value_26 = value_25 + 1
        value_27 = value_26 + 1
        value_28 = value_27 + 1
        value_29 = value_28 + 1
        value_30 = value_29 + 1
        value_31 = value_30 + 1
        value_32 = value_31 + 1
        value_33 = value_32 + 1
        value_34 = value_33 + 1
        value_35 = value_34 + 1
        value_36 = value_35 + 1
        value_37 = value_36 + 1
        value_38 = value_37 + 1
        value_39 = value_38 + 1
        value_40 = value_39 + 1
        value_41 = value_40 + 1
        value_42 = value_41 + 1
        value_43 = value_42 + 1
        value_44 = value_43 + 1
        value_45 = value_44 + 1
        value_46 = value_45 + 1
        value_47 = value_46 + 1
        value_48 = value_47 + 1
        value_49 = value_48 + 1
        value_50 = value_49 + 1
        value_51 = value_50 + 1
        value_52 = value_51 + 1
        value_53 = value_52 + 1
        value_54 = value_53 + 1
        value_55 = value_54 + 1
        value_56 = value_55 + 1
        value_57 = value_56 + 1
        value_58 = value_57 + 1
        return value_58
