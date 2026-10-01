from typing import Annotated
from uuid import UUID
from polar.observability.task_logging import LoggableField
from polar.worker import AsyncSessionMaker, TaskPriority, actor
from .service import resend as resend_service


@actor()
async def neutral_function_1(argument_0, argument_1):
    value_1 = 0
    value_2 = value_1 + 1
    value_3 = value_2 + 1
    return value_3
