"""ST9 — Seas of Fate (Blue / Sea Fairy Cookie).

Each function is the card's printed text, sentence by sentence.
"""

from braverse.cost import Cost
from braverse.effects import Ctx, Trigger, effect
from braverse.enums import Color

SEA_FAIRY = "Sea Fairy Cookie"


# --- ST9-003 Blue Whale Cookie (FLIP) --------------------------------------
@effect("ST9-003", Trigger.FLIP)
def blue_whale_flip(ctx: Ctx) -> None:
    """Return this Cookie to your hand."""
    if ctx.source_cookie:
        ctx.return_to_hand(ctx.source_cookie)


# --- ST9-004 Pond Dino Cookie (FLIP) ---------------------------------------
@effect("ST9-004", Trigger.FLIP)
def pond_dino_flip(ctx: Ctx) -> None:
    """Draw up to 1 card from your deck."""
    ctx.draw(1)


# --- ST9-006 Sea Fairy Cookie ----------------------------------------------
@effect("ST9-006", Trigger.ACTIVATE)
def sea_fairy_activate(ctx: Ctx) -> None:
    """If there are 5 cards or less in your hand, draw up to 1 card."""
    if ctx.hand_size <= 5:
        ctx.draw(1)


@effect("ST9-006", Trigger.ATTACK)
def sea_fairy_attack(ctx: Ctx) -> None:
    """Then, <discard 2 cards.> All of your opponent's Cookies receive 1 damage."""
    if not ctx.enemy_cookies():
        return
    discarded = ctx.discard(2, optional=True)
    if not discarded:
        return
    # ST9-009 Wave Drop: "When this Cookie is placed from your hand into your
    # trash by the effect of your [Sea Fairy Cookie], draw up to 1 card."
    for card in discarded:
        if ctx.db[card.card_id].base_id == "ST9-009":
            ctx.draw(1)
    for cookie in list(ctx.enemy_cookies()):
        ctx.deal_damage(cookie, 1)


# --- ST9-007 Peppermint Cookie ---------------------------------------------
@effect("ST9-007", Trigger.ACTIVATE)
def peppermint_activate(ctx: Ctx) -> None:
    """If there are 3 cards or less in your hand, draw up to 1 card."""
    if ctx.hand_size <= 3:
        ctx.draw(1)


# --- ST9-008 Crimson Coral Cookie ------------------------------------------
@effect("ST9-008", Trigger.ON_PLAY)
def crimson_coral_on_play(ctx: Ctx) -> None:
    """<Return 1 Cookie that is LV.2 or lower from your battle area to your
    hand.> This Cookie gains +1 HP."""
    others = [c for c in ctx.own_cookies()
              if c is not ctx.source_cookie and c.level(ctx.db) <= 2]
    if not others:
        return
    chosen = ctx.choose("Return a LV.2 or lower Cookie to hand", others, optional=True)
    if chosen is None:
        return
    ctx.return_to_hand(chosen)
    if ctx.source_cookie:
        ctx.gain_hp(ctx.source_cookie, 1)


# --- ST9-010 / ST9-011 shared FLIP -----------------------------------------
def _flip_bolster(ctx: Ctx) -> None:
    """<Discard 1 card.> The Cookie with this card attached for HP gains +1 HP."""
    if ctx.source_cookie and ctx.discard(1, optional=True):
        ctx.gain_hp(ctx.source_cookie, 1)


effect("ST9-010", Trigger.FLIP)(_flip_bolster)
effect("ST9-011", Trigger.FLIP)(_flip_bolster)


# --- ST9-012 White Pearl Cookie --------------------------------------------
@effect("ST9-012", Trigger.ON_PLAY)
def white_pearl_on_play(ctx: Ctx) -> None:
    """If there are 4 cards or less in your hand, draw up to 2 cards."""
    if ctx.hand_size <= 4:
        ctx.draw(2)


@effect("ST9-012", Trigger.ATTACK)
def white_pearl_attack(ctx: Ctx) -> None:
    """Then, <{B}> If [Sea Fairy Cookie] is in your battle area, select up to 1
    of your opponent's Cookies. That Cookie receives 1 damage."""
    if not ctx.name_in_battle(SEA_FAIRY):
        return
    if not ctx.can_pay(Cost.parse("{B}")):
        return
    target = ctx.select_enemy()
    if target is None:
        return
    ctx.pay(Cost.parse("{B}"))
    ctx.deal_damage(target, 1)


