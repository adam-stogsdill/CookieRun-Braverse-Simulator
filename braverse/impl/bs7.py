"""Hand-written cards from BS7 — the 【Arena】 set.

Three shapes dominate: continuous stat buffs with no trigger to hang on,
"named Cookie A or B is in your battle area" conditions, and deck-viewing.
"""

from __future__ import annotations

from braverse.cost import Cost
from braverse.effects import (ATTACK_COST_MODIFIERS, EFFECT_DAMAGE_BONUSES,
                              STATIC_ABILITY_CARDS, Ctx, Trigger, effect)
from braverse.enums import Color, Keyword


def _named_in_battle(ctx: Ctx, *names: str) -> bool:
    return any(c.name(ctx.db) in names for c in ctx.me.battle)


def _arena_cards(ctx: Ctx, cards) -> int:
    return sum(1 for c in cards if Keyword.ARENA in ctx.db[c.card_id].keywords)


# --- BS7-013 / BS7-077 Chili Pepper Cookie ----------------------------------
def _chili_pepper_bonus(ctx: Ctx, source) -> int:
    """"If this Cookie is in the battle area, your {R} 【Arena】 Cookies that are
    LV.2 or higher deal +1 effect damage."

    A continuous ability, so it is registered as an effect-damage modifier
    rather than a trigger: it has to apply to *other* Cookies' damage.
    """
    if source is None:
        return 0
    if not any(c.defn(ctx.db).base_id in ("BS7-013", "BS7-077")
               for c in ctx.me.battle):
        return 0
    defn = source.defn(ctx.db)
    if (defn.color is Color.RED and Keyword.ARENA in defn.keywords
            and (defn.level or 0) >= 2):
        return 1
    return 0


EFFECT_DAMAGE_BONUSES.append(_chili_pepper_bonus)
STATIC_ABILITY_CARDS.update(("BS7-013", "BS7-077"))


# --- BS7-014 Capsaicin Cookie -----------------------------------------------
@effect("BS7-014", Trigger.ATTACK_START)
def capsaicin_static(ctx: Ctx) -> None:
    """If [Kouign-Amann Cookie] or [Prune Juice Cookie] are in your battle area,
    this Cookie gains +1 attack damage."""
    if ctx.source_cookie and _named_in_battle(
            ctx, "Kouign-Amann Cookie", "Prune Juice Cookie"):
        ctx.modify_attack(ctx.source_cookie, 1)


# --- BS7-035 Kouign-Amann Cookie --------------------------------------------
@effect("BS7-035", Trigger.ON_PLAY)
def kouign_amann_on_play(ctx: Ctx) -> None:
    """When [Capsaicin Cookie] or [Prune Juice Cookie] is in your battle area,
    this Cookie gains +1 HP."""
    if ctx.source_cookie and _named_in_battle(
            ctx, "Capsaicin Cookie", "Prune Juice Cookie"):
        ctx.gain_hp(ctx.source_cookie, 1)


@effect("BS7-035", Trigger.ACTIVATE)
def kouign_amann_activate(ctx: Ctx) -> None:
    """<{N}> <Rest this card.> When another 【Arena】 Cookie is in your battle
    area, select up to 1 of your opponent's Cookies. That Cookie receives 1
    damage."""
    cookie = ctx.source_cookie
    if cookie is None or cookie.rested:
        return
    if not ctx.pay(Cost.parse("{N}")):
        return
    cookie.rested = True
    if not any(c is not cookie and Keyword.ARENA in c.defn(ctx.db).keywords
               for c in ctx.me.battle):
        return
    target = ctx.select_enemy()
    if target is not None:
        ctx.deal_damage(target, 1)


