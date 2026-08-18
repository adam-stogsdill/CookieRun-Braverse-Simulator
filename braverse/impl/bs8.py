"""Hand-written cards from BS8.

The set is built on break-area recursion — deliberately breaking your own
Cookies to replay them — plus 【EXTRA】 play gates and a stage that taxes both
players' attack costs.
"""

from __future__ import annotations

from braverse.cost import Cost
from braverse.effects import (ATTACK_COST_MODIFIERS, PLAY_CONDITIONS,
                              STATIC_ABILITY_CARDS, Ctx, Trigger, effect)
from braverse.enums import CardType, Color


def _break_from_hand(ctx: Ctx, predicate=None) -> bool:
    """Move a Cookie from hand into your own break area, as a cost."""
    pool = [c for c in ctx.me.hand
            if ctx.db[c.card_id].is_cookie
            and (predicate is None or predicate(ctx.db[c.card_id]))]
    if not pool:
        return False
    card = ctx.choose("Send a Cookie from hand to your break area", pool,
                      optional=True)
    if card is None:
        return False
    ctx.me.hand.remove(card)
    ctx.me.break_area.append(card)
    ctx.game._check_win()
    return not ctx.state.over


def _break_self(ctx: Ctx) -> bool:
    cookie = ctx.source_cookie
    if cookie is None or cookie not in ctx.me.battle:
        return False
    ctx.faint(cookie)
    return not ctx.state.over


# --- 【EXTRA】 play gates -----------------------------------------------------
def _extra_play_gates(db, player, opponent, defn) -> bool:
    """"Can be played if ..." conditions printed on 【EXTRA】 Cookies."""
    card_id = defn.base_id
    if card_id == "BS8-005":     # 2 or more of your Cookies fainted this turn
        return player.cookies_fainted_this_turn >= 2
    if card_id == "BS8-069":     # support area 2+ cards behind the opponent's
        return len(opponent.support) - len(player.support) >= 2
    if card_id == "BS8-090":     # 2 cards or less in hand
        return len(player.hand) <= 2
    return True


PLAY_CONDITIONS.append(_extra_play_gates)
STATIC_ABILITY_CARDS.update(("BS8-005", "BS8-069", "BS8-090"))


@effect("BS8-005", Trigger.ON_PLAY)
def avatar_of_ruin_on_play(ctx: Ctx) -> None:
    """All of your opponent's Cookies receive 1 damage."""
    for cookie in list(ctx.enemy_cookies()):
        ctx.deal_damage(cookie, 1)


@effect("BS8-090", Trigger.ON_PLAY)
def will_of_nature_on_play(ctx: Ctx) -> None:
    """Return up to 1 {B} Cookie that is LV.2 or lower from your battle area to
    your hand."""
    target = ctx.select_own(
        lambda c: c.defn(ctx.db).color is Color.BLUE and c.level(ctx.db) <= 2)
    if target is not None:
        ctx.return_to_hand(target)


@effect("BS8-069", Trigger.ON_PLAY)
def peak_of_apathy_on_play(ctx: Ctx) -> None:
    """Place up to 1 {G} card from your trash into your support area as active."""
    options = [c for c in ctx.me.trash if ctx.db[c.card_id].color is Color.GREEN]
    if not options:
        return
    card = ctx.choose("Move a {G} card into your support area", options,
                      optional=True)
    if card is not None:
        ctx.me.trash.remove(card)
        card.rested = False
        ctx.me.support.append(card)


# --- BS8-009 Burning Spice Cookie -------------------------------------------
@effect("BS8-009", Trigger.ACTIVATE)
def burning_spice_activate(ctx: Ctx) -> None:
    """<{R}> If there is another Cookie in your battle area, all other Cookies
    receive 1 damage. Then, for each 3 levels your break area has reached, this
    Cookie gains +1 attack damage during this turn."""
    if not ctx.pay(Cost.parse("{R}")):
        return
    if any(c is not ctx.source_cookie for c in ctx.me.battle):
        for cookie in list(ctx.me.battle) + list(ctx.opp.battle):
            if cookie is not ctx.source_cookie:
                ctx.deal_damage(cookie, 1)
    bonus = ctx.me.break_level_total(ctx.db) // 3
    if bonus and ctx.source_cookie is not None:
        ctx.modify_attack(ctx.source_cookie, bonus)


