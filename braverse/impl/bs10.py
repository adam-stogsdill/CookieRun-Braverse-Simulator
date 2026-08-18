"""Hand-written cards from BS10.

The set introduces cost reduction, attack prohibition, and 【Equip】 — an
attachment that grants a continuous benefit until it is removed.
"""

from __future__ import annotations

from braverse.cost import Cost
from braverse.effects import (ATTACK_COST_MODIFIERS, ATTACK_PROHIBITIONS,
                              MOVEMENT_PROTECTORS, STATIC_ABILITY_CARDS,
                              Ctx, Trigger, effect)
from braverse.enums import Color


# --- BS10-021 / BS10-024 Hollyberry Cookie ----------------------------------
# "If this Cookie's remaining HP is 3 or less, this Cookie cannot attack."
#
# A prohibition rather than a trigger, so it is enforced where attacks are
# enumerated. `CANNOT_ATTACK_WHEN` entries take (db, cookie) and return True to
# forbid that Cookie from attacking.
def _hollyberry_cannot_attack(db, cookie) -> bool:
    return (cookie.defn(db).base_id in ("BS10-021", "BS10-024")
            and cookie.remaining_hp <= 3)


ATTACK_PROHIBITIONS.append(_hollyberry_cannot_attack)
STATIC_ABILITY_CARDS.update(("BS10-021", "BS10-024"))


# --- BS10-009 Cranberry Cookie ----------------------------------------------
def _cranberry_cost(db, player, cookie, cost):
    """During this turn, the attack cost of this Cookie is reduced by 1 {R}."""
    if cookie.defn(db).base_id != "BS10-009" or not cookie.attack_cost_discount:
        return None
    colored = []
    removed = False
    for color, count in cost.colored:
        if color is Color.RED and not removed and count:
            count -= 1
            removed = True
        if count:
            colored.append((color, count))
    return Cost(tuple(colored), cost.generic) if removed else None


ATTACK_COST_MODIFIERS.append(_cranberry_cost)


@effect("BS10-009", Trigger.ACTIVATE)
def cranberry_activate(ctx: Ctx) -> None:
    """<Place 1 card from the top of your LV.2 or higher Cookie's HP into your
    trash.> During this turn, the attack cost of this Cookie is reduced by
    1 {R}."""
    fodder = ctx.select_own(lambda c: c.level(ctx.db) >= 2 and c.remaining_hp >= 1,
                            prompt="Pay 1 HP from which LV.2+ Cookie?")
    if fodder is None:
        return
    ctx.trash_hp(fodder, 1)
    if ctx.source_cookie is not None:
        ctx.source_cookie.attack_cost_discount += 1


# --- BS10-014 Facing the Past -----------------------------------------------
@effect("BS10-014", Trigger.ITEM)
def facing_the_past(ctx: Ctx) -> None:
    """Select up to 1 of your opponent's Cookies. During this turn, that Cookie
    deals -1 attack damage.

    The printed cost reduction ("if your Cookie fainted, this costs 1 {R}
    less") is applied by the engine when the trap's cost is paid, so the body
    only carries the effect.
    """
    target = ctx.select_enemy(prompt="Debuff which attacker?")
    if target is not None:
        ctx.modify_attack(target, -1)


# --- BS10-023 Wildberry Cookie ----------------------------------------------
@effect("BS10-023", Trigger.ACTIVATE)
def wildberry_activate(ctx: Ctx) -> None:
    """<{R}> Select up to 1 of your opponent's stage cards. Place that card in
    your opponent's trash."""
    if ctx.pay(Cost.parse("{R}")):
        ctx.trash_stage(1, mine=False)


@effect("BS10-023", Trigger.ATTACK)
def wildberry_attack(ctx: Ctx) -> None:
    """Then, <discard 1 card.> If your opponent's Cookie faints from this
    Cookie's attack, this Cookie gains +1 HP."""
    if not getattr(ctx.game, "_attack_killed", False):
        return
    if ctx.discard(1, optional=True) and ctx.source_cookie:
        ctx.gain_hp(ctx.source_cookie, 1)


