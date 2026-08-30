# Tournament decklists

Eighteen lists from the **$11,000 CookieRun: Braverse North America
Championship** (83 players, 13 December 2025), read from that event's public
standings on [topdeck.gg](https://topdeck.gg/bracket/saturday-11000-cookierun-braverse-north-america-1)
on 2026-08-29. Each file keeps its placing and match record in the header.

They are stored as a folder rather than loose in `decks/` because they are read
as a *set*:

```bash
python train_rl.py --deck-pool            # every game draws two decks from here
python evolve_deck.py --gauntlet decks/meta
```

Both of those, and the game's own deck menu, read this folder — a `.txt` you
add here shows up in all three.

**These are main decks only.** topdeck.gg's decklist field is the 60 cards; the
EXTRA deck is not part of it, so these lists play with no EXTRA pile. That is
not how they were played in the room, and it is the first thing to check before
reading anything into a win rate.
