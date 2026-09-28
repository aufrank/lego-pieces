"""Design a bulk stock order for a mixed round of builds, recolored into a batch palette.

    uv run make-kit                                  # 4 Student Scissors + 8-10 BrickMecha, auto palette
    uv run make-kit --ss 4 --bm 8-10 --palette "Black,White,Red,Blue,..."

Simulates many rounds, recolors every build into the palette (keeping color
blocking; see recolor.py), and stocks:
  - visible pieces (plates, bricks, tiles, slopes...) in palette colors, to cover
    `--visible-coverage` of rounds;
  - moving pieces (bars, clips, handles, claws, balls, hinges, pin/axle holes)
    deeper, to cover `--moving-coverage` of rounds, including rarer ones. Their
    color mostly doesn't matter, so they're bought in each part's cheapest color.

Special finishes (pearl, metallic, chrome), rubber, tires/wheels and printed parts
keep their designed colors and are specific to a few builds, so they're left
out of the kit and bought with each round.

Then checks the kit on fresh rounds: how much of a round it supplies, and what
you'd still have to buy.
"""

from __future__ import annotations

import argparse
import csv
import random
import re
from collections import Counter
from pathlib import Path
from statistics import median

from lego_pieces.analysis import percentile, write_bricklink_wanted_list
from lego_pieces.catalog import Build, Catalog, Lot, load_catalog
from lego_pieces.rebrickable import RebrickableCatalog
from lego_pieces.recolor import INFEASIBLE, Pricing, recolor_round, resolve_colors
from lego_pieces.roles import HYBRID, JOINT, accent_colors, is_fixed_color, is_fixed_part, lot_kind, part_role

_MOVING_WORDS = re.compile(
    r"\b(bar|handles?|clips?|claw|ball|socket|hinge|pin|axle|holes?|towball|click|t piece|joint|swivel)\b", re.I
)


def is_moving(item: str, name: str) -> bool:
    """Articulation and connection pieces: bars, clips, handles, claws, balls, hinges, pin/axle holes."""
    return part_role(item, name) in (JOINT, HYBRID) or bool(_MOVING_WORDS.search(name))


def pick_palette(catalog: Catalog, pricing: Pricing, n_solid: int, n_trans: int) -> list[str]:
    """Cheapest colors for the visible parts builds actually use, solid and transparent ranked separately."""
    weights: dict[bool, Counter[str]] = {False: Counter(), True: Counter()}
    for build in catalog.builds:
        accents = accent_colors(build, catalog)
        for lot, qty in build.lots.items():
            if lot_kind(lot, build, catalog, accents) == "visible":
                trans = catalog.describe(lot)[1].startswith("Trans")
                weights[trans][lot[0]] += qty

    def score(color: str, trans: bool) -> float:
        w = weights[trans]
        return sum(q * min(pricing.price((item, color)), 5.0) for item, q in w.items()) / sum(w.values())

    palette = []
    for trans, n in ((False, n_solid), (True, n_trans)):
        colors = [c for c, name in pricing.choices.items() if name.startswith("Trans") == trans]
        palette += sorted(colors, key=lambda c: score(c, trans))[:n]
    return palette


def parse_range(text: str) -> tuple[int, int]:
    lo, _, hi = text.partition("-")
    return int(lo), int(hi or lo)


def simulate_rounds(catalog: Catalog, mix: dict[str, tuple[int, int]], n: int, seed: int) -> list[list[Build]]:
    rng = random.Random(seed)
    pools = {src: [b for b in catalog.builds if b.source == src] for src in mix}
    return [[b for src, (lo, hi) in mix.items() for b in rng.sample(pools[src], rng.randint(lo, hi))]
            for _ in range(n)]


def round_usage(builds: list[Build], catalog: Catalog, pricing: Pricing, stock: Counter[Lot],
                palette: set[str], balance: float) -> tuple[Counter[Lot], int]:
    """(pieces used, in the colors built with; pieces to buy) for one recolored round."""
    used: Counter[Lot] = Counter()
    buy = 0
    for plan in recolor_round(builds, catalog, pricing, stock, palette, balance=balance):
        for line in plan.lines:
            used[line.new] += line.qty
        buy += sum(plan.buy.values())
    return used, buy


def is_build_specific(lot: Lot, catalog: Catalog) -> bool:
    part, color = catalog.describe(lot)
    return is_fixed_color(color) or is_fixed_part(part) or lot[0].startswith("ldraw:") or lot[1].startswith("name:")


def size_kit(rounds: list[list[Build]], catalog: Catalog, pricing: Pricing, stock: Counter[Lot],
             palette: set[str], visible_p: float, moving_p: float, balance: float) -> Counter[Lot]:
    usages = [round_usage(r, catalog, pricing, stock, palette, balance)[0] for r in rounds]
    lots = set().union(*usages)
    kit: Counter[Lot] = Counter()
    for lot in lots:
        if is_build_specific(lot, catalog):
            continue
        name = catalog.describe(lot)[0]
        qty = percentile([u[lot] for u in usages], moving_p if is_moving(lot[0], name) else visible_p)
        if qty > 0:
            kit[lot] = qty
    return kit