# --- BS8-017 Cake Monster Army ----------------------------------------------
@effect("BS8-017", Trigger.ACTIVATE)
def cake_monster_activate(ctx: Ctx) -> None:
    """<{R}> Play up to 1 {R} Cookie that has 1 HP from your trash. If you did,
    select up to 1 Cookie in the battle area. That Cookie receives 1 damage."""
    if not ctx.pay(Cost.parse("{R}")):
        return
    played = ctx.play_cookie_from_trash(
        lambda d: d.color is Color.RED and (d.hp or 0) == 1)
    if not played:
        return
    options = list(ctx.me.battle) + list(ctx.opp.battle)
    target = ctx.choose("Damage which Cookie?", options, optional=True)
    if target is not None:
        ctx.deal_damage(target, 1)


# --- BS8-021 Soul Jam: Light of Destruction ---------------------------------
@effect("BS8-021", Trigger.ITEM)
def soul_jam_destruction(ctx: Ctx) -> None:
    """<{R}{R}> All Cookies that are not [Burning Spice Cookie] receive 1
    damage. Then, you can 【Equip】 this card to your [Burning Spice Cookie]."""
    for cookie in list(ctx.me.battle) + list(ctx.opp.battle):
        if cookie.name(ctx.db) != "Burning Spice Cookie":
            ctx.deal_damage(cookie, 1)
    holder = ctx.select_own(lambda c: c.name(ctx.db) == "Burning Spice Cookie",
                            prompt="Equip to Burning Spice Cookie?")
    card = ctx.source_card
    if holder is not None and card is not None:
        if card in ctx.me.trash:
            ctx.me.trash.remove(card)
        holder.equipment.append(card)


# --- BS8-023 Shadow of the Destroyer ----------------------------------------
@effect("BS8-023", Trigger.ITEM)
def shadow_of_the_destroyer(ctx: Ctx) -> None:
    """All Cookies with 2 or more HP remaining receive 1 damage."""
    for cookie in list(ctx.me.battle) + list(ctx.opp.battle):
        if cookie.remaining_hp >= 2:
            ctx.deal_damage(cookie, 1)


# --- BS8-031 Mozzarella Cookie ----------------------------------------------
@effect("BS8-031", Trigger.ON_PLAY)
def mozzarella_yellow_on_play(ctx: Ctx) -> None:
    """<{Y}> <Place 1 LV.3 Cookie from your trash into your break area.> Select
    2 Cookies in your break area with a total LV. sum of 3 or lower. Return
    those Cookies to your hand."""
    fodder = [c for c in ctx.me.trash
              if ctx.db[c.card_id].is_cookie and (ctx.db[c.card_id].level or 0) == 3]
    if not fodder or not ctx.pay(Cost.parse("{Y}")):
        return
    paid = ctx.choose("Bank a LV.3 Cookie into your break area", fodder,
                      optional=True)
    if paid is None:
        return
    ctx.me.trash.remove(paid)
    ctx.me.break_area.append(paid)
    ctx.game._check_win()
    if ctx.state.over:
        return

    budget = 3
    for _ in range(2):
        options = [c for c in ctx.me.break_area
                   if ctx.db[c.card_id].is_cookie
                   and (ctx.db[c.card_id].level or 0) <= budget]
        if not options:
            break
        card = ctx.choose("Return a Cookie from your break area", options,
                          optional=True)
        if card is None:
            break
        budget -= ctx.db[card.card_id].level or 0
        ctx.me.break_area.remove(card)
        ctx.me.hand.append(card)


# --- BS8-032 / BS8-034 self-break recursion ---------------------------------
@effect("BS8-032", Trigger.ACTIVATE)
def burnt_cheese_yellow_activate(ctx: Ctx) -> None:
    """If there is a Cookie in your break area, <place this Cookie and a Cookie
    that is LV.2 or above from your hand into your break area.> Draw up to 2
    cards from your deck."""
    if not any(ctx.db[c.card_id].is_cookie for c in ctx.me.break_area):
        return
    if not _break_from_hand(ctx, lambda d: (d.level or 0) >= 2):
        return
    if _break_self(ctx):
        ctx.draw(2)


