"""Hand-written cards from BS1.

The set leans on triggers the compiler has no grammar for — surviving damage,
being attacked, end-of-turn on a Cookie — plus damage that scales off a zone.
"""

from __future__ import annotations

from braverse.cost import Cost
from braverse.effects import Ctx, Trigger, effect
from braverse.enums import Color


# --- BS1-006 Mala Sauce Cookie ----------------------------------------------
@effect("BS1-006", Trigger.SURVIVED_DAMAGE)
def mala_sauce_survived(ctx: Ctx) -> None:
    """If this Cookie remains in the battle area after receiving damage, select
    up to 1 of your opponent's Cookies. That Cookie receives 1 damage.

    ``ctx.opp`` here is the attacker, since the trigger resolves for the
    defending player.
    """
    target = ctx.select_enemy()
    if target is not None:
        ctx.deal_damage(target, 1)


# --- BS1-012 Wildberry Cookie -----------------------------------------------
@effect("BS1-012", Trigger.ATTACK_START)
def wildberry_static(ctx: Ctx) -> None:
    """If your break area is LV.9, this Cookie gains +2 attack damage."""
    if ctx.source_cookie and ctx.me.break_level_total(ctx.db) == 9:
        ctx.modify_attack(ctx.source_cookie, 2)


# --- BS1-016 Choco Ball Cookie ----------------------------------------------
@effect("BS1-016", Trigger.FAINT)
def choco_ball_faint(ctx: Ctx) -> None:
    """When this Cookie faints and you have 4 cards or less in your hand,
    select up to 1 of your opponent's Cookies. That Cookie receives 1 damage."""
    if ctx.hand_size > 4:
        return
    target = ctx.select_enemy()
    if target is not None:
        ctx.deal_damage(target, 1)


# --- BS1-033 Cyborg Cookie --------------------------------------------------
@effect("BS1-033", Trigger.ATTACK)
def cyborg_attack(ctx: Ctx) -> None:
    """Then, select up to 1 of your opponent's Cookies. That Cookie receives 1
    damage for each Cookie that is LV.2 or higher in your break area."""
    count = sum(1 for c in ctx.me.break_area
                if (ctx.db[c.card_id].level or 0) >= 2)
    if not count:
        return
    target = ctx.select_enemy()
    if target is not None:
        ctx.deal_damage(target, count)


# --- BS1-042 Grapefruit Cookie ----------------------------------------------
@effect("BS1-042", Trigger.WHEN_ATTACKED)
def grapefruit_when_attacked(ctx: Ctx) -> None:
    """When your opponent attacks this Cookie, <can be used as {Y}.> This
    Cookie receives -1 attack damage during this battle.

    Damage *reduction*, not an attack debuff: it only applies to the incoming
    attack, and is reset before each one.
    """
    if ctx.source_cookie is not None:
        ctx.source_cookie.incoming_damage_reduction += 1


# --- BS1-044 Bell Pepper Cookie ---------------------------------------------
@effect("BS1-044", Trigger.ACTIVATE)
def bell_pepper_activate(ctx: Ctx) -> None:
    """<{Y}> <Discard 1 card.> If this Cookie's HP is less than 3, gain +1 HP."""
    cookie = ctx.source_cookie
    if cookie is None or cookie.remaining_hp >= 3:
        return
    if not ctx.pay(Cost.parse("{Y}")):
        return
    if ctx.discard(1, optional=True):
        ctx.gain_hp(cookie, 1)


# --- BS1-049 Tropical Slushie -----------------------------------------------
@effect("BS1-049", Trigger.ATTACK)
def tropical_slushie_attack(ctx: Ctx) -> None:
    """Deals 1 damage for each {Y} LV.2 or higher Cookie in your break area to
    1 of your opponent's Cookies."""
    count = sum(1 for c in ctx.me.break_area
                if ctx.db[c.card_id].color is Color.YELLOW
                and (ctx.db[c.card_id].level or 0) >= 2)
    if not count:
        return
    target = ctx.select_enemy()
    if target is not None:
        ctx.deal_damage(target, count)


# --- BS1-066 Lillybell Cookie -----------------------------------------------
@effect("BS1-066", Trigger.END_TURN)
def lillybell_end_turn(ctx: Ctx) -> None:
    """When your turn ends, set 1 card from your support area as active."""
    ctx.set_support_active(1)
