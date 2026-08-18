"""ST8 — Green / Wind Archer Cookie starter deck."""

from braverse.cost import Cost
from braverse.effects import Ctx, Trigger, effect
from braverse.enums import CardType, Color

WIND_ARCHER = "Wind Archer Cookie"


# --- ST8-002 Muscle Cookie (FLIP) ------------------------------------------
@effect("ST8-002", Trigger.FLIP)
def muscle_flip(ctx: Ctx) -> None:
    """Return this Cookie to your hand."""
    # "this Cookie" is this card, not the Cookie it was HP for.
    ctx.return_self_to_hand()


# --- ST8-004 Red Panna Cotta Cookie ----------------------------------------
@effect("ST8-004", Trigger.ATTACK)
def red_panna_cotta_attack(ctx: Ctx) -> None:
    """Then, if there are 5 cards or more in your support area, set up to 1
    card from your support area as active."""
    if ctx.support_count() >= 5:
        ctx.set_support_active(1)


# --- ST8-005 Wind Archer Cookie --------------------------------------------
@effect("ST8-005", Trigger.ACTIVATE)
def wind_archer_activate(ctx: Ctx) -> None:
    """If there are 5 cards or less in your hand and, during this turn, an Item
    card was activated, draw 1 card."""
    if ctx.hand_size <= 5 and ctx.me.items_played_this_turn > 0:
        ctx.draw(1)


@effect("ST8-005", Trigger.ATTACK)
def wind_archer_attack(ctx: Ctx) -> None:
    """Then, <rest up to 2 cards in your support area.> Select up to 1 of your
    opponent's Cookies. That Cookie receives damage equal to the number of
    cards rested by this effect."""
    target = ctx.select_enemy()
    if target is None:
        return
    rested = ctx.rest_support(2)
    if rested:
        ctx.deal_damage(target, rested)


# --- ST8-006 / ST8-007 shared FLIP -----------------------------------------
def _flip_bolster(ctx: Ctx) -> None:
    """<Discard 1 card.> The Cookie with this card attached for HP gains +1 HP."""
    if ctx.source_cookie and ctx.discard(1, optional=True):
        ctx.gain_hp(ctx.source_cookie, 1)


effect("ST8-006", Trigger.FLIP)(_flip_bolster)
effect("ST8-007", Trigger.FLIP)(_flip_bolster)


# --- ST8-008 Sugar Swan Cookie ---------------------------------------------
@effect("ST8-008", Trigger.FAINT)
def sugar_swan_faint(ctx: Ctx) -> None:
    """When this Cookie faints, if the number of cards in your support area is
    the same or less than your opponent's, place up to 1 card from the top of
    your deck in your support area as rested."""
    if ctx.support_count() <= ctx.support_count(mine=False):
        ctx.mill_to_support(1)


@effect("ST8-008", Trigger.ATTACK)
def sugar_swan_attack(ctx: Ctx) -> None:
    """Then, if there is a [Wind Archer Cookie] in your support area, draw 1
    card and discard 1 card."""
    if ctx.name_in_support(WIND_ARCHER):
        if ctx.draw(1):
            ctx.discard(1, optional=True)


# --- ST8-009 Cucumber Cookie (FLIP) ----------------------------------------
@effect("ST8-009", Trigger.FLIP)
def cucumber_flip(ctx: Ctx) -> None:
    """Draw up to 1 card from your deck."""
    ctx.draw(1)


# --- ST8-010 Cookiemals ----------------------------------------------------
@effect("ST8-010", Trigger.FAINT)
def cookiemals_faint(ctx: Ctx) -> None:
    """When this Cookie faints, <return 1 Item card from your support area to
    your hand.> Place up to 1 card from the top of your deck in your support
    area as rested."""
    returned = ctx.return_support_to_hand(
        predicate=lambda d: d.type is CardType.ITEM
    )
    if returned:
        ctx.mill_to_support(1)


# --- ST8-012 Windcatcher ---------------------------------------------------
@effect("ST8-012", Trigger.ACTIVATE)
def windcatcher_activate(ctx: Ctx) -> None:
    """<Rest this card.> During this turn, if an Item card was activated,
    select up to 1 of your opponent's Cookies. That Cookie receives 1 damage.
    Then, <{G}> Select up to 1 of your [Wind Archer Cookie]. That Cookie gains
    +1 HP."""
    cookie = ctx.source_cookie
    if cookie is None or cookie.rested:
        return
    cookie.rested = True
    if ctx.me.items_played_this_turn > 0:
        target = ctx.select_enemy()
        if target is not None:
            ctx.deal_damage(target, 1)
    green = Cost.parse("{G}")
    archer = ctx.select_own(lambda c: c.name(ctx.db) == WIND_ARCHER)
    if archer is not None and ctx.can_pay(green) and ctx.pay(green):
        ctx.gain_hp(archer, 1)


