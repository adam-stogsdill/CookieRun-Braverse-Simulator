#!/usr/bin/env python3
"""Download the card images that go with `braverse_cards.csv`.

    python fetch_images.py                    # every card -> card_images/
    python fetch_images.py --sets ST8 ST9     # just those sets
    python fetch_images.py --no-alt-art       # skip @1/@2 reprints
    python fetch_images.py --card-back URL    # the reverse -> card_back.webp

Images come from the same CDN the cookierun.gg card browser uses:
``https://static.dotgg.gg/cookierun/cards/<card id>.webp``. The URL is keyed on
the exact card id, alt arts included, so `BS8-104@1` resolves on its own.

Downloads are skipped when the file already exists, so re-running only fetches
what is missing — interrupt it freely.

The one thing that CDN does not serve is the card *back*: it is keyed on card
id and every id is a front. `--card-back` takes a URL (or a local path) and
saves it as `card_images/card_back.webp`, which the viewer picks up on its own
and uses for every face-down card. Without it the viewer draws its own sleeve,
which is what it has always done.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from braverse.console import utf8_output

IMAGE_URL = "https://static.dotgg.gg/cookierun/cards/{card_id}.webp"
USER_AGENT = "braverse-sim-images/1.0 (personal tabletop project)"
DEFAULT_CSV = Path(__file__).resolve().parent / "braverse_cards.csv"


def card_ids(csv_path: Path, sets: set[str] | None, alt_art: bool) -> list[str]:
    ids = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if not alt_art and row.get("is_alt_art") == "1":
                continue
            if sets and row.get("setId") not in sets:
                continue
            if row.get("id"):
                ids.append(row["id"])
    return sorted(dict.fromkeys(ids))


def download(card_id: str, out_dir: Path, retries: int = 3) -> tuple[str, str]:
    """Returns (card_id, status) where status is ok / skip / missing / error."""
    # `@` is legal in a filename but awkward in shells and TTS tooling.
    target = out_dir / f"{card_id.replace('@', '_alt')}.webp"
    if target.exists() and target.stat().st_size > 0:
        return card_id, "skip"

    url = IMAGE_URL.format(card_id=urllib.parse.quote(card_id, safe=""))
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=30) as response:
                if "image" not in response.headers.get("Content-Type", ""):
                    return card_id, "missing"     # soft 404: an HTML error page
                data = response.read()
            target.write_bytes(data)
            return card_id, "ok"
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return card_id, "missing"
            if attempt == retries - 1:
                return card_id, f"error {exc.code}"
        except Exception as exc:                  # noqa: BLE001 - report and move on
            if attempt == retries - 1:
                return card_id, f"error {type(exc).__name__}"
        time.sleep(1.5 * (attempt + 1))
    return card_id, "error"


def fetch_card_back(source: str, out_dir: Path) -> str:
    """Save the card back as `card_back.webp`, from a URL or a local file."""
    target = out_dir / "card_back.webp"
    out_dir.mkdir(parents=True, exist_ok=True)
    if "://" not in source:
        data = Path(source).expanduser().read_bytes()
    else:
        request = urllib.request.Request(source, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as response:
            if "image" not in response.headers.get("Content-Type", ""):
                return f"not an image: {response.headers.get('Content-Type')}"
            data = response.read()
    if not data:
        return "empty"
    target.write_bytes(data)
    return f"ok -> {target} ({len(data) // 1024} KB)"


def main() -> None:
    utf8_output()   # a redirected stdout on Windows is cp1252
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--out", type=Path, default=Path("card_images"))
    parser.add_argument("--sets", nargs="*", help="limit to these set ids")
    parser.add_argument("--no-alt-art", action="store_true",
                        help="skip @1/@2 alternate printings")
    parser.add_argument("--workers", type=int, default=8,
                        help="parallel downloads; keep this modest")
    parser.add_argument("--card-back", metavar="URL_OR_PATH",
                        help="the reverse of a card, saved as card_back.webp; "
                             "the viewer uses it for every face-down card")
    args = parser.parse_args()

    if args.card_back:
        print("card back:", fetch_card_back(args.card_back, args.out))
        return

    ids = card_ids(args.csv, set(args.sets) if args.sets else None,
                   alt_art=not args.no_alt_art)
    args.out.mkdir(parents=True, exist_ok=True)
    print(f"{len(ids)} cards -> {args.out}/")

    counts: dict[str, int] = {}
    missing: list[str] = []
    started = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for done, (card_id, status) in enumerate(
                pool.map(lambda c: download(c, args.out), ids), 1):
            key = status.split()[0]
            counts[key] = counts.get(key, 0) + 1
            if key in ("missing", "error"):
                missing.append(f"{card_id} ({status})")
            if done % 100 == 0 or done == len(ids):
                rate = done / max(time.time() - started, 1e-9)
                print(f"  {done}/{len(ids)}  {rate:.0f}/s  "
                      + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())),
                      flush=True)

    print(f"\ndone in {time.time() - started:.0f}s: "
          + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if missing:
        print(f"\n{len(missing)} without an image:")
        for entry in missing[:25]:
            print(f"    {entry}")
        if len(missing) > 25:
            print(f"    ... and {len(missing) - 25} more")


if __name__ == "__main__":
    sys.exit(main())
