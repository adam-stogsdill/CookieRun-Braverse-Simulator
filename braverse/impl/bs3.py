"""Hand-written cards from BS3.

The set's signature is HP-pile manipulation and continuous stat auras, plus a
few once-per-game and damage-capping abilities with no equivalent elsewhere.
"""

from __future__ import annotations

from braverse.cost import Cost
from braverse.effects import (SELECTION_PROTECTORS, STATIC_ABILITY_CARDS,
                              TRASH_PROTECTORS, Ctx, Trigger, effect)
from braverse.enums import Color, Marker


# --- BS3-001 Princess Cookie ------------------------------------------------
@effect("BS3-001", Trigger.ATTACK_START)
def princess_static(ctx: Ctx) -> None:
    """When this Cookie attacks a Cookie whose remaining HP is 4 or more, this
    Cookie gains +1 attack damage.

    Conditional on the defender, so it is read once the target is known.
    """
    cookie = ctx.source_cookie
    target = ctx.attack_target
    if cookie is not None and target is not None and target.remaining_hp >= 4:
        ctx.modify_attack(cookie, 1)


# --- BS3-006 Snapdragon Cookie ----------------------------------------------
@effect("BS3-006", Trigger.ATTACK_START)
def snapdragon_aura(ctx: Ctx) -> None:
    """If this Cookie is in the battle area, your {R} LV.2 or higher Cookies
    gain +1 attack damage.

    An aura on *other* Cookies, so it fires from whichever Cookie is attacking
    and checks whether Snapdragon is on the field alongside it.
    """
    cookie = ctx.source_cookie
    if cookie is None:
        return
    if not any(c.defn(ctx.db).base_id == "BS3-006" for c in ctx.me.battle):
        return
    defn = cookie.defn(ctx.db)
    if defn.color is Color.RED and (defn.level or 0) >= 2:
        ctx.modify_attack(cookie, 1)


# --- BS3-007 Tea Knight Cookie ----------------------------------------------
@effect("BS3-007", Trigger.ATTACK_START)
def tea_knight_static(ctx: Ctx) -> None:
    """If your break area is LV.7 or higher, this Cookie gains +2 attack damage."""
    if ctx.source_cookie and ctx.me.break_level_total(ctx.db) >= 7:
        ctx.modify_attack(ctx.source_cookie, 2)


# --- BS3-013 Tiger Lily Cookie ----------------------------------------------
@effect("BS3-013", Trigger.ON_PLAY)
def tiger_lily_activate(ctx: Ctx) -> None:
    """During this turn, this Cookie gains +1 attack damage."""
    if ctx.source_cookie:
        ctx.modify_attack(ctx.source_cookie, 1)


@effect("BS3-013", Trigger.ATTACK)
def tiger_lily_attack(ctx: Ctx) -> None:
    """Then, attack damage of 2 or more received by this Cookie is reduced to 1
    until the end of your opponent's turn.

    A damage cap rather than a flat reduction, so it is stored as a cap the
    damage path consults.
    """
    if ctx.source_cookie:
        ctx.source_cookie.damage_cap = 1


# --- BS3-014 Schwarzwälder --------------------------------------------------
@effect("BS3-014", Trigger.ATTACK_START)
def schwarzwalder_red_static(ctx: Ctx) -> None:
    """If there is a Cookie that has 【Blocker】 in either player's battle area,
    this Cookie gains +1 attack damage."""
    cookie = ctx.source_cookie
    if cookie is None:
        return
    everyone = list(ctx.me.battle) + list(ctx.opp.battle)
    if any(c.defn(ctx.db).has(Marker.BLOCKER) for c in everyone):
        ctx.modify_attack(cookie, 1)


