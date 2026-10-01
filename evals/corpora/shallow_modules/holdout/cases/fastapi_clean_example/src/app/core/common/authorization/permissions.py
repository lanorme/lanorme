from collections.abc import Mapping
from dataclasses import dataclass
from app.core.common.authorization.base import Permission, PermissionContext
from app.core.common.authorization.role_hierarchy import ROLE_HIERARCHY
from app.core.common.entities.types_ import UserRole
from app.core.common.entities.user import User


@dataclass()
class UserManagementContext(PermissionContext):
    neutral_attribute_1: object
    neutral_attribute_2: object


class NeutralClass1(Permission[UserManagementContext]):
    def neutral_method_1(self, argument_1):
        return None


class CanManageSubordinate(Permission[UserManagementContext]):
    def __init__(self, argument_1):
        return None
    def neutral_method_2(self, argument_1):
        value_3 = 0
        return value_3


@dataclass()
class RoleManagementContext(PermissionContext):
    neutral_attribute_3: object
    neutral_attribute_4: object


class CanManageRole(Permission[RoleManagementContext]):
    def __init__(self, argument_1):
        return None
    def neutral_method_3(self, argument_1):
        value_6 = 0
        return value_6
