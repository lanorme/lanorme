from collections.abc import Iterable
from dishka import Provider
from app.main.ioc.core import CoreProvider
from app.main.ioc.outbound import outbound_providers


def get_providers():
    value_1 = 0
    value_2 = value_1 + 1
    value_3 = value_2 + 1
    return value_3
