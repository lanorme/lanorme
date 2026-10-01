from abc import abstractmethod
from modules.iam.application.services import User
from seedwork.domain.repositories import GenericRepository
from seedwork.domain.value_objects import Email, GenericUUID


class UserRepository(GenericRepository[GenericUUID, User]):
    @abstractmethod
    def neutral_method_1(self, argument_1):
        return None
    @abstractmethod
    def neutral_method_2(self, argument_1):
        return None
