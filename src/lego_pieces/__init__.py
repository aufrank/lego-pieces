"""Which LEGO pieces do Student Scissors and BrickMecha builds use most?"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from lego_pieces.analysis import write_reports
from lego_pieces.catalog import load_catalog

MIN_SCORES = [0.3, 0.2, 0.15, 0.1, 0.07, 0.05, 0.03]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=Path("data/raw"), help="downloaded posts")
    parser.add_argument("--brickmecha", type=Path, default=Path("data/brickmecha/html"),
                        help="cached BrickMecha pages (from crawl-brickmecha)")
    parser.add_argument("--out", type=Path, default=Path("data/out"), help="where reports go")
    parser.add_argument("--round-size", type=int, default=4, help="builds assembled at once (default 4)")
    parser.add_argument("--coverage", type=float, default=0.9,
                        help="stock each kit piece for this share of rounds (default 0.9)")
    parser.add_argument("--kit-min-score", type=float, default=0.1,
                        help="kit holds pieces whose score (share of builds, averaged across sources) "
                             "is at least this (default 0.1)")
    parser.add_argument("--top", type=int, default=25, help="rows to print per ranking")
    args = parser.parse_args()

    catalog = load_catalog(args.raw, args.brickmecha)
    report = write_reports(catalog, args.out, args.round_size, args.coverage, args.kit_min_score, MIN_SCORES)

    print(f"{len(catalog.builds)} builds, {sum(b.pieces for b in catalog.builds):,} pieces, "
          f"{len(catalog.parts):,} distinct part+color lots")
    for note in catalog.notes:
        print(f"  - {note}")

    sizes = Counter(b.source for b in catalog.builds)
    header = "  ".join(f"{src[:14]:>14}" for src in sizes)
    print(f"\nRanked by share of builds using the piece, averaged across sources "
          f"({', '.join(f'{src}: {k} builds' for src, k in sizes.items())})")

    def per_source(u) -> str:
        return "  ".join(
            f"{len(u.by_source.get(src, [])):>4} bld {sum(u.by_source.get(src, [])):>5}" for src in sizes
        )

    print(f"\nMost-used part+color lots\n{'score':>5}  {header}  part / color")
    for u in report["lots"][: args.top]:
        info = catalog.parts[u.key]
        print(f"{u.score:>5.0%}  {per_source(u)}  {info.name} / {info.color_name}  [{u.key[0]}]")

    names = {item: info.name for (item, _), info in catalog.parts.items()}
    print(f"\nMost-used shapes, any color\n{'score':>5}  {header}  part")
    for u in report["shapes"][: args.top]:
        print(f"{u.score:>5.0%}  {per_source(u)}  {names[u.key]}  [{u.key}]")

    print(f"\nCore kit options: pieces stocked for {args.coverage:.0%} of random rounds of {args.round_size} builds,"
          f"\njudged on fresh random rounds (median round shown; top-up = pieces you'd still buy)")
    print(f"{'min score':>9} {'kit lots':>9} {'kit pcs':>8}  " + "  ".join(f"{src:^27}" for src in sizes))
    print(f"{'':>9} {'':>9} {'':>8}  " + "  ".join(f"{'coverage':>9} {'top-up pcs / lots':>17}" for _ in sizes))
    for t in report["tradeoff"]:
        cells = []
        for src in sizes:
            slug = src.lower().replace(" ", "_")
            cells.append(f"{t[f'{slug}_round_coverage']:>9.0%} {t[f'{slug}_round_topup_pieces']:>9.0f} / "
                         f"{t[f'{slug}_round_topup_lots']:<5.0f}")
        mark = "  <- kit.csv" if t["min_score"] == args.kit_min_score else ""
        print(f"{t['min_score']:>8.0%}+ {t['kit_lots']:>9} {t['kit_pieces']:>8}  " + "  ".join(cells) + mark)

    print(f"\nReports written to {args.out}/: parts_by_color.csv, parts_by_shape.csv, builds.csv, "
          f"kit.csv + kit_bricklink.xml, build_topups.csv, kit_tradeoff.csv")