# --- BS3-025 Golden Cheese Cookie -------------------------------------------
@effect("BS3-025", Trigger.END_TURN)
def golden_cheese_revive(ctx: Ctx) -> None:
    """【Your Turn】 This skill can only be used once per game. If this Cookie is
    in your break area, <can be used as {Y}.> Play this Cookie in your battle
    area with 1 HP.

    Resurrection from the break area, which also un-does the Level the
    opponent had banked for it.
    """
    card = next((c for c in ctx.me.break_area
                 if ctx.db[c.card_id].base_id == "BS3-025"), None)
    if card is None or card.uid in ctx.me.used_once_per_game:
        return
    if len(ctx.me.battle) >= ctx.game.rules.max_battle_cookies:
        return
    # `<can be used as {Y}.>` is the price of the revival, not a note about it.
    if not ctx.pay(Cost.parse("{Y}")):
        return
    ctx.me.used_once_per_game.add(card.uid)
    ctx.me.break_area.remove(card)
    cookie = ctx.game._deploy_cookie(ctx.me, card, run_on_play=False)
    # "with 1 HP" overrides the printed value.
    while len(cookie.hp_cards) > 1:
        ctx.me.trash.append(cookie.hp_cards.pop())


STATIC_ABILITY_CARDS.add("BS3-025")


# --- BS3-026 / BS3-083 view-and-reorder -------------------------------------
@effect("BS3-026", Trigger.ON_PLAY)
def linzer_activate(ctx: Ctx) -> None:
    """<{Y}> Select up to 1 of your Cookies. View all of that Cookie's HP cards
    and rearrange them in any order."""
    if not ctx.pay(Cost.parse("{Y}")):
        return
    target = ctx.select_own()
    if target is not None:
        # FLIPs to the top, where damage reveals them first.
        target.hp_cards.sort(key=lambda c: ctx.db[c.card_id].is_flip)


@effect("BS3-083", Trigger.ON_PLAY)
def captain_caviar_blue_activate(ctx: Ctx) -> None:
    """View 3 cards from the top of your deck; place them on the top of your
    deck in any order."""
    viewed = ctx.me.deck[:3]
    if len(viewed) < 2:
        return
    del ctx.me.deck[:len(viewed)]
    viewed.sort(key=lambda c: not ctx.db[c.card_id].is_flip)
    ctx.me.deck[:0] = viewed


# --- BS3-028 Mozzarella Cookie ----------------------------------------------
@effect("BS3-028", Trigger.ON_PLAY)
def mozzarella_activate(ctx: Ctx) -> None:
    """<{Y}> <Discard 1 card.> If your opponent's break area is LV.6 or lower,
    select up to 1 LV.1 Cookie from your opponent's trash. Place that Cookie in
    your opponent's break area."""
    if ctx.opp.break_level_total(ctx.db) > 6:
        return
    if not ctx.pay(Cost.parse("{Y}")) or not ctx.discard(1, optional=True):
        return
    options = [c for c in ctx.opp.trash
               if ctx.db[c.card_id].is_cookie and (ctx.db[c.card_id].level or 0) == 1]
    if not options:
        return
    card = ctx.choose("Bank a LV.1 Cookie into your opponent's break area",
                      options, optional=True)
    if card is not None:
        ctx.opp.trash.remove(card)
        ctx.opp.break_area.append(card)
        ctx.game._check_win()


# --- BS3-029 Burnt Cheese Cookie --------------------------------------------
@effect("BS3-029", Trigger.FAINT)
def burnt_cheese_faint(ctx: Ctx) -> None:
    """When this Cookie faints, play up to 1 {Y} Cookie from your hand. Then,
    that Cookie gains +1 HP."""
    if len(ctx.me.battle) >= ctx.game.rules.max_battle_cookies:
        return
    options = [c for c in ctx.me.hand
               if ctx.db[c.card_id].is_cookie
               and ctx.db[c.card_id].color is Color.YELLOW]
    if not options:
        return
    card = ctx.choose("Play a {Y} Cookie from your hand", options, optional=True)
    if card is None:
        return
    ctx.me.hand.remove(card)
    cookie = ctx.game._deploy_cookie(ctx.me, card)
    ctx.gain_hp(cookie, 1)


