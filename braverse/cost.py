"""Energy costs: parsing `{B}{B}{N}` strings and deciding how to pay them."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .enums import ANY, SYMBOL_TO_COLOR, Color

_SYMBOL_RE = re.compile(r"\{([A-Za-z]+)\}")


@dataclass(frozen=True)
class Cost:
    """A colour requirement, e.g. two blue plus one of anything."""

    colored: tuple[tuple[Color, int], ...] = ()
    generic: int = 0

    @property
    def total(self) -> int:
        return sum(n for _, n in self.colored) + self.generic

    @staticmethod
    def parse(text: str) -> "Cost":
        """Parse the symbol run of a cost, e.g. ``"{B}{B}{N}"``.

        Unknown symbols (a handful of typo'd rows in the card dump) are treated
        as generic so a bad string never makes a card unplayable.
        """
        counts: Counter[Color] = Counter()
        generic = 0
        for sym in _SYMBOL_RE.findall(text or ""):
            key = sym.upper()
            if key == ANY:
                generic += 1
            elif key in SYMBOL_TO_COLOR:
                counts[SYMBOL_TO_COLOR[key]] += 1
            else:
                generic += 1
        return Cost(tuple(sorted(counts.items(), key=lambda kv: kv[0].value)), generic)

    def __str__(self) -> str:
        parts = []
        for color, n in self.colored:
            parts.append(("{%s}" % color.value[0]) * n)
        parts.append("{N}" * self.generic)
        return "".join(parts) or "{free}"

    def __bool__(self) -> bool:
        return self.total > 0


@dataclass
class PaymentPlan:
    """Which support cards to rest, chosen by :func:`plan_payment`."""

    indices: list[int] = field(default_factory=list)

    def __bool__(self) -> bool:
        return True


def plan_payment(
    cost: Cost,
    available: Sequence[Color],
    *,
    substitutes: Iterable[Color] = (),
) -> PaymentPlan | None:
    """Pick support cards to rest for ``cost``.

    ``available`` is the colour of each *active* support card, indexed
    positionally. ``substitutes`` are extra colours an effect has granted for
    this payment ("can be used as {B}").

    Returns ``None`` when the cost cannot be paid. The greedy order matters:
    coloured requirements are filled from exact matches first so that generic
    requirements do not eat the only card of a needed colour.
    """
    pool = {c: [] for c in Color}
    for i, color in enumerate(available):
        pool.setdefault(color, []).append(i)

    extra = list(substitutes)
    chosen: list[int] = []

    for color, needed in cost.colored:
        bucket = pool.get(color, [])
        while needed and bucket:
            chosen.append(bucket.pop())
            needed -= 1
        while needed and color in extra:
            extra.remove(color)  # a granted substitute pays without resting
            needed -= 1
        if needed:
            return None

    remaining = [i for bucket in pool.values() for i in bucket]
    if len(remaining) < cost.generic:
        return None
    # Spend the least useful cards on generic: those whose colour is not
    # required by this cost at all.
    needed_colors = {c for c, _ in cost.colored}
    remaining.sort(key=lambda i: available[i] in needed_colors)
    chosen.extend(remaining[: cost.generic])
    return PaymentPlan(sorted(chosen))


def can_pay(cost: Cost, available: Sequence[Color], **kw) -> bool:
    return plan_payment(cost, available, **kw) is not None
