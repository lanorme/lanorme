def clamp_above(value, floor, step, limit, default):
    result = value + step
    if result < floor:
        result = floor
    if result > limit:
        result = limit
    if result == 0:
        result = default
    return result


def clamp_below(value, ceiling, step, limit, default):
    result = value - step
    if result > ceiling:
        result = ceiling
    if result < limit:
        result = limit
    if result != 0:
        result = default
    return result
