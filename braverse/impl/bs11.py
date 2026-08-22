"""Hand-written cards from BS11.

BS11 is the deepest set: it introduces 【Special Play】, 【Awaken】, the Extra
Deck, and replacement effects that rewrite an opponent's abilities. Those four
are not modelled, so the cards resting on them stay unimplemented rather than
half-resolving. Everything else is here.
"""

from __future__ import annotations

from braverse.cost import Cost
from braverse.effects import (MOVEMENT_PROTECTORS, STATIC_ABILITY_CARDS,
                              Ctx, Trigger, effect)
from braverse.enums import CardType, Color, Keyword


def _self_break(ctx: Ctx) -> bool:
    cookie = ctx.source_cookie
    if cookie is None or cookie not in ctx.me.battle:
        return False
    ctx.faint(cookie)
    return not ctx.state.over


# --- BS11-002 Macaron Cookie ------------------------------------------------
@effect("BS11-002", Trigger.ACTIVATE)
def macaron_activate(ctx: Ctx) -> None:
    """<{R}> <Discard 1 {R} Item card from your hand.> Draw 1 card from your
    deck and select 1 of your opponent's Cookies. That Cookie receives 1
    damage."""
    items = [c for c in ctx.me.hand
             if ctx.db[c.card_id].color is Color.RED
             and ctx.db[c.card_id].type is CardType.ITEM]
    if not items or not ctx.pay(Cost.parse("{R}")):
        return
    paid = ctx.choose("Discard a {R} Item card", items, optional=False) or items[0]
    ctx.me.hand.remove(paid)
    ctx.me.trash.append(paid)
    ctx.draw(1)
    target = ctx.select_enemy()
    if target is not None:
        ctx.deal_damage(target, 1)


# --- BS11-016 Fire Spirit Cookie --------------------------------------------
@effect("BS11-016", Trigger.ACTIVATE)
def fire_spirit_activate(ctx: Ctx) -> None:
    """<Place a total of 2 cards from the top of your {R} Cookies' HP into your
    trash.> All of your opponent's Cookies receive 1 damage."""
    paid = 0
    for _ in range(2):
        options = [c for c in ctx.me.battle
                   if c.defn(ctx.db).color is Color.RED and c.remaining_hp >= 1]
        if not options:
            break
        fodder = ctx.choose("Pay 1 HP from which {R} Cookie?", options,
                            optional=False) or options[0]
        ctx.trash_hp(fodder, 1)
        paid += 1
    if paid < 2:
        return
    for cookie in list(ctx.enemy_cookies()):
        ctx.deal_damage(cookie, 1)


# --- BS11-018 Burning Spice Cookie ------------------------------------------
@effect("BS11-018", Trigger.ACTIVATE)
def burning_spice_blue_activate(ctx: Ctx) -> None:
    """<Make 1 of your {R} Cookies faint.> If there are 5 cards or less in your
    hand, draw up to 2 cards from your deck."""
    fodder = ctx.select_own(lambda c: c.defn(ctx.db).color is Color.RED,
                            prompt="Make which {R} Cookie faint?")
    if fodder is None:
        return
    ctx.faint(fodder)
    if not ctx.state.over and ctx.hand_size <= 5:
        ctx.draw(2)


@effect("BS11-018", Trigger.ATTACK)
def burning_spice_blue_attack(ctx: Ctx) -> None:
    """Then, <make 1 of your Cookies faint.> Deals 1 damage."""
    fodder = ctx.select_own(prompt="Make which Cookie faint?")
    if fodder is None:
        return
    ctx.faint(fodder)
    if ctx.state.over:
        return
    target = ctx.select_enemy()
    if target is not None:
        ctx.deal_damage(target, 1)


# --- BS11-025 Fettuccine Cookie ---------------------------------------------
@effect("BS11-025", Trigger.ATTACK)
def fettuccine_yellow_attack(ctx: Ctx) -> None:
    """Then, draw up to 2 cards from your deck and place this Cookie in your
    break area."""
    ctx.draw(2)
    _self_break(ctx)


# --- BS11-031 Winged Tree ---------------------------------------------------
@effect("BS11-031", Trigger.ITEM)
def winged_tree(ctx: Ctx) -> None:
    """<{Y}> If there are 4 or more Cookies in your break area, select up to 1
    of your opponent's Cookies. Until the end of your opponent's next turn,
    that Cookie's 【Activate】 cannot be used."""
    if len(ctx.opp.battle) == 0 or len(ctx.me.break_area) < 4:
        return
    target = ctx.select_enemy()
    if target is not None:
        target.activate_locked = True


