"""Load per-build part lists from the downloaded Patreon posts.

Each post folder in data/raw holds a post.json (from gallery-dl) plus one or more
BrickLink Studio exports: *_Partslist.csv files and .io model files. A "build" is
one post; posts with several models (a stand, cassettes, a trailer) are summed,
since that's what you'd gather pieces for when you pick that post to build.
"""

from __future__ import annotations

import csv
import json
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from lego_pieces import brickmecha, rebrickable
from lego_pieces.parts import PartResolver, canonical_item

# A lot is one part in one color: (BrickLink item no, BrickLink color id).
# Parts Studio couldn't map to BrickLink fall back to "ldraw:<id>" keys.
Lot = tuple[str, str]

# Posts to leave out entirely, keyed by post id.
SKIP_POSTS = {
    "92244780": "Ironhide: superseded by 'Ironhide [UPDATE]' (97342773)",
}

# Files attached to the wrong post, keyed by (post id, file name). The creator
# attached Halbird's parts list to the Headspin post, so Headspin's parts come
# from its Studio model instead.
SKIP_FILES = {
    ("139306561", "Halbird_Partslist_Update.csv"): "Halbird's parts list, misattached to Headspin",
}
IO_FALLBACK = {
    "139306561": "Headspin.io",
}

# Studio's parts list merges some two-piece assemblies that a model file stores
# as separate halves: {assembly: (half, half)} in LDraw ids.
LDRAW_ASSEMBLIES = {
    "2429c01": ("2429", "2430"),  # Hinge Plate 1 x 4 Swivel
}


@dataclass
class PartInfo:
    name: str
    color_name: str
    element_id: str = ""


@dataclass
class Build:
    source: str
    build_id: str
    title: str
    published: str
    url: str
    sources: list[str] = field(default_factory=list)
    lots: Counter[Lot] = field(default_factory=Counter)

    @property
    def pieces(self) -> int:
        return sum(self.lots.values())


@dataclass
class Catalog:
    builds: list[Build]
    parts: dict[Lot, PartInfo]
    notes: list[str]
    bridge: rebrickable.Bridge | None = None

    def describe(self, lot: Lot) -> tuple[str, str]:
        """(part name, color name) for any lot, including part/color pairs no build uses."""
        if lot in self.parts:
            return self.parts[lot].name, self.parts[lot].color_name
        if not hasattr(self, "_names"):
            self._names = ({item: info.name for (item, _), info in self.parts.items()},
                           {color: info.color_name for (_, color), info in self.parts.items()})
        items, colors = self._names
        return items.get(lot[0], lot[0]), colors.get(lot[1], lot[1])


def clean_title(title: str) -> str:
    title = re.sub(r"^Instructions vote #\d+:\s*", "", title)
    title = re.sub(r"\s*\binstructions\b\s*", " ", title, flags=re.I)
    return re.sub(r"\s+", " ", title).strip()


def read_partslist(path: Path) -> list[dict[str, str]]:
    """Rows of a Studio parts-list export, checked against its 'Total qty' footer."""
    rows, footer_total = [], None
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        for row in reader:
            if row and row[0] == "Total qty":
                footer_total = int(next(reader)[0])
                break
            if any(cell.strip() for cell in row):
                rows.append(dict(zip(header, row)))
    # Studio's footer leaves out parts it couldn't map to BrickLink.
    total = sum(int(r["Qty"]) for r in rows if r["BLItemNo"].strip())
    if footer_total is not None and total != footer_total:
        raise ValueError(f"{path}: rows sum to {total} but footer says {footer_total}")
    return rows


def lot_for_row(row: dict[str, str]) -> Lot:
    if row["BLItemNo"].strip():
        return canonical_item(row["BLItemNo"].strip()), row["BLColorId"].strip()
    return f"ldraw:{row['LdrawId'].removesuffix('.dat')}", f"ldraw:{row['LDrawColorId']}"