@effect("BS8-034", Trigger.ACTIVATE)
def smoked_cheese_activate(ctx: Ctx) -> None:
    """If there is a Cookie in your break area, <place this Cookie and a Cookie
    from your hand into your break area.> Then, play up to 1 [Golden Cheese
    Cookie] from your break area."""
    if not any(ctx.db[c.card_id].is_cookie for c in ctx.me.break_area):
        return
    if not _break_from_hand(ctx):
        return
    if not _break_self(ctx):
        return
    options = [c for c in ctx.me.break_area
               if ctx.db[c.card_id].name == "Golden Cheese Cookie"]
    if not options or len(ctx.me.battle) >= ctx.game.rules.max_battle_cookies:
        return
    card = ctx.choose("Play Golden Cheese Cookie from your break area", options,
                      optional=True)
    if card is not None:
        ctx.me.break_area.remove(card)
        ctx.game._deploy_cookie(ctx.me, card, from_zone="break")


# --- BS8-039 Shelly ---------------------------------------------------------
@effect("BS8-039", Trigger.ACTIVATE)
def shelly_activate(ctx: Ctx) -> None:
    """<Place 1 LV.2 Cookie from your hand into the break area.> Play up to 1
    Cookie that is LV.2 or lower from your break area."""
    if not _break_from_hand(ctx, lambda d: (d.level or 0) == 2):
        return
    if len(ctx.me.battle) >= ctx.game.rules.max_battle_cookies:
        return
    options = [c for c in ctx.me.break_area
               if ctx.db[c.card_id].is_cookie and (ctx.db[c.card_id].level or 0) <= 2]
    if not options:
        return
    card = ctx.choose("Play a Cookie from your break area", options, optional=True)
    if card is not None:
        ctx.me.break_area.remove(card)
        ctx.game._deploy_cookie(ctx.me, card, from_zone="break")


# --- BS8-042 Adventurer Cookie ----------------------------------------------
@effect("BS8-042", Trigger.PLAYED_FROM_BREAK)
def adventurer_played_from_break(ctx: Ctx) -> None:
    """When this Cookie is played from the break area, select up to 1 card in
    your opponent's support area. That card is not set as active during your
    opponent's next Active Phase."""
    rested = [c for c in ctx.opp.support if not c.rested]
    if not rested:
        return
    card = ctx.choose("Keep which support card rested?", rested, optional=True)
    if card is not None:
        card.rested = True
        ctx.opp.support_skip_untap.add(card.uid)


# --- BS8-043 Fettuccine Cookie ----------------------------------------------
@effect("BS8-043", Trigger.ACTIVATE)
def fettuccine_activate(ctx: Ctx) -> None:
    """<{Y}> During this turn, if a LV.3 Cookie has been played from your break
    area, that Cookie gains +1 HP."""
    if not ctx.me.played_from_break_this_turn:
        return
    if not ctx.pay(Cost.parse("{Y}")):
        return
    target = ctx.select_own(
        lambda c: c.level(ctx.db) == 3 and c.uid in ctx.me.played_from_break_this_turn)
    if target is not None:
        ctx.gain_hp(target, 1)


# --- BS8-047 Puny Strength --------------------------------------------------
@effect("BS8-047", Trigger.ITEM)
def puny_strength(ctx: Ctx) -> None:
    """<Reveal 1 LV.3 Cookie in your hand.> Play up to 1 {Y} LV.3 Cookie from
    your break area. Then, place the revealed Cookie in your break area."""
    revealed = [c for c in ctx.me.hand
                if ctx.db[c.card_id].is_cookie and (ctx.db[c.card_id].level or 0) == 3]
    if not revealed:
        return
    shown = ctx.choose("Reveal a LV.3 Cookie", revealed, optional=False) or revealed[0]

    if len(ctx.me.battle) < ctx.game.rules.max_battle_cookies:
        options = [c for c in ctx.me.break_area
                   if ctx.db[c.card_id].color is Color.YELLOW
                   and (ctx.db[c.card_id].level or 0) == 3]
        if options:
            card = ctx.choose("Play a {Y} LV.3 Cookie from your break area",
                              options, optional=True)
            if card is not None:
                ctx.me.break_area.remove(card)
                ctx.game._deploy_cookie(ctx.me, card, from_zone="break")
    ctx.me.hand.remove(shown)
    ctx.me.break_area.append(shown)
    ctx.game._check_win()


