#!/usr/bin/env python3
"""
Fetch the complete CookieRun: Braverse card database from the official card
list and write it to CSV.

The source is the feed behind cookierunbraverse.com's card browser: one static
JSON document holding every card, so there is nothing to page through and no
HTML to parse. It replaced a third-party mirror (DotGG) that ran a set behind
-- the mirror never picked up BS12, and its image CDN still 404s on it -- so
the official feed is the only source that is current on the day a set lands.

Usage:
    python fetch_cards.py                          # all cards -> braverse_cards.csv
    python fetch_cards.py --cookies-only           # COOKIE + FLIP only
    python fetch_cards.py --drop-alt-art           # collapse @1/@2 reprints
    python fetch_cards.py --raw-json cards.json    # also dump the raw payload
    python fetch_cards.py --from-file cards.json   # re-parse a saved dump (no network)
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

API_URL = "https://cookierunbraverse.com/data/json/cardList_asia.json"
USER_AGENT = "braverse-sim-dataset/1.0 (personal deck-simulator project)"

# The feed's field names, mapped onto the CSV's. The map is written out rather
# than derived so the CSV's schema is ours: a column the feed renames or adds
# does not silently change the file every downstream script reads.
FIELD_MAP = [
    ("name",                 "card_name"),
    ("productCategoryTitle", "card_product_title"),
    ("type",                 "card_type"),
    ("color",                "card_color"),
    ("energyType",           "card_energy_type"),
    ("cardLevel",            "card_level"),
    ("hp",                   "card_hp"),
    ("keyword",              "card_keyword"),
    ("rarity",               "card_rare"),
    ("skillName",            "card_skill_name"),
    ("description",          "card_skill_text"),
    ("attackText",           "card_attack_text"),
    ("flipText",             "card_flip"),
    ("isBan",                "card_is_ban"),
    ("isLimit",              "card_is_limit"),
    ("isExtra",              "card_is_extra"),
    ("grade",                "card_grade"),
    ("imageUrl",             "card_image"),
]

# Fields carrying rules text, which needs the normalisation below. Everything
# else is a scalar and only gets NFKC.
TEXT_FIELDS = {"name", "skillName", "description", "attackText", "flipText"}

# Column order: the rules-engine fields first, then the ones derived here.
HEADER = [out for out, _ in FIELD_MAP]
HEADER = (["id", "setId", "number"] + HEADER[:HEADER.index("grade")]
          + ["base_id", "is_alt_art", "all_rules_text", "grade", "imageUrl"])

# Legacy shorthand used in the older ST1-ST5 rows, mapped to the modern markers
# used everywhere else. Without this you have two vocabularies for one concept.
LEGACY_TOKENS = {
    "{ap}": "【Activate】",
    "{mob}": "【Activate】",
    "{mt}": "【Your Turn】",
    "{t1}": "【Once Per Turn】",
    "{sk}": "",
    "{sk]": "",
    "{da}": "deals",
}

# Every card carries its localisations in the same field as its English text,
# introduced by a "Card name :" line. Everything from there on is another
# language and must not reach the CSV.
_LOCALE_TAIL = re.compile(r"\r?\n\s*Card name\s*:.*\Z", re.S)

# A card with no attack still prints a lone {da} placeholder where its attack
# line would go. Left alone it normalises to the bare word "deals", which is
# the whole of that card's attack text -- 151 cards, mostly ITEM/TRAP/STAGE.
_BARE_DAMAGE_VERB = LEGACY_TOKENS["{da}"]

# A handful of ids are printed with an underscore where every other card has a
# hyphen (BS2_058@2, BS4_026@2, BS7_093@2). The id is kept exactly as the feed
# spells it -- it is the key downstream files join on -- but the set and number
# are split off either separator, so a typo does not invent a set called
# "BS7_093".
_ID_SEPARATOR = re.compile(r"[-_]")


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
    text = text.replace("《", "<").replace("》", ">")   # << >> -> < >
    for token, replacement in LEGACY_TOKENS.items():
        text = text.replace(token, replacement)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize("NFKC", text)                   # full-width -> ASCII
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def clean_text(value):
    """Normalise one rules-text field: drop the localisations, then the
    placeholder attack line, then undo the feed's double-escaped backslashes
    ("100\\\\% Bravery")."""
    text = normalize_text(_LOCALE_TAIL.sub("", value or ""))
    if text == _BARE_DAMAGE_VERB:
        return ""
    return text.replace("\\\\", "\\")


def _is_placeholder(value):
    """A JSON null that got stringified upstream before it reached the feed."""
    return isinstance(value, str) and value.strip().lower() in (
        "null", "none", "undefined")


def to_records(payload):
    """The feed is {productCategoryList, filterList, cardList}."""
    if isinstance(payload, list):          # a bare list of cards, just in case
        return payload
    return payload["cardList"]


def convert(rec):
    """One feed record -> one CSV row."""
    card_id = rec["card_no"]
    base_id = card_id.split("@")[0]
    set_id = _ID_SEPARATOR.split(base_id, 1)[0]
    number = _ID_SEPARATOR.split(card_id, 1)[-1] if _ID_SEPARATOR.search(card_id) else ""

    row = {"id": card_id, "setId": set_id, "number": number}
    for out, src in FIELD_MAP:
        value = rec.get(src)
        if out in TEXT_FIELDS:
            row[out] = clean_text(value)
        elif value is None or _is_placeholder(value):
            # Six rows carry the *string* "null" as their colour. Written out
            # it is worse than a blank: downstream reads it as a colour named
            # null rather than as missing.
            row[out] = ""
        else:
            # NFKC here too: a few rows carry full-width digits in hp/cardLevel,
            # which would otherwise silently break int() downstream.
            row[out] = unicodedata.normalize("NFKC", str(value)).strip()

    row["base_id"] = base_id
    row["is_alt_art"] = "1" if "@" in card_id else "0"
    # One field holding every scrap of rules text, for grepping and for
    # feeding to an effect-IR translation pass later.
    row["all_rules_text"] = "\n".join(
        t for t in (row["description"], row["attackText"], row["flipText"]) if t
    )
    return row


def sort_key(row):
    """A total order over the rows that belongs to the data, not to the feed.

    Row order is not cosmetic here: the card pool is built by iterating the
    CSV, and deck generation draws from that pool with a seeded RNG -- so a
    feed that reorders its cards silently changes every seeded result that
    was ever reported. The previous source happened to serve cards newest
    first and this one serves them oldest first, which is exactly the kind of
    difference nothing else would have noticed. Sorting here pins it.
    """
    def as_int(text):
        return int(text) if text.isdigit() else 0

    family, digits = re.match(r"([A-Za-z]*)(\d*)", row["setId"]).groups()
    base, _, alt = row["number"].partition("@")
    return (family, as_int(digits), as_int(base), as_int(alt), row["id"])


def main():
    utf8_output()   # a redirected stdout on Windows is cp1252
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="braverse_cards.csv")
    ap.add_argument("--url", default=API_URL, help="card list feed to read")
    ap.add_argument("--cookies-only", action="store_true",
                    help="keep only COOKIE and FLIP cards")
    ap.add_argument("--drop-alt-art", action="store_true",
                    help="collapse @1/@2 alt-art reprints onto the base card id")
    ap.add_argument("--raw-json", help="also write the untouched payload here")
    ap.add_argument("--from-file", help="parse a saved payload instead of fetching")
    args = ap.parse_args()

    if args.from_file:
        with open(args.from_file, encoding="utf-8") as fh:
            payload = json.load(fh)
    else:
        print(f"fetching {args.url}", file=sys.stderr)
        payload = fetch(args.url)
        if args.raw_json:
            with open(args.raw_json, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=1)
            print(f"raw payload -> {args.raw_json}", file=sys.stderr)

    records = to_records(payload)
    print(f"{len(records)} rows returned", file=sys.stderr)

    rows = []
    seen_base_ids = set()
    withdrawn = 0

    for rec in records:
        # A card pulled from the list upstream should leave the CSV with it.
        if rec.get("delete_dt") or str(rec.get("card_enable", "1")) == "0":
            withdrawn += 1
            continue
        if args.cookies_only and rec.get("card_type") not in ("COOKIE", "FLIP"):
            continue

        card_id = rec.get("card_no") or ""
        if args.drop_alt_art:
            base_id = card_id.split("@")[0]
            if base_id in seen_base_ids:
                continue
            seen_base_ids.add(base_id)

        rows.append(convert(rec))

    if not rows:
        raise SystemExit("no rows matched the filters")

    rows.sort(key=sort_key)

    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=HEADER, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    missing_text = sum(1 for r in rows if not r["all_rules_text"])
    print(f"wrote {len(rows)} cards -> {args.out}", file=sys.stderr)
    if withdrawn:
        print(f"  {withdrawn} rows skipped (withdrawn upstream)", file=sys.stderr)
    print(f"  {missing_text} rows have no rules text (vanilla or incomplete entries)",
          file=sys.stderr)


if __name__ == "__main__":
    main()
