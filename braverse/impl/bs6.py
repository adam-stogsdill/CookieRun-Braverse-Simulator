"""Hand-written cards from BS6.

The set is built around denial — suppressing traps, FLIPs, Blockers and
movement — plus a few "select any number" clauses the compiler cannot express.
"""

from __future__ import annotations

from braverse.cost import Cost
from braverse.effects import (MOVEMENT_LOCK_CARDS, STATIC_ABILITY_CARDS,
                              Ctx, Trigger, effect)
from braverse.enums import Color


# --- BS6-001 Blue Lily Cookie -----------------------------------------------
@effect("BS6-001", Trigger.ACTIVATE)
def blue_lily_activate(ctx: Ctx) -> None:
    """<Place 2 cards from the top of 1 of your {R} Cookies' HP into the trash.>
    Select up to 1 of your Cookies. During this turn, that Cookie gains +1
    attack damage."""
    fodder = ctx.select_own(
        lambda c: c.defn(ctx.db).color is Color.RED and c.remaining_hp >= 1,
        prompt="Pay 2 HP from which {R} Cookie?",
    )
    if fodder is None:
        return
    ctx.trash_hp(fodder, 2)
    target = ctx.select_own()
    if target is not None:
        ctx.modify_attack(target, 1)


# --- BS6-008 Sugar Swan Cookie ----------------------------------------------
@effect("BS6-008", Trigger.ATTACK_START)
def sugar_swan_attacks(ctx: Ctx) -> None:
    """When this Cookie attacks, if this Cookie's remaining HP is 4 or less,
    during this battle, your opponent cannot activate traps."""
    cookie = ctx.source_cookie
    if cookie is not None and cookie.remaining_hp <= 4:
        ctx.opp.traps_disabled = True


# --- BS6-010 Timekeeper Cookie ----------------------------------------------
# "If this Cookie is in the battle area, your opponent cannot use effects to
# move either player's Cookies from the battle area."
#
# A continuous ability rather than a trigger: it has no event to fire on, so it
# registers as a movement lock that the Cookie-moving primitives consult while
# this card is on the field.
MOVEMENT_LOCK_CARDS.add("BS6-010")
STATIC_ABILITY_CARDS.add("BS6-010")


# --- BS6-017 Pink Choco Cookie ----------------------------------------------
@effect("BS6-017", Trigger.ON_PLAY)
def pink_choco_on_play(ctx: Ctx) -> None:
    """<{R}> Select up to 1 of your opponent's Cookies. During this turn, the
    FLIP effects of that Cookie's HP cards cannot be activated."""
    if not ctx.pay(Cost.parse("{R}")):
        return
    target = ctx.select_enemy()
    if target is not None:
        target.flip_disabled = True


# --- BS6-034 Prophet Cookie -------------------------------------------------
@effect("BS6-034", Trigger.ON_PLAY)
def prophet_on_play(ctx: Ctx) -> None:
    """Select up to 1 of your Cookies. View all of that Cookie's HP cards and
    rearrange them in any order.

    Rearranged so the FLIPs sit on top, where damage reveals them first — that
    is the whole point of the effect.
    """
    target = ctx.select_own()
    if target is None:
        return
    target.hp_cards.sort(key=lambda c: ctx.db[c.card_id].is_flip)


# --- BS6-039 Croissant Cookie -----------------------------------------------
@effect("BS6-039", Trigger.ON_PLAY)
def croissant_on_play(ctx: Ctx) -> None:
    """<{Y}> If your opponent's break area is LV.6 or lower, place 1 Cookie
    from your opponent's break area into the trash. Then, select up to 1 Cookie
    in your opponent's battle area that is 1 LV. higher than that Cookie and
    place it in the trash."""
    if ctx.opp.break_level_total(ctx.db) > 6:
        return
    if not ctx.pay(Cost.parse("{Y}")):
        return
    options = [c for c in ctx.opp.break_area if ctx.db[c.card_id].is_cookie]
    if not options:
        return
    card = ctx.choose("Trash a Cookie from your opponent's break area",
                      options, optional=True)
    if card is None:
        return
    level = ctx.db[card.card_id].level or 0
    ctx.opp.break_area.remove(card)
    ctx.opp.trash.append(card)

    victim = ctx.select_enemy(lambda c: c.level(ctx.db) == level + 1)
    if victim is not None:
        ctx.trash_cookie(victim)


# --- BS6-047 Lemon Cookie ---------------------------------------------------
@effect("BS6-047", Trigger.ATTACK_START)
def lemon_static(ctx: Ctx) -> None:
    """If there are 5 cards or less in your support area, this Cookie gains +3
    attack damage."""
    if ctx.source_cookie and ctx.support_count() <= 5:
        ctx.modify_attack(ctx.source_cookie, 3)