# --- BS8-050 City of Eternal Gold -------------------------------------------
@effect("BS8-050", Trigger.STAGE_ACTIVATE)
def city_of_eternal_gold(ctx: Ctx) -> None:
    """<Rest this card.> Select up to 1 LV.3 Cookie that was played from your
    break area during this turn. That Cookie gains +1 HP."""
    card = ctx.source_card
    if card is None or card.rested:
        return
    card.rested = True
    target = ctx.select_own(
        lambda c: c.level(ctx.db) == 3 and c.uid in ctx.me.played_from_break_this_turn)
    if target is not None:
        ctx.gain_hp(target, 1)


# --- BS8-053 Gim Cookie -----------------------------------------------------
@effect("BS8-053", Trigger.ON_PLAY)
def gim_on_play(ctx: Ctx) -> None:
    """Set up to 1 {G} card from your support area as active."""
    for card in ctx.me.support:
        if card.rested and ctx.db[card.card_id].color is Color.GREEN:
            card.rested = False
            return


# --- BS8-059 Mystic Flour Cookie --------------------------------------------
@effect("BS8-059", Trigger.ACTIVATE)
def mystic_flour_activate(ctx: Ctx) -> None:
    """<{G}> <Return 2 {G} cards from your support area to your hand.> If
    another [Mystic Flour Cookie] is not in your battle area, place up to 2
    cards from the top of your deck into your support area as rested."""
    green = [c for c in ctx.me.support if ctx.db[c.card_id].color is Color.GREEN]
    if len(green) < 2 or not ctx.pay(Cost.parse("{G}")):
        return
    for _ in range(2):
        card = ctx.choose("Return a {G} support card to hand", green,
                          optional=False) or green[0]
        green.remove(card)
        ctx.me.support.remove(card)
        card.rested = False
        ctx.me.hand.append(card)
    others = [c for c in ctx.me.battle
              if c is not ctx.source_cookie
              and c.name(ctx.db) == "Mystic Flour Cookie"]
    if not others:
        ctx.mill_to_support(2)


# --- BS8-060 Peach Blossom Cookie -------------------------------------------
@effect("BS8-060", Trigger.ACTIVATE)
def peach_blossom_green_activate(ctx: Ctx) -> None:
    """<Return 1 {G} card from your support area to your hand.> Select 1 of the
    following: a Cookie of yours gains +1 HP, or an opponent's Cookie takes 1
    damage."""
    green = [c for c in ctx.me.support if ctx.db[c.card_id].color is Color.GREEN]
    if not green:
        return
    card = ctx.choose("Return a {G} support card to hand", green, optional=True)
    if card is None:
        return
    ctx.me.support.remove(card)
    card.rested = False
    ctx.me.hand.append(card)

    mode = ctx.choose("Choose one", ["heal", "damage"], optional=False) or "heal"
    if mode == "heal":
        target = ctx.select_own()
        if target is not None:
            ctx.gain_hp(target, 1)
    else:
        target = ctx.select_enemy()
        if target is not None:
            ctx.deal_damage(target, 1)


# --- BS8-061 Chives Dumpling King -------------------------------------------
@effect("BS8-061", Trigger.ATTACK_START)
def chives_dumpling_static(ctx: Ctx) -> None:
    """If your support area has 2 or more cards less than your opponent's, this
    Cookie gains +1 attack damage."""
    if ctx.source_cookie and len(ctx.opp.support) - len(ctx.me.support) >= 2:
        ctx.modify_attack(ctx.source_cookie, 1)


