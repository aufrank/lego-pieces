"""Aggregate part usage across builds and size a shared parts kit.

Two views of "most used":
  - by lot (part + color): what you'd actually buy.
  - by shape (part, any color): which molds the designers lean on.

Rankings weight each source equally: a part's score is the average, across
sources, of the share of that source's builds using it. Otherwise hundreds of
small BrickMecha builds would outvote the larger Student Scissors ones.

The kit is meant for building in rounds: a few builds at once, then taking
them apart for the next round. It holds every lot whose score clears a
threshold, stocked to cover most rounds: we simulate random rounds of
`round_size` builds within each source, and stock each lot to its `coverage`
percentile of per-round demand in whichever source needs more.
"""

from __future__ import annotations

import csv
import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from xml.sax.saxutils import escape

from lego_pieces.catalog import Build, Catalog, Lot


@dataclass
class Usage:
    key: str | Lot
    per_build: list[int]  # quantity in each build that uses it
    by_source: dict[str, list[int]]  # the same quantities, split by source
    source_sizes: dict[str, int]  # builds per source

    @property
    def score(self) -> float:
        return sum(len(self.by_source.get(src, [])) / n for src, n in self.source_sizes.items()) / len(self.source_sizes)

    @property
    def n_builds(self) -> int:
        return len(self.per_build)

    @property
    def total_qty(self) -> int:
        return sum(self.per_build)


def _usage(builds: list[Build], qty_by_key) -> list[Usage]:
    sizes = dict(Counter(b.source for b in builds))
    per_key: dict = defaultdict(lambda: defaultdict(list))
    for build in builds:
        for key, qty in qty_by_key(build).items():
            per_key[key][build.source].append(qty)
    usages = [
        Usage(key, [q for qs in by_src.values() for q in qs], dict(by_src), sizes)
        for key, by_src in per_key.items()
    ]
    return sorted(usages, key=lambda u: (-u.score, -u.total_qty))


def lot_usage(builds: list[Build]) -> list[Usage]:
    return _usage(builds, lambda b: b.lots)


def by_item(lots: Counter[Lot]) -> Counter[str]:
    items: Counter[str] = Counter()
    for (item, _color), qty in lots.items():
        items[item] += qty
    return items


def shape_usage(builds: list[Build]) -> list[Usage]:
    return _usage(builds, lambda b: by_item(b.lots))


def source_columns(u: Usage) -> dict:
    cols = {}
    for src, n in u.source_sizes.items():
        qtys = u.by_source.get(src, [])
        slug = src.lower().replace(" ", "_")
        cols[f"{slug}_builds"] = len(qtys)
        cols[f"{slug}_share"] = round(len(qtys) / n, 3)
        cols[f"{slug}_qty"] = sum(qtys)
    return cols


def simulate_rounds(builds: list[Build], round_size: int, n_rounds: int, seed: int) -> dict[str, list[Counter[Lot]]]:
    """Per source, the combined parts demand of `n_rounds` random rounds of `round_size` builds."""
    rng = random.Random(seed)
    pools: dict[str, list[Build]] = defaultdict(list)
    for build in builds:
        pools[build.source].append(build)
    rounds: dict[str, list[Counter[Lot]]] = {}
    for source, pool in pools.items():
        rounds[source] = []
        for _ in range(n_rounds):
            demand: Counter[Lot] = Counter()
            for build in rng.sample(pool, round_size):
                demand.update(build.lots)
            rounds[source].append(demand)
    return rounds


def percentile(values: list[int], p: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p * len(ordered)) - 1)]


def round_quantities(keys, rounds: dict[str, list[Counter]], coverage: float) -> dict:
    """For each key, enough to cover `coverage` of rounds in whichever source needs the most."""
    return {
        key: max(percentile([r[key] for r in source_rounds], coverage) for source_rounds in rounds.values())
        for key in keys
    }


def build_kit(usage: list[Usage], round_qty: dict[Lot, int], min_score: float) -> dict[Lot, int]:
    return {u.key: round_qty[u.key] for u in usage if u.score >= min_score and round_qty[u.key] > 0}


def shortfall(demand: Counter[Lot], stock: dict[Lot, int]) -> Counter[Lot]:
    """What's left to buy after taking everything `stock` can supply."""
    return +Counter({lot: qty - stock.get(lot, 0) for lot, qty in demand.items()})


def coverage(build: Build, kit: dict[Lot, int]) -> tuple[float, Counter[Lot]]:
    """Share of the build's pieces the kit supplies, and what's left to buy."""
    topup = shortfall(build.lots, kit)
    return 1 - sum(topup.values()) / build.pieces, topup


def kit_tradeoff(usage: list[Usage], round_qty: dict[Lot, int], eval_rounds: dict[str, list[Counter[Lot]]],
                 min_scores: list[float]) -> list[dict]:
    rows = []
    for min_score in min_scores:
        kit = build_kit(usage, round_qty, min_score)
        row = {"min_score": min_score, "kit_lots": len(kit), "kit_pieces": sum(kit.values())}
        for source, rounds in eval_rounds.items():
            slug = source.lower().replace(" ", "_")
            topups = [sum(shortfall(r, kit).values()) for r in rounds]
            covered = [1 - t / sum(r.values()) for t, r in zip(topups, rounds)]
            row[f"{slug}_round_coverage"] = round(median(covered), 3)
            row[f"{slug}_round_topup_pieces"] = median(topups)
            row[f"{slug}_round_topup_lots"] = median(len(shortfall(r, kit)) for r in rounds)
        rows.append(row)
    return rows


