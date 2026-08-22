# Writing cards

How to teach the engine a card it does not play yet.

Everything here lives under `braverse/impl/`. A card you write there always
wins: `compile_all` skips any card already in the registry, so a hand-written
effect can only add or correct coverage, never lose to the text compiler.

- [Where your card goes](#where-your-card-goes)
- [The five-minute version](#the-five-minute-version)
- [Anatomy of an effect](#anatomy-of-an-effect)
- [Triggers](#triggers)
- [The `Ctx` vocabulary](#the-ctx-vocabulary)
- [Costs](#costs)
- [Choices and targeting](#choices-and-targeting)
- [Cards with no trigger at all](#cards-with-no-trigger-at-all)
- [EXTRA deck cards](#extra-deck-cards)
- [When `Ctx` has no verb for it](#when-ctx-has-no-verb-for-it)
- [Rules that will bite you](#rules-that-will-bite-you)
- [Testing what you wrote](#testing-what-you-wrote)
- [Finding work](#finding-work)
- [It doesn't show up in the game](#it-doesnt-show-up-in-the-game)
- [Checklist](#checklist)

## Where your card goes

| The card is… | Write it in | Registered as |
| --- | --- | --- |
| a normal Cookie / ITEM / TRAP / STAGE effect | `braverse/impl/<set>.py` | `@effect(card_id, Trigger.X)` |
| purely continuous — a lock, an aura, a targeting rule | `braverse/impl/<set>.py` | a function appended to a registry in `effects.py` |
| an 【EXTRA】 deck card | `braverse/impl/extra.py` | `@extra_play(card_id)` |
| a *phrasing* that appears on twenty cards | `braverse/compiler.py` | a pattern → IR op |

Default to the first row. The compiler is worth extending when you notice the
same sentence on many cards; for one card it is far more work than writing the
card.

There is one module per set — `bs1.py` … `bs11.py`, `st8_green.py`,
`st9_blue.py`, `st_misc.py` (everything from the ST starters that isn't the
green or blue starter). Put the card in the module for its set. If you add a
new module, **import it in `braverse/impl/__init__.py`** — a module nobody
imports registers nothing and fails silently.

## The five-minute version

```python
# braverse/impl/st_misc.py
@effect("ST10-006", Trigger.TRASHED)
def blueberry_pie_trashed(ctx: Ctx) -> None:
    """When this Cookie is placed from your battle area into your trash, select
    up to 1 of your opponent's Cookies. That Cookie receives 1 damage."""
    target = ctx.select_enemy()
    if target is not None:
        ctx.deal_damage(target, 1)
```

That is the whole shape, and the convention is strict about two halves of it:

- **The docstring is the printed text, verbatim.** It is how anyone reading the
  file checks the code against the card without going to look the card up. Copy
  it out of `braverse_cards.csv` (or off the card) rather than paraphrasing.
- **The body is the same sentences in `Ctx` verbs.** Cards never touch
  `GameState` directly — they call `Ctx`, which is the only layer that knows
  about immunities, movement locks, the event log and the prompts a human seat
  needs to be shown.

Use the **base card id** (`"ST10-006"`). Alt arts and promos carry suffixes like
`BS4-026@1`, and `get_effect` strips them, so one registration covers every
printing.

## Anatomy of an effect

```python
@effect("ST8-014", Trigger.ITEM)
@playable_if(lambda ctx: ctx.support_count() >= 5
             and bool(ctx.own_cookies(
                 lambda c: c.defn(ctx.db).color is Color.GREEN
                 and c.remaining_hp <= 3)))
def cape_of_the_vanquisher(ctx: Ctx) -> None:
    """If there are 5 cards or more in your support area, select up to 1 of
    your {G} Cookies with 3 or less HP remaining. That Cookie gains +1 HP."""
    if ctx.support_count() < 5:
        return
    target = ctx.select_own(
        lambda c: c.defn(ctx.db).color is Color.GREEN and c.remaining_hp <= 3
    )
    if target is not None:
        ctx.gain_hp(target, 1)
```

**`@effect(card_id, trigger)`** registers the body. Registering the same
`(card_id, trigger)` pair twice raises at import time, so you cannot shadow an
existing card by accident.

**`@playable_if(predicate)`** is how the engine knows not to *offer* a move that
would do nothing. A compiled card carries its conditions as ops the engine can
read; a hand-written body is opaque Python, so the predicate is the `if` at the
top of your function, hoisted where the action list can see it. Note the
condition is written twice on purpose — once as the gate, once as the real check
inside the body, because the board can change between the two.

> **The predicate must only read the board.** The engine calls it to probe, and
> a mutation there corrupts a state nobody asked to change. No drawing, no
> paying, no `select_*` (that would prompt a human just to build a menu).

Leave `@playable_if` off and the card is always offered — correct for a card
that always does something, wrong for one that often fizzles.

**`ctx.me` is always the effect's controller** and `ctx.opp` the other player,
whoever's turn it is. A TRAP resolving on your opponent's turn still sees
itself as `me`.

## Triggers

Full list in [`braverse/effects.py`](../braverse/effects.py) (`class Trigger`).
The ones people pick wrong:

| Trigger | Fires when | Notes |
| --- | --- | --- |
| `ON_PLAY` | 【On Play】, as the card enters | not during setup's opening Cookie |
| `ACTIVATE` | 【Activate】, main phase | needs the 【Activate】 marker on the printed card; treated as once per turn per source |
| `ATTACK` | the `Then, ...` rider on an attack line | *after* the swing has been dealt, not the swing itself |
| `ATTACK_START` | as an attack is declared | where static 【Your Turn】 attack buffs go, so they read the board live |
| `WHEN_ATTACKED` | on the defending Cookie, once an attack is declared at it | |
| `SURVIVED_DAMAGE` | "if this Cookie remains in the battle area after receiving damage" | |
| `FLIP` | the card is revealed off an HP pile by damage | see ["this Cookie"](#rules-that-will-bite-you) |
| `FAINT` | the Cookie reached 0 HP | |
| `TRASHED` | "placed from your battle area into your trash" | **not** the same as `FAINT` — no break area, no Level |
| `ITEM` | an ITEM's or a **TRAP's** body | one trigger for both |
| `STAGE_ACTIVATE` | a STAGE card's 【Activate】 | placing the stage is a separate move |
| `END_TURN` | the controller's end phase | |
| `PLAYED_FROM_TRASH` / `PLAYED_FROM_SUPPORT` / `PLAYED_FROM_BREAK` | recursion payoffs | |

A card with several abilities gets several registrations — one function per
trigger, same card id.

## The `Ctx` vocabulary

Roughly forty verbs, defined in [`braverse/effects.py`](../braverse/effects.py).
Read the source when you need the exact behaviour; this is the map.

**Who and what**
`ctx.me`, `ctx.opp` — the two `PlayerState`s (`.hand`, `.deck`, `.battle`,
`.support`, `.stage`, `.trash`, `.break_area`, `.extra_deck`).
`ctx.source_cookie` — the Cookie whose ability this is, or the **host** of a
FLIP. `ctx.source_card` — the physical card, which for a FLIP is the revealed
card itself. `ctx.attacker` / `ctx.attack_target` during combat. `ctx.db` for
printed cards, `ctx.state` for the whole game.

**Asking about the board**
`hand_size`, `name_in_battle(name, mine=)`, `name_in_support(name, mine=)`,
`count_in_trash(predicate)`, `support_count(mine=)`,
`active_support_count(mine=)`, `me.break_level_total(db)`.

**Cards moving around**
`draw(n)` (never loses the game on an empty deck), `discard(n, optional=)`,
`discard_colored(n, color)`, `mill_deck(n)`, `mill_to_support(n, rested=)`,
`return_support_to_hand(predicate=)`, `opponent_discards(n)`,
`play_cookie_from_trash(predicate=)`,
`view_top(n, pick=, criterion=, reveal=, rest=)`, `reveal_top(n)`,
`discard_matching(n, predicate)`, `run_flip(card, host=)`,
`steal_to_hp(cookie, card, source)`.

`reveal_top` is **not** `view_top`. A view is private and you take out of it; a
reveal is shown to both players, moves nothing, and leaves the cards on top for
the clause that follows to ask about — which is why the names go in the (public)
log and a view's do not. `steal_to_hp` puts a card on the *bottom* of an HP pile
face up; bottom is index 0, because damage pops off the end, so a stolen card is
the last one that pile turns over. `run_flip` fires a FLIP from somewhere that
is not an HP pile — one card does this, and `host` is the Cookie the effect
treats as its own.

`view_top` is "View N cards from the top of your deck, take one, put the rest
back", and the thing to get right about it is that those are **two separate
instructions**. `pick` is a predicate over the `CardDef` and narrows what may
be *taken*; every card viewed is still put in front of the player, greyed where
the criterion rules it out. Filtering the view down to the eligible cards turns
"look at three" into "look at one", which is a different and much worse card —
that was the bug in Aloe Cookie before this verb existed. `criterion` is the
text for the prompt ("{B}"), `reveal=True` is "show it to your opponent" and
names the taken card in the (public) log while never naming the ones left
behind, and `rest` is `"bottom"` or `"trash"`. When nothing matches, the view is
still shown, as a look-and-acknowledge.

**Cookies**
`deal_damage(cookie, n)` — effect damage, which is what everything that is not
the swing itself deals. `gain_hp(cookie, n)`, `modify_attack(cookie, delta)`,
`faint(cookie)`, `trash_cookie(cookie)`, `return_to_hand(cookie)`,
`return_self_to_hand()`, `trash_hp(cookie, n, opponent_trash=)` (**not**
damage — no FLIPs fire, which is exactly why cards word it that way),
`skip_next_active(cookie)`.

**Support area**
`rest_support(n, mine=)` — "up to N", so it asks a human seat which cards and
how many. `set_support_active(n, mine=)`, `trash_stage(n, mine=)`.

**Everything else** is a flag on the `Cookie` or `PlayerState` dataclass in
[`braverse/state.py`](../braverse/state.py) — `hp_cannot_reach_zero`,
`damage_cap`, `effect_damage_reduction`, `traps_disabled`, `blockers_disabled`,
`level_override`, and so on. Set the flag directly; the machinery that resets
them lives in `Game._begin_turn`.

## Costs

**Anything in `<...>` is optional.** The player may pay it; if they decline,
nothing happens. Getting this wrong means a FLIP turning over on your
opponent's turn silently rests your support or bins a card from your hand.

**The `<...>` at the front of an ITEM or a TRAP is the card's play cost.** The
engine has already rested support for it — `CardDef.play_cost` — by the time
your body runs. Paying it again in the body charges twice, and a `<...>` cost
that cannot be met fails *silently*, so the card just does nothing. That bug
shipped twice; there is a test that now catches it.

A *second* bracket later in the text ("Then, `<{Y}>` you can 【Equip】 …") is a
real extra cost and you do pay that one.

An energy cost:

```python
if target is not None and ctx.pay(Cost.parse("{G}{G}")):
    target.hp_cannot_reach_zero = True
```

`ctx.pay` rests the support cards and returns False if it could not be paid —
so it belongs in the `if`, never on its own line. `ctx.can_pay(cost)` is the
read-only version for `@playable_if`.

A cost that is itself an action takes `optional=True`, which *is* the printed
`<Discard 1 card.>`:

```python
if ctx.discard(1, optional=True):
    ...
```

Both routes go through `Ctx.wants_to_pay`, which decides whether to actually ask
the human. Triggers the controller opted into by choosing an action —
`ACTIVATE`, `ITEM`, `STAGE_ACTIVATE` — pay without a prompt, because taking the
action *was* the decision. Everything that merely happens to them (a FLIP, a
`WHEN_ATTACKED`) asks first. You get that for free; just don't bypass it.

Cost strings: `{R} {B} {G} {Y} {P} {K}` for the colours and `{N}` for "any".
`Cost.parse("{G}{G}{N}")`.

## Choices and targeting

`select_enemy(predicate=, prompt=)` and `select_own(predicate=, prompt=)` are
the two workhorses. Both return `None` when there is nothing to pick and both
are optional-by-default, which is what "select **up to** 1" means — a human can
decline. The predicate takes a `Cookie`:

```python
target = ctx.select_enemy(lambda c: c.level(ctx.db) <= 2)
```

`enemy_cookies(predicate)` / `own_cookies(predicate)` return the whole list
instead, for "all of your opponent's Cookies receive 1 damage" — iterate over
`list(...)`, since damage can remove Cookies from the list you're walking.

`ctx.choose(prompt, options, optional=True)` is the general one-of-N question,
and `ctx.confirm(prompt)` a yes/no. Both route to whatever controller is in that
seat — a bot answers instantly, a human seat blocks on the browser.

`ctx.note(message)` writes a line to the game log. It's already stamped with
your card's name, so write the consequence, not the card.

## Cards with no trigger at all

A continuous ability has no event to hang off, so it registers with the engine
where the rule is *enforced* instead. The registries live at the top of
`effects.py`:

| Registry | Signature | For |
| --- | --- | --- |
| `TAUNT_PROVIDERS` | `(db, defender) -> Cookie \| None` | "can only attack this Cookie" |
| `ATTACK_PROHIBITIONS` | `(db, cookie) -> bool` | "this Cookie cannot attack" |
| `MOVEMENT_LOCK_CARDS` | set of card ids | "your opponent's effects cannot move Cookies" |
| `MOVEMENT_PROTECTORS` | `(db, owner, cookie) -> bool` | the same, but protecting only itself |
| `EFFECT_DAMAGE_BONUSES` | `(ctx, cookie) -> int` | continuous "+N effect damage" |

```python
def _hollyberry_cannot_attack(db, cookie) -> bool:
    return (cookie.defn(db).base_id in ("BS10-021", "BS10-024")
            and cookie.remaining_hp <= 3)


ATTACK_PROHIBITIONS.append(_hollyberry_cannot_attack)
STATIC_ABILITY_CARDS.update(("BS10-021", "BS10-024"))
```

**Always add the card id to `STATIC_ABILITY_CARDS` too.** That set is how
`is_implemented` knows the card is finished — without it the coverage report and
`KNOWN_UNCODED` still count it as a hole.

## EXTRA deck cards

`braverse/impl/extra.py`, one `@extra_play` per card. You are describing how the
card *leaves* the EXTRA pile, not what it does once it lands (that is a normal
`@effect(..., Trigger.ON_PLAY)` if it has one).

```python
@extra_play("BS8-090")
def will_of_nature_gate(ctx: Ctx) -> bool:
    """【EXTRA】 Can be played if there are 2 cards or less in your hand."""
    return len(ctx.me.hand) <= 2
```

The gate is a **condition, not a cost**: while it is false the card is not a
legal move at all. Two optional keywords:

- `pay=fn` — the printed `<...>` cost, run once the move is taken; return False
  to abort.
- `hosts=fn` — 【Awaken】 only. Returns the Cookies this card may be stacked on
  top of; an empty list means unplayable.

`EXTRA_PLAYS` is the whole registry, and `tests/test_extra_deck.py` pins that it
is complete — a card missing from it can never be played, so the test will tell
you.

## When `Ctx` has no verb for it

Add one. Three places, in order of how far the change reaches:

1. **A new `Ctx` method** — the usual answer. Put it with its neighbours
   (movement verbs together, cost verbs together) and route anything that
   changes HP or damage through `Game.deal_damage` / `Game.gain_hp` so it lands
   in the log and in `state.events`.
2. **A new field on `Cookie` or `PlayerState`** in `state.py`, for a new kind of
   modifier. Nearly always this also needs a reset in `Game._begin_turn` — a
   "during this battle" flag that never clears is a bug that shows up ten turns
   later.
3. **A new constant in `config.py`**, if it is a tunable rule rather than a card
   behaviour. Every constant there is cited to the PLAY GUIDE, with "NOT IN
   GUIDE" marking an assumption; keep that up. Don't inline rule numbers.

If the viewer should *show* the new thing, append a record to `state.events` and
teach `play_server.py` to translate it — the prose log alone can't distinguish a
swing from a rider on the same Cookie, which is why the structured stream
exists. **Append it at the moment the thing happens**, not afterwards: the
browser plays a batch of events in the order it receives them, so a record
written after the effect it describes gets animated after it too.

## Rules that will bite you

**"This Cookie" on a FLIP is the card, not its host.** A FLIP sits in an HP pile
and fires when damage reveals it. Every one of the 92 cards that means the host
says so at length — "the Cookie with this card attached for HP" — so plain
"this Cookie" means the revealed card. Use `ctx.return_self_to_hand()` for that,
`ctx.source_cookie` for the host.

**Trashing is not fainting.** `trash_cookie` skips the break area, so the
opponent banks no Level. That difference is why those effects are priced the way
they are. Read the card: "place into the trash" vs "make faint".

**`trash_hp` is not damage.** No FLIPs fire.

**A listed move must do something.** That's `@playable_if`, and the probe that
reads it must never mutate.

**A Cookie leaving the battle area sheds `cookie.spent_cards`**, which is its HP
pile *plus* anything it was 【Awaken】ed on top of — not `hp_cards` alone.

**A card filter cannot see the table.** `parse_card_filter` matches *printed*
cards, so a phrase like "active cards" or "rested cards" is refused rather than
having the word dropped. If a card needs one, it needs a `Condition`, not a
filter.

**Test the card the way it is played.** Calling the effect function directly
with a hand-made `Ctx` skips everything the engine does around it — paying the
play cost, the response window, the probe. A test written that way can pass
while the card is broken in every real game. If the card is a TRAP, drive it
through `_response_window`; if it is an ITEM, step the action.

**Damage is a loop, and the board can change inside it.** Each HP card is
revealed one at a time and a FLIP can heal its host, bounce itself, or end the
game mid-hit. If you write anything that walks a list of Cookies, re-check the
list.

**Self-play numbers are the regression check.** Everything is seeded and
deterministic. If `selfplay.py` moves after your change, either you broke
something or you deliberately changed a rule — and if it's the latter, say so in
the README rather than letting the number drift silently. Adding a card that
wasn't in the starter decks won't move it; changing shared machinery will.

## Testing what you wrote

Tests for individual cards go in `tests/test_engine.py` (or
`tests/test_compiler.py` if you extended the compiler). The pattern is: build a
real game, force the board into the shape you care about, call the effect, then
assert on the state.

```python
def test_blueberry_pie_pings_on_the_way_to_the_trash():
    from braverse.effects import Ctx, Trigger, get_effect

    db = default_db()
    game = new_game(seed=8, db=db)
    me, them = game.state.players[0], game.state.players[1]
    victim = them.battle[0]
    before = victim.remaining_hp

    fn = get_effect("ST10-006", Trigger.TRASHED)
    fn(Ctx(game=game, state=game.state, db=db, me=me, opp=them,
           trigger=Trigger.TRASHED.value))

    assert victim.remaining_hp == before - 1
```

Two helpers worth knowing: `_plain_pile(game, db)` / `_plain_hp(game, db)` swap
every FLIP out of the HP piles, so your assertion measures your card and not
some other card's flip firing mid-test. And `SeatedAgent(HeuristicAgent(...), n)`
is the standard bot seat; substitute your own tiny class into
`game._controllers[n]` when you need to script the answers to prompts.

```bash
python -m pytest -q tests/                    # everything, ~30s
python -m pytest -q tests/test_engine.py -k blueberry
python selfplay.py -n 200                     # did the numbers move?
BRAVERSE_NO_COMPILE=1 python selfplay.py -n 50 # hand-written cards only
```

That last one is the fastest way to answer "is this my card or the compiler?" —
run your own scenario under it, not the suite, since a few tests exist
specifically to pin compiled behaviour and will fail without it.

## Finding work

```bash
python coverage_report.py             # per-set: how many cards the engine plays
python coverage_report.py --phrases   # uncovered rules text, ranked by frequency
```

`--phrases` is the one to use when you want leverage: it tells you which
sentence appears on twenty unimplemented cards, so you write one primitive
instead of twenty bodies.

`KNOWN_UNCODED` in `tests/test_compiler.py` is the explicit list of cards
deliberately left unimplemented. **When you implement one, delete it from that
list** — a test asserts the list has no stale entries, on purpose, so the list
can't quietly become a blanket exemption.

## It doesn't show up in the game

In the order worth checking:

1. **Module not imported** in `braverse/impl/__init__.py`.
2. **Wrong trigger** — a TRAP's body is `Trigger.ITEM`; an attack rider is
   `Trigger.ATTACK`, not `ON_PLAY`.
3. **Wrong id** — base id, no `@1` suffix, and the set prefix is upper case.
4. **`@playable_if` returns False.** Call the predicate by hand in a REPL with a
   real `Ctx` and see.
5. **Missing printed marker** — an `ACTIVATE` effect is only offered on a card
   whose text carries 【Activate】, and the engine treats every one as once per
   turn per source.
6. **The cost can't be paid.** A move whose cost has no legal payment is never
   listed.
7. **It's a Cookie's 【Activate】 and the Cookie is rested**, or it already
   activated this turn.

## Checklist

- [ ] Function in `braverse/impl/<set>.py`, decorated `@effect("XXX-000", Trigger.Y)`
- [ ] Docstring is the printed text, copied not paraphrased
- [ ] Body written in `Ctx` verbs — no direct `GameState` poking
- [ ] `<...>` costs go through `ctx.pay` / `optional=True`
- [ ] `@playable_if` if the card can fizzle, and it only reads
- [ ] Module imported in `impl/__init__.py`
- [ ] Removed from `KNOWN_UNCODED` if it was there
- [ ] `STATIC_ABILITY_CARDS` updated if it's a continuous ability
- [ ] A test that pins the behaviour
- [ ] `python -m pytest -q tests/` green
- [ ] `python selfplay.py -n 200` unchanged (or the change is deliberate and written down)
- [ ] Version bumped in `README.md`, entry in `changelog.md`
