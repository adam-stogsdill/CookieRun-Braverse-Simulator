#!/usr/bin/env python3
"""Compose downloaded card images into Tabletop Simulator deck sheets.

    python fetch_images.py                 # first, get the images
    python build_tts_sheets.py             # then, build the sheets

TTS does not take one file per card. A custom deck is a single grid image plus
the number of columns and rows, and each card is an index into that grid. This
builds those grids (10x7 = 70 cards each, TTS's maximum) and writes a manifest
mapping every card id to its sheet and index.

    python build_tts_sheets.py --sets ST8 ST9 --out tts/starters
    python build_tts_sheets.py --deck decks/evolved_deck_heur.txt --out tts/evolved
    python build_tts_sheets.py --width 400          # smaller files

The manifest also emits a ready-to-paste `CustomDeck` block per sheet; fill in
`FaceURL` once the sheet is hosted somewhere TTS can reach.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from PIL import Image

from braverse.console import utf8_output

# TTS caps a deck sheet at 10 columns by 7 rows.
MAX_COLUMNS = 10
MAX_ROWS = 7
CARDS_PER_SHEET = MAX_COLUMNS * MAX_ROWS

DEFAULT_CSV = Path(__file__).resolve().parent / "braverse_cards.csv"


def image_path(image_dir: Path, card_id: str) -> Path:
    return image_dir / f"{card_id.replace('@', '_alt')}.webp"


def load_rows(csv_path: Path) -> list[dict]:
    with open(csv_path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def deck_card_ids(deck_file: Path) -> list[str]:
    """Read a decklist written by evolve_deck.py (JSON blob at the end)."""
    text = deck_file.read_text(encoding="utf-8")
    blob = json.loads(text[text.index("{", text.rindex("\n\n")):])
    return list(blob["deck"])


def build_sheet(card_ids: list[str], image_dir: Path, card_width: int
                ) -> tuple[Image.Image, int, int]:
    """Grid the given cards into one sheet image."""
    columns = min(MAX_COLUMNS, len(card_ids))
    rows = (len(card_ids) + columns - 1) // columns

    first = Image.open(image_path(image_dir, card_ids[0]))
    ratio = first.height / first.width
    card_height = int(round(card_width * ratio))

    sheet = Image.new("RGB", (columns * card_width, rows * card_height),
                      (0, 0, 0))
    for index, card_id in enumerate(card_ids):
        with Image.open(image_path(image_dir, card_id)) as art:
            art = art.convert("RGB").resize((card_width, card_height),
                                            Image.LANCZOS)
            sheet.paste(art, ((index % columns) * card_width,
                              (index // columns) * card_height))
    return sheet, columns, rows


def main() -> None:
    utf8_output()   # a redirected stdout on Windows is cp1252
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--images", type=Path, default=Path("card_images"))
    parser.add_argument("--out", type=Path, default=Path("tts_sheets"))
    parser.add_argument("--sets", nargs="*", help="limit to these set ids")
    parser.add_argument("--deck", type=Path,
                        help="build sheets for one decklist file instead")
    parser.add_argument("--width", type=int, default=500,
                        help="per-card width in pixels")
    parser.add_argument("--no-alt-art", action="store_true")
    parser.add_argument("--quality", type=int, default=88)
    args = parser.parse_args()

    rows = load_rows(args.csv)
    names = {r["id"]: r["name"] for r in rows}

    if args.deck:
        # A decklist repeats cards; a TTS sheet only needs each face once.
        wanted = list(dict.fromkeys(deck_card_ids(args.deck)))
        label = args.deck.stem
    else:
        wanted = [r["id"] for r in rows
                  if (not args.sets or r.get("setId") in args.sets)
                  and not (args.no_alt_art and r.get("is_alt_art") == "1")]
        label = "-".join(args.sets) if args.sets else "all"

    have = [c for c in wanted if image_path(args.images, c).exists()]
    absent = [c for c in wanted if c not in set(have)]
    if not have:
        raise SystemExit(f"no images found in {args.images}/ — run fetch_images.py first")

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"{len(have)} cards -> {args.out}/  ({len(absent)} without an image)")

    manifest = []
    for sheet_index in range(0, len(have), CARDS_PER_SHEET):
        chunk = have[sheet_index:sheet_index + CARDS_PER_SHEET]
        sheet, columns, grid_rows = build_sheet(chunk, args.images, args.width)
        name = f"{label}-{sheet_index // CARDS_PER_SHEET + 1:02d}.jpg"
        sheet.save(args.out / name, quality=args.quality, optimize=True)
        size_mb = (args.out / name).stat().st_size / 1e6
        print(f"  {name}  {columns}x{grid_rows}  {len(chunk)} cards  "
              f"{sheet.width}x{sheet.height}  {size_mb:.1f}MB")

        manifest.append({
            "sheet": name,
            "columns": columns,
            "rows": grid_rows,
            "count": len(chunk),
            # TTS card ids are sheet_number * 100 + index within the sheet.
            "cards": [{"id": c, "name": names.get(c, ""), "index": i,
                       "tts_card_id": (sheet_index // CARDS_PER_SHEET + 1) * 100 + i}
                      for i, c in enumerate(chunk)],
            "custom_deck_entry": {
                "FaceURL": f"REPLACE_WITH_HOSTED_URL/{name}",
                "BackURL": "REPLACE_WITH_HOSTED_CARD_BACK",
                "NumWidth": columns,
                "NumHeight": grid_rows,
                "BackIsHidden": True,
                "UniqueBack": False,
            },
        })

    (args.out / "manifest.json").write_text(json.dumps(
        {"label": label, "card_width": args.width,
         "missing_images": absent, "sheets": manifest}, indent=1),
        encoding="utf-8")
    print(f"\nmanifest -> {args.out}/manifest.json")
    if absent:
        print(f"{len(absent)} cards had no image, e.g. {absent[:5]}")


if __name__ == "__main__":
    main()
