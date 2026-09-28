"""BrickLink order for one build: its parts list by color, plus a wanted list to upload.

Picks the largest build whose title matches the query, so "optimus" gives the
biggest Optimus Prime. Colors are kept exactly as the parts list specifies; only
BrickLink alternate item numbers for the same part are merged, so no lot is split
across two catalog entries.

    uv run build-order optimus
    uv run build-order "G1 Optimus Prime V2" --condition N
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

from lego_pieces.analysis import write_bricklink_wanted_list
from lego_pieces.catalog import Build, Catalog, Lot, PartInfo, load_catalog

# BrickLink lists these as alternate numbers of one catalog item: {alternate: primary}.
BL_ALTERNATES = {
    "4032b": "4032",  # Plate, Round 2 x 2 with Axle Hole
    "54657": "44302",  # Hinge Plate 1 x 2 Locking with 2 Fingers on End (7-tooth version)
}


def pick_build(catalog: Catalog, query: str) -> Build:
    matches = [b for b in catalog.builds if query.lower() in b.title.lower()]
    if not matches:
        raise SystemExit(f"no build title contains {query!r}")
    return max(matches, key=lambda b: b.pieces)


def order_lots(build: Build, catalog: Catalog) -> tuple[Counter[Lot], dict[Lot, PartInfo]]:
    lots: Counter[Lot] = Counter()
    info: dict[Lot, PartInfo] = {}
    for (item, color), qty in build.lots.items():
        lot = (BL_ALTERNATES.get(item, item), color)
        lots[lot] += qty
        info.setdefault(lot, catalog.parts.get(lot, catalog.parts[(item, color)]))
    return lots, info


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("query", help="text in the build title; the largest match is used")
    parser.add_argument("--raw", type=Path, default=Path("data/raw"), help="downloaded posts")
    parser.add_argument("--out", type=Path, default=Path("data/out/orders"), help="where files go")
    parser.add_argument("--condition", choices="NUX", default="X", help="N new, U used, X either (default)")
    args = parser.parse_args()

    catalog = load_catalog(args.raw)
    build = pick_build(catalog, args.query)
    lots, info = order_lots(build, catalog)

    rows = sorted(({
        "color": info[lot].color_name, "bl_color": lot[1], "bl_item": lot[0],
        "part": info[lot].name, "element_id": info[lot].element_id, "qty": qty,
    } for lot, qty in lots.items()), key=lambda r: (r["color"], r["part"]))

    args.out.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", build.title.lower()).strip("-")
    csv_path = args.out / f"{slug}_parts_by_color.csv"
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    xml_path = args.out / f"{slug}_bricklink.xml"
    write_bricklink_wanted_list(xml_path, dict(lots), args.condition)

    by_color: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        by_color[r["color"]].append(r["qty"])
    print(f"{build.title}: {build.pieces} pcs, {len(lots)} lots ({'; '.join(build.sources)})")
    print(f"{'color':<20} {'lots':>5} {'pcs':>5}")
    for color, qtys in sorted(by_color.items(), key=lambda kv: -sum(kv[1])):
        print(f"{color:<20} {len(qtys):>5} {sum(qtys):>5}")
    unmapped = [lot for lot in lots if lot[0].startswith("ldraw:") or lot[1].startswith("ldraw:")]
    if unmapped:
        print(f"\nnot in the wanted list (no BrickLink id): {unmapped}")
    print(f"\nwrote {csv_path} and {xml_path}")