# --- BS11-032 Burnt Cheese Cookie -------------------------------------------
@effect("BS11-032", Trigger.END_TURN)
def burnt_cheese_bs11_end_turn(ctx: Ctx) -> None:
    """Can be activated when your turn ends. During this turn, if a LV.3 Cookie
    was played from your break area, place this Cookie in your trash."""
    if not ctx.me.played_from_break_this_turn:
        return
    played_lv3 = any(c.level(ctx.db) == 3 and c.uid in ctx.me.played_from_break_this_turn
                     for c in ctx.me.battle)
    if played_lv3 and ctx.source_cookie is not None:
        ctx.trash_cookie(ctx.source_cookie)


@effect("BS11-032", Trigger.ATTACK)
def burnt_cheese_bs11_attack(ctx: Ctx) -> None:
    """Then, <place 1 other {Y} Cookie from your battle area into your break
    area.> Draw up to 1 card from your deck."""
    fodder = ctx.select_own(
        lambda c: c is not ctx.source_cookie
        and c.defn(ctx.db).color is Color.YELLOW)
    if fodder is None:
        return
    ctx.faint(fodder)
    if not ctx.state.over:
        ctx.draw(1)


# --- BS11-034 Golden Cheese Cookie ------------------------------------------
@effect("BS11-034", Trigger.ATTACK)
def golden_cheese_bs11_attack(ctx: Ctx) -> None:
    """Then, <{N}> deals 1 damage for each LV.2 or higher 【Ancient】 Cookie in
    your break area."""
    count = sum(1 for c in ctx.me.break_area
                if (ctx.db[c.card_id].level or 0) >= 2
                and Keyword.ANCIENT in ctx.db[c.card_id].keywords)
    if not count or not ctx.pay(Cost.parse("{N}")):
        return
    target = ctx.select_enemy()
    if target is not None:
        ctx.deal_damage(target, count)


# --- BS11-036 Eternal Sugar Cookie ------------------------------------------
@effect("BS11-036", Trigger.ACTIVATE)
def eternal_sugar_activate(ctx: Ctx) -> None:
    """If there is no other [Eternal Sugar Cookie] in your battle area, select
    up to 1 of your opponent's Cookies. Until the end of your opponent's next
    turn, the attack cost of that Cookie is increased by 1 {N}."""
    others = [c for c in ctx.me.battle
              if c is not ctx.source_cookie
              and c.name(ctx.db) == "Eternal Sugar Cookie"]
    if others:
        return
    target = ctx.select_enemy()
    if target is not None:
        target.attack_cost_surcharge += 1


@effect("BS11-036", Trigger.ATTACK)
def eternal_sugar_attack(ctx: Ctx) -> None:
    """Then, if this Cookie's remaining HP is 3 or less, this Cookie gains
    +1 HP."""
    cookie = ctx.source_cookie
    if cookie is not None and cookie.remaining_hp <= 3:
        ctx.gain_hp(cookie, 1)


# --- BS11-044 Silverbell Cookie ---------------------------------------------
@effect("BS11-044", Trigger.ATTACK_START)
def silverbell_bs11_static(ctx: Ctx) -> None:
    """If there are 7 cards or more in your support area, this Cookie gains +1
    attack damage."""
    if ctx.source_cookie and ctx.support_count() >= 7:
        ctx.modify_attack(ctx.source_cookie, 1)


# --- BS11-053 Mystic Flour Cookie -------------------------------------------
@effect("BS11-053", Trigger.ACTIVATE)
def mystic_flour_bs11_activate(ctx: Ctx) -> None:
    """<{G}> Place 1 card from the top of the HP of all your opponent's Cookies
    with 5 or more remaining HP into your opponent's trash."""
    if not ctx.pay(Cost.parse("{G}")):
        return
    for cookie in list(ctx.enemy_cookies()):
        if cookie.remaining_hp >= 5:
            ctx.trash_hp(cookie, 1)


@effect("BS11-053", Trigger.ATTACK)
def mystic_flour_bs11_attack(ctx: Ctx) -> None:
    """Then, if there are less cards in your support area than your opponent's,
    <place this Cookie in your trash.> Place 1 card from the top of your deck
    into your support area as rested."""
    if len(ctx.me.support) >= len(ctx.opp.support):
        return
    cookie = ctx.source_cookie
    if cookie is None:
        return
    # Trashing your own attacker is a price, not a consequence: the brackets
    # make it a choice even though the attack that triggered this was one.
    if not ctx.wants_to_pay("place this Cookie in your trash."):
        return
    ctx.trash_cookie(cookie)
    ctx.mill_to_support(1)


