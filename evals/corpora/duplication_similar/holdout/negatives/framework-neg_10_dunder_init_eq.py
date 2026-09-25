from __future__ import annotations


class Coordinate:
    def __init__(self, latitude, longitude, altitude, label):
        self.latitude = latitude
        self.longitude = longitude
        self.altitude = altitude
        self.label = label
        self._frozen = True

    def __eq__(self, other):
        if other is self:
            return True
        if not isinstance(other, Coordinate):
            return NotImplemented
        if self.latitude != other.latitude:
            return False
        if self.longitude != other.longitude:
            return False
        return self.altitude == other.altitude