# --- BS6-050 Butter Pretzel Cookie ------------------------------------------
@effect("BS6-050", Trigger.ACTIVATE)
def butter_pretzel_activate(ctx: Ctx) -> None:
    """Select any number of {G} cards in your support area. Return those cards
    to your hand."""
    while True:
        options = [c for c in ctx.me.support
                   if ctx.db[c.card_id].color is Color.GREEN]
        if not options:
            return
        card = ctx.choose("Return a {G} support card to hand", options,
                          optional=True)
        if card is None:
            return
        ctx.me.support.remove(card)
        card.rested = False
        ctx.me.hand.append(card)


# --- BS6-051 Timekeeper Cookie ----------------------------------------------
@effect("BS6-051", Trigger.END_TURN)
def timekeeper_green_end_turn(ctx: Ctx) -> None:
    """When your turn ends, select 5 cards to keep in your support area and
    return the rest to your hand."""
    while len(ctx.me.support) > 5:
        card = ctx.choose("Return a support card to hand",
                          list(ctx.me.support), optional=False) or ctx.me.support[-1]
        ctx.me.support.remove(card)
        card.rested = False
        ctx.me.hand.append(card)


@effect("BS6-051", Trigger.ATTACK)
def timekeeper_green_attack(ctx: Ctx) -> None:
    """Then, if there are 3 or more cards in your opponent's support area,
    select up to 2 {G} cards from your hand. Place those cards in your support
    area as active."""
    if ctx.support_count(mine=False) < 3:
        return
    for _ in range(2):
        options = [c for c in ctx.me.hand
                   if ctx.db[c.card_id].color is Color.GREEN]
        if not options:
            return
        card = ctx.choose("Place a {G} card into your support area", options,
                          optional=True)
        if card is None:
            return
        ctx.me.hand.remove(card)
        card.rested = False
        ctx.me.support.append(card)


# --- BS6-055 Grapefruit Cookie ----------------------------------------------
@effect("BS6-055", Trigger.WHEN_ATTACKED)
def grapefruit_green_when_attacked(ctx: Ctx) -> None:
    """【Your Turn】 If there are less cards in your support area than your
    opponent's support area, this Cookie takes no damage."""
    cookie = ctx.source_cookie
    if cookie is not None and len(ctx.me.support) < len(ctx.opp.support):
        cookie.damage_immune = True


# --- BS6-079 Croissant Cookie -----------------------------------------------
@effect("BS6-079", Trigger.ATTACK)
def croissant_blue_attack(ctx: Ctx) -> None:
    """Then, <discard 1 card.> Select up to 3 cards in your opponent's support
    area. Rest those cards."""
    if ctx.discard(1, optional=True):
        ctx.rest_support(3, mine=False)


# --- BS6-081 Truffle Cookie -------------------------------------------------
@effect("BS6-081", Trigger.ACTIVATE)
def truffle_activate(ctx: Ctx) -> None:
    """<{B}> Select up to 1 LV.1 Cookie in your opponent's battle area or 1
    stage card from either player's stage area. Place that card on the bottom
    of its owner's deck."""
    if not ctx.pay(Cost.parse("{B}")):
        return
    cookies = [c for c in ctx.opp.battle if c.level(ctx.db) == 1]
    stages = list(ctx.me.stage) + list(ctx.opp.stage)
    options = cookies + stages
    if not options:
        return
    picked = ctx.choose("Deck a LV.1 Cookie or a stage card", options,
                        optional=True)
    if picked is None:
        return
    if picked in cookies:
        ctx.opp.battle.remove(picked)
        ctx.opp.deck.append(picked.card)
        ctx.opp.trash.extend(picked.spent_cards)
        ctx.game._check_battle_area(ctx.opp)
    else:
        owner = ctx.me if picked in ctx.me.stage else ctx.opp
        owner.stage.remove(picked)
        owner.deck.append(picked)


# --- BS6-096 Cherry Cookie --------------------------------------------------
@effect("BS6-096", Trigger.ATTACK)
def cherry_attack(ctx: Ctx) -> None:
    """Then, if there is a LV.3 Cookie in your battle area, <can be used as
    {P}.> <Place this Cookie in the trash.> Play 1 {P} LV.1 Cookie from your
    trash."""
    if not any(c.level(ctx.db) == 3 for c in ctx.me.battle):
        return
    if ctx.source_cookie is None:
        return
    ctx.trash_cookie(ctx.source_cookie)
    ctx.play_cookie_from_trash(
        lambda d: d.color is Color.PURPLE and (d.level or 0) == 1
    )
