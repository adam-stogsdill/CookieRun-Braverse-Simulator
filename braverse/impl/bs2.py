"""Hand-written cards from BS2.

The compiler covers the rest of the set; these are the clauses it cannot
express — running totals, "either A or B" removal across two zones, deck
viewing, optional self-sacrifice, and static buffs.
"""

from __future__ import annotations

from braverse.effects import Ctx, Trigger, effect
from braverse.enums import Color


# --- BS2-011 Blackberry Cookie ----------------------------------------------
@effect("BS2-011", Trigger.ACTIVATE)
def blackberry_activate(ctx: Ctx) -> None:
    """Select {Y} Cookies from your break area until their total LV. sum
    reaches LV.3. Return those Cookies to your hand and place this Cookie into
    your break area.

    A running total, so the selection loop has to re-check after each pick.
    """
    total = 0
    picked = []
    while total < 3:
        options = [c for c in ctx.me.break_area
                   if ctx.db[c.card_id].color is Color.YELLOW
                   and ctx.db[c.card_id].is_cookie and c not in picked]
        if not options:
            break
        card = ctx.choose("Return a {Y} Cookie from your break area",
                          options, optional=True)
        if card is None:
            break
        picked.append(card)
        total += ctx.db[card.card_id].level or 0

    for card in picked:
        ctx.me.break_area.remove(card)
        ctx.me.hand.append(card)
    if ctx.source_cookie is not None:
        # Faint sends it to the break area, which is exactly what is asked.
        ctx.faint(ctx.source_cookie)


# --- BS2-022 Licorice Cookie ------------------------------------------------
@effect("BS2-022", Trigger.ON_PLAY)
def licorice_activate(ctx: Ctx) -> None:
    """This Cookie takes no damage from effects until the start of the player's
    next turn.

    Immunity is to *effect* damage only — attacks still connect. The flag is
    cleared in the Active Phase.
    """
    if ctx.source_cookie is not None:
        ctx.source_cookie.effect_damage_immune = True


# --- BS2-025 Mango Cookie ---------------------------------------------------
@effect("BS2-025", Trigger.FAINT)
def mango_faint(ctx: Ctx) -> None:
    """When this Cookie faints, you can draw 1 card from your deck. If you did,
    discard 1 card from your hand."""
    if ctx.draw(1):
        ctx.discard(1, optional=True)


# --- BS2-028 Pond Dino Cookie -----------------------------------------------
@effect("BS2-028", Trigger.ACTIVATE)
def pond_dino_activate(ctx: Ctx) -> None:
    """<Discard 1 card.> During this turn, your opponent cannot activate
    【Blocker】."""
    if ctx.discard(1, optional=True):
        ctx.opp.blockers_disabled = True


# --- BS2-031 Black Raisin Cookie --------------------------------------------
@effect("BS2-031", Trigger.ON_PLAY)
def black_raisin_activate(ctx: Ctx) -> None:
    """<Discard 3 cards.> Select up to 2 of your opponent's Cookies. Deals 2
    damage to 1 of the Cookies and 1 damage to the other."""
    if not ctx.discard(3, optional=True):
        return
    _split_damage(ctx, 2, 1)


def _split_damage(ctx: Ctx, big: int, small: int) -> None:
    """Shared by the "N damage to one and M to the other" cards."""
    targets = ctx.enemy_cookies()
    if not targets:
        return
    primary = ctx.choose(f"Deal {big} damage to which Cookie?", targets,
                         optional=True)
    if primary is None:
        return
    others = [c for c in targets if c is not primary]
    secondary = (ctx.choose(f"Deal {small} damage to which Cookie?", others,
                            optional=True) if others else None)
    ctx.deal_damage(primary, big)
    if secondary is not None and secondary in ctx.opp.battle:
        ctx.deal_damage(secondary, small)


