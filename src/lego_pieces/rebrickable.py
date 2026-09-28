"""Rebrickable's public catalog downloads: which colors each part comes in, and how common each is.

    uv run fetch-rebrickable     # refresh data/rebrickable/*.csv.gz

Files (https://rebrickable.com/downloads/):
  colors.csv          color ids, names, transparency
  parts.csv           part numbers and names
  elements.csv        part + color combinations LEGO has produced
  inventory_parts.csv parts in every official set inventory (how common a part + color is)
"""

from __future__ import annotations

import argparse
import csv
import gzip
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

BASE = "https://cdn.rebrickable.com/media/downloads"
FILES = ["colors", "parts", "elements", "inventory_parts"]
DATA_DIR = Path("data/rebrickable")
USER_AGENT = "lego-pieces/0.1 (personal parts-list tally)"


def fetch(out_dir: Path = DATA_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        request = urllib.request.Request(f"{BASE}/{name}.csv.gz", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()
        (out_dir / f"{name}.csv.gz").write_bytes(data)
        print(f"  {name}.csv.gz  {len(data) / 1048576:.1f} MB")


def _rows(path: Path):
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f)


@dataclass
class RebrickableCatalog:
    colors: dict[int, tuple[str, bool]] = field(default_factory=dict)  # id -> (name, is_trans)
    part_names: dict[str, str] = field(default_factory=dict)
    # (part, color id) -> pieces across all set inventories; elements with no set use count 0
    supply: Counter[tuple[str, int]] = field(default_factory=Counter)
    colors_of: dict[str, set[int]] = field(default_factory=dict)  # part -> colors it's made in

    @classmethod
    def load(cls, data_dir: Path = DATA_DIR) -> "RebrickableCatalog":
        cat = cls()
        for row in _rows(data_dir / "colors.csv.gz"):
            cat.colors[int(row["id"])] = (row["name"], row["is_trans"].lower() in ("t", "true", "1"))
        for row in _rows(data_dir / "parts.csv.gz"):
            cat.part_names[row["part_num"]] = row["name"]
        for row in _rows(data_dir / "inventory_parts.csv.gz"):
            if row["is_spare"].lower() in ("t", "true", "1"):
                continue
            key = (row["part_num"], int(row["color_id"]))
            cat.supply[key] += int(row["quantity"])
        made = set(cat.supply)
        for row in _rows(data_dir / "elements.csv.gz"):
            made.add((row["part_num"], int(row["color_id"])))
        for part, color in made:
            cat.colors_of.setdefault(part, set()).add(color)
        return cat


def main() -> None:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    print(f"Downloading Rebrickable catalog to {DATA_DIR}/")
    fetch()


def _majority(votes: dict[str, Counter]) -> dict:
    return {key: counter.most_common(1)[0][0] for key, counter in votes.items()}


@dataclass
class Bridge:
    """BrickLink <-> Rebrickable part and color ids.

    Learned from Studio exports, whose LEGO element ids Rebrickable also lists;
    element ids win over matching numbers, since the catalogs sometimes use one
    number for different parts (BrickLink 92946 is Rebrickable 15672).
    """

    rb_part_names: dict[str, str]
    rb_colors: dict[int, tuple[str, bool]]
    bl_to_rb_part: dict[str, str]
    rb_to_bl_part: dict[str, str]
    bl_to_rb_color: dict[str, int]
    rb_to_bl_color: dict[int, str]
    rb_color_by_name: dict[str, int]

    @classmethod
    def build(cls, studio_rows: list[dict[str, str]], data_dir: Path = DATA_DIR) -> "Bridge":
        rb_colors = {int(r["id"]): (r["name"], r["is_trans"].lower() in ("t", "true", "1"))
                     for r in _rows(data_dir / "colors.csv.gz")}
        rb_part_names = {r["part_num"]: r["name"] for r in _rows(data_dir / "parts.csv.gz")}
        elements = {r["element_id"]: (r["part_num"], int(r["color_id"])) for r in _rows(data_dir / "elements.csv.gz")}
        rb_color_by_name = {name.lower(): cid for cid, (name, _) in rb_colors.items()}

        part_votes: dict[str, Counter] = {}
        rb_part_votes: dict[str, Counter] = {}
        color_votes: dict[str, Counter] = {}
        rb_color_votes: dict[int, Counter] = {}
        for row in studio_rows:
            item, color = row["bl_item"], row["bl_color"]
            if row["element_id"] in elements:
                rb_part, rb_color = elements[row["element_id"]]
                part_votes.setdefault(item, Counter())[rb_part] += 1
                rb_part_votes.setdefault(rb_part, Counter())[item] += 1
                color_votes.setdefault(color, Counter())[rb_color] += 1
                rb_color_votes.setdefault(rb_color, Counter())[color] += 1
            else:
                for candidate in (item, row["ldraw_id"]):
                    if candidate in rb_part_names:
                        part_votes.setdefault(item, Counter())[candidate] += 0  # fallback, never outvotes
                        break
                if (cid := rb_color_by_name.get(row["color_name"].lower())) is not None:
                    color_votes.setdefault(color, Counter())[cid] += 0

        bl_to_rb_color = _majority(color_votes)
        rb_to_bl_color = _majority(rb_color_votes)
        for bl, rb in bl_to_rb_color.items():
            rb_to_bl_color.setdefault(rb, bl)
        return cls(rb_part_names, rb_colors, _majority(part_votes), _majority(rb_part_votes),
                   bl_to_rb_color, rb_to_bl_color, rb_color_by_name)

    def rb_part(self, bl_item: str) -> str | None:
        if bl_item in self.bl_to_rb_part:
            return self.bl_to_rb_part[bl_item]
        return bl_item if bl_item in self.rb_part_names else None

    def rb_color(self, bl_color: str) -> int | None:
        return self.bl_to_rb_color.get(bl_color)
