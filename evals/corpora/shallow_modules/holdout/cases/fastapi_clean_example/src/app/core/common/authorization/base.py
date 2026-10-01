from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass()
class PermissionContext:
    pass


class Permission(ABC):
    @abstractmethod
    def neutral_method_1(self, argument_1): ...
