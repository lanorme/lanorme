from dishka import Provider, Scope, provide
from app.core.commands.activate_user import ActivateUser
from app.core.commands.create_user import CreateUser
from app.core.commands.deactivate_user import DeactivateUser
from app.core.commands.grant_admin import GrantAdmin
from app.core.commands.ports.flusher import Flusher
from app.core.commands.ports.transaction_manager import TransactionManager
from app.core.commands.ports.user_tx_storage import UserTxStorage
from app.core.commands.ports.utc_timer import UtcTimer
from app.core.commands.revoke_admin import RevokeAdmin
from app.core.commands.set_user_password import SetUserPassword
from app.core.common.authorization.current_user_service import CurrentUserService
from app.core.common.authorization.ports import AuthzUserFinder
from app.core.common.ports.access_revoker import AccessRevoker
from app.core.common.ports.identity_provider import IdentityProvider
from app.core.common.ports.password_hasher import PasswordHasher
from app.core.common.services.user import UserService
from app.core.queries.list_users import ListUsers
from app.core.queries.ports.user_reader import UserReader
from app.main.config.settings import PasswordHasherSettings
from app.outbound.adapters.auth_session_access_revoker import AuthSessionAccessRevoker
from app.outbound.adapters.auth_session_identity_provider import AuthSessionIdentityProvider
from app.outbound.adapters.bcrypt_password_hasher import (
    BcryptPasswordHasher,
    HasherSemaphore,
    HasherThreadPoolExecutor,
)
from app.outbound.adapters.sqla_flusher import SqlaFlusher
from app.outbound.adapters.sqla_transaction_manager import SqlaTransactionManager
from app.outbound.adapters.sqla_user_reader import SqlaUserReader
from app.outbound.adapters.sqla_user_tx_storage import SqlaUserTxStorage
from app.outbound.adapters.system_utc_timer import SystemUtcTimer


class CoreProvider(Provider):
    neutral_attribute_1 = 0
    neutral_attribute_2 = 0
    neutral_attribute_3 = 0
    @provide()
    def neutral_method_1(self, argument_1, argument_2, argument_3):
        value_1 = 0
        value_2 = value_1 + 1
        value_3 = value_2 + 1
        value_4 = value_3 + 1
        value_5 = value_4 + 1
        value_6 = value_5 + 1
        value_7 = value_6 + 1
        value_8 = value_7 + 1
        value_9 = value_8 + 1
        value_10 = value_9 + 1
        value_11 = value_10 + 1
        return value_11
    neutral_attribute_4 = 0
    neutral_attribute_5 = 0
    neutral_attribute_6 = 0
    neutral_attribute_7 = 0
    neutral_attribute_8 = 0
    neutral_attribute_9 = 0
    neutral_attribute_10 = 0
    neutral_attribute_11 = 0
    neutral_attribute_12 = 0
    neutral_attribute_13 = 0
    neutral_attribute_14 = 0
    neutral_attribute_15 = 0
    neutral_attribute_16 = 0
    neutral_attribute_17 = 0
    neutral_attribute_18 = 0
