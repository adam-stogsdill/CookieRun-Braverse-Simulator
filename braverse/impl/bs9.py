"""Hand-written cards from BS9.

BS9 keys off *when* your Cookies died — its recursion payoffs read the previous
turn's losses — and adds blanket damage shields plus HP-stealing.

The set also leans on the Extra Deck, 【Special Play】 and 【Awaken】, none of
which the engine models. Cards resting on those stay unimplemented rather than
half-resolving.
"""

from __future__ import annotations

from braverse.cost import Cost
from braverse.effects import (OPPONENT_DAMAGE_SHIELDS, STATIC_ABILITY_CARDS,
                              Ctx, Trigger, effect)
from braverse.enums import Color, Keyword


def _faints_last_opponent_turn(ctx: Ctx, color=None, level=None) -> int:
    """How many of my Cookies fainted during the opponent's previous turn."""
    want = ctx.state.turn_counter - 1
    return sum(1 for turn, c, lv in ctx.me.faint_log
               if turn == want
               and (color is None or c is color)
               and (level is None or lv == level))


# --- BS9-002 Princess Cookie ------------------------------------------------
@effect("BS9-002", Trigger.ACTIVATE)
def princess_bs9_activate(ctx: Ctx) -> None:
    """If your {R} LV.1 Cookie fainted during your opponent's previous turn,
    this Cookie gains +1 attack damage during this turn."""
    if ctx.source_cookie and _faints_last_opponent_turn(ctx, Color.RED, 1):
        ctx.modify_attack(ctx.source_cookie, 1)


# --- BS9-006 Melted Choco Cookie --------------------------------------------
@effect("BS9-006", Trigger.ON_PLAY)
def melted_choco_on_play(ctx: Ctx) -> None:
    """During this turn, if 2 or more of your Cookies fainted, this Cookie
    takes -3 damage until the end of the turn."""
    if ctx.source_cookie and ctx.me.cookies_fainted_this_turn >= 2:
        ctx.source_cookie.effect_damage_reduction += 3
        ctx.source_cookie.incoming_damage_reduction += 3


# --- BS9-011 Devil Cookie ---------------------------------------------------
@effect("BS9-011", Trigger.ON_PLAY)
def devil_on_play(ctx: Ctx) -> None:
    """During this turn, if 2 or more of your {R} LV.1 Cookies fainted, select
    up to 1 of your opponent's Cookies. That Cookie receives 1 damage."""
    fainted = sum(1 for turn, c, lv in ctx.me.faint_log
                  if turn == ctx.state.turn_counter
                  and c is Color.RED and lv == 1)
    if fainted < 2:
        return
    target = ctx.select_enemy()
    if target is not None:
        ctx.deal_damage(target, 1)


# --- BS9-012 Knight Cookie --------------------------------------------------
@effect("BS9-012", Trigger.END_TURN)
def knight_end_turn(ctx: Ctx) -> None:
    """At the end of your opponent's turn, if there are 2 Cookies in your
    battle area, this Cookie receives 3 damage."""
    if len(ctx.me.battle) == 2 and ctx.source_cookie is not None:
        ctx.deal_damage(ctx.source_cookie, 3)


# --- BS9-017 Hollyberry Cookie ----------------------------------------------
@effect("BS9-017", Trigger.ACTIVATE)
def hollyberry_bs9_activate(ctx: Ctx) -> None:
    """<{N}> If there is another 【Ancient】 Cookie in your battle area, this
    Cookie gains +2 attack damage during this turn."""
    cookie = ctx.source_cookie
    if cookie is None or not ctx.pay(Cost.parse("{N}")):
        return
    if any(c is not cookie and Keyword.ANCIENT in c.defn(ctx.db).keywords
           for c in ctx.me.battle):
        ctx.modify_attack(cookie, 2)


