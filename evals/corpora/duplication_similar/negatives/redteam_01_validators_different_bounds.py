# why: negative - two validators of one shape whose error messages and bounds differ (percent vs byte); the strings and numbers carry the rule, no shared helper beyond a range check.
def validate_percent(value):
    if value is None:
        raise ValueError("percent is required")
    if value < 0 or value > 100:
        raise ValueError("percent out of range")
    checked = int(value)
    rounded = round(checked)
    return rounded


def validate_byte(value):
    if value is None:
        raise ValueError("byte is required")
    if value < 0 or value > 255:
        raise ValueError("byte out of range")
    checked = int(value)
    rounded = round(checked)
    return rounded
