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
from braverse.enums import Marker


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


# --- BS9 and BS11: the seven the dump filed as ordinary Cookies --------------
# These print 【EXTRA】 and were typed COOKIE by the scrape, which meant they sat
# in the main 60 and could be played from hand for free, gate and all ignored.
# `_promote_extra_cards` fixes the type; these are the gates that go with it.
# Their 【On Play】 and 【Activate】 bodies are still unwritten — an EXTRA card
# with a gate and no body is a vanilla body with a real entry condition, which
# is the right way round to be incomplete.
@extra_play("BS9-102")
def shadow_milk_purple_gate(ctx: Ctx) -> bool:
    """【EXTRA】 Can be played if there are 20 cards or more in each player's
    trash."""
    return len(ctx.me.trash) >= 20 and len(ctx.opp.trash) >= 20


@extra_play("BS9-055")
def shadow_milk_green_gate(ctx: Ctx) -> bool:
    """【EXTRA】 Can be played if there are 3 cards or more in your opponent's
    support area and if, during this turn, 2 cards or more were placed from
    your support area into your trash."""
    return (len(ctx.opp.support) >= 3
            and ctx.me.support_trashed_this_turn >= 2)


@extra_play("BS9-010")
def shadow_milk_red_gate(ctx: Ctx) -> bool:
    """【EXTRA】 Can be played if 2 or more of your {R} LV.1 Cookies fainted
    during your opponent's previous turn."""
    from braverse.enums import Color
    want = ctx.state.turn_counter - 1
    return sum(1 for turn, color, level in ctx.me.faint_log
               if turn == want and color is Color.RED and level == 1) >= 2


def _discard_three_yellow_flips(ctx: Ctx) -> bool:
    from braverse.enums import CardType, Color
    return bool(ctx.discard_matching(
        3, lambda d: d.color is Color.YELLOW and d.type is CardType.FLIP))


@extra_play("BS9-030", pay=_discard_three_yellow_flips)
def shadow_milk_yellow_gate(ctx: Ctx) -> bool:
    """【EXTRA】 <Discard 3 {Y} Cookies that have FLIP from your hand.> Play this
    Cookie.

    "Cookies that have FLIP" is the FLIP card type, not a Cookie with a flip
    effect stapled on — FLIP cards *are* Cookies in this game, which is why
    they can sit in an HP pile and stand up when they turn over.
    """
    from braverse.enums import CardType, Color
    return sum(1 for c in ctx.me.hand
               if ctx.db[c.card_id].color is Color.YELLOW
               and ctx.db[c.card_id].type is CardType.FLIP) >= 3


@extra_play("BS11-091")
def avatar_of_destiny_gate(ctx: Ctx) -> bool:
    """【EXTRA】 If both players' break areas are LV.6 or higher and there are
    3 cards or less in both players' hands, this Cookie can be played."""
    return (ctx.me.break_level_total(ctx.db) >= 6
            and ctx.opp.break_level_total(ctx.db) >= 6
            and len(ctx.me.hand) <= 3 and len(ctx.opp.hand) <= 3)


@extra_play("BS9-088", hosts=lambda ctx: _named_in_battle(
    ctx, "Pure Vanilla Cookie"))
def pure_vanilla_awaken_gate(ctx: Ctx) -> bool:
    """【EXTRA】 During this turn, if a Cookie was placed from your battle area
    on the bottom of your deck, you can 【Awaken】 [Pure Vanilla Cookie].

    BS9-080 and BS9-087 are the Cookies that bury themselves to set this up,
    which is what `cookies_to_deck_bottom_this_turn` counts.
    """
    return ctx.me.cookies_to_deck_bottom_this_turn > 0


@extra_play("BS11-116", hosts=lambda ctx: _named_in_battle(
    ctx, "Dark Enchantress Cookie",
    lambda c: c.level(ctx.db) == 3 and c.defn(ctx.db).has(Marker.SPECIAL_PLAY)))
def dark_enchantress_awaken_gate(ctx: Ctx) -> bool:
    """【EXTRA】 If your break area is LV.7 or higher and [Dark Enchantress's
    Castle] is in your stage area, you can 【Awaken】 your LV.3 [Dark Enchantress
    Cookie] that has Special Play.

    【Special Play】 itself is not modelled — that is how the LV.3 gets onto the
    board in the first place, and there is no path to it yet. Naming the marker
    in the host filter anyway keeps this gate honest about which Cookie it
    means rather than awakening any LV.3 of that name; it simply cannot open
    until the other half exists.
    """
    return (ctx.me.break_level_total(ctx.db) >= 7
            and any(ctx.db[c.card_id].name == "Dark Enchantress's Castle"
                    for c in ctx.me.stage))


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