@effect("BS9-017", Trigger.ATTACK)
def hollyberry_bs9_attack(ctx: Ctx) -> None:
    """Then, until the end of your opponent's turn, any time your 【Ancient】
    Cookie would receive 3 or more damage, the damage is reduced to 2."""
    for cookie in ctx.me.battle:
        if Keyword.ANCIENT in cookie.defn(ctx.db).keywords:
            cookie.damage_cap = 2


# --- BS9-018 Hero Cookie ----------------------------------------------------
def _hero_shield(db, owner) -> bool:
    """"If this Cookie is in your battle area, your Cookies take no damage from
    your opponent."

    A blanket shield while it is on the field, so it is consulted in the damage
    path rather than fired as a trigger.
    """
    return any(c.defn(db).base_id == "BS9-018" for c in owner.battle)


OPPONENT_DAMAGE_SHIELDS.append(_hero_shield)
STATIC_ABILITY_CARDS.add("BS9-018")


# --- BS9-020 Fateful Cookie Cutter ------------------------------------------
@effect("BS9-020", Trigger.ITEM)
def fateful_cookie_cutter(ctx: Ctx) -> None:
    """If your {R} LV.1 Cookie fainted during your opponent's previous turn,
    draw up to 2 cards from your deck and discard 1 card."""
    if not _faints_last_opponent_turn(ctx, Color.RED, 1):
        return
    if ctx.draw(2):
        ctx.discard(1, optional=True)


# --- BS9-021 Stolen Light of Truth ------------------------------------------
@effect("BS9-021", Trigger.ITEM)
def stolen_light_of_truth(ctx: Ctx) -> None:
    """Select up to 1 of your opponent's Cookies. Add 1 card from the top of
    that Cookie's HP face-up to the bottom of your Cookie's HP.

    HP theft: the card changes owner, which is what "your Cookie has an
    opponent's card as HP" elsewhere in the set keys off.
    """
    victim = ctx.select_enemy()
    mine = ctx.select_own()
    if victim is None or mine is None or not victim.hp_cards:
        return
    stolen = victim.hp_cards.pop()
    mine.hp_cards.insert(0, stolen)
    if not victim.hp_cards:
        ctx.game.faint(victim)


# --- BS9-024 Golden Cheese Cookie -------------------------------------------
@effect("BS9-024", Trigger.ACTIVATE)
def golden_cheese_bs9_activate(ctx: Ctx) -> None:
    """If this Cookie's remaining HP is 4 or less and another 【Ancient】 Cookie
    is in your battle area, this Cookie gains +1 HP."""
    cookie = ctx.source_cookie
    if cookie is None or cookie.remaining_hp > 4:
        return
    if any(c is not cookie and Keyword.ANCIENT in c.defn(ctx.db).keywords
           for c in ctx.me.battle):
        ctx.gain_hp(cookie, 1)


@effect("BS9-024", Trigger.ATTACK)
def golden_cheese_bs9_attack(ctx: Ctx) -> None:
    """Then, <return 1 card from the top of this Cookie's HP to your hand.>
    Deals 1 damage."""
    cookie = ctx.source_cookie
    if cookie is None or not cookie.hp_cards:
        return
    ctx.me.hand.append(cookie.hp_cards.pop())
    if not cookie.hp_cards:
        ctx.game.faint(cookie)
        return
    target = ctx.select_enemy()
    if target is not None:
        ctx.deal_damage(target, 1)


# --- BS9-027 Vampire Cookie -------------------------------------------------
@effect("BS9-027", Trigger.ACTIVATE)
def vampire_activate(ctx: Ctx) -> None:
    """Add up to 1 card from your hand to the top of this Cookie's HP. Then,
    this Cookie receives 1 damage."""
    cookie = ctx.source_cookie
    if cookie is None:
        return
    if ctx.me.hand:
        card = ctx.choose("Add a card from hand to this Cookie's HP",
                          list(ctx.me.hand), optional=True)
        if card is not None:
            ctx.me.hand.remove(card)
            card.face_up = False
            cookie.hp_cards.append(card)
    ctx.deal_damage(cookie, 1)