# --- BS2-040 Aloe Cookie ----------------------------------------------------
@effect("BS2-040", Trigger.FAINT)
def aloe_faint(ctx: Ctx) -> None:
    """When this Cookie faints, view the top 3 cards of your deck. Out of the 3
    cards, select 1 {B} card, show it to your opponent, and place that card in
    your hand. Then, return the remaining cards to the bottom of your deck in
    any order.

    "View the top 3" and "select 1 {B}" are two separate instructions, and this
    used to run them as one: it filtered to the blue cards and offered only
    those, so a card that lets you look at three showed you one. `pick` narrows
    what is selectable and nothing else — all three are still put in front of
    you, which is most of what the card is for.
    """
    ctx.view_top(3, pick=lambda d: d.color is Color.BLUE,
                 criterion="{B}", reveal=True, rest="bottom")


# BS2-047 Diving Goggles was written here by hand, against a dump that split
# its line and lost the damage number. The line is whole in the CSV now and
# the compiler reads the card in full — cost, discard, two targets and 2
# damage each — so the hand-written version is gone. It was doing nothing
# regardless: this is an ITEM, an item's body runs on `Trigger.ITEM`, and the
# effect was registered against `Trigger.ATTACK`, which only a Cookie in the
# battle area ever fires. The card was inert and `is_implemented` said
# otherwise, which is how it stayed that way.


# --- BS2-060 Beet Cookie ----------------------------------------------------
@effect("BS2-060", Trigger.FAINT)
def beet_faint(ctx: Ctx) -> None:
    """When this Cookie faints and your opponent has 20 cards or more in their
    trash, you can draw 1 card from your deck."""
    if len(ctx.opp.trash) >= 20:
        ctx.draw(1)


# --- BS2-062 Starfruit Cookie -----------------------------------------------
@effect("BS2-062", Trigger.ON_PLAY)
def starfruit_activate(ctx: Ctx) -> None:
    """<{P}> Other than this Cookie, you can place 1 {P} Cookie that is LV.2 or
    lower from your battle area into the trash. If you did, place up to 1 of
    your opponent's Cookies that is LV.2 or lower from their battle area into
    the trash."""
    from braverse.cost import Cost

    if not ctx.pay(Cost.parse("{P}")):
        return
    fodder = ctx.select_own(
        lambda c: c is not ctx.source_cookie
        and c.defn(ctx.db).color is Color.PURPLE
        and c.level(ctx.db) <= 2,
        prompt="Trash one of your own {P} Cookies?",
    )
    if fodder is None:
        return
    ctx.trash_cookie(fodder)
    victim = ctx.select_enemy(lambda c: c.level(ctx.db) <= 2)
    if victim is not None:
        ctx.trash_cookie(victim)


# --- BS2-063 Space Doughnut (FLIP) ------------------------------------------
@effect("BS2-063", Trigger.FLIP)
def space_doughnut_flip(ctx: Ctx) -> None:
    """<Discard 1 card.> If your break area is LV.3 or higher, place either 1
    of your opponent's Cookies that is LV.2 or lower from their battle area or
    1 stage card from their stage area into the trash."""
    if ctx.me.break_level_total(ctx.db) < 3:
        return
    if not ctx.discard(1, optional=True):
        return
    cookies = [c for c in ctx.opp.battle if c.level(ctx.db) <= 2]
    options = list(cookies) + list(ctx.opp.stage)
    if not options:
        return
    picked = ctx.choose("Trash a Cookie or a stage card", options, optional=True)
    if picked is None:
        return
    if picked in cookies:
        ctx.trash_cookie(picked)
    else:
        ctx.opp.stage.remove(picked)
        ctx.opp.trash.append(picked)


# --- BS2-073 Peperoncino Cookie ---------------------------------------------
@effect("BS2-073", Trigger.ATTACK_START)
def peperoncino_static(ctx: Ctx) -> None:
    """If there are 15 cards or more in your trash, this Cookie deals +2 attack
    damage. A static buff, so it is read as the attack is declared."""
    if ctx.source_cookie and len(ctx.me.trash) >= 15:
        ctx.modify_attack(ctx.source_cookie, 2)