def read_io_model(path: Path) -> Counter[tuple[str, int]]:
    """Part counts from a Studio .io file's LDraw model: (ldraw id, ldraw color) -> qty."""
    text = zipfile.ZipFile(path).read("model.ldr").decode("utf-8-sig", "replace")
    models: dict[str, list[tuple[int, str]]] = {}
    current = None
    for line in text.splitlines():
        tokens = line.split()
        if tokens[:2] == ["0", "FILE"]:
            current = " ".join(tokens[2:]).lower()
            models[current] = []
        elif tokens and tokens[0] == "1" and len(tokens) >= 15:
            if current is None:
                current = ""
                models[current] = []
            models[current].append((int(tokens[1]), " ".join(tokens[14:]).lower()))

    counts: Counter[tuple[str, int]] = Counter()

    def walk(model: str, inherited_color: int) -> None:
        for color, ref in models[model]:
            color = inherited_color if color == 16 else color  # 16 = inherit
            if ref in models:
                walk(ref, color)
            else:
                counts[(ref.removesuffix(".dat"), color)] += 1

    walk(next(iter(models)), 16)

    for assembly, (a, b) in LDRAW_ASSEMBLIES.items():
        for part, color in list(counts):
            if part == a and (b, color) in counts:
                n = min(counts[(a, color)], counts[(b, color)])
                counts[(a, color)] -= n
                counts[(b, color)] -= n
                counts[(assembly, color)] += n
    return +counts


STUDENT_SCISSORS = "Student Scissors"
BRICKMECHA = "BrickMecha"


def load_catalog(raw_dir: Path, brickmecha_dir: Path | None = None,
                 rebrickable_dir: Path | None = rebrickable.DATA_DIR) -> Catalog:
    catalog = Catalog([], {}, [])
    resolver = PartResolver()
    studio_rows = load_student_scissors(raw_dir, catalog, resolver)
    if rebrickable_dir is not None and (rebrickable_dir / "elements.csv.gz").exists():
        catalog.bridge = rebrickable.Bridge.build(studio_rows, rebrickable_dir)
    if brickmecha_dir is not None and brickmecha_dir.exists():
        load_brickmecha(brickmecha_dir, catalog, resolver)
    return catalog


def load_student_scissors(raw_dir: Path, catalog: Catalog, resolver: PartResolver) -> list[dict[str, str]]:
    """Load the Patreon builds; returns their BrickLink-mapped rows for the Rebrickable bridge."""
    parts, notes = catalog.parts, catalog.notes
    builds: list[Build] = []
    studio_rows: list[dict[str, str]] = []
    # LDraw color -> (BL color, color name), learned from every CSV, for reading .io files.
    ldraw_to_color: dict[int, tuple[str, str]] = {}

    for post_dir in sorted(p for p in raw_dir.iterdir() if (p / "post.json").exists()):
        post = json.loads((post_dir / "post.json").read_text())
        post_id = str(post["id"])
        title = clean_title(post["title"])
        if post_id in SKIP_POSTS:
            notes.append(f"skipped post {title}: {SKIP_POSTS[post_id]}")
            continue
        build = Build(STUDENT_SCISSORS, post_id, title, post["published_at"][:10], post["url"])

        for csv_path in sorted(post_dir.glob("*.csv")):
            if (post_id, csv_path.name) in SKIP_FILES:
                notes.append(f"{title}: ignored {csv_path.name} ({SKIP_FILES[(post_id, csv_path.name)]})")
                continue
            for row in read_partslist(csv_path):
                lot = lot_for_row(row)
                build.lots[lot] += int(row["Qty"])
                info = PartInfo(row["PartName"], row["ColorName"], row["ElementId"])
                # Prefer the name listed under the current (merged) number.
                if lot not in parts or row["BLItemNo"].strip() == lot[0]:
                    parts[lot] = info
                if row["BLItemNo"].strip():
                    studio_rows.append({
                        "bl_item": lot[0], "bl_color": lot[1], "element_id": row["ElementId"].strip(),
                        "ldraw_id": row["LdrawId"].lower().removesuffix(".dat"), "color_name": row["ColorName"],
                    })
                    resolver.learn(row["BLItemNo"].strip(), row["LdrawId"], row["PartName"],
                                   lot[1], row["ColorName"])
                    ldraw_to_color.setdefault(int(row["LDrawColorId"]), (lot[1], row["ColorName"]))
            build.sources.append(csv_path.name)

        if not build.lots and post_id not in IO_FALLBACK:
            notes.append(f"no parts list for {title} ({post['published_at'][:10]}): post has no attachments")
            continue
        builds.append(build)

    # Second pass, once every CSV has taught us the LDraw -> BrickLink mapping.
    for build in builds:
        if build.build_id not in IO_FALLBACK:
            continue
        io_name = IO_FALLBACK[build.build_id]
        io_path = next(raw_dir.glob(f"{build.build_id} */{io_name}"))
        unmapped = 0
        for (ldraw_id, color), qty in read_io_model(io_path).items():
            item, known = resolver.item(ldraw_id)
            item = item if known else f"ldraw:{ldraw_id}"
            bl_color, color_name = ldraw_to_color.get(color, (f"ldraw:{color}", f"LDraw color {color}"))
            lot = (item, bl_color)
            if not known or bl_color.startswith("ldraw:"):
                unmapped += qty
            parts.setdefault(lot, PartInfo(resolver.part_names.get(item, f"LDraw part {ldraw_id}"), color_name))
            build.lots[lot] += qty
        build.sources.append(io_name)
        notes.append(
            f"{build.title}: parts read from {io_name} ({build.pieces} pcs; "
            f"{unmapped} pcs had no BrickLink mapping and keep LDraw ids)"
        )
    catalog.builds += builds
    return studio_rows