# --- BS8-072 Soul Jam: Light of Apathy --------------------------------------
@effect("BS8-072", Trigger.ITEM)
def soul_jam_apathy(ctx: Ctx) -> None:
    """<{G}{G}> If there are less cards in your support area than your
    opponent's, reveal up to 2 cards from the top of your deck and place up to
    1 of them in your support area as active."""
    if len(ctx.me.support) >= len(ctx.opp.support):
        return
    viewed = ctx.me.deck[:2]
    if not viewed:
        return
    del ctx.me.deck[:len(viewed)]
    picked = ctx.choose("Place a card into your support area", list(viewed),
                        optional=True)
    if picked is not None:
        viewed.remove(picked)
        picked.rested = False
        ctx.me.support.append(picked)
    ctx.me.deck[:0] = viewed


# --- BS8-074 White Flour Fog ------------------------------------------------
@effect("BS8-074", Trigger.ITEM)
def white_flour_fog(ctx: Ctx) -> None:
    """Select up to 1 of your opponent's Cookies. During this turn, that Cookie
    deals -1 attack damage.

    Its printed discount ("costs 1 {G} less when behind on support") is applied
    where the trap's cost is paid.
    """
    target = ctx.select_enemy(prompt="Debuff which attacker?")
    if target is not None:
        ctx.modify_attack(target, -1)


# --- BS8-075 The Ivory Pagoda ----------------------------------------------
def _ivory_pagoda_tax(db, player, cookie, cost):
    """"Players with 6 cards or more in their support area have all their
    attack costs increased by 1 {N}."

    A symmetric stage tax, so it applies to whoever is attacking rather than to
    the stage's controller.
    """
    if len(player.support) < 6:
        return None
    on_field = any(any(db[c.card_id].base_id == "BS8-075" for c in side.stage)
                   for side in (player,))
    if not on_field:
        return None
    return Cost(cost.colored, cost.generic + 1)


ATTACK_COST_MODIFIERS.append(_ivory_pagoda_tax)
STATIC_ABILITY_CARDS.add("BS8-075")


# --- BS8-076 Icicle Yeti Cookie ---------------------------------------------
@effect("BS8-076", Trigger.ATTACK)
def icicle_yeti_attack(ctx: Ctx) -> None:
    """Then, <place this Cookie on the bottom of your deck.> Draw 1 card from
    your deck and select up to 1 of your opponent's Cookies. That Cookie is not
    set as active during your opponent's next Active Phase."""
    cookie = ctx.source_cookie
    if cookie is None or cookie not in ctx.me.battle:
        return
    ctx.me.battle.remove(cookie)
    ctx.me.deck.append(cookie.card)
    ctx.me.trash.extend(cookie.hp_cards)
    ctx.game._check_battle_area(ctx.me)

    ctx.draw(1)
    target = ctx.select_enemy()
    if target is not None:
        ctx.skip_next_active(target)


# --- BS8-078 Snow Sugar Cookie ----------------------------------------------
@effect("BS8-078", Trigger.ACTIVATE)
def snow_sugar_blue_activate(ctx: Ctx) -> None:
    """If there are 3 cards or less in your hand, <place this Cookie on the
    bottom of the deck.> Play up to 1 {B} Cookie that is LV.2 or higher from
    your hand. Then, that Cookie gains +1 HP."""
    cookie = ctx.source_cookie
    if ctx.hand_size > 3 or cookie is None or cookie not in ctx.me.battle:
        return
    options = [c for c in ctx.me.hand
               if ctx.db[c.card_id].is_cookie
               and ctx.db[c.card_id].color is Color.BLUE
               and (ctx.db[c.card_id].level or 0) >= 2]
    if not options:
        return
    ctx.me.battle.remove(cookie)
    ctx.me.deck.append(cookie.card)
    ctx.me.trash.extend(cookie.hp_cards)

    card = ctx.choose("Play a {B} LV.2+ Cookie from your hand", options,
                      optional=True)
    if card is not None:
        ctx.me.hand.remove(card)
        played = ctx.game._deploy_cookie(ctx.me, card)
        ctx.gain_hp(played, 1)
    ctx.game._check_battle_area(ctx.me)


