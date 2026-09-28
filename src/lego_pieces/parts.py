"""Put part numbers and colors from different sources onto one BrickLink key.

Sources disagree on numbering: older Studio exports use BrickLink numbers that
BrickLink has since merged (3069b -> 3069), and BrickMecha uses a mix of
BrickLink and LDraw/Rebrickable-style numbers (6141 for BrickLink 4073).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Older BrickLink numbers that BrickLink has merged into the plain mold number.
# Studio exports from different years use both, so the same piece shows up twice.
BL_MERGED = {
    "3062b": "3062",    # Brick, Round 1 x 1
    "3068b": "3068",    # Tile 2 x 2
    "3069b": "3069",    # Tile 1 x 2
    "3070b": "3070",    # Tile 1 x 1
    "32064c": "32064",  # Technic, Brick 1 x 2 with Axle Hole
}


# Color names other sources use for a BrickLink color, lowercased.
COLOR_ALIASES = {
    "trans-black": "trans-brown",  # LEGO's Trans-Black is BrickLink's Trans-Brown (13)
}


def canonical_item(item: str) -> str:
    return BL_MERGED.get(item, item)


@dataclass
class PartResolver:
    """Maps a foreign part number / color name to BrickLink ids, learning from Studio data."""

    bl_items: set[str] = field(default_factory=set)
    ldraw_to_bl: dict[str, str] = field(default_factory=dict)
    color_ids: dict[str, str] = field(default_factory=dict)  # lowercased BL color name -> BL color id
    part_names: dict[str, str] = field(default_factory=dict)  # BL item -> name

    def learn(self, bl_item: str, ldraw_id: str, part_name: str, bl_color: str, color_name: str) -> None:
        item = canonical_item(bl_item)
        self.bl_items.add(item)
        self.ldraw_to_bl.setdefault(ldraw_id.lower().removesuffix(".dat"), item)
        self.part_names.setdefault(item, part_name)
        self.color_ids.setdefault(color_name.strip().lower(), bl_color)

    def item(self, number: str) -> tuple[str, bool]:
        """(BrickLink item, whether it matched a part seen in Studio data)."""
        number = number.strip()
        item = canonical_item(number)
        if item in self.bl_items:
            return item, True
        if (mapped := self.ldraw_to_bl.get(number.lower())) is not None:
            return mapped, True
        return item, False

    def color(self, name: str) -> str | None:
        name = name.strip().lower()
        return self.color_ids.get(COLOR_ALIASES.get(name, name))
