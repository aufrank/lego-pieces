"""Recolor builds to use pieces you own and common (cheaper) colors, keeping color blocking.

Each build's visible pieces are grouped by color. Every group moves to a new
color as a whole, and no two groups share a color, so the build keeps its
color blocking; solids stay solid, transparents stay transparent, and black/gray
groups stay black/gray while colored groups stay colored (so the build keeps its
look of a gray skeleton with colored panels). A part only
goes into a color LEGO actually makes it in. Joint pieces ("free", see roles.py)
come from whatever you own in any color. Special finishes, rubber and printed
parts stay as designed.

The choice minimizes an estimated cost: pieces you'd have to buy, each weighted
by how rare that part is in that color across all LEGO sets (Rebrickable), as a
stand-in for price: 1 for a part's most common color, +1 per 10x rarer.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linear_sum_assignment

from lego_pieces.catalog import Build, Catalog, Lot
from lego_pieces.rebrickable import RebrickableCatalog
from lego_pieces.roles import accent_colors, is_fixed_color, is_neutral_color, lot_kind

INFEASIBLE = 1e9
OFF_PALETTE = 1e5  # keeping a group's original color when it isn't in the palette
TIE_BREAK = 0.01  # per piece: only change a color when it saves something


class Pricing:
    """Availability and relative price of BrickLink lots, from Rebrickable."""

    def __init__(self, catalog: Catalog, rb: RebrickableCatalog):
        self.catalog, self.rb, self.bridge = catalog, rb, catalog.bridge
        self.known = set(catalog.parts)  # lots used in some build certainly exist
        self.names = {color: info.color_name for (_, color), info in catalog.parts.items()}
        self.choices = color_choices(catalog)
        self._cache: dict[Lot, float] = {}
        self._max_supply: dict[str, int] = {}

    def price(self, lot: Lot) -> float:
        """Relative cost per piece, or INFEASIBLE if the part isn't made in that color."""
        if lot in self._cache:
            return self._cache[lot]
        item, color = lot
        rb_part, rb_color = self.bridge.rb_part(item), self.bridge.rb_color(color)
        if rb_part is None or rb_color is None or rb_part not in self.rb.colors_of:
            cost = 2.0 if lot in self.known else INFEASIBLE
        elif rb_color not in self.rb.colors_of[rb_part]:
            cost = 2.0 if lot in self.known else INFEASIBLE
        else:
            if rb_part not in self._max_supply:
                self._max_supply[rb_part] = max(self.rb.supply[(rb_part, c)] for c in self.rb.colors_of[rb_part])
            supply = self.rb.supply[(rb_part, rb_color)]
            cost = 1 + math.log10((self._max_supply[rb_part] + 1) / (supply + 1))
        self._cache[lot] = cost
        return cost


@dataclass
class Line:
    original: Lot
    new: Lot
    qty: int
    owned: int
    kind: str  # visible, free, fixed

    @property
    def buy(self) -> int:
        return self.qty - self.owned


@dataclass
class BuildPlan:
    build: Build
    mapping: dict[str, str]  # original color id -> new color id, for visible groups
    lines: list[Line] = field(default_factory=list)

    @property
    def buy(self) -> Counter[Lot]:
        out: Counter[Lot] = Counter()
        for line in self.lines:
            if line.buy:
                out[line.new] += line.buy
        return out

    @property
    def cost(self) -> float:
        return sum(line.buy for line in self.lines)


def color_choices(catalog: Catalog) -> dict[str, str]:
    """Colors a group can move to: plain colors with BrickLink and Rebrickable ids. id -> name."""
    names = {color: info.color_name for (_, color), info in catalog.parts.items()}
    return {
        color: name for color, name in names.items()
        if not color.startswith(("ldraw:", "name:")) and catalog.bridge.rb_color(color) is not None
        and not is_fixed_color(name)
    }


def resolve_colors(catalog: Catalog, names: list[str]) -> set[str]:
    by_name = {info.color_name.lower(): color for (_, color), info in catalog.parts.items()}
    out = set()
    for name in names:
        if name.strip().lower() not in by_name:
            known = sorted(set(color_choices(catalog).values()))
            raise SystemExit(f"unknown color '{name}'. Colors: {', '.join(known)}")
        out.add(by_name[name.strip().lower()])
    return out