def evaluate(kit: Counter[Lot], rounds: list[list[Build]], catalog: Catalog, pricing: Pricing,
             palette: set[str] | None) -> dict:
    buys, covers, specific = [], [], []
    for r in rounds:
        total = sum(b.pieces for b in r)
        buy = Counter()
        for p in recolor_round(r, catalog, pricing, kit, palette):
            buy.update(p.buy)
        buys.append(sum(buy.values()))
        specific.append(sum(q for lot, q in buy.items() if is_build_specific(lot, catalog)))
        covers.append(1 - buys[-1] / total)
    ordered = sorted(buys)
    return {"coverage": median(covers), "buy_median": median(buys), "buy_p90": ordered[int(0.9 * len(ordered))],
            "specific_median": median(specific)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ss", default="4", help="Student Scissors builds per round, e.g. 4 or 3-4 (default 4)")
    parser.add_argument("--bm", default="8-10", help="BrickMecha builds per round (default 8-10)")
    parser.add_argument("--palette", help="comma-separated colors; default: the 10 solid + 4 transparent "
                                          "colors that are cheapest for these parts")
    parser.add_argument("--visible-coverage", type=float, default=0.9)
    parser.add_argument("--moving-coverage", type=float, default=0.99)
    parser.add_argument("--balance", type=float, default=1.0,
                        help="spread colored groups across the palette instead of piling into the cheapest "
                             "colors (0 = cheapest only; default 1)")
    parser.add_argument("--rounds", type=int, default=300, help="simulated rounds (default 300)")
    parser.add_argument("--out", type=Path, default=Path("data/out"))
    args = parser.parse_args()

    catalog = load_catalog(Path("data/raw"), Path("data/brickmecha/html"))
    if catalog.bridge is None:
        raise SystemExit("needs the Rebrickable catalog: run `uv run fetch-rebrickable`")
    pricing = Pricing(catalog, RebrickableCatalog.load())
    palette_ids = (list(resolve_colors(catalog, args.palette.split(","))) if args.palette
                   else pick_palette(catalog, pricing, 10, 4))
    palette = set(palette_ids)
    mix = {"Student Scissors": parse_range(args.ss), "BrickMecha": parse_range(args.bm)}
    print(f"Round: {args.ss} Student Scissors + {args.bm} BrickMecha builds")
    print("Palette: " + ", ".join(pricing.names[c] for c in palette_ids))

    # Size against an empty shelf, then again against that kit so recoloring favors what's in it.
    sizing = simulate_rounds(catalog, mix, args.rounds, seed=0)
    sizes = (args.visible_coverage, args.moving_coverage, args.balance)
    kit = size_kit(sizing, catalog, pricing, Counter(), palette, *sizes)
    kit = size_kit(sizing, catalog, pricing, kit, palette, *sizes)

    rows = []
    for lot, qty in kit.items():
        part, color = catalog.describe(lot)
        rows.append({"group": "moving" if is_moving(lot[0], part) else "visible", "bl_item": lot[0],
                     "bl_color": lot[1], "part": part, "color": color, "qty": qty,
                     "rarity_price": round(pricing.price(lot), 2) if pricing.price(lot) < INFEASIBLE else ""})
    rows.sort(key=lambda r: (r["group"], r["color"], -r["qty"]))
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "stock_kit.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    write_bricklink_wanted_list(args.out / "stock_kit_bricklink.xml", kit)

    by_group, by_color = Counter(), Counter()
    for r in rows:
        by_group[r["group"]] += r["qty"]
        by_color[r["color"]] += r["qty"]
    print(f"\nKit: {sum(kit.values()):,} pieces in {len(kit)} lots "
          f"({by_group['moving']:,} moving, {by_group['visible']:,} visible)")
    print("By color: " + ", ".join(f"{c} {q:,}" for c, q in by_color.most_common()))
    print("\nMost-stocked moving pieces:")
    for r in sorted((r for r in rows if r["group"] == "moving"), key=lambda r: -r["qty"])[:15]:
        print(f"  {r['qty']:>4}  {r['part'][:60]} / {r['color']}  [{r['bl_item']}]")

    test = simulate_rounds(catalog, mix, max(60, args.rounds // 3), seed=1)
    print("\nOn fresh simulated rounds (median round: "
          f"{median(sum(b.pieces for b in r) for r in test):.0f} pieces):")
    for label, pal in (("recolored into the palette", palette), ("recolored freely", None)):
        e = evaluate(kit, test, catalog, pricing, pal)
        print(f"  {label:28} kit supplies {e['coverage']:.0%}; still to buy: median {e['buy_median']:.0f} "
              f"(of which {e['specific_median']:.0f} special-finish/tire/printed), "
              f"bad round (90th pct) {e['buy_p90']:.0f} pieces")
    print(f"\nOrder list: {args.out / 'stock_kit.csv'} and {args.out / 'stock_kit_bricklink.xml'} "
          f"(BrickLink: Want > Upload shows current prices)")
