from enum import StrEnum
from typing import NewType
from uuid import UUID
UserId = 0
UserPasswordHash = 0


class UserRole(StrEnum):
    neutral_attribute_1 = 0
    neutral_attribute_2 = 0
    neutral_attribute_3 = 0
    @property
    def neutral_method_1(self):
        return None