# --- ST9-013 Shimmering Moonlit Coral (ITEM) -------------------------------
@effect("ST9-013", Trigger.ITEM)
def moonlit_coral(ctx: Ctx) -> None:
    """<Discard 1 {B} card from your hand.> Draw up to 2 cards."""
    if ctx.discard_colored(1, Color.BLUE):
        ctx.draw(2)


# --- ST9-014 Glittering Pearl Shell (ITEM) ---------------------------------
@effect("ST9-014", Trigger.ITEM)
def pearl_shell(ctx: Ctx) -> None:
    """<Discard 3 cards.> Select up to 1 of your Cookies. That Cookie gains +2 HP."""
    target = ctx.select_own()
    if target is None:
        return
    if ctx.discard(3, optional=True):
        ctx.gain_hp(target, 2)


# --- ST9-015 Essence of the Ocean (ITEM) -----------------------------------
@effect("ST9-015", Trigger.ITEM)
def essence_of_ocean(ctx: Ctx) -> None:
    """If there are 5 cards or less in your hand, draw up to 1 card. Then,
    select up to 1 of your [Sea Fairy Cookie] with 5 or less HP remaining.
    That Cookie gains +1 HP."""
    if ctx.hand_size <= 5:
        ctx.draw(1)
    target = ctx.select_own(
        lambda c: c.name(ctx.db) == SEA_FAIRY and c.remaining_hp <= 5
    )
    if target is not None:
        ctx.gain_hp(target, 1)


# --- ST9-016 Bubble Wave Shell (ITEM) --------------------------------------
@effect("ST9-016", Trigger.ITEM)
def bubble_wave_shell(ctx: Ctx) -> None:
    """<Discard 3 cards.> Select up to 1 of your opponent's Cookies.
    That Cookie receives 3 damage."""
    target = ctx.select_enemy()
    if target is None:
        return
    if ctx.discard(3, optional=True):
        ctx.deal_damage(target, 3)


# --- ST9-017 Revelation of the Seas (TRAP) ---------------------------------
@effect("ST9-017", Trigger.ITEM)
def revelation_of_the_seas(ctx: Ctx) -> None:
    """That Cookie deals -3 attack damage. Then, if there are 3 cards or less
    in your hand, draw up to 2 cards."""
    target = ctx.select_enemy(prompt="Debuff which attacker?")
    if target is not None:
        ctx.modify_attack(target, -3)
    if ctx.hand_size <= 3:
        ctx.draw(2)


# --- ST9-018 Tower of Frozen Waves (TRAP) ----------------------------------
@effect("ST9-018", Trigger.ITEM)
def tower_of_frozen_waves(ctx: Ctx) -> None:
    """-1 attack damage. Then, <discard 1 card.> an additional -1."""
    target = ctx.select_enemy(prompt="Debuff which attacker?")
    if target is None:
        return
    ctx.modify_attack(target, -1)
    if ctx.discard(1, optional=True):
        ctx.modify_attack(target, -1)


# --- ST9-019 Curse of the Seas (TRAP) --------------------------------------
@effect("ST9-019", Trigger.ITEM)
def curse_of_the_seas(ctx: Ctx) -> None:
    """-1 attack damage. Then, <discard 2 cards.> Select up to 1 of your
    opponent's Cookies that has 2 or more HP remaining. That Cookie receives
    1 damage."""
    target = ctx.select_enemy(prompt="Debuff which attacker?")
    if target is not None:
        ctx.modify_attack(target, -1)
    victims = ctx.enemy_cookies(lambda c: c.remaining_hp >= 2)
    if not victims:
        return
    victim = ctx.choose("Damage which Cookie?", victims, optional=True)
    if victim is not None and ctx.discard(2, optional=True):
        ctx.deal_damage(victim, 1)


# --- ST9-020 Tearcrown (STAGE) ---------------------------------------------
@effect("ST9-020", Trigger.STAGE_ACTIVATE)
def tearcrown(ctx: Ctx) -> None:
    """<Rest this card.> If there are 3 cards or less in your hand, draw up to 1."""
    if ctx.source_card is None or ctx.source_card.rested:
        return
    ctx.source_card.rested = True
    if ctx.hand_size <= 3:
        ctx.draw(1)
