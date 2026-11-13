"""Logistic regression with the standard library: a co-location model small enough to ship.

A check that runs on every commit cannot depend on a numerical library, so the
model that scores a pair of definitions is a ridge-penalised logistic
regression fitted by Newton's method (iteratively reweighted least squares)
on a handful of standardised features. With nine features each step solves a
ten-by-ten system by Gaussian elimination, and a fit converges in a few
steps. The fitted model is a list of means, scales and weights: scoring a
pair is one dot product.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

ITERATIONS = 12
RIDGE = 1.0
_LIMIT = 30.0


@dataclass(frozen=True)
class Model:
    """A fitted model: each feature's mean and scale, its weight, and the intercept."""

    means: tuple[float, ...]
    scales: tuple[float, ...]
    weights: tuple[float, ...]
    intercept: float

    def score(self, features: list[float]) -> float:
        """The probability the model gives a pair of being kept in one module."""
        total = self.intercept + sum(
            weight * (value - mean) / scale
            for weight, value, mean, scale in zip(
                self.weights,
                features,
                self.means,
                self.scales,
                strict=True,
            )
        )
        return 1 / (1 + math.exp(-max(-_LIMIT, min(_LIMIT, total))))


def solve(*, matrix: list[list[float]], vector: list[float]) -> list[float]:
    """The solution of ``matrix x = vector`` by Gaussian elimination with partial pivoting."""
    size = len(vector)
    rows = [[*matrix[index], vector[index]] for index in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda index: abs(rows[index][column]))
        rows[column], rows[pivot] = rows[pivot], rows[column]
        for index in range(column + 1, size):
            factor = rows[index][column] / rows[column][column]
            for position in range(column, size + 1):
                rows[index][position] -= factor * rows[column][position]
    solution = [0.0] * size
    for index in range(size - 1, -1, -1):
        known = sum(
            rows[index][position] * solution[position] for position in range(index + 1, size)
        )
        solution[index] = (rows[index][size] - known) / rows[index][index]
    return solution


def _standardise(rows: list[list[float]]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Each feature's mean and standard deviation (1 for a constant feature)."""
    count = len(rows)
    means = tuple(sum(column) / count for column in zip(*rows, strict=True))
    scales = tuple(
        math.sqrt(sum((value - mean) ** 2 for value in column) / count) or 1.0
        for column, mean in zip(zip(*rows, strict=True), means, strict=True)
    )
    return means, scales


def _step(*, design: list[list[float]], labels: list[bool], weights: list[float]) -> list[float]:
    """One Newton step of the penalised likelihood; the intercept (last weight) is not penalised."""
    size = len(weights)
    hessian = [[0.0] * size for _index in range(size)]
    gradient = [0.0] * size
    for row, label in zip(design, labels, strict=True):
        total = sum(weight * value for weight, value in zip(weights, row, strict=True))
        probability = 1 / (1 + math.exp(-max(-_LIMIT, min(_LIMIT, total))))
        spread = probability * (1 - probability)
        for first in range(size):
            gradient[first] += (label - probability) * row[first]
            scaled = spread * row[first]
            for second in range(first, size):
                hessian[first][second] += scaled * row[second]
    for first in range(size):
        for second in range(first):
            hessian[first][second] = hessian[second][first]
    for index in range(size - 1):
        hessian[index][index] += RIDGE
        gradient[index] -= RIDGE * weights[index]
    change = solve(matrix=hessian, vector=gradient)
    return [weight + delta for weight, delta in zip(weights, change, strict=True)]


def fit(*, rows: list[list[float]], labels: list[bool]) -> Model:
    """Fit the model to *rows* of features and their *labels* (true for a same-module pair)."""
    means, scales = _standardise(rows)
    design = [
        [(value - mean) / scale for value, mean, scale in zip(row, means, scales, strict=True)]
        + [1.0]
        for row in rows
    ]
    weights = [0.0] * (len(means) + 1)
    for _iteration in range(ITERATIONS):
        weights = _step(design=design, labels=labels, weights=weights)
    return Model(means=means, scales=scales, weights=tuple(weights[:-1]), intercept=weights[-1])
