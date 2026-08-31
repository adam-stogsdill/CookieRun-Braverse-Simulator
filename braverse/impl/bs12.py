"""BS12 — BOOSTER PACK [Festival Arena].

The set is built around 【Arena】, and its two entrances are the ones this
module wires up: six 【EXTRA】 Cookies, each with its own gate, and five
【Special Play】 bodies that print the same sentence BS11's Dough Cookies do.

Almost every attack line in the set carries a "if you started the game going
second" rider. That is the set's compensation mechanic, and it compiles — the
condition is a plain question about `GameState.first_player` — so those riders
are not written out here. What is written here is what the compiler refuses:
the play gates, which are conditions rather than clauses, and the two
continuous abilities.
"""

from __future__ import annotations

from braverse.cost import Cost
from braverse.effects import (ATTACK_DAMAGE_AURAS, STATIC_ABILITY_CARDS, Ctx,
                              Trigger, effect, extra_play, special_play)
from braverse.enums import Color, Keyword, Marker

from .bs11 import register_special_play


def _is_arena(defn) -> bool:
    return Keyword.ARENA in defn.keywords


def _arena_cards(ctx: Ctx, cards, color: Color | None = None) -> int:
    """How many of `cards` are 【Arena】 cards of `color`."""
    out = 0
    for card in cards:
        defn = ctx.db[card.card_id]
        if _is_arena(defn) and (color is None or defn.color is color):
            out += 1
    return out


# --- 【Special Play】 --------------------------------------------------------
# "Place 1 {K} LV.1 Cookie from your battle area into your trash." — the same
# entrance BS11's Dough Cookies print, so it is the same registration. The
# four Cake Hounds are LV.1 FLIPs; Red Velvet asks for a LV.1 Cookie that has
# Special Play, which is what `marked` means.
register_special_play("BS12-095", 1, 1)     # Blueberry Cake Hound
register_special_play("BS12-096", 1, 1)     # Crimson Danger Cake Hound
register_special_play("BS12-098", 1, 1)     # Caramel Pudding Cake Hound
register_special_play("BS12-100", 1, 1)     # Strategist Cake Hound
register_special_play("BS12-112", 1, 1, marked=True)   # Red Velvet Cookie


# --- 【EXTRA】 gates ---------------------------------------------------------
def _discard_one_arena(ctx: Ctx) -> bool:
    return bool(ctx.discard_matching(1, _is_arena))


@extra_play("BS12-018", pay=_discard_one_arena)
def shining_glitter_gate(ctx: Ctx) -> bool:
    """【EXTRA】 If your break area is LV.4 or higher, <discard 1 【Arena】 card
    from your hand.> Play this Cookie."""
    if ctx.me.break_level_total(ctx.db) < 4:
        return False
    # The discard is part of the move, so a hand that cannot pay it is not a
    # hand that can make it.
    return _arena_cards(ctx, ctx.me.hand) >= 1


@extra_play("BS12-036")
def clotted_cream_gate(ctx: Ctx) -> bool:
    """【EXTRA】 Can be played if there are 4 {Y} 【Arena】 Cookies or more in
    your break area."""
    return _arena_cards(ctx, ctx.me.break_area, Color.YELLOW) >= 4


@extra_play("BS12-056")
def apple_faerie_gate(ctx: Ctx) -> bool:
    """【EXTRA】 Can be played if there is a [Candy Apple Cookie] that has
    【Arena】 in your battle area, or if there are 7 {G} cards or more in your
    support area."""
    named = any(c.name(ctx.db) == "Candy Apple Cookie" and _is_arena(c.defn(ctx.db))
                for c in ctx.me.battle)
    green = sum(1 for c in ctx.me.support if ctx.db[c.card_id].color is Color.GREEN)
    return named or green >= 7


@extra_play("BS12-074")
def popping_candy_gate(ctx: Ctx) -> bool:
    """【EXTRA】 Can be played if, during this turn, an 【Arena】 Cookie was
    placed from your battle area on the bottom of your deck."""
    return ctx.me.arena_cookies_to_deck_bottom_this_turn > 0


def _purple_low_cookies(ctx: Ctx) -> list:
    return [c for c in ctx.me.battle
            if c.defn(ctx.db).color is Color.PURPLE and c.level(ctx.db) <= 2]


def _trash_a_purple(ctx: Ctx) -> bool:
    options = _purple_low_cookies(ctx)
    if not options:
        return False
    cookie = ctx.choose("【EXTRA】: place a Cookie in your trash",
                        options, optional=False)
    if cookie is None:
        return False
    # Trashing is not fainting: no break area, and no Level for the opponent.
    ctx.game.trash_cookie(cookie)
    return True


