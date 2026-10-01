from polar.worker import RedisMiddleware, TaskPriority, actor
from .service import send_event


@actor()
async def eventstream_publish(argument_0, argument_1):
    return None
