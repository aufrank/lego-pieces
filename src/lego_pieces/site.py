"""Export the data the round-planner website (docs/) needs, encrypted.

    uv run export-site

The build parts lists include paid Patreon content, so the site's data is
AES-GCM encrypted with a key derived (PBKDF2-SHA256) from a passphrase; the page
asks for it once per device. The passphrase lives in data/site-passphrase.txt
(gitignored). A random one is created on first run; put your own there to change
it, then re-run.
"""

from __future__ import annotations

import argparse
import base64
import csv
import gzip
import json
import os
import secrets
from datetime import date
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from lego_pieces import rebrickable
from lego_pieces.catalog import STUDENT_SCISSORS, load_catalog
from lego_pieces.rebrickable import RebrickableCatalog
from lego_pieces.recolor import INFEASIBLE, Pricing
from lego_pieces.roles import accent_colors, is_neutral_color, lot_kind
from lego_pieces.stock_kit import is_moving

PASSPHRASE_FILE = Path("data/site-passphrase.txt")
OUT = Path("docs/data.enc.json")
ITERATIONS = 310_000
KIND_CODES = {"visible": "v", "free": "f", "fixed": "x"}


def rgb_by_name(data_dir: Path = rebrickable.DATA_DIR) -> dict[str, str]:
    with gzip.open(data_dir / "colors.csv.gz", "rt", encoding="utf-8", newline="") as f:
        return {row["name"].lower(): row["rgb"] for row in csv.DictReader(f)}


def build_bundle() -> dict:
    catalog = load_catalog(Path("data/raw"), Path("data/brickmecha/html"))
    if catalog.bridge is None:
        raise SystemExit("needs the Rebrickable catalog: run `uv run fetch-rebrickable`")
    pricing = Pricing(catalog, RebrickableCatalog.load())
    rgbs = rgb_by_name()
    rb_names = {cid: name for cid, (name, _) in catalog.bridge.rb_colors.items()}

    colors = {}
    for (_, color), info in catalog.parts.items():
        colors.setdefault(color, info.color_name)
    for color, name in pricing.choices.items():
        colors.setdefault(color, name)

    def rgb(color: str, name: str) -> str:
        rb_id = catalog.bridge.rb_color(color)
        return rgbs.get((rb_names.get(rb_id) or name).lower()) or rgbs.get(name.lower()) or "999999"

    color_table = {
        color: {"n": pricing.names.get(color, name), "rgb": rgb(color, name), "t": name.startswith("Trans"),
                "g": is_neutral_color(name), "sel": color in pricing.choices}
        for color, name in colors.items()
    }

    parts = {}
    items = {item for item, _ in catalog.parts}
    for item in sorted(items):
        name = catalog.describe((item, ""))[0]
        seen = {color for (i, color) in catalog.parts if i == item}
        avail = sorted({c for c in pricing.choices if pricing.price((item, c)) < INFEASIBLE} | seen)
        priced = [c for c in avail if c in pricing.choices]
        cheapest = min(priced, key=lambda c: (pricing.price((item, c)), c != "11")) if priced else None
        parts[item] = {"n": name, "m": is_moving(item, name), "a": avail, "k": cheapest}

    builds = []
    for build in catalog.builds:
        accents = accent_colors(build, catalog)
        builds.append({
            "id": build.build_id,
            "s": "SS" if build.source == STUDENT_SCISSORS else "BM",
            "t": build.title,
            "u": build.url,
            "d": build.published,
            "p": build.pieces,
            "l": [[item, color, qty, KIND_CODES[lot_kind((item, color), build, catalog, accents)]]
                  for (item, color), qty in sorted(build.lots.items())],
        })
    return {"generated": date.today().isoformat(), "colors": color_table, "parts": parts, "builds": builds}


def encrypt(payload: bytes, passphrase: str) -> dict:
    salt, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERATIONS).derive(passphrase.encode())
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iter": ITERATIONS, "salt": b64(salt), "iv": b64(iv),
            "data": b64(AESGCM(key).encrypt(iv, gzip.compress(payload), None))}


def read_passphrase(path: Path) -> str:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("-".join(secrets.token_urlsafe(4) for _ in range(4)) + "\n")
        path.chmod(0o600)
        print(f"Created a passphrase in {path} (gitignored). Open that file to see it.")
    passphrase = path.read_text().strip()
    if len(passphrase) < 12:
        raise SystemExit(f"{path}: use a passphrase of at least 12 characters")
    return passphrase


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--passphrase-file", type=Path, default=PASSPHRASE_FILE)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    bundle = build_bundle()
    payload = json.dumps(bundle, separators=(",", ":")).encode()
    encrypted = encrypt(payload, read_passphrase(args.passphrase_file))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(encrypted))
    sources = {s: sum(b["s"] == s for b in bundle["builds"]) for s in ("SS", "BM")}
    print(f"{len(bundle['builds'])} builds ({sources['SS']} Student Scissors, {sources['BM']} BrickMecha), "
          f"{len(bundle['parts'])} parts, {len(bundle['colors'])} colors; "
          f"{len(payload) / 1024:.0f} KB -> {args.out} ({args.out.stat().st_size / 1024:.0f} KB encrypted)")