@extra_play("BS12-092", pay=_trash_a_purple, frees=1)
def black_lemonade_gate(ctx: Ctx) -> bool:
    """【EXTRA】 If there are 3 【Arena】 Cookies that have 【Blocker】 or more in
    your break area, <place 1 {P} LV.2 or lower Cookie from your battle area
    into your trash.> Play this Cookie."""
    blockers = sum(1 for c in ctx.me.break_area
                   if _is_arena(ctx.db[c.card_id])
                   and ctx.db[c.card_id].has(Marker.BLOCKER))
    return blockers >= 3 and bool(_purple_low_cookies(ctx))


@extra_play("BS12-111")
def poison_mushroom_gate(ctx: Ctx) -> bool:
    """【EXTRA】 Can be played if there are 4 cards or more in your opponent's
    support area and there is a Cookie that has Special Play in your battle
    area."""
    return (len(ctx.opp.support) >= 4
            and any(c.defn(ctx.db).has(Marker.SPECIAL_PLAY) for c in ctx.me.battle))


# --- continuous abilities ----------------------------------------------------
def _poison_mushroom_aura(db, player, attacker) -> int:
    """"Other {K} 【Arena】 Cookies in your battle area gain +1 attack damage."

    An aura over the *other* Cookies, so it cannot be an ATTACK_START trigger
    on this card — that trigger fires for the attacker alone. "Other" is the
    whole of the exclusion: Poison Mushroom does not buff itself, so a board
    with one of him and nothing else is no stronger for it.
    """
    if not any(c.defn(db).base_id == "BS12-111" for c in player.battle):
        return 0
    if attacker.defn(db).base_id == "BS12-111":
        return 0
    defn = attacker.defn(db)
    if defn.color is Color.BLACK and _is_arena(defn):
        return 1
    return 0


ATTACK_DAMAGE_AURAS.append(_poison_mushroom_aura)
STATIC_ABILITY_CARDS.add("BS12-111")


# --- BS12-077 Spotlight Fan --------------------------------------------------
# The set's one 【Equip】, and it is the odd one: every other Equip in the game
# is an ITEM that attaches itself on the way to the trash, while Spotlight Fan
# is a *Cookie* already standing in the battle area that climbs onto another
# one. So the move has to take it off the field first — a Cookie leaving the
# battle area sheds its HP pile — and what it leaves behind is a card riding
# on Rockstar Cookie, with the rider registered against Spotlight Fan's own id
# the way every Soul Jam's is.
@effect("BS12-077", Trigger.ACTIVATE)
def spotlight_fan_equip(ctx: Ctx) -> None:
    """【Activate】 【Once Per Turn】 <{P}> 【Equip】 this Cookie to your
    [Rockstar Cookie]. When that Cookie attacks, during this battle, your
    opponent cannot activate 【Blocker】."""
    fan = ctx.source_cookie
    if fan is None or fan not in ctx.me.battle:
        return
    holder = ctx.select_own(lambda c: c.name(ctx.db) == "Rockstar Cookie",
                            prompt="Equip to Rockstar Cookie?")
    if holder is None or holder is fan:
        return
    if not ctx.pay(Cost.parse("{P}")):
        return
    ctx.me.battle.remove(fan)
    # Leaving the battle area sheds the HP pile (and anything stacked under),
    # which is `spent_cards` — not `hp_cards`, which would strand an 【Awaken】.
    ctx.me.trash.extend(fan.spent_cards)
    ctx.me.trash.extend(fan.equipment)
    holder.equipment.append(fan.card)
    ctx.state.record(f"{fan.label(ctx.db)} is equipped to "
                     f"{holder.label(ctx.db)}")


spotlight_fan_equip.playable = lambda ctx: (
    ctx.source_cookie is not None
    and any(c.name(ctx.db) == "Rockstar Cookie" and c is not ctx.source_cookie
            for c in ctx.me.battle)
    and ctx.can_pay(Cost.parse("{P}")))


@effect("BS12-077", Trigger.ATTACK_START)
def spotlight_fan_rider(ctx: Ctx) -> None:
    """"When that Cookie attacks, during this battle, your opponent cannot
    activate 【Blocker】."

    Scoped to the battle rather than the turn: Rockstar can swing again later
    in the same turn off a second untap, and the fan only covers the swing it
    was there for.
    """
    ctx.game.for_this_battle(ctx.opp, "blockers_disabled", True)


@effect("BS12-092", Trigger.ALLY_FAINTED)
def black_lemonade_on_faint(ctx: Ctx) -> None:
    """When one of your Cookies faints, if there are 3 cards or more in your
    opponent's hand, your opponent places 1 card from their hand into their
    trash."""
    if len(ctx.opp.hand) >= 3:
        ctx.opponent_discards(1)