def load_brickmecha(html_dir: Path, catalog: Catalog, resolver: PartResolver) -> None:
    """BrickMecha pages give Rebrickable part numbers and color names; map both onto BrickLink ids."""
    bridge = catalog.bridge
    unknown_parts: Counter[str] = Counter()
    unknown_colors: Counter[str] = Counter()
    pages = brickmecha.load_pages(html_dir)
    for page in pages:
        build = Build(BRICKMECHA, f"moc-{page.moc_no}", page.title, "",
                      f"{brickmecha.BASE}?moc-no={page.moc_no}", [f"moc-{page.moc_no:04d}.html"])
        for number, color_name, qty in page.rows:
            if bridge and number in bridge.rb_to_bl_part:
                item, known = canonical_item(bridge.rb_to_bl_part[number]), True
            else:
                item, known = resolver.item(number)
            color = resolver.color(color_name)
            if color is None and bridge:
                rb_color = bridge.rb_color_by_name.get(color_name.strip().lower())
                color = bridge.rb_to_bl_color.get(rb_color)
            if not known:
                unknown_parts[item] += qty
            if color is None:
                unknown_colors[color_name] += qty
                color = f"name:{color_name}"
            lot = (item, color)
            build.lots[lot] += qty
            fallback_name = bridge.rb_part_names.get(number) if bridge else None
            name = resolver.part_names.get(item) or fallback_name or f"BrickLink {item}"
            catalog.parts.setdefault(lot, PartInfo(name, color_name))
        catalog.builds.append(build)

    total = sum(b.pieces for b in catalog.builds if b.source == BRICKMECHA)
    catalog.notes.append(f"BrickMecha: {len(pages)} builds, {total} pcs")
    if unknown_parts:
        catalog.notes.append(
            f"BrickMecha: {len(unknown_parts)} part numbers ({sum(unknown_parts.values())} pcs) never appear in "
            f"Student Scissors data, so they're kept as-is (named from Rebrickable when available): "
            + ", ".join(f"{p} ({q})" for p, q in unknown_parts.most_common(15))
            + (" ..." if len(unknown_parts) > 15 else "")
        )
    if unknown_colors:
        catalog.notes.append(
            "BrickMecha: color names with no BrickLink match: "
            + ", ".join(f"{c} ({q})" for c, q in unknown_colors.most_common())
        )
