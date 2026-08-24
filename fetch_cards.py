#!/usr/bin/env python3
"""
Fetch the complete CookieRun: Braverse card database from the DotGG public API
and write it to CSV.

The cookierun.gg card browser is client-side rendered against this endpoint, so
there is nothing to page through and no HTML to parse -- one request returns
every card.

Usage:
    python fetch_braverse_cards.py                          # all cards -> braverse_cards.csv
    python fetch_braverse_cards.py --cookies-only           # COOKIE + FLIP only
    python fetch_braverse_cards.py --drop-alt-art           # collapse @1/@2 reprints
    python fetch_braverse_cards.py --raw-json cards.json    # also dump the raw payload
    python fetch_braverse_cards.py --from-file cards.json   # re-parse a saved dump (no network)
"""

import argparse
import csv
import gzip
import io
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request

from braverse.console import utf8_output

API_URL = "https://api.dotgg.gg/cgfw/getcards?game=cookierun&mode=indexed"
USER_AGENT = "braverse-sim-dataset/1.0 (personal deck-simulator project)"

# Columns most useful for a rules engine, in a sensible order. Anything in the
# payload that isn't listed here is still written, appended after these.
PREFERRED_ORDER = [
    "id", "setId", "number", "name", "productCategoryTitle", "type",
    "color", "energyType", "cardLevel", "hp", "keyword", "rarity",
    "skillName", "description", "attackText", "flipText",
    "isBan", "isLimit", "isExtra",
]

# Price/market noise -- irrelevant to a simulator, dropped unless --keep-prices.
PRICE_FIELDS = {
    "price", "foilPrice", "deltaPrice", "deltaFoilPrice", "delta7dPrice",
    "delta7dPriceFoil", "price_date", "marketIds", "hasNormal", "hasFoil",
    "img_status", "slug", "illusrtrator", "product_title",
}

# Legacy shorthand used in the older ST1-ST5 rows, mapped to the modern markers
# used everywhere else. Without this you have two vocabularies for one concept.
LEGACY_TOKENS = {
    "{ap}": "\u3010Activate\u3011",
    "{mob}": "\u3010Activate\u3011",
    "{mt}": "\u3010Your Turn\u3011",
    "{t1}": "\u3010Once Per Turn\u3011",
    "{sk}": "",
    "{sk]": "",
    "{da}": "deals",
}


def fetch(url, retries=4, backoff=3.0):
    """GET with a real UA, gzip support, and exponential backoff."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        },
    )
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                return json.loads(raw.decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            last = exc
            if attempt < retries - 1:
                wait = backoff * (2 ** attempt)
                print(f"  request failed ({exc}); retrying in {wait:.0f}s",
                      file=sys.stderr)
                time.sleep(wait)
    raise SystemExit(f"giving up after {retries} attempts: {last}")


def normalize_text(value):
    """Make effect text consistent across printing eras.

    The database mixes two markup conventions and two eras of shorthand:
      - cost/cost-payment brackets appear as both <...> and the CJK <<...>>
      - older rows use {ap}/{mob}/{t1}; newer rows use [Activate]/[Once Per Turn]
      - a few rows contain full-width digits in numeric fields
    """
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\u300a", "<").replace("\u300b", ">")   # << >> -> < >
    for token, replacement in LEGACY_TOKENS.items():
        text = text.replace(token, replacement)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize("NFKC", text)                   # full-width -> ASCII
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def to_records(payload):
    """The 'indexed' response is {names: [...], data: [[...], ...]}."""
    if isinstance(payload, list):          # plain mode, just in case
        return payload
    names = payload["names"]
    return [dict(zip(names, row)) for row in payload["data"]]


def main():
    utf8_output()   # a redirected stdout on Windows is cp1252
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="braverse_cards.csv")
    ap.add_argument("--cookies-only", action="store_true",
                    help="keep only COOKIE and FLIP cards")
    ap.add_argument("--drop-alt-art", action="store_true",
                    help="collapse @1/@2 alt-art reprints onto the base card id")
    ap.add_argument("--keep-prices", action="store_true",
                    help="retain market/price columns (off by default)")
    ap.add_argument("--raw-json", help="also write the untouched API payload here")
    ap.add_argument("--from-file", help="parse a saved payload instead of fetching")
    args = ap.parse_args()

    if args.from_file:
        with open(args.from_file, encoding="utf-8") as fh:
            payload = json.load(fh)
    else:
        print(f"fetching {API_URL}", file=sys.stderr)
        payload = fetch(API_URL)
        if args.raw_json:
            with open(args.raw_json, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=1)
            print(f"raw payload -> {args.raw_json}", file=sys.stderr)

    records = to_records(payload)
    print(f"{len(records)} rows returned", file=sys.stderr)

    text_fields = {"description", "attackText", "flipText", "skillName", "name"}
    rows = []
    seen_base_ids = set()

    for rec in records:
        if args.cookies_only and rec.get("type") not in ("COOKIE", "FLIP"):
            continue

        card_id = rec.get("id") or ""
        base_id = card_id.split("@")[0]
        if args.drop_alt_art:
            if base_id in seen_base_ids:
                continue
            seen_base_ids.add(base_id)

        row = {}
        for key, value in rec.items():
            if not args.keep_prices and key in PRICE_FIELDS:
                continue
            if key in text_fields:
                row[key] = normalize_text(value)
            elif value is None:
                row[key] = ""
            else:
                # NFKC here too: a few rows carry full-width digits in hp/cardLevel,
                # which would otherwise silently break int() downstream.
                row[key] = unicodedata.normalize("NFKC", str(value)).strip()

        row["base_id"] = base_id
        row["is_alt_art"] = "1" if "@" in card_id else "0"
        # One field holding every scrap of rules text, for grepping and for
        # feeding to an effect-IR translation pass later.
        row["all_rules_text"] = "\n".join(
            t for t in (row.get("description"), row.get("attackText"),
                        row.get("flipText")) if t
        )
        rows.append(row)

    if not rows:
        raise SystemExit("no rows matched the filters")

    all_keys = set().union(*(r.keys() for r in rows))
    header = [k for k in PREFERRED_ORDER if k in all_keys]
    header += ["base_id", "is_alt_art", "all_rules_text"]
    header += sorted(k for k in all_keys if k not in header)

    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    missing_text = sum(1 for r in rows if not r["all_rules_text"])
    print(f"wrote {len(rows)} cards -> {args.out}", file=sys.stderr)
    print(f"  {missing_text} rows have no rules text (vanilla or incomplete entries)",
          file=sys.stderr)


if __name__ == "__main__":
    main()