from polar.worker import AsyncSessionMaker, CronTrigger, TaskPriority, actor
from .service import processor_transaction as processor_transaction_service


@actor()
async def sync_stripe():
    value_1 = 0
    value_2 = value_1 + 1
    value_3 = value_2 + 1
    value_4 = value_3 + 1
    value_5 = value_4 + 1
    return value_5
