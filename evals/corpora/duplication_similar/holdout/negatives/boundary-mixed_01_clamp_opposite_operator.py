def clamp_to_floor(values, limit):
    out = []
    if not values:
        return out
    touched = 0
    for value in values:
        if value < limit:
            out.append(limit)
            touched += 1
        else:
            out.append(value)
    return out


def clamp_to_ceiling(values, limit):
    out = []
    if not values:
        return out
    touched = 0
    for value in values:
        if value > limit:
            out.append(limit)
            touched += 1
        else:
            out.append(value)
    return out