# --- BS3-030 Black Raisin Cookie --------------------------------------------
@effect("BS3-030", Trigger.ON_PLAY)
def black_raisin_yellow_activate(ctx: Ctx) -> None:
    """Place up to 1 card from your hand to the top of this Cookie's HP."""
    cookie = ctx.source_cookie
    if cookie is None or not ctx.me.hand:
        return
    card = ctx.choose("Add a card from hand to this Cookie's HP",
                      list(ctx.me.hand), optional=True)
    if card is not None:
        ctx.me.hand.remove(card)
        card.face_up = False
        cookie.hp_cards.append(card)


# --- BS3-036 Olive Cookie ---------------------------------------------------
@effect("BS3-036", Trigger.ON_PLAY)
def olive_activate(ctx: Ctx) -> None:
    """Select 1 of your other {Y} Cookies in your battle area. Place that
    Cookie in your break area. Then, draw up to 2 cards from your deck."""
    target = ctx.select_own(
        lambda c: c is not ctx.source_cookie
        and c.defn(ctx.db).color is Color.YELLOW)
    if target is None:
        return
    ctx.faint(target)
    if not ctx.state.over:
        ctx.draw(2)


# --- BS3-037 Angel Cookie ---------------------------------------------------
@effect("BS3-037", Trigger.ATTACK)
def angel_attack(ctx: Ctx) -> None:
    """Then, when your opponent's Cookie faints from this Cookie's attack, this
    Cookie gains +1 HP."""
    if getattr(ctx.game, "_attack_killed", False) and ctx.source_cookie:
        ctx.gain_hp(ctx.source_cookie, 1)


# --- BS3-040 Adventurer Cookie ----------------------------------------------
@effect("BS3-040", Trigger.ON_PLAY)
def adventurer_yellow_activate(ctx: Ctx) -> None:
    """<{Y}{Y}> Select up to 1 LV.1 Cookie from either player's battle area.
    Place that Cookie in the owner's break area."""
    if not ctx.pay(Cost.parse("{Y}{Y}")):
        return
    options = [c for c in list(ctx.me.battle) + list(ctx.opp.battle)
               if c.level(ctx.db) == 1]
    if not options:
        return
    target = ctx.choose("Break a LV.1 Cookie", options, optional=True)
    if target is not None:
        ctx.faint(target)


# --- BS3-068 Elder Faerie's Sword -------------------------------------------
def _sweep_one(ctx: Ctx) -> None:
    """Deals 1 damage to all of your opponent's Cookies.

    The scrape splits the damage number off these lines, so they are written
    out rather than fought with in the compiler.
    """
    for cookie in list(ctx.enemy_cookies()):
        ctx.deal_damage(cookie, 1)


# BS3-043 Soul Jam: Light of Abundance is deliberately left unimplemented. Its
# second sentence — "you can 【Equip】 this card to your [Golden Cheese Cookie]"
# — is an Equip, which the engine does not model, and a body that only swept
# would silently misreport the card. It stays a vanilla item until Equip exists.

_SWORD_PLACE = "place this card in your support area as rested"
_SWORD_SWEEP = "damage all their Cookies, then trash 2 of your supports"


@effect("BS3-068", Trigger.ITEM)
def elder_faeries_sword(ctx: Ctx) -> None:
    """Select 1 of the following.
    ・Place this card in your support area as rested.
    ・Deal 1 damage to all of your opponent's Cookies. Then, place 2 cards from
      your support area into the trash.

    A modal effect, so the mode is offered before either branch runs. The first
    branch leaves the card in the support area and the engine's item path finds
    it there, so it is not also sent to the trash.
    """
    mode = ctx.choose("Choose one", [_SWORD_PLACE, _SWORD_SWEEP],
                      optional=False) or _SWORD_SWEEP
    if mode == _SWORD_PLACE:
        card = ctx.source_card
        if card is not None:
            card.rested = True
            ctx.me.support.append(card)
        return
    _sweep_one(ctx)
    for _ in range(2):
        if not ctx.me.support:
            break
        ctx.me.trash.append(ctx.me.support.pop())