# --- BS11-058 Cream Soda Cookie ---------------------------------------------
@effect("BS11-058", Trigger.WHEN_ATTACKED)
def cream_soda_bs11_when_attacked(ctx: Ctx) -> None:
    """When one of your opponent's Cookies attacks, <discard 2 cards.> Select
    up to 1 of your Cookies. That Cookie gains +1 HP."""
    if not ctx.discard(2, optional=True):
        return
    target = ctx.select_own()
    if target is not None:
        ctx.gain_hp(target, 1)


# --- BS11-062 Top of the Spire of Deceit ------------------------------------
@effect("BS11-062", Trigger.STAGE_ACTIVATE)
def spire_of_deceit(ctx: Ctx) -> None:
    """<Place this card in your trash.> View all cards in your opponent's hand.

    Purely informational: the card moves no game state, and both controllers
    already reason over the full state when they are asked a question. The
    stage still pays its own cost by going to the trash.
    """
    card = ctx.source_card
    if card is not None and card in ctx.me.stage:
        ctx.me.stage.remove(card)
        ctx.me.trash.append(card)


# --- BS11-066 Milk Lake of Truth --------------------------------------------
@effect("BS11-066", Trigger.ITEM)
def milk_lake_of_truth(ctx: Ctx) -> None:
    """Select up to 1 of your opponent's Cookies. During this turn, that Cookie
    deals -1 attack damage. Then, view 1 card from the top of your opponent's
    deck and place that card on the top or bottom of their deck."""
    target = ctx.select_enemy(prompt="Debuff which attacker?")
    if target is not None:
        ctx.modify_attack(target, -1)
    if ctx.opp.deck:
        top = ctx.opp.deck[0]
        # Bury anything that would help them; leave dead cards on top.
        if ctx.db[top.card_id].is_cookie:
            ctx.opp.deck.pop(0)
            ctx.opp.deck.append(top)


# --- BS11-068 Black Sapphire Cookie -----------------------------------------
@effect("BS11-068", Trigger.FAINT)
def black_sapphire_bs11_faint(ctx: Ctx) -> None:
    """When this Cookie faints, <{B}> select up to 1 of your opponent's
    Cookies. Place 1 card from the top of that Cookie's HP on the bottom of
    their deck."""
    if not ctx.pay(Cost.parse("{B}")):
        return
    target = ctx.select_enemy()
    if target is None:
        return
    if target.hp_cards:
        ctx.opp.deck.append(target.hp_cards.pop())
        if not target.hp_cards:
            ctx.game.faint(target)


# --- BS11-069 Sea Fairy Cookie ----------------------------------------------
@effect("BS11-069", Trigger.WHEN_ATTACKED)
def sea_fairy_bs11_when_attacked(ctx: Ctx) -> None:
    """Can be activated when one of your opponent's Cookies attacks. If there
    are 5 cards or less in your hand, draw up to 2 cards from your deck and
    place 1 card from your hand into your support area as rested."""
    if ctx.hand_size > 5:
        return
    ctx.draw(2)
    if not ctx.me.hand:
        return
    card = ctx.choose("Place a card into your support area", list(ctx.me.hand),
                      optional=True)
    if card is not None:
        ctx.me.hand.remove(card)
        card.rested = True
        ctx.me.support.append(card)


# --- BS11-070 Pure Vanilla Cookie -------------------------------------------
@effect("BS11-070", Trigger.ON_PLAY)
def pure_vanilla_on_play(ctx: Ctx) -> None:
    """<Discard 1 【Ancient】 Cookie from your hand.> Draw up to 2 cards from
    your deck."""
    ancients = [c for c in ctx.me.hand
                if ctx.db[c.card_id].is_cookie
                and Keyword.ANCIENT in ctx.db[c.card_id].keywords]
    if not ancients:
        return
    paid = ctx.choose("Discard an 【Ancient】 Cookie", ancients,
                      optional=False) or ancients[0]
    ctx.me.hand.remove(paid)
    ctx.me.trash.append(paid)
    ctx.draw(2)