# --- BS7-038 Clotted Cream Cookie -------------------------------------------
@effect("BS7-038", Trigger.ATTACK)
def clotted_cream_attack(ctx: Ctx) -> None:
    """Then, <place 1 Cookie from your hand into your break area.> Select up to
    1 LV.1 【Arena】 Cookie in your break area other than the placed Cookie.
    Return that Cookie to your hand."""
    cookies = [c for c in ctx.me.hand if ctx.db[c.card_id].is_cookie]
    if not cookies:
        return
    paid = ctx.choose("Send a Cookie from hand to your break area", cookies,
                      optional=True)
    if paid is None:
        return
    ctx.me.hand.remove(paid)
    ctx.me.break_area.append(paid)
    ctx.game._check_win()
    if ctx.state.over:
        return

    options = [c for c in ctx.me.break_area
               if c is not paid and (ctx.db[c.card_id].level or 0) == 1
               and Keyword.ARENA in ctx.db[c.card_id].keywords]
    if not options:
        return
    card = ctx.choose("Return an 【Arena】 Cookie to your hand", options,
                      optional=True)
    if card is not None:
        ctx.me.break_area.remove(card)
        ctx.me.hand.append(card)


# --- BS7-049 Strawberry Crepe Cookie ----------------------------------------
@effect("BS7-049", Trigger.ACTIVATE)
def strawberry_crepe_activate(ctx: Ctx) -> None:
    """<{G}> <Place 1 【Arena】 card from your support area into the trash.> If
    there are 6 cards or less in your hand, draw 1 card from your deck and
    place 1 card from the top of your deck in your support area as rested."""
    if not ctx.pay(Cost.parse("{G}")):
        return
    arena = [c for c in ctx.me.support
             if Keyword.ARENA in ctx.db[c.card_id].keywords]
    if not arena:
        return
    card = ctx.choose("Trash an 【Arena】 support card", arena, optional=False) or arena[0]
    ctx.me.support.remove(card)
    ctx.me.trash.append(card)
    if ctx.hand_size <= 6:
        ctx.draw(1)
        ctx.mill_to_support(1)


# --- BS7-051 Mint Choco Cookie ----------------------------------------------
@effect("BS7-051", Trigger.ACTIVATE)
def mint_choco_activate(ctx: Ctx) -> None:
    """<Place 1 【Arena】 card from your support area into the trash.> View 3
    cards from the top of your deck and place up to 1 of them in your support
    area as rested. Then, place the rest at the bottom of your deck."""
    arena = [c for c in ctx.me.support
             if Keyword.ARENA in ctx.db[c.card_id].keywords]
    if not arena:
        return
    paid = ctx.choose("Trash an 【Arena】 support card", arena, optional=False) or arena[0]
    ctx.me.support.remove(paid)
    ctx.me.trash.append(paid)

    viewed = ctx.me.deck[:3]
    if not viewed:
        return
    del ctx.me.deck[:len(viewed)]
    picked = ctx.choose("Place a card into your support area", list(viewed),
                        optional=True)
    if picked is not None:
        viewed.remove(picked)
        picked.rested = True
        ctx.me.support.append(picked)
    ctx.me.deck.extend(viewed)


# --- BS7-057 Pudding à la Mode Cookie ---------------------------------------
@effect("BS7-057", Trigger.ACTIVATE)
def pudding_activate(ctx: Ctx) -> None:
    """<{G}{G}> <Select 1 LV.2 or higher 【Arena】 Cookie from your support
    area.> Place this card in your support area as rested. Then, play that
    Cookie."""
    options = [c for c in ctx.me.support
               if (ctx.db[c.card_id].level or 0) >= 2
               and Keyword.ARENA in ctx.db[c.card_id].keywords]
    if not options or not ctx.pay(Cost.parse("{G}{G}")):
        return
    target = ctx.choose("Play which 【Arena】 Cookie from your support area?",
                        options, optional=True)
    if target is None:
        return

    cookie = ctx.source_cookie
    if cookie is not None and cookie in ctx.me.battle:
        ctx.me.battle.remove(cookie)
        cookie.card.rested = True
        ctx.me.support.append(cookie.card)
        ctx.me.trash.extend(cookie.hp_cards)

    ctx.me.support.remove(target)
    ctx.game._deploy_cookie(ctx.me, target, from_zone="support")


# --- BS7-058 Schwarzwälder --------------------------------------------------
@effect("BS7-058", Trigger.ATTACK_START)
def schwarzwalder_static(ctx: Ctx) -> None:
    """If there are 5 【Arena】 cards or more in your support area, this Cookie
    gains +2 attack damage."""
    if ctx.source_cookie and _arena_cards(ctx, ctx.me.support) >= 5:
        ctx.modify_attack(ctx.source_cookie, 2)