# --- BS3-051 Fig Cookie -----------------------------------------------------
@effect("BS3-051", Trigger.WHEN_ATTACKED)
def fig_when_attacked(ctx: Ctx) -> None:
    """When your opponent's Cookie attacks, if your support area contains 5
    cards or more, select up to 1 of your opponent's Cookies. During this turn,
    that Cookie deals -1 attack damage."""
    if ctx.support_count() < 5:
        return
    target = ctx.select_enemy(prompt="Debuff which attacker?")
    if target is not None:
        ctx.modify_attack(target, -1)


# --- BS3-060 Elder Faerie Cookie --------------------------------------------
@effect("BS3-060", Trigger.ON_PLAY)
def elder_faerie_green_activate(ctx: Ctx) -> None:
    """<{G}> Select up to 1 active card from your opponent's support area. Rest
    that card."""
    if ctx.pay(Cost.parse("{G}")):
        ctx.rest_support(1, mine=False)


@effect("BS3-060", Trigger.ATTACK)
def elder_faerie_green_attack(ctx: Ctx) -> None:
    """Then, place 2 cards from the top of this Cookie's HP cards into the
    trash. When this makes the Cookie's HP reach 0, set up to 2 cards from your
    support area as active."""
    cookie = ctx.source_cookie
    if cookie is None:
        return
    ctx.trash_hp(cookie, 2)
    if cookie not in ctx.me.battle:
        ctx.set_support_active(2)


# --- BS3-073 Candy Diver Cookie ---------------------------------------------
@effect("BS3-073", Trigger.ACTIVATE)
def candy_diver_activate(ctx: Ctx) -> None:
    """<{B}> Reveal up to 1 card from the bottom of your deck. If that card is
    a Cookie, place that card on the top of your deck. If it is a non-Cookie
    card, add that card to your hand."""
    if not ctx.pay(Cost.parse("{B}")) or not ctx.me.deck:
        return
    card = ctx.me.deck.pop()
    if ctx.db[card.card_id].is_cookie:
        ctx.me.deck.insert(0, card)
    else:
        ctx.me.hand.append(card)


# --- BS3-076 Strawberry Crepe Cookie ----------------------------------------
@effect("BS3-076", Trigger.ON_PLAY)
def strawberry_crepe_blue_activate(ctx: Ctx) -> None:
    """Select up to 1 Cookie that is LV.2 or lower from either player's battle
    area. Place that Cookie on the top of the owner's deck."""
    options = [c for c in list(ctx.me.battle) + list(ctx.opp.battle)
               if c.level(ctx.db) <= 2]
    if not options:
        return
    target = ctx.choose("Put a Cookie on top of its owner's deck", options,
                        optional=True)
    if target is None:
        return
    owner = ctx.state.players[target.owner]
    owner.battle.remove(target)
    owner.deck.insert(0, target.card)
    owner.trash.extend(target.spent_cards)
    ctx.game._check_battle_area(owner)


# --- BS3-082 GingerBrave ----------------------------------------------------
@effect("BS3-082", Trigger.WHEN_ATTACKED)
def gingerbrave_blue_when_attacked(ctx: Ctx) -> None:
    """If your hand contains 5 cards or less, this Cookie takes no damage from
    effects."""
    cookie = ctx.source_cookie
    if cookie is not None and ctx.hand_size <= 5:
        cookie.effect_damage_immune = True


# --- BS3-103 Red Velvet Cookie ----------------------------------------------
@effect("BS3-103", Trigger.FAINT)
def red_velvet_faint(ctx: Ctx) -> None:
    """When this Cookie faints and you have 10 cards or more in your trash,
    select up to 1 of your opponent's Cookies. That Cookie receives 1 damage."""
    if len(ctx.me.trash) < 10:
        return
    target = ctx.select_enemy()
    if target is not None:
        ctx.deal_damage(target, 1)