@effect("BS11-070", Trigger.ATTACK)
def pure_vanilla_attack(ctx: Ctx) -> None:
    """Then, <{N}> place up to 1 other Cookie that is LV.2 or lower from your
    battle area on the top or bottom of your deck."""
    target = ctx.select_own(
        lambda c: c is not ctx.source_cookie and c.level(ctx.db) <= 2)
    if target is None or not ctx.pay(Cost.parse("{N}")):
        return
    ctx.me.battle.remove(target)
    ctx.me.deck.insert(0, target.card)     # top: replay it sooner
    ctx.me.trash.extend(target.spent_cards)
    ctx.game._check_battle_area(ctx.me)


# --- BS11-080 Banner of the Solitary Oath -----------------------------------
@effect("BS11-080", Trigger.ITEM)
def banner_of_solitary_oath(ctx: Ctx) -> None:
    """If you refreshed 2 or more times during this game, draw 1 card from your
    deck and select up to 1 of your opponent's Cookies. That Cookie receives 2
    damage."""
    if ctx.me.refresh_count < 2:
        return
    ctx.draw(1)
    target = ctx.select_enemy()
    if target is not None:
        ctx.deal_damage(target, 2)


# --- BS11-085 Salt Cellar Cookie --------------------------------------------
@effect("BS11-085", Trigger.ACTIVATE)
def salt_cellar_activate(ctx: Ctx) -> None:
    """<{P}> <Place this Cookie in your trash.> Place up to 1 of your {P}
    Cookies that is LV.2 or lower in your trash."""
    cookie = ctx.source_cookie
    if cookie is None or not ctx.pay(Cost.parse("{P}")):
        return
    ctx.trash_cookie(cookie)
    target = ctx.select_own(
        lambda c: c.defn(ctx.db).color is Color.PURPLE and c.level(ctx.db) <= 2)
    if target is not None:
        ctx.trash_cookie(target)


# --- BS11-088 Moonlight Cookie ----------------------------------------------
@effect("BS11-088", Trigger.ON_PLAY)
def moonlight_bs11_on_play(ctx: Ctx) -> None:
    """<Place 1 {P} LV.1 Cookie from your battle area in your trash.> Return up
    to 1 {P} Cookie that is LV.2 or higher from your trash to your hand."""
    fodder = ctx.select_own(
        lambda c: c.defn(ctx.db).color is Color.PURPLE and c.level(ctx.db) == 1)
    if fodder is None:
        return
    ctx.trash_cookie(fodder)
    options = [c for c in ctx.me.trash
               if ctx.db[c.card_id].is_cookie
               and ctx.db[c.card_id].color is Color.PURPLE
               and (ctx.db[c.card_id].level or 0) >= 2]
    if not options:
        return
    card = ctx.choose("Return a {P} Cookie to your hand", options, optional=True)
    if card is not None:
        ctx.me.trash.remove(card)
        ctx.me.hand.append(card)


# --- BS11-104 Cake Witch ----------------------------------------------------
@effect("BS11-104", Trigger.FAINT)
def cake_witch_faint(ctx: Ctx) -> None:
    """When this Cookie faints, view 3 cards from the top of your deck, reveal
    up to 1 {K} card from the viewed cards, and add it to your hand. Then,
    place the remaining cards in your trash."""
    viewed = ctx.me.deck[:3]
    if not viewed:
        return
    del ctx.me.deck[:len(viewed)]
    black = [c for c in viewed if ctx.db[c.card_id].color is Color.BLACK]
    if black:
        picked = ctx.choose("Add a {K} card to your hand", black, optional=True)
        if picked is not None:
            viewed.remove(picked)
            ctx.me.hand.append(picked)
    ctx.me.trash.extend(viewed)


# --- BS11-107 Oven of Burning Fate ------------------------------------------
@effect("BS11-107", Trigger.ITEM)
def oven_of_burning_fate(ctx: Ctx) -> None:
    """If the total LV. sum of the Cookies in your battle area is 5 or higher,
    select up to 1 of your opponent's Cookies. That Cookie receives 2 damage."""
    if sum(c.level(ctx.db) for c in ctx.me.battle) < 5:
        return
    target = ctx.select_enemy()
    if target is not None:
        ctx.deal_damage(target, 2)


# --- BS11-116 Dark Enchantress Cookie ----------------------------------------
def _dark_enchantress_protected(db, owner, cookie) -> bool:
    """"This Cookie cannot be moved from the battle area by your opponent's
    effects."

    The whole of the card's text below its 【EXTRA】 gate. Unconditional and
    self-only, so it is the per-Cookie registry rather than a player-wide lock.
    """
    return cookie.defn(db).base_id == "BS11-116"


MOVEMENT_PROTECTORS.append(_dark_enchantress_protected)
STATIC_ABILITY_CARDS.add("BS11-116")