# --- BS10-038 Passion Meets Sloth -------------------------------------------
@effect("BS10-038", Trigger.ITEM)
def passion_meets_sloth(ctx: Ctx) -> None:
    """Select up to 1 of your opponent's Cookies. During this turn, that Cookie
    deals -2 attack damage. Then, if a Cookie has more remaining HP than its
    original HP, the selected Cookie deals an additional -1."""
    target = ctx.select_enemy(prompt="Debuff which attacker?")
    if target is None:
        return
    ctx.modify_attack(target, -2)
    everyone = list(ctx.me.battle) + list(ctx.opp.battle)
    if any(c.remaining_hp > (c.defn(ctx.db).hp or 0) for c in everyone):
        ctx.modify_attack(target, -1)


# --- BS10-045 / BS10-119 Soul Jam equipment ---------------------------------
def _equip_soul_jam(ctx: Ctx, holder_name: str) -> None:
    """【Equip】 this card to your [named Cookie].

    Equipping moves the card out of play into the Cookie's attachments, where
    it stays until that Cookie leaves the battle area.
    """
    card = ctx.source_card
    if card is None:
        return
    holder = ctx.select_own(lambda c: c.name(ctx.db) == holder_name,
                            prompt=f"Equip to {holder_name}?")
    if holder is None:
        return
    if card in ctx.me.trash:
        ctx.me.trash.remove(card)
    holder.equipment.append(card)


@effect("BS10-045", Trigger.ITEM)
def soul_jam_sloth(ctx: Ctx) -> None:
    """<{Y}> Select up to 1 of your {Y} LV.3 Cookies. That Cookie gains +1 HP.
    Then, <{Y}> You can 【Equip】 this card to your [Eternal Sugar Cookie]."""
    target = ctx.select_own(
        lambda c: c.defn(ctx.db).color is Color.YELLOW and c.level(ctx.db) == 3)
    if target is not None:
        ctx.gain_hp(target, 1)
    if ctx.can_pay(Cost.parse("{Y}")) and ctx.pay(Cost.parse("{Y}")):
        _equip_soul_jam(ctx, "Eternal Sugar Cookie")


@effect("BS10-119", Trigger.ITEM)
def soul_jam_silence(ctx: Ctx) -> None:
    """<{P}> If you refreshed during this game, select up to 1 of your
    opponent's Cookies. That Cookie receives 1 damage. Then, <{P}> You can
    【Equip】 this card to your [Silent Salt Cookie]."""
    if ctx.me.refreshed:
        target = ctx.select_enemy()
        if target is not None:
            ctx.deal_damage(target, 1)
    if ctx.can_pay(Cost.parse("{P}")) and ctx.pay(Cost.parse("{P}")):
        _equip_soul_jam(ctx, "Silent Salt Cookie")


# --- BS10-046 Sugarfly Cookie -----------------------------------------------
@effect("BS10-046", Trigger.ACTIVATE)
def sugarfly_activate(ctx: Ctx) -> None:
    """If there are 6 cards or less in your hand and, during this turn, any of
    your Cookies gained HP, draw up to 1 card from your deck."""
    if ctx.hand_size <= 6 and ctx.me.hp_gained_this_turn:
        ctx.draw(1)


# --- BS10-048 Warden of the Heart -------------------------------------------
@effect("BS10-048", Trigger.ACTIVATE)
def warden_activate(ctx: Ctx) -> None:
    """<{Y}> All Cookies receive 1 damage."""
    if not ctx.pay(Cost.parse("{Y}")):
        return
    for cookie in list(ctx.me.battle) + list(ctx.opp.battle):
        ctx.deal_damage(cookie, 1)


@effect("BS10-048", Trigger.ATTACK)
def warden_attack(ctx: Ctx) -> None:
    """Then, until the end of your opponent's turn, all of your Cookies receive
    -1 damage from effects."""
    for cookie in ctx.me.battle:
        cookie.effect_damage_reduction += 1


