"""The 【EXTRA】 deck: how each EXTRA card leaves that pile and enters the game.

An EXTRA card is never shuffled into the main deck and never drawn. It sits in
a second, face-up-to-its-owner pile all game, and the only way it arrives is
through the gate printed on it — the "Can be played if ..." line. The gate is a
condition, not a cost: while it is false the card is not a legal move at all.

Two shapes share that mechanism. A standalone EXTRA Cookie takes a free battle
slot like any other Cookie. An 【Awaken】 card has no battle slot of its own —
it goes *on top of* a Cookie already in play, named on the card, keeping the HP
that Cookie has left and adding its own printed modifier on top. That is why
these cards print HP as `+1` or `+2` rather than a total, and why an Awaken is
worth most on a Cookie that has already been chipped down.
"""

from __future__ import annotations

from braverse.cost import Cost
from braverse.effects import Ctx, Trigger, effect, extra_play


# --- standalone EXTRA Cookies ------------------------------------------------
@extra_play("BS8-005")
def avatar_of_ruin_gate(ctx: Ctx) -> bool:
    """【EXTRA】 Can be played if 2 or more of your Cookies fainted this turn."""
    return ctx.me.cookies_fainted_this_turn >= 2


@extra_play("BS8-069")
def peak_of_apathy_gate(ctx: Ctx) -> bool:
    """【EXTRA】 Can be played if your support area has 2 or more cards less
    than your opponent's support area."""
    return len(ctx.opp.support) - len(ctx.me.support) >= 2


@extra_play("BS8-090")
def will_of_nature_gate(ctx: Ctx) -> bool:
    """【EXTRA】 Can be played if there are 2 cards or less in your hand."""
    return len(ctx.me.hand) <= 2


@extra_play("BS10-048")
def warden_of_the_heart_gate(ctx: Ctx) -> bool:
    """【EXTRA】 If your break area is LV.5 or higher and, during this turn, any
    of your Cookies gained HP, you can play this Cookie."""
    return (ctx.me.break_level_total(ctx.db) >= 5
            and ctx.me.hp_gained_this_turn)


def _discard_two_blue(ctx: Ctx) -> bool:
    from braverse.enums import Color
    return bool(ctx.discard_colored(2, Color.BLUE))


@extra_play("BS10-098", pay=_discard_two_blue)
def jagae_gate(ctx: Ctx) -> bool:
    """【EXTRA】 If there are 7 cards or more in your hand, <discard 2 {B}
    cards.> Play this Cookie."""
    from braverse.enums import Color
    if len(ctx.me.hand) < 7:
        return False
    # The cost is part of the move, so a hand that cannot pay it is not a hand
    # that can make it.
    return sum(1 for c in ctx.me.hand
               if ctx.db[c.card_id].color is Color.BLUE) >= 2


def _discard_two(ctx: Ctx) -> bool:
    return bool(ctx.discard(2))


@extra_play("BS10-123", pay=_discard_two)
def spectral_warmaster_gate(ctx: Ctx) -> bool:
    """【EXTRA】 If you refreshed during this game, <discard 2 cards.> Play this
    Cookie."""
    return ctx.me.refreshed and len(ctx.me.hand) >= 2


# --- 【Awaken】 --------------------------------------------------------------
def _named_in_battle(ctx: Ctx, name: str, predicate=None) -> list:
    return [c for c in ctx.me.battle
            if c.name(ctx.db) == name and (predicate is None or predicate(c))]


@extra_play("BS8-027", hosts=lambda ctx: [
    c for c in _named_in_battle(ctx, "Golden Cheese Cookie")
    if c.uid in ctx.me.played_from_break_this_turn])
def golden_cheese_awaken_gate(ctx: Ctx) -> bool:
    """【EXTRA】 During this turn, if [Golden Cheese Cookie] was played from your
    break area, you can 【Awaken】 that Cookie."""
    return True


@extra_play("BS8-104", hosts=lambda ctx: [
    c for c in _named_in_battle(ctx, "Dark Cacao Cookie")
    if c.uid in ctx.me.played_from_trash_this_turn])
def dark_cacao_awaken_gate(ctx: Ctx) -> bool:
    """【EXTRA】 During this turn, if [Dark Cacao Cookie] was played from your
    trash, you can 【Awaken】 that Cookie."""
    return True


def _discard_one(ctx: Ctx) -> bool:
    return bool(ctx.discard(1))


@extra_play("BS10-024", pay=_discard_one, hosts=lambda ctx: _named_in_battle(
    ctx, "Hollyberry Cookie", lambda c: c.remaining_hp <= 3))
def hollyberry_awaken_gate(ctx: Ctx) -> bool:
    """【EXTRA】 <Discard 1 card.> You can 【Awaken】 your [Hollyberry Cookie]
    with 3 or less HP remaining."""
    return bool(ctx.me.hand)


@extra_play("BS10-073", hosts=lambda ctx: _named_in_battle(
    ctx, "White Lily Cookie"))
def white_lily_awaken_gate(ctx: Ctx) -> bool:
    """【EXTRA】 If there are 8 cards or more in your support area, you can
    【Awaken】 your [White Lily Cookie]."""
    return len(ctx.me.support) >= 8


# --- the one Awaken card with no effect of its own on file -------------------
@effect("BS10-024", Trigger.ON_PLAY)
def hollyberry_extra_on_play(ctx: Ctx) -> None:
    """Until the end of your opponent's next turn, this Cookie receives -1 from
    all damage."""
    if ctx.source_cookie is not None:
        ctx.source_cookie.all_damage_reduction += 1


@effect("BS10-024", Trigger.ATTACK)
def hollyberry_extra_attack(ctx: Ctx) -> None:
    """Then, <{R}{R}> Select up to 1 of your opponent's Cookies. That Cookie
    receives 2 damage."""
    if not ctx.wants_to_pay("{R}{R}") or not ctx.pay(Cost.parse("{R}{R}")):
        return
    target = ctx.select_enemy()
    if target is not None:
        ctx.deal_damage(target, 2)
