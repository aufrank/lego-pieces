# lego-pieces

Which LEGO pieces do [Student Scissors](https://www.patreon.com/StSc) (Patreon) and
[BrickMecha](https://brickmecha.net) builds use most, and what to buy for each round of builds.

`data/` holds downloaded instructions and crawled pages. It's gitignored: the Patreon
files are paid content.

## Refresh the data

```bash
scripts/download.sh          # Patreon collection via your Chrome login (gallery-dl)
uv run crawl-brickmecha      # BrickMecha build pages (5 s between requests, per robots.txt)
uv run fetch-rebrickable     # Rebrickable catalog: which colors each part comes in, how common each is
```

The first two skip what they already have; `fetch-rebrickable` downloads a fresh copy (~16 MB).

## Rankings and core kit

```bash
uv run lego-pieces
```

Writes to `data/out/`:

- `parts_by_shape.csv`: every piece ignoring color, ranked
- `parts_by_color.csv`: every part + color, ranked
- `kit.csv` / `kit_bricklink.xml`: core kit, sized for rounds of 4 builds
- `kit_tradeoff.csv`: kit size vs. how much of a round it covers
- `builds.csv`, `build_topups.csv`: per build, what the kit doesn't cover

Pieces are ranked by the share of builds that use them, averaged across the two sources,
so the many small BrickMecha builds don't outvote Student Scissors.

## BrickLink wanted lists

Every `*_bricklink.xml` file here is BrickLink's wanted-list XML. To use one, open
https://www.bricklink.com/v2/wanted/upload.page, choose **Upload BrickLink XML format**, paste the
file's contents, verify, and add to a wanted list; then **Easy Buy** prices it. (The upload dialog
inside an existing list only takes files like .bsx or .io, not XML.)

## One build

```bash
uv run build-order optimus                           # largest build whose title matches
uv run build-order "G1 Optimus Prime V2" --condition N
```

Writes that build's parts list by color and a wanted list to `data/out/orders/`, in the designed colors.

## Planning rounds

Build a few models, take them apart, build the next few. The pieces you own only grow.

```bash
uv run plan-round --suggest --source "Student Scissors"   # builds that best fit what you own
uv run plan-round "Carrion" "Pulse" "M-caw-b" "moc-401"   # shopping list for a round
uv run plan-round "Carrion" "Pulse" "M-caw-b" "moc-401" --record   # after buying: update inventory
```

The inventory is `data/inventory.csv`. Until it exists, the planner assumes you own the core kit.
Shopping lists go to `data/out/round_shopping.csv` and `round_shopping_bricklink.xml`.

## Recoloring

```bash
uv run plan-round --suggest --recolor
uv run plan-round "Carrion" "Pulse" --recolor --palette "Dark Green,Tan,Black,Light Bluish Gray"
uv run plan-round "Carrion" "Pulse" --recolor --keep "Black,Light Bluish Gray,Dark Bluish Gray"
```

`--recolor` moves each build's color groups to colors you own or that are common, keeping the
color blocking: pieces that shared a color still do, different colors stay different, solids
stay solid, transparents stay transparent, and black/gray groups stay black/gray while colored
groups stay colored. A part only goes into a color LEGO makes it in.
`round_recolor_guide.csv` says which color to use for each piece while following the instructions.

- `--palette`: only build in these colors, so a round can be bought in a few batch colors.
- `--keep`: leave these colors as designed.

Which pieces' colors matter (`src/lego_pieces/roles.py`):

- **Joint pieces** (bars, T-pieces, claws, balls, pins) can be any color, unless they're in the
  build's accent color, which means the designer meant them to be seen.
- **Plates/tiles with a clip, handle or bar** can be any color when black or gray; otherwise
  they're treated as visible.
- **Plates, bricks, tiles, slopes, brackets** keep their color blocking.
- **Special finishes, rubber, tires/wheels and printed parts** stay as designed.

Classification is by BrickLink name, with overrides where the designers' coloring disagreed
with the name (e.g. the 1 x 4 swivel hinge plate is visible; tow-ball plates are internal).

"Cheaper" is estimated from how common a part is in a color across all LEGO sets (Rebrickable),
not actual prices: 1 for a part's most common color, +1 for each 10x rarer.

## Stock kit

```bash
uv run make-kit --visible-coverage 0.75 --moving-coverage 0.9   # the ~1,850-piece kit
uv run make-kit --ss 4 --bm 8-10 --palette "Black,White,Red,Blue,..."
```

A bulk order sized for rounds of 4 Student Scissors + 8-10 BrickMecha builds, recolored into a
batch palette (by default the 10 solid + 4 transparent colors that are cheapest for these parts).
It simulates many rounds and stocks visible pieces for `--visible-coverage` of them and moving
pieces (bars, clips, handles, claws, balls, hinges, pin/axle holes) deeper, for
`--moving-coverage`, in each part's cheapest color. `--balance` spreads colored groups across
the palette so builds don't all land in the cheapest few colors. Special finishes, tires and
printed parts are left out; buy them with each round. Writes `stock_kit.csv` and
`stock_kit_bricklink.xml`.

Because every round's purchases stay in your collection, a bigger kit mostly moves buying up
front: over four simulated rounds, the 75%/90% kit (~1,850 pieces) ends up about 14% more
pieces bought than buying round by round, versus about 75% more for a 90%/99% kit (~4,000).
