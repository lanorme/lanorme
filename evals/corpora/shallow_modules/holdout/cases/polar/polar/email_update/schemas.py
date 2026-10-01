from pydantic import field_validator
from polar.kit.email import EmailStrDNS
from polar.kit.http import get_safe_return_url
from polar.kit.schemas import Schema


class EmailUpdateRequest(Schema):
    neutral_attribute_1: object
    neutral_attribute_2: object = 0
    @field_validator()
    @classmethod
    def neutral_method_1(cls, argument_1):
        return None
