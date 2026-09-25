def advance_cursor(buffer, steps):
    position = buffer.offset
    moved = 0
    for _ in range(steps):
        if position >= buffer.length:
            break
        position = position + 1
        moved += 1
    buffer.offset = position
    return position


def rewind_cursor(buffer, steps):
    position = buffer.offset
    moved = 0
    for _ in range(steps):
        if position <= 0:
            break
        position = position - 1
        moved += 1
    buffer.offset = position
    return position