# --- BS9-031 Alchemist Cookie (FLIP) ----------------------------------------
@effect("BS9-031", Trigger.FLIP)
def alchemist_flip(ctx: Ctx) -> None:
    """One of your LV.3 Cookies gains +1 HP."""
    target = ctx.select_own(lambda c: c.level(ctx.db) == 3)
    if target is not None:
        ctx.gain_hp(target, 1)


# --- BS9-034 Fortune Teller Cookie ------------------------------------------
@effect("BS9-034", Trigger.ON_PLAY)
def fortune_teller_on_play(ctx: Ctx) -> None:
    """<{Y}> Select up to 1 of your opponent's Cookies. View all of that
    Cookie's HP cards and rearrange them in any order."""
    if not ctx.pay(Cost.parse("{Y}")):
        return
    target = ctx.select_enemy()
    if target is not None:
        # Bury their FLIPs so damage does not turn them up.
        target.hp_cards.sort(key=lambda c: not ctx.db[c.card_id].is_flip)


# --- BS9-035 Truthless Recluse ----------------------------------------------
@effect("BS9-035", Trigger.ACTIVATE)
def truthless_recluse_activate(ctx: Ctx) -> None:
    """<Discard 1 card.> During this turn, your opponent cannot add HP to
    Cookies via card effects."""
    if ctx.discard(1, optional=True):
        ctx.opp.hp_gain_locked = True


# --- BS9-036 Bookseller -----------------------------------------------------
@effect("BS9-036", Trigger.END_TURN)
def bookseller_end_turn(ctx: Ctx) -> None:
    """When your turn ends, discard 1 Cookie that has FLIP from your hand or
    place 1 card from the top of this Cookie's HP into your trash."""
    flips = [c for c in ctx.me.hand if ctx.db[c.card_id].is_flip]
    cookie = ctx.source_cookie
    if flips:
        card = ctx.choose("Discard a FLIP Cookie instead of paying HP?", flips,
                          optional=True)
        if card is not None:
            ctx.me.hand.remove(card)
            ctx.me.trash.append(card)
            return
    if cookie is not None:
        ctx.trash_hp(cookie, 1)


# --- BS9-038 Chess Choco Cookie ---------------------------------------------
@effect("BS9-038", Trigger.ON_PLAY)
def chess_choco_on_play(ctx: Ctx) -> None:
    """If there is another [Chess Choco Cookie] in your battle area, all your
    Cookies gain +1 HP."""
    others = [c for c in ctx.me.battle
              if c is not ctx.source_cookie
              and c.name(ctx.db) == "Chess Choco Cookie"]
    if not others:
        return
    for cookie in list(ctx.me.battle):
        ctx.gain_hp(cookie, 1)


# --- BS9-052 Ring Candy Cookie ----------------------------------------------
@effect("BS9-052", Trigger.ATTACK_START)
def ring_candy_static(ctx: Ctx) -> None:
    """If there are 7 cards or more in your support area, this Cookie gains +1
    attack damage."""
    if ctx.source_cookie and ctx.support_count() >= 7:
        ctx.modify_attack(ctx.source_cookie, 1)


# --- BS9-043 Heart Stained With Lies ----------------------------------------
@effect("BS9-043", Trigger.ITEM)
def heart_stained_with_lies(ctx: Ctx) -> None:
    """<{Y}> If your break area is LV.4 or higher, select up to 1 of your
    opponent's Equipped [Soul Jam]. Place that card on top of the Equipped
    Cookie's HP."""
    if ctx.me.break_level_total(ctx.db) < 4:
        return
    if not ctx.pay(Cost.parse("{Y}")):
        return
    holders = [c for c in ctx.opp.battle if c.equipment]
    if not holders:
        return
    holder = ctx.choose("Strip which Cookie's equipment?", holders, optional=True)
    if holder is None:
        return
    card = holder.equipment.pop()
    card.face_up = False
    holder.hp_cards.append(card)