# --- BS8-099 Frozen Mountain Depths -----------------------------------------
@effect("BS8-099", Trigger.STAGE_ACTIVATE)
def frozen_mountain_depths(ctx: Ctx) -> None:
    """<{B}{B}> <Rest this card.> If there are 3 rested Cookies or more in the
    battle area, draw up to 3 cards from your deck."""
    card = ctx.source_card
    if card is None or card.rested:
        return
    if not ctx.pay(Cost.parse("{B}{B}")):
        return
    card.rested = True
    rested = sum(1 for c in list(ctx.me.battle) + list(ctx.opp.battle) if c.rested)
    if rested >= 3:
        ctx.draw(3)


# --- BS8-100 Snowfall Lantern Tree ------------------------------------------
@effect("BS8-100", Trigger.STAGE_ACTIVATE)
def snowfall_lantern_tree(ctx: Ctx) -> None:
    """<{B}> <Place this card in the trash.> Discard any number of {B} cards
    from your hand. Then, draw as many cards from your deck as you discarded."""
    card = ctx.source_card
    if card is None or not ctx.pay(Cost.parse("{B}")):
        return
    if card in ctx.me.stage:
        ctx.me.stage.remove(card)
        ctx.me.trash.append(card)

    discarded = 0
    while True:
        blue = [c for c in ctx.me.hand if ctx.db[c.card_id].color is Color.BLUE]
        if not blue:
            break
        pick = ctx.choose("Discard a {B} card", blue, optional=True)
        if pick is None:
            break
        ctx.me.hand.remove(pick)
        ctx.me.trash.append(pick)
        discarded += 1
    if discarded:
        ctx.draw(discarded)


# --- BS8-104 Dark Cacao Cookie ----------------------------------------------
@effect("BS8-104", Trigger.ON_PLAY)
def dark_cacao_on_play(ctx: Ctx) -> None:
    """<Discard 1 card.> Return up to 1 {P} card from your trash to your hand."""
    if not ctx.discard(1, optional=True):
        return
    options = [c for c in ctx.me.trash if ctx.db[c.card_id].color is Color.PURPLE]
    if not options:
        return
    card = ctx.choose("Return a {P} card to your hand", options, optional=True)
    if card is not None:
        ctx.me.trash.remove(card)
        ctx.me.hand.append(card)


# --- BS8-121 Black Concoction -----------------------------------------------
@effect("BS8-121", Trigger.ITEM)
def black_concoction(ctx: Ctx) -> None:
    """<{P}> Place up to 3 cards from the top of your deck into the trash. If a
    {P} Item card was among the placed cards, select up to 1 of your Cookies.
    That Cookie gains +1 HP."""
    placed = []
    for _ in range(3):
        if not ctx.me.deck:
            break
        card = ctx.me.deck.pop(0)
        ctx.me.trash.append(card)
        placed.append(card)
    found = any(ctx.db[c.card_id].color is Color.PURPLE
                and ctx.db[c.card_id].type is CardType.ITEM for c in placed)
    if not found:
        return
    target = ctx.select_own()
    if target is not None:
        ctx.gain_hp(target, 1)


# --- BS8-125 The Days of Resolution and Dignity -----------------------------
def _days_of_resolution_discount(db, player, cookie, cost):
    """"If there are 15 cards or more in your trash, the attack cost of your
    [Dark Cacao Cookie] is reduced by 1 {P}."""
    if cookie.name(db) != "Dark Cacao Cookie" or len(player.trash) < 15:
        return None
    if not any(db[c.card_id].base_id == "BS8-125" for c in player.stage):
        return None
    colored, removed = [], False
    for color, count in cost.colored:
        if color is Color.PURPLE and not removed and count:
            count -= 1
            removed = True
        if count:
            colored.append((color, count))
    return Cost(tuple(colored), cost.generic) if removed else None


ATTACK_COST_MODIFIERS.append(_days_of_resolution_discount)
STATIC_ABILITY_CARDS.add("BS8-125")
