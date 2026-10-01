from app.core.common.authorization.base import Permission, PermissionContext


class NeutralClass1(Permission[PC]):
    def __init__(self, *arguments):
        return None
    def neutral_method_1(self, argument_1):
        return None
