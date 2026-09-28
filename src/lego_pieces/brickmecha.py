"""Crawl and parse BrickMecha (brickmecha.net) build pages.

The site's robots.txt allows crawling with a 5 second crawl-delay; every request
here waits that long. Build pages are cached under data/brickmecha/html, so
re-running only fetches builds it hasn't seen.

    uv run crawl-brickmecha
"""

from __future__ import annotations

import argparse
import html
import re
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

BASE = "https://brickmecha.net/index.php"
USER_AGENT = "lego-pieces/0.1 (personal parts-list tally; honors robots.txt crawl-delay)"
CRAWL_DELAY = 5.0

_last_request = 0.0


def fetch(url: str) -> str:
    global _last_request
    wait = CRAWL_DELAY - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read().decode("utf-8", "replace")
    finally:
        _last_request = time.monotonic()


def list_moc_numbers() -> list[int]:
    """Every build number, from the site's paged list of all builds."""
    first = fetch(f"{BASE}?lng=en&page=1")
    total = int(re.search(r"([\d,]+) robots", first).group(1).replace(",", ""))
    last_page = max(int(p) for p in re.findall(r"[?&;]page=(\d+)", first))
    mocs = set(map(int, re.findall(r'data-moc-no="(\d+)"', first)))
    for page in range(2, last_page + 1):
        mocs |= set(map(int, re.findall(r'data-moc-no="(\d+)"', fetch(f"{BASE}?lng=en&page={page}"))))
        print(f"  list page {page}/{last_page}: {len(mocs)} builds so far", flush=True)
    if len(mocs) != total:
        print(f"  warning: site says {total} builds but the list pages had {len(mocs)}")
    return sorted(mocs)


@dataclass
class MocPage:
    moc_no: int
    title: str
    rows: list[tuple[str, str, int]]  # (part no, color name, qty)


def parse_moc(moc_no: int, page: str) -> MocPage:
    title = html.unescape(re.search(r'<h1 class="robot-detail-title">\s*(.*?)\s*</h1>', page, re.S).group(1))
    rows = [
        (part.strip(), html.unescape(color).strip(), int(qty))
        for part, color, qty in re.findall(
            r'<span class="rp-part-no">([^<]+)</span>.*?'
            r'<td class="rp-color-name">([^<]*)</td>\s*'
            r'<td class="rp-qty">(\d+)</td>',
            page, re.S,
        )
    ]
    summary = re.search(r"(\d+) unique parts / (\d+) total pcs", page)
    if summary:
        lots, pieces = map(int, summary.groups())
        if (len(rows), sum(q for *_, q in rows)) != (lots, pieces):
            raise ValueError(f"MOC {moc_no}: parsed {len(rows)} lots / {sum(q for *_, q in rows)} pcs, "
                             f"page says {lots} / {pieces}")
    return MocPage(moc_no, re.sub(r"\s+", " ", title), rows)


def load_pages(html_dir: Path) -> list[MocPage]:
    return [parse_moc(int(p.stem.split("-")[1]), p.read_text(encoding="utf-8"))
            for p in sorted(html_dir.glob("moc-*.html"))]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=Path("data/brickmecha/html"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    print("Listing builds...", flush=True)
    mocs = list_moc_numbers()
    todo = [m for m in mocs if not (args.out / f"moc-{m:04d}.html").exists()]
    print(f"{len(mocs)} builds listed, {len(mocs) - len(todo)} already cached, fetching {len(todo)} "
          f"(~{len(todo) * CRAWL_DELAY / 60:.0f} min)", flush=True)
    for i, moc in enumerate(todo, 1):
        page = fetch(f"{BASE}?moc-no={moc}&lng=en")
        parsed = parse_moc(moc, page)  # fail fast on a page we can't read
        (args.out / f"moc-{moc:04d}.html").write_text(page, encoding="utf-8")
        print(f"  [{i}/{len(todo)}] MOC {moc}: {sum(q for *_, q in parsed.rows)} pcs  {parsed.title[:60]}", flush=True)
    print("done", flush=True)
