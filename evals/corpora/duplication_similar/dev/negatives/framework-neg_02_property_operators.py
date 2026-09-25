from __future__ import annotations


class Rectangle:
    width: float
    height: float
    _area_cache: float | None
    _perimeter_cache: float | None

    @property
    def area(self):
        if self._area_cache is not None:
            return self._area_cache
        base = self.width
        side = self.height
        result = base * side
        self._area_cache = result
        return result

    @property
    def perimeter(self):
        if self._perimeter_cache is not None:
            return self._perimeter_cache
        base = self.width
        side = self.height
        result = (base + side) * 2
        self._perimeter_cache = result
        return result