# --- static attack buffs ----------------------------------------------------
@effect("BS10-054", Trigger.ATTACK_START)
def avocado_static(ctx: Ctx) -> None:
    """If there are 7 cards or more in your support area, this Cookie gains +1
    attack damage."""
    if ctx.source_cookie and ctx.support_count() >= 7:
        ctx.modify_attack(ctx.source_cookie, 1)


@effect("BS10-081", Trigger.ATTACK_START)
def cappuccino_static(ctx: Ctx) -> None:
    """If there are 7 cards or more in your hand, this Cookie gains +1 attack
    damage."""
    if ctx.source_cookie and ctx.hand_size >= 7:
        ctx.modify_attack(ctx.source_cookie, 1)


@effect("BS10-105", Trigger.ATTACK_START)
def truffle_purple_static(ctx: Ctx) -> None:
    """If you refreshed during this game, this Cookie gains +1 attack damage."""
    if ctx.source_cookie and ctx.me.refreshed:
        ctx.modify_attack(ctx.source_cookie, 1)


# --- BS10-068 A Single Lily -------------------------------------------------
@effect("BS10-068", Trigger.ITEM)
def a_single_lily(ctx: Ctx) -> None:
    """<Place 1 [White Lily Cookie] from your hand or your trash in your
    support area as rested.> Draw up to 1 card from your deck."""
    pool = [c for c in list(ctx.me.hand) + list(ctx.me.trash)
            if ctx.db[c.card_id].name == "White Lily Cookie"]
    if not pool:
        return
    card = ctx.choose("Move a White Lily Cookie into your support area", pool,
                      optional=True)
    if card is None:
        return
    (ctx.me.hand if card in ctx.me.hand else ctx.me.trash).remove(card)
    card.rested = True
    ctx.me.support.append(card)
    ctx.draw(1)


# --- BS10-070 Silverbell Cookie ---------------------------------------------
def _silverbell_protected(db, owner, cookie) -> bool:
    """"If there are 4 cards or less in your support area, this Cookie cannot
    be moved from the battle area by your opponent's effects."

    Protects only itself, and only while its controller's support area is
    small — narrower than a whole-player movement lock, so it uses the
    per-Cookie registry and reads the owner's zones.
    """
    return (cookie.defn(db).base_id == "BS10-070"
            and len(owner.support) <= 4)


MOVEMENT_PROTECTORS.append(_silverbell_protected)
STATIC_ABILITY_CARDS.add("BS10-070")


# --- BS10-088 Butterfly Lantern ---------------------------------------------
@effect("BS10-088", Trigger.ITEM)
def butterfly_lantern(ctx: Ctx) -> None:
    """View 1 card from the top of your deck and place it on the top or bottom
    of your deck. Then, draw up to 1 card from your deck."""
    if ctx.me.deck:
        top = ctx.me.deck[0]
        # Keep a Cookie on top to draw it; bury anything else.
        if not ctx.db[top.card_id].is_cookie:
            ctx.me.deck.pop(0)
            ctx.me.deck.append(top)
    ctx.draw(1)


# --- BS10-098 Jagae Cookie --------------------------------------------------
@effect("BS10-098", Trigger.ACTIVATE)
def jagae_activate(ctx: Ctx) -> None:
    """<Discard 1 card.> Select up to 1 of your Cookies. During this turn, that
    Cookie gains +1 attack damage."""
    if not ctx.discard(1, optional=True):
        return
    target = ctx.select_own()
    if target is not None:
        ctx.modify_attack(target, 1)


# --- BS10-109 Licorice Cookie -----------------------------------------------
@effect("BS10-109", Trigger.TRASHED)
def licorice_trashed(ctx: Ctx) -> None:
    """When this Cookie is placed from the battle area into the trash, place 3
    cards from the top of your deck into the trash."""
    for _ in range(3):
        if not ctx.me.deck:
            break
        ctx.me.trash.append(ctx.me.deck.pop(0))