# --- BS3-112 Prune Juice Cookie ---------------------------------------------
@effect("BS3-112", Trigger.ON_PLAY)
def prune_juice_purple_activate(ctx: Ctx) -> None:
    """<{P}> <Select 2 non-Cookie cards from your trash and place them on the
    bottom of your deck in any order.> Return up to 1 {P} Cookie from your
    trash to your hand."""
    pool = [c for c in ctx.me.trash if not ctx.db[c.card_id].is_cookie]
    if len(pool) < 2 or not ctx.pay(Cost.parse("{P}")):
        return
    for _ in range(2):
        card = ctx.choose("Put a non-Cookie card on the bottom of your deck",
                          pool, optional=False) or pool[0]
        pool.remove(card)
        ctx.me.trash.remove(card)
        ctx.me.deck.append(card)

    options = [c for c in ctx.me.trash
               if ctx.db[c.card_id].is_cookie
               and ctx.db[c.card_id].color is Color.PURPLE]
    if not options:
        return
    card = ctx.choose("Return a {P} Cookie to your hand", options, optional=True)
    if card is not None:
        ctx.me.trash.remove(card)
        ctx.me.hand.append(card)


# --- BS3-019/043/066/091/115 the Soul Jams ----------------------------------
# One 【Equip】 item per Ancient Hero, all built the same way: the item does
# something on the way down, and then *may* attach itself to the one Cookie it
# names, where it grants that Cookie a rider for as long as it rides there.
# The rider is registered against the jam's own card id — as a trigger for the
# ones that fire on an event, in a protection registry for the ones that are
# continuous — and `Game._run_equipment_effects` is what reaches it. Strip the
# jam (BS9-090) or move the Cookie and the rider leaves with it, which is what
# makes attaching the card the whole of the mechanic.


def _equip_soul_jam(ctx: Ctx, holder_name: str) -> bool:
    """"You can 【Equip】 this card to your [named Cookie]."

    Optional, and it does not ask when there is nobody to equip: with no such
    Cookie in the battle area, `select_own` has nothing to offer and the jam
    goes to the trash like any other spent item.
    """
    card = ctx.source_card
    if card is None:
        return False
    holder = ctx.select_own(lambda c: c.name(ctx.db) == holder_name,
                            prompt=f"Equip to {holder_name}?")
    if holder is None:
        return False
    if card in ctx.me.trash:
        ctx.me.trash.remove(card)
    holder.equipment.append(card)
    return True


def _wearing(cookie, card_id: str) -> bool:
    return any(c.card_id.split("@")[0] == card_id for c in cookie.equipment)


# --- BS3-019 Soul Jam: Light of Passion -------------------------------------
@effect("BS3-019", Trigger.ITEM)
def soul_jam_passion(ctx: Ctx) -> None:
    """<{R}{R}{R}> Select up to 1 of your opponent's Cookies. That Cookie
    receives 2 damage. Then, you can 【Equip】 this card to your [Hollyberry
    Cookie]. That Cookie gains +1 attack damage."""
    target = ctx.select_enemy()
    if target is not None:
        ctx.deal_damage(target, 2)
    _equip_soul_jam(ctx, "Hollyberry Cookie")


@effect("BS3-019", Trigger.ATTACK_START)
def soul_jam_passion_aura(ctx: Ctx) -> None:
    """"That Cookie gains +1 attack damage." — the rider on the host.

    Read as the swing is worked out, the way every other continuous attack
    aura in this set is (BS3-001, BS3-006), so the number the log prints is
    the number that lands.
    """
    if ctx.source_cookie is not None:
        ctx.modify_attack(ctx.source_cookie, 1)


# --- BS3-043 Soul Jam: Light of Abundance -----------------------------------
@effect("BS3-043", Trigger.ITEM)
def soul_jam_abundance(ctx: Ctx) -> None:
    """<{Y}{Y}{Y}> Deals 1 damage to all of your opponent's Cookies. Then, you
    can 【Equip】 this card to your [Golden Cheese Cookie]. That Cookie gains
    +2 HP.

    The HP is the equip's rider, so it is paid once, when the jam lands — a
    Cookie that was never equipped gains nothing.
    """
    for cookie in list(ctx.opp.battle):
        ctx.deal_damage(cookie, 1)
    holder = ctx.select_own(lambda c: c.name(ctx.db) == "Golden Cheese Cookie",
                            prompt="Equip to Golden Cheese Cookie?")
    card = ctx.source_card
    if holder is None or card is None:
        return
    if card in ctx.me.trash:
        ctx.me.trash.remove(card)
    holder.equipment.append(card)
    ctx.gain_hp(holder, 2)