# --- ST8-013 Windgrass (ITEM) ----------------------------------------------
@effect("ST8-013", Trigger.ITEM)
def windgrass(ctx: Ctx) -> None:
    """Rest up to 1 card in your opponent's support area. Then, if [Wind Archer
    Cookie] is in your battle area, set up to 1 card from your support area as
    active."""
    ctx.rest_support(1, mine=False)
    if ctx.name_in_battle(WIND_ARCHER):
        ctx.set_support_active(1)


# --- ST8-014 Cape of the Vanquisher (ITEM) ---------------------------------
@effect("ST8-014", Trigger.ITEM)
def cape_of_the_vanquisher(ctx: Ctx) -> None:
    """If there are 5 cards or more in your support area, select up to 1 of
    your {G} Cookies with 3 or less HP remaining. That Cookie gains +1 HP."""
    if ctx.support_count() < 5:
        return
    target = ctx.select_own(
        lambda c: c.defn(ctx.db).color is Color.GREEN and c.remaining_hp <= 3
    )
    if target is not None:
        ctx.gain_hp(target, 1)


# --- ST8-015 Essence of the Tempest (ITEM) ---------------------------------
@effect("ST8-015", Trigger.ITEM)
def essence_of_the_tempest(ctx: Ctx) -> None:
    """If [Wind Archer Cookie] is in your battle or support area, place up to 1
    card from the top of your deck into your support area as rested. Then,
    return up to 1 card from your support area to your hand."""
    if ctx.name_in_battle(WIND_ARCHER) or ctx.name_in_support(WIND_ARCHER):
        ctx.mill_to_support(1)
    ctx.return_support_to_hand()


# --- ST8-016 Wind's Grace Earrings (ITEM) ----------------------------------
@effect("ST8-016", Trigger.ITEM)
def winds_grace_earrings(ctx: Ctx) -> None:
    """If [Wind Archer Cookie] is in your battle area and there are 5 cards or
    more in your support area, all of your opponent's Cookies receive 1 damage."""
    if ctx.name_in_battle(WIND_ARCHER) and ctx.support_count() >= 5:
        for cookie in list(ctx.enemy_cookies()):
            ctx.deal_damage(cookie, 1)


# --- ST8-017 Hindering Darkness (TRAP) -------------------------------------
@effect("ST8-017", Trigger.ITEM)
def hindering_darkness(ctx: Ctx) -> None:
    """-2 attack damage. Then, if the number of cards in both support areas
    match, draw up to 1 card."""
    target = ctx.select_enemy(prompt="Debuff which attacker?")
    if target is not None:
        ctx.modify_attack(target, -2)
    if ctx.support_count() == ctx.support_count(mine=False):
        ctx.draw(1)


# --- ST8-018 Piercing Arrow of Purity (TRAP) -------------------------------
@effect("ST8-018", Trigger.ITEM)
def piercing_arrow(ctx: Ctx) -> None:
    """-1 attack damage. Then, if there are 5 cards or more in your support
    area, an additional -1."""
    target = ctx.select_enemy(prompt="Debuff which attacker?")
    if target is None:
        return
    ctx.modify_attack(target, -1)
    if ctx.support_count() >= 5:
        ctx.modify_attack(target, -1)


# --- ST8-019 Bad Omen (TRAP) -----------------------------------------------
@effect("ST8-019", Trigger.ITEM)
def bad_omen(ctx: Ctx) -> None:
    """-2 attack damage. Then, if the number of cards in both support areas
    match, an additional -1."""
    target = ctx.select_enemy(prompt="Debuff which attacker?")
    if target is None:
        return
    ctx.modify_attack(target, -2)
    if ctx.support_count() == ctx.support_count(mine=False):
        ctx.modify_attack(target, -1)


# --- ST8-020 Dark Enchantress Laboratory (STAGE) ---------------------------
@effect("ST8-020", Trigger.END_TURN)
def dark_enchantress_lab(ctx: Ctx) -> None:
    """When your turn ends, <place this card in your trash.> Set up to 2 cards
    from your support area as active."""
    card = ctx.source_card
    if card is None or card not in ctx.me.stage:
        return
    if not ctx.confirm("Trash Dark Enchantress Laboratory to ready 2 supports?"):
        return
    ctx.me.stage.remove(card)
    ctx.me.trash.append(card)
    ctx.set_support_active(2)
