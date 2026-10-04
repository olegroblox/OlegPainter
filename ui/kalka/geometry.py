"""Небольшие векторные помощники для работы с QPointF.

Вынесены отдельно, чтобы математика трансформаций читалась как векторная
алгебра, а не как набор обращений к .x()/.y().
"""

from __future__ import annotations

from PySide6.QtCore import QPointF


def vmul(p: QPointF, s: float) -> QPointF:
    """Умножение вектора на скаляр."""
    return QPointF(p.x() * s, p.y() * s)


def vdot(a: QPointF, b: QPointF) -> float:
    """Скалярное произведение."""
    return a.x() * b.x() + a.y() * b.y()


def sign(x: float) -> float:
    return -1.0 if x < 0 else 1.0


def clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else hi if value > hi else value