# --- BS7-059 Choco Drizzle Cookie -------------------------------------------
@effect("BS7-059", Trigger.PLAYED_FROM_SUPPORT)
def choco_drizzle_played(ctx: Ctx) -> None:
    """When this Cookie is played from the support area, select up to 1 of your
    opponent's Cookies. That Cookie receives 2 damage."""
    target = ctx.select_enemy()
    if target is not None:
        ctx.deal_damage(target, 2)


@effect("BS7-059", Trigger.ATTACK)
def choco_drizzle_attack(ctx: Ctx) -> None:
    """Then, <place 1 card from your support area into the trash.> Select up to
    1 {G} LV.2 or lower Cookie in your battle area. Place that Cookie in your
    support area as active."""
    if not ctx.me.support:
        return
    paid = ctx.choose("Trash a support card", list(ctx.me.support),
                      optional=True)
    if paid is None:
        return
    ctx.me.support.remove(paid)
    ctx.me.trash.append(paid)

    target = ctx.select_own(
        lambda c: c.defn(ctx.db).color is Color.GREEN and c.level(ctx.db) <= 2)
    if target is None:
        return
    ctx.me.battle.remove(target)
    target.card.rested = False
    ctx.me.support.append(target.card)
    ctx.me.trash.extend(target.hp_cards)
    ctx.game._check_battle_area(ctx.me)


# --- BS7-079 Cream Soda Cookie ----------------------------------------------
@effect("BS7-079", Trigger.WHEN_ATTACKED)
def cream_soda_when_attacked(ctx: Ctx) -> None:
    """【Once Per Turn】 When your opponent's Cookie attacks, <discard 1 card.>
    Select up to 1 of your opponent's Cookies. During this turn, that Cookie
    deals -1 attack damage."""
    if not ctx.discard(1, optional=True):
        return
    target = ctx.select_enemy(prompt="Debuff which attacker?")
    if target is not None:
        ctx.modify_attack(target, -1)


# --- BS7-090 Black Sapphire Cookie ------------------------------------------
@effect("BS7-090", Trigger.FAINT)
def black_sapphire_faint(ctx: Ctx) -> None:
    """When this Cookie faints, <discard 2 cards.> View 5 cards from the top of
    your deck, reveal up to 2 【Arena】 cards from the viewed cards, and add
    those to your hand. Then, place the remaining cards in the trash."""
    if not ctx.discard(2, optional=True):
        return
    viewed = ctx.me.deck[:5]
    if not viewed:
        return
    del ctx.me.deck[:len(viewed)]
    for _ in range(2):
        options = [c for c in viewed
                   if Keyword.ARENA in ctx.db[c.card_id].keywords]
        if not options:
            break
        card = ctx.choose("Add an 【Arena】 card to your hand", options,
                          optional=True)
        if card is None:
            break
        viewed.remove(card)
        ctx.me.hand.append(card)
    ctx.me.trash.extend(viewed)


# --- BS7-094 Tea Knight Cookie ----------------------------------------------
@effect("BS7-094", Trigger.ATTACK_START)
def tea_knight_static(ctx: Ctx) -> None:
    """If there are 30 【Arena】 cards or more in your trash, this Cookie gains
    +3 attack damage."""
    if ctx.source_cookie and _arena_cards(ctx, ctx.me.trash) >= 30:
        ctx.modify_attack(ctx.source_cookie, 3)


# --- BS7-104 Prune Juice Cookie ---------------------------------------------
def _prune_juice_cost(db, player, cookie, cost):
    """"If your break area is LV.3 or higher, or if there is a [Capsaicin
    Cookie] or [Kouign-Amann Cookie] in your battle area, each cost required
    for this Cookie's attack becomes {N}."

    A cost rewrite rather than a trigger — the cost is consulted before any
    effect could fire, so it hooks the payment path directly.
    """
    if cookie.defn(db).base_id != "BS7-104":
        return None
    names = {"Capsaicin Cookie", "Kouign-Amann Cookie"}
    qualifies = (player.break_level_total(db) >= 3
                 or any(c.name(db) in names for c in player.battle))
    if not qualifies:
        return None
    return Cost(generic=cost.total)


ATTACK_COST_MODIFIERS.append(_prune_juice_cost)
STATIC_ABILITY_CARDS.add("BS7-104")