# --- BS3-066 Soul Jam: Light of Freedom -------------------------------------
@effect("BS3-066", Trigger.ITEM)
def soul_jam_freedom(ctx: Ctx) -> None:
    """<{G}{G}{G}> Return 1 card from your support area to your hand, and place
    1 card from the top of your deck into your support area as active. Then,
    you can 【Equip】 this card to your [White Lily Cookie]. When that Cookie
    attacks, set up to 1 card from your support area as active."""
    ctx.return_support_to_hand(optional=False)
    ctx.mill_to_support(1, rested=False)
    _equip_soul_jam(ctx, "White Lily Cookie")


@effect("BS3-066", Trigger.ATTACK_START)
def soul_jam_freedom_rider(ctx: Ctx) -> None:
    """"When that Cookie attacks, set up to 1 card from your support area as
    active." — so the swing is paid for out of a support area that refills."""
    ctx.set_support_active(1)


# --- BS3-091 Soul Jam: Light of Truth ---------------------------------------
@effect("BS3-091", Trigger.ITEM)
def soul_jam_truth(ctx: Ctx) -> None:
    """<{B}{B}{B}> View 3 cards from the top of your deck. Add up to 2 of them
    to your hand and return the remaining cards to the top of your deck in any
    order. Then, you can 【Equip】 this card to your [Pure Vanilla Cookie].
    When that Cookie attacks, draw 1 card from your deck.

    The remainder goes back on *top*, not the bottom: those are the next cards
    drawn, and their controller has just seen them.
    """
    ctx.view_top(3, take=2, rest="top",
                 prompt="Add up to 2 of these to your hand")
    _equip_soul_jam(ctx, "Pure Vanilla Cookie")


@effect("BS3-091", Trigger.ATTACK_START)
def soul_jam_truth_rider(ctx: Ctx) -> None:
    """"When that Cookie attacks, draw 1 card from your deck."
    """
    ctx.draw(1)


# --- BS3-115 Soul Jam: Light of Resolution ----------------------------------
def _dark_cacao_jam_holder(db, owner, cookie) -> bool:
    """Whether this Cookie is wearing Soul Jam: Light of Resolution.

    The protections are the jam's, not the Cookie's, so they are read off the
    attachment rather than off a flag: strip the equipment (BS9-090 does) or
    move the Cookie, and the Cookie is a normal Cookie again.
    """
    return _wearing(cookie, "BS3-115")


SELECTION_PROTECTORS.append(_dark_cacao_jam_holder)
TRASH_PROTECTORS.append(_dark_cacao_jam_holder)


@effect("BS3-115", Trigger.ITEM)
def soul_jam_resolution(ctx: Ctx) -> None:
    """<{P}{P}{P}> Select up to 2 of your opponent's LV.2 or lower Cookies.
    Place up to 1 card from the top of each Cookie's HP into the trash. Then,
    you can 【Equip】 this card to your [Dark Cacao Cookie]. That Cookie cannot
    be selected by your opponent's effects and cannot be trashed.

    Two selections rather than one prompt for two, because "up to 2" lets its
    controller stop after one, and the second pick must not offer a Cookie the
    first already took.
    """
    remaining = ctx.enemy_cookies(lambda c: c.level(ctx.db) <= 2)
    for _ in range(2):
        if not remaining:
            break
        target = ctx.choose("Trash 1 HP from which Cookie?", remaining,
                            optional=True)
        if target is None:
            break
        remaining.remove(target)
        ctx.trash_hp(target, 1)

    _equip_soul_jam(ctx, "Dark Cacao Cookie")