def color_mix(build: Build, catalog: Catalog, top: int = 3) -> str:
    by_color: Counter[str] = Counter()
    for lot, qty in build.lots.items():
        by_color[catalog.parts[lot].color_name] += qty
    return "; ".join(f"{name} {qty / build.pieces:.0%}" for name, qty in by_color.most_common(top))


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_reports(catalog: Catalog, out_dir: Path, round_size: int, coverage_target: float,
                  kit_min_score: float, min_scores: list[float], n_rounds: int = 2000, seed: int = 0) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    lots = lot_usage(catalog.builds)
    shapes = shape_usage(catalog.builds)
    names = {item: info.name for (item, _), info in catalog.parts.items()}

    rounds = simulate_rounds(catalog.builds, round_size, n_rounds, seed)
    lot_round_qty = round_quantities([u.key for u in lots], rounds, coverage_target)
    item_rounds = {src: [by_item(r) for r in rs] for src, rs in rounds.items()}
    item_round_qty = round_quantities([u.key for u in shapes], item_rounds, coverage_target)
    round_col = f"qty_per_round_of_{round_size}_p{coverage_target * 100:.0f}"

    _write_csv(out_dir / "parts_by_color.csv", [{
        "rank": rank, "bl_item": u.key[0], "bl_color": u.key[1],
        "part": catalog.parts[u.key].name, "color": catalog.parts[u.key].color_name,
        "score": round(u.score, 3), **source_columns(u),
        "builds_using": u.n_builds, "total_qty": u.total_qty, "median_qty_per_build": median(u.per_build),
        "max_qty_per_build": max(u.per_build), round_col: lot_round_qty[u.key],
    } for rank, u in enumerate(lots, 1)])

    colors_by_item: dict[str, Counter[str]] = defaultdict(Counter)
    for build in catalog.builds:
        for lot, qty in build.lots.items():
            colors_by_item[lot[0]][catalog.parts[lot].color_name] += qty
    _write_csv(out_dir / "parts_by_shape.csv", [{
        "rank": rank, "bl_item": u.key, "part": names[u.key],
        "score": round(u.score, 3), **source_columns(u),
        "builds_using": u.n_builds, "total_qty": u.total_qty, "median_qty_per_build": median(u.per_build),
        "max_qty_per_build": max(u.per_build), round_col: item_round_qty[u.key],
        "n_colors": len(colors_by_item[u.key]),
        "top_colors": "; ".join(f"{c} ({q})" for c, q in colors_by_item[u.key].most_common(4)),
    } for rank, u in enumerate(shapes, 1)])

    kit = build_kit(lots, lot_round_qty, kit_min_score)
    _write_csv(out_dir / "kit.csv", [{
        "bl_item": lot[0], "bl_color": lot[1],
        "part": catalog.parts[lot].name, "color": catalog.parts[lot].color_name,
        "element_id": catalog.parts[lot].element_id, "qty": qty,
    } for lot, qty in sorted(kit.items(), key=lambda kv: (catalog.parts[kv[0]].color_name, kv[0]))])
    write_bricklink_wanted_list(out_dir / "kit_bricklink.xml", kit)

    build_rows, topup_rows = [], []
    for build in sorted(catalog.builds, key=lambda b: (b.source, b.published, b.build_id)):
        covered, topup = coverage(build, kit)
        build_rows.append({
            "source": build.source, "build": build.title, "published": build.published,
            "pieces": build.pieces, "lots": len(build.lots),
            "kit_coverage": round(covered, 3), "topup_pieces": sum(topup.values()),
            "topup_lots": len(topup), "main_colors": color_mix(build, catalog),
            "sources": "; ".join(build.sources), "url": build.url,
        })
        topup_rows += [{
            "source": build.source, "build": build.title, "bl_item": lot[0], "bl_color": lot[1],
            "part": catalog.parts[lot].name, "color": catalog.parts[lot].color_name, "qty": qty,
        } for lot, qty in sorted(topup.items())]
    _write_csv(out_dir / "builds.csv", build_rows)
    _write_csv(out_dir / "build_topups.csv", topup_rows)

    # Judge kits on fresh rounds, not the ones used to size them.
    eval_rounds = simulate_rounds(catalog.builds, round_size, n_rounds, seed + 1)
    tradeoff = kit_tradeoff(lots, lot_round_qty, eval_rounds, min_scores)
    _write_csv(out_dir / "kit_tradeoff.csv", tradeoff)
    return {"lots": lots, "shapes": shapes, "kit": kit, "builds": build_rows, "tradeoff": tradeoff}


def write_bricklink_wanted_list(path: Path, kit: dict[Lot, int], condition: str = "X") -> None:
    """BrickLink wanted-list XML (Want > Upload). Skips parts with no BrickLink id.

    condition: N (new), U (used) or X (either).
    """
    items = [
        f"<ITEM><ITEMTYPE>P</ITEMTYPE><ITEMID>{escape(item)}</ITEMID>"
        f"<COLOR>{escape(color)}</COLOR><MINQTY>{qty}</MINQTY>"
        f"<CONDITION>{condition}</CONDITION></ITEM>"
        for (item, color), qty in sorted(kit.items())
        if not item.startswith("ldraw:") and not color.startswith(("ldraw:", "name:"))
    ]
    path.write_text("<INVENTORY>\n" + "\n".join(items) + "\n</INVENTORY>\n")
