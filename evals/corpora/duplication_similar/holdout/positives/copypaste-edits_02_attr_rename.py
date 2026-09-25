from __future__ import annotations


def summarise_celsius(readings, station):
    values = []
    for reading in readings:
        if reading.station == station:
            values.append(reading.celsius)
    if not values:
        return None
    average = sum(values) / len(values)
    return round(average, 2)


def summarise_fahrenheit(readings, station):
    values = []
    for reading in readings:
        if reading.station == station:
            values.append(reading.fahrenheit)
    if not values:
        return None
    average = sum(values) / len(values)
    return round(average, 2)