def recolor_build(build: Build, catalog: Catalog, pricing: Pricing, available: Counter[Lot],
                  palette: set[str] | None = None, keep: frozenset[str] = frozenset(),
                  commit: bool = True, color_penalty: dict[str, float] | None = None) -> BuildPlan:
    """Plan one build against `available` stock; with commit, take the pieces it uses out of `available`."""
    names, choices = pricing.names, pricing.choices
    accents = accent_colors(build, catalog)
    groups: dict[str, list[tuple[str, int]]] = {}
    other: list[tuple[Lot, int, str]] = []
    for lot, qty in build.lots.items():
        kind = lot_kind(lot, build, catalog, accents)
        if kind == "visible":
            groups.setdefault(lot[1], []).append((lot[0], qty))
        else:
            other.append((lot, qty, kind))

    # Assignment: visible color groups -> distinct colors.
    originals = list(groups)
    is_trans = {c: names.get(c, c).lower().replace("name:", "").startswith("trans") for c in set(originals) | set(choices)}
    targets = sorted(set(choices) | set(originals))
    cost = np.full((len(originals), len(targets)), INFEASIBLE)
    for i, original in enumerate(originals):
        for j, target in enumerate(targets):
            if target != original and (original in keep or is_trans[target] != is_trans[original]
                                       or is_neutral_color(names.get(target, target))
                                       != is_neutral_color(names.get(original, original))
                                       or target not in choices or (palette is not None and target not in palette)):
                continue
            total = 0.0
            for item, qty in groups[original]:
                price = pricing.price((item, target))
                if price >= INFEASIBLE:
                    total = INFEASIBLE
                    break
                total += max(0, qty - available[(item, target)]) * price
                if color_penalty and target != original:
                    total += qty * color_penalty.get(target, 0.0)
            if palette is not None and target == original and original not in palette and original not in keep:
                total += OFF_PALETTE
            if target != original:
                total += TIE_BREAK * sum(qty for _, qty in groups[original])
            cost[i, j] = min(total, INFEASIBLE)
    rows, cols = linear_sum_assignment(cost)
    mapping = {originals[r]: targets[c] for r, c in zip(rows, cols)}

    plan = BuildPlan(build, mapping)
    stock = available if commit else Counter(available)

    def take(original: Lot, new: Lot, qty: int, kind: str) -> None:
        owned = min(qty, stock[new])
        stock[new] -= owned
        plan.lines.append(Line(original, new, qty, owned, kind))

    for original, items in groups.items():
        for item, qty in items:
            take((item, original), (item, mapping[original]), qty, "visible")
    for lot, qty, kind in other:
        if kind == "fixed":
            take(lot, lot, qty, kind)
            continue
        # Free: owned pieces in any color (the original color first), then buy the cheapest color.
        item, remaining = lot[0], qty
        owned_colors = sorted((lot2 for lot2, n in stock.items() if lot2[0] == item and n > 0),
                              key=lambda l2: (l2 != lot, -stock[l2]))
        for owned_lot in owned_colors:
            if remaining == 0:
                break
            n = min(remaining, stock[owned_lot])
            stock[owned_lot] -= n
            plan.lines.append(Line(lot, owned_lot, n, n, kind))
            remaining -= n
        if remaining:
            cheapest = min([lot] + [(item, c) for c in choices], key=lambda l2: (pricing.price(l2), l2 != lot))
            plan.lines.append(Line(lot, cheapest, remaining, 0, kind))
    return plan


def recolor_round(builds: list[Build], catalog: Catalog, pricing: Pricing, stock: Counter[Lot],
                  palette: set[str] | None = None, keep: frozenset[str] = frozenset(),
                  balance: float = 0.0) -> list[BuildPlan]:
    """Plan builds that will be assembled at the same time, sharing (not reusing) owned pieces.

    balance > 0 spreads colored groups across colors: each colored target costs an extra
    `balance` per piece times how over-used it already is in this round (1 = its fair share).
    """
    available = Counter(stock)
    used: Counter[str] = Counter()  # colored pieces per new color so far this round
    plans = []
    for b in sorted(builds, key=lambda b: -b.pieces):
        penalty = None
        if balance and used:
            n_colors = len({c for c in (palette or pricing.choices)
                            if not is_neutral_color(pricing.names.get(c, c))})
            total = sum(used.values())
            penalty = {c: balance * n * n_colors / total for c, n in used.items()}
        plan = recolor_build(b, catalog, pricing, available, palette, keep, color_penalty=penalty)
        for line in plan.lines:
            if line.kind == "visible" and not is_neutral_color(pricing.names.get(line.new[1], "")):
                used[line.new[1]] += line.qty
        plans.append(plan)
    return plans
