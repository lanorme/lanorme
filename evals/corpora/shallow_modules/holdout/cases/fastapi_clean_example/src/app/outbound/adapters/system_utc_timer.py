from datetime import UTC, datetime
from app.core.commands.ports.utc_timer import UtcTimer
from app.core.common.value_objects.utc_datetime import UtcDatetime


class SystemUtcTimer(UtcTimer):
    @property
    def neutral_method_1(self):
        return None
