"""Which pieces' colors matter when recoloring a build.

Joint pieces (bars, T-pieces, claws, balls, pins) are mostly internal, so their
color doesn't matter. Plates, bricks, tiles and slopes carry the color blocking.
Plates and tiles with a clip, handle or bar ("hybrids") are both; they're treated
as internal when black or gray, and as visible otherwise.

Classification is by BrickLink part name, plus overrides from checking how the
designers actually color each part (see README).
"""

from __future__ import annotations

import re
from collections import Counter

from lego_pieces.catalog import Build, Catalog, Lot

JOINT, HYBRID, VISIBLE = "joint", "hybrid", "visible"

NEUTRAL_COLORS = {"Black", "Light Bluish Gray", "Dark Bluish Gray"}
_GRAYSCALE = NEUTRAL_COLORS | {"Light Gray", "Dark Gray"}


def is_neutral_color(color_name: str) -> bool:
    """Black and grays: a build's skeleton. Recoloring keeps these neutral and colors colorful."""
    return color_name in _GRAYSCALE


_JOINT_FAMILY = re.compile(
    r"^(bar\b|pneumatic|technic, pin|technic, axle|minifigure, utensil|minifigure, weapon|hinge|clip|ball)"
)
_VISIBLE_FAMILY = re.compile(r"^(plate|brick|tile|slope|wedge|panel|cone|dish|arch|cylinder|windscreen)\b")
_JOINT_WORDS = re.compile(r"\b(bar|handles?|clips?|claw|ball|socket|pin|axle|towball)\b")

# Where the name says one thing but the designers' coloring says another.
ROLE_OVERRIDES = {
    # Named like joints, but take the build's accent color 27-38% of the time.
    "2429c01": VISIBLE,  # Hinge Plate 1 x 4 Swivel
    "11090": VISIBLE,    # Bar Holder with Clip
    "23443": VISIBLE,    # Bar Holder with Handle
    "99563": VISIBLE,    # Minifigure, Utensil Ingot / Bar
    # Internal: 85-100% black or gray.
    "30377": JOINT,      # Arm Mechanical, Battle Droid
    "93609": JOINT,      # Arm Skeleton, Bent with Clips
    "73230": JOINT,      # Technic, Brick 1 x 1 with Axle Hole
    "14704": JOINT,      # Plate 1 x 2 with Small Tow Ball Socket on Side
    "14417": JOINT,      # Plate 1 x 2 with Tow Ball on Side
    "14418": JOINT,      # Plate 1 x 2 with Small Tow Ball Socket on End
    "78257": JOINT,      # Plate 1 x 1 with Bar Handles on Ends
    "20482": JOINT,      # Tile, Round 1 x 1 with Bar and Pin Holder
}

# Colors that stay as designed: special finishes and rubber don't swap for plain colors.
_FIXED_COLOR = re.compile(r"pearl|metallic|chrome|glitter|satin|opal|speckle|rubber|glow|milky|flat silver", re.I)
# Parts that keep their color: tires/wheels and printed parts.
_FIXED_PART = re.compile(r"\b(tire|tyre|wheel)\b|pattern", re.I)


def part_role(item: str, name: str) -> str:
    if item in ROLE_OVERRIDES:
        return ROLE_OVERRIDES[item]
    n = re.sub(r"\s+", " ", name.lower())
    if _JOINT_FAMILY.search(n):
        return JOINT
    if _VISIBLE_FAMILY.search(n) and _JOINT_WORDS.search(n):
        return HYBRID
    return VISIBLE


def is_fixed_color(color_name: str) -> bool:
    return bool(_FIXED_COLOR.search(color_name))


def is_fixed_part(name: str) -> bool:
    return bool(_FIXED_PART.search(name))


def accent_colors(build: Build, catalog: Catalog, n: int = 2) -> set[str]:
    """The build's `n` most-used colors other than black and grays."""
    by_color: Counter[str] = Counter()
    for lot, qty in build.lots.items():
        by_color[catalog.parts[lot].color_name] += qty
    return set([c for c, _ in by_color.most_common() if c not in NEUTRAL_COLORS][:n])


def lot_kind(lot: Lot, build: Build, catalog: Catalog, accents: set[str] | None = None) -> str:
    """'free' (any color works), 'fixed' (keep as designed) or 'visible' (recolor with its color group)."""
    info = catalog.parts[lot]
    if is_fixed_color(info.color_name) or is_fixed_part(info.name) or lot[0].startswith("ldraw:"):
        return "fixed"
    accents = accent_colors(build, catalog) if accents is None else accents
    role = part_role(lot[0], info.name)
    if role == JOINT and info.color_name not in accents:
        return "free"  # an accent-colored joint piece is meant to be seen
    if role == HYBRID and info.color_name in NEUTRAL_COLORS:
        return "free"
    return "visible"
