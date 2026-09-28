"""Plan a round of builds against the pieces you own.

You build a few models at once, then take them apart for the next round, so
what you own only grows: after a round you have at least as many of each piece
as that round needed. This tool:

  - suggests builds whose pieces you mostly own already,
  - prints a shopping list for a round you pick (CSV + BrickLink wanted list),
  - records a finished round into your inventory.

    uv run plan-round --suggest                          # pick 4 builds for me
    uv run plan-round --suggest "Bonecrusher"            # start from this build
    uv run plan-round "Bonecrusher" "Brawl" "moc-401"    # shopping list for these
    uv run plan-round "Bonecrusher" "Brawl" --record     # after buying: update inventory

Recoloring (--recolor) swaps each build's colors for ones you own or that are
common, keeping its color blocking; see recolor.py. Joint pieces can be any
color. --palette limits the new colors, so a round can be bought in a few
batch colors; --keep pins colors that must stay as designed.

    uv run plan-round --suggest --recolor
    uv run plan-round "Carrion" "Pulse" --recolor --palette "Dark Green,Tan,Black,Light Bluish Gray"
    uv run plan-round "Carrion" "Pulse" --recolor --keep "Black"

Inventory is data/inventory.csv (bl_item, bl_color, qty). Until it exists, the
planner assumes you own the core kit (data/out/kit.csv).
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from lego_pieces.analysis import shortfall, write_bricklink_wanted_list
from lego_pieces.catalog import Build, Catalog, Lot, load_catalog
from lego_pieces.recolor import Pricing, recolor_build, recolor_round, resolve_colors

INVENTORY = Path("data/inventory.csv")
KIT = Path("data/out/kit.csv")


def read_stock(path: Path) -> Counter[Lot]:
    stock: Counter[Lot] = Counter()
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            stock[(row["bl_item"], row["bl_color"])] += int(row["qty"])
    return stock


def write_stock(path: Path, stock: Counter[Lot], catalog: Catalog) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["bl_item", "bl_color", "part", "color", "qty"])
        for lot, qty in sorted(stock.items(), key=lambda kv: (catalog.describe(kv[0])[1], kv[0])):
            writer.writerow([*lot, *catalog.describe(lot), qty])


def find_build(catalog: Catalog, query: str) -> Build:
    q = query.strip().lower()
    for key in (lambda b: b.build_id.lower() == q or b.build_id.lower() == f"moc-{q}",
                lambda b: b.title.lower() == q,
                lambda b: b.title.lower().startswith(q),
                lambda b: q in b.title.lower()):
        matches = [b for b in catalog.builds if key(b)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            listing = "\n".join(f"  {b.build_id:>10}  {b.title}  ({b.source})" for b in matches[:15])
            raise SystemExit(f"'{query}' matches {len(matches)} builds; be more specific or use the id:\n{listing}")
    raise SystemExit(f"no build matches '{query}'")


def demand_of(builds: list[Build]) -> Counter[Lot]:
    demand: Counter[Lot] = Counter()
    for build in builds:
        demand.update(build.lots)
    return demand


class Planner:
    """Pieces a round needs, in exact colors or recolored."""

    def __init__(self, catalog: Catalog, stock: Counter[Lot], pricing: Pricing | None = None,
                 palette: set[str] | None = None, keep: frozenset[str] = frozenset()):
        self.catalog, self.stock, self.pricing, self.palette, self.keep = catalog, stock, pricing, palette, keep

    def plan(self, builds: list[Build]):
        """(pieces used in the colors you'll build with, pieces to buy, per-build recolor plans or None)."""
        if self.pricing is None:
            demand = demand_of(builds)
            return demand, shortfall(demand, self.stock), None
        plans = recolor_round(builds, self.catalog, self.pricing, self.stock, self.palette, self.keep)
        used: Counter[Lot] = Counter()
        need: Counter[Lot] = Counter()
        for p in plans:
            for line in p.lines:
                used[line.new] += line.qty
            need.update(p.buy)
        return used, need, plans

    def extra(self, chosen: list[Build], candidates: list[Build]) -> dict[str, int]:
        """Pieces each candidate adds to the round's shopping list."""
        if self.pricing is None:
            base = sum(shortfall(demand_of(chosen), self.stock).values())
            return {b.build_id: sum(shortfall(demand_of(chosen + [b]), self.stock).values()) - base
                    for b in candidates}
        available = Counter(self.stock)
        for b in sorted(chosen, key=lambda b: -b.pieces):
            recolor_build(b, self.catalog, self.pricing, available, self.palette, self.keep)
        return {b.build_id: sum(recolor_build(b, self.catalog, self.pricing, available, self.palette,
                                              self.keep, commit=False).buy.values())
                for b in candidates}


def suggest(catalog: Catalog, planner: Planner, chosen: list[Build], round_size: int,
            source: str | None, exclude: set[str]) -> list[Build]:
    """Greedily add builds whose pieces you mostly own already."""
    chosen = list(chosen)
    pool = [b for b in catalog.builds
            if b not in chosen and b.build_id not in exclude and (source is None or b.source == source)]
    while len(chosen) < round_size and pool:
        extra = planner.extra(chosen, pool)
        # Rank by the share of the build you'd have to buy, so small builds don't win by default.
        ranked = sorted(pool, key=lambda b: (extra[b.build_id] / b.pieces, -b.pieces))
        print(f"\nSlot {len(chosen) + 1}: builds needing the smallest share of new pieces")
        for b in ranked[:5]:
            print(f"  {extra[b.build_id] / b.pieces:>4.0%} new (+{extra[b.build_id]:>3} pcs)  {b.pieces:>4}-pc "
                  f"{b.source:<16}  {b.title[:60]}  [{b.build_id}]")
        chosen.append(ranked[0])
        pool.remove(ranked[0])
    return chosen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("builds", nargs="*", help="build titles (or part of one) or ids like moc-401")
    parser.add_argument("--suggest", action="store_true", help="fill the round with builds whose pieces you mostly own already")
    parser.add_argument("--round-size", type=int, default=4)
    parser.add_argument("--source", choices=["Student Scissors", "BrickMecha"], help="only suggest from this source")
    parser.add_argument("--exclude-done", action="store_true",
                        help="don't suggest builds recorded in data/done.txt")
    parser.add_argument("--owned", type=Path, help=f"inventory CSV (default {INVENTORY}, else {KIT})")
    parser.add_argument("--record", action="store_true",
                        help="add this round to the inventory (run after buying the shopping list)")
    parser.add_argument("--recolor", action="store_true",
                        help="swap colors for ones you own or that are common, keeping each build's color blocking")
    parser.add_argument("--palette", help="with --recolor: comma-separated colors to build in, e.g. 'Dark Green,Tan'")
    parser.add_argument("--keep", help="with --recolor: comma-separated colors to leave as designed, e.g. 'Black'")
    parser.add_argument("--out", type=Path, default=Path("data/out"))
    args = parser.parse_args()
    if (args.palette or args.keep) and not args.recolor:
        parser.error("--palette and --keep need --recolor")

    catalog = load_catalog(Path("data/raw"), Path("data/brickmecha/html"))
    owned_path = args.owned or (INVENTORY if INVENTORY.exists() else KIT)
    stock = read_stock(owned_path) if owned_path.exists() else Counter()
    print(f"Owned pieces: {sum(stock.values()):,} from {owned_path if owned_path.exists() else 'nothing'}")

    done_path = Path("data/done.txt")
    done = set(done_path.read_text().split()) if args.exclude_done and done_path.exists() else set()

    pricing = None
    palette, keep = None, frozenset()
    if args.recolor:
        from lego_pieces.rebrickable import RebrickableCatalog
        if catalog.bridge is None:
            raise SystemExit("--recolor needs the Rebrickable catalog: run `uv run fetch-rebrickable`")
        pricing = Pricing(catalog, RebrickableCatalog.load())
        palette = resolve_colors(catalog, args.palette.split(",")) if args.palette else None
        keep = frozenset(resolve_colors(catalog, args.keep.split(","))) if args.keep else frozenset()
    planner = Planner(catalog, stock, pricing, palette, keep)

    chosen = [find_build(catalog, q) for q in args.builds]
    if args.suggest:
        chosen = suggest(catalog, planner, chosen, args.round_size, args.source, done)
    if not chosen:
        parser.error("name some builds, or use --suggest")

    used, need, plans = planner.plan(chosen)
    total = sum(b.pieces for b in chosen)
    print(f"\nRound: {len(chosen)} builds, {total} pieces, {total - sum(need.values())} from what you own, "
          f"{sum(need.values())} to buy in {len(need)} lots")
    for b in chosen:
        print(f"  {b.pieces:>4}-pc  {b.title}  ({b.source}, {b.build_id})  {b.url}")
    if plans:
        names = pricing.names
        print("\nColor changes (color groups keep their blocking; joint pieces use any color you own):")
        for p in plans:
            changes = [f"{names.get(o, o)} -> {names.get(n, n)}" for o, n in p.mapping.items() if o != n]
            print(f"  {p.build.title[:40]:40} " + (", ".join(changes) if changes else "as designed"))
        guide = args.out / "round_recolor_guide.csv"
        args.out.mkdir(parents=True, exist_ok=True)
        with guide.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["build", "part", "bl_item", "designed_color", "use_color", "qty", "from_owned", "buy",
                             "kind"])
            for p in plans:
                for line in sorted(p.lines, key=lambda l: (names.get(l.original[1], ""), l.original[0])):
                    writer.writerow([p.build.title, catalog.describe(line.original)[0], line.original[0],
                                     names.get(line.original[1], line.original[1]), names.get(line.new[1], line.new[1]),
                                     line.qty, line.owned, line.buy, line.kind])
        print(f"Recolor guide (which color to use for each piece while following the instructions): {guide}")

    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "round_shopping.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["bl_item", "bl_color", "part", "color", "qty"])
        for lot, qty in sorted(need.items(), key=lambda kv: (catalog.describe(kv[0])[1], kv[0])):
            writer.writerow([*lot, *catalog.describe(lot), qty])
    write_bricklink_wanted_list(args.out / "round_shopping_bricklink.xml", need)
    print(f"\nShopping list: {args.out / 'round_shopping.csv'} and {args.out / 'round_shopping_bricklink.xml'}")
    unlisted = [lot for lot in need if lot[0].startswith("ldraw:") or lot[1].startswith(("ldraw:", "name:"))]
    if unlisted:
        print(f"  ({len(unlisted)} lots lack BrickLink ids and are only in the CSV)")

    if args.record:
        for lot, qty in used.items():
            stock[lot] = max(stock[lot], qty)
        write_stock(INVENTORY, stock, catalog)
        with done_path.open("a") as f:
            f.writelines(f"{b.build_id}\n" for b in chosen)
        print(f"Recorded: {INVENTORY} now holds {sum(stock.values()):,} pieces; builds added to {done_path}")
