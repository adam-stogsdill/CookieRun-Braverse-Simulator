"""Hand-written cards from the ST starter sets.

These are the cards the text compiler cannot reach: static 【Your Turn】 buffs,
"Cookie *or* stage card" choices, deck-viewing, and asymmetric damage splits.
Everything here was checked against the printed text quoted in each docstring.
"""

from __future__ import annotations

from braverse.cost import Cost
from braverse.effects import Ctx, Trigger, effect, playable_if
from braverse.enums import Color


# --- ST1-009 Espresso Cookie ------------------------------------------------
@effect("ST1-009", Trigger.ATTACK_START)
def espresso_your_turn(ctx: Ctx) -> None:
    """【Your Turn】 If your break area is LV.6 or higher, this Cookie gains +1
    attack damage.

    A static buff, so it is evaluated as the attack is declared rather than
    stored. The Cookie rests to attack, so it cannot double-count in a turn.
    """
    if ctx.source_cookie and ctx.me.break_level_total(ctx.db) >= 6:
        ctx.modify_attack(ctx.source_cookie, 1)


# --- ST2-015 Hero Cookie ----------------------------------------------------
@effect("ST2-015", Trigger.ATTACK)
def hero_attack(ctx: Ctx) -> None:
    """Then, select up to 1 of your opponent's LV.1 Cookies. That Cookie cannot
    attack during the next turn.

    Modelled as staying rested through the owner's next Active Phase, which is
    what stops it attacking.
    """
    target = ctx.select_enemy(lambda c: c.level(ctx.db) == 1)
    if target is not None:
        target.rested = True
        ctx.skip_next_active(target)


# --- ST4-004 Lobster Cookie -------------------------------------------------
@effect("ST4-004", Trigger.ACTIVATE)
def lobster_activate(ctx: Ctx) -> None:
    """<Discard 3 cards.> Set this Cookie and 1 card in your support area as
    active."""
    if not ctx.discard(3, optional=True):
        return
    if ctx.source_cookie:
        ctx.source_cookie.rested = False
    ctx.set_support_active(1)


# --- ST4-013 Captain Caviar Cookie ------------------------------------------
@effect("ST4-013", Trigger.ON_PLAY)
def captain_caviar_activate(ctx: Ctx) -> None:
    """View the top 3 cards of your deck; you can draw 1 of them to your hand.
    Then, place the remaining cards at the bottom of your deck in any order."""
    viewed = ctx.me.deck[:3]
    if not viewed:
        return
    del ctx.me.deck[:len(viewed)]
    picked = ctx.choose("Draw one of the top 3 cards", list(viewed), optional=True)
    if picked is not None:
        viewed.remove(picked)
        ctx.me.hand.append(picked)
    ctx.me.deck.extend(viewed)


# --- ST5-001 / ST5-006 / ST5-007 shared removal -----------------------------
def _trash_cookie_or_stage(ctx: Ctx, max_level: int | None) -> None:
    """"Place 1 of your opponent's LV.N Cookies from their battle area or 1
    stage card from their stage area into the trash."

    One choice across two zones, so the options list mixes Cookies and stage
    cards and the branch is taken on what came back.
    """
    cookies = [c for c in ctx.opp.battle
               if max_level is None or c.level(ctx.db) <= max_level]
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


@effect("ST5-001", Trigger.ON_PLAY)
def madeleine_activate(ctx: Ctx) -> None:
    """<{P}> Place 1 of your opponent's LV.1 Cookies from their battle area or
    1 stage card from their stage area into the trash."""
    from braverse.cost import Cost
    if ctx.pay(Cost.parse("{P}")):
        _trash_cookie_or_stage(ctx, max_level=1)


@effect("ST5-006", Trigger.ON_PLAY)
def string_gummy_activate(ctx: Ctx) -> None:
    """<{P}{P}> ... LV.2 or lower Cookies ... or 1 stage card ... into the trash."""
    from braverse.cost import Cost
    if ctx.pay(Cost.parse("{P}{P}")):
        _trash_cookie_or_stage(ctx, max_level=2)


@effect("ST5-007", Trigger.ACTIVATE)
def yoga_activate(ctx: Ctx) -> None:
    """<{P}> <Discard 1 card.> Place 1 of your opponent's LV.1 Cookies from
    their battle area or 1 stage card from their stage area into the trash."""
    from braverse.cost import Cost
    if not ctx.pay(Cost.parse("{P}")):
        return
    if not ctx.discard(1, optional=True):
        return
    _trash_cookie_or_stage(ctx, max_level=1)


# --- ST6-016 Dragon's Breath ------------------------------------------------
@effect("ST6-016", Trigger.ITEM)
def dragons_breath(ctx: Ctx) -> None:
    """<Discard 1 card.> Select up to 2 of your opponent's Cookies. Deals 2
    damage to 1 of the Cookies and 1 damage to the other."""
    if not ctx.discard(1, optional=True):
        return
    targets = ctx.enemy_cookies()
    if not targets:
        return
    primary = ctx.choose("Deal 2 damage to which Cookie?", targets, optional=True)
    if primary is None:
        return
    others = [c for c in targets if c is not primary]
    secondary = (ctx.choose("Deal 1 damage to which Cookie?", others, optional=True)
                 if others else None)
    ctx.deal_damage(primary, 2)
    if secondary is not None and secondary in ctx.opp.battle:
        ctx.deal_damage(secondary, 1)


# --- ST6-017 Restored Power of Fire -----------------------------------------
@effect("ST6-017", Trigger.ITEM)
def restored_power_of_fire(ctx: Ctx) -> None:
    """Select up to 1 of your opponent's Cookies. During this turn, that Cookie
    deals -2 attack damage. Then, if the remaining HP of one of your Cookies is
    1, that opponent's Cookie deals an additional -1 attack damage."""
    target = ctx.select_enemy(prompt="Debuff which attacker?")
    if target is None:
        return
    ctx.modify_attack(target, -2)
    if any(c.remaining_hp == 1 for c in ctx.me.battle):
        ctx.modify_attack(target, -1)


# --- ST9-009 Wave Drop ------------------------------------------------------
@effect("ST9-009", Trigger.TRASHED)
def wave_drop_trashed(ctx: Ctx) -> None:
    """When this Cookie is placed from your hand into your trash by the effect
    of your [Sea Fairy Cookie], draw up to 1 card from your deck.

    Sea Fairy's own discard already handles the hand case; this covers the same
    card being removed from the battle area.
    """
    ctx.draw(1)


# --- ST10-002 Moonlight Cookie ----------------------------------------------
@effect("ST10-002", Trigger.ON_PLAY)
def moonlight_on_play(ctx: Ctx) -> None:
    """Play up to 1 {P} Cookie that is LV.2 or lower from your trash."""
    ctx.play_cookie_from_trash(
        lambda d: d.color is Color.PURPLE and (d.level or 0) <= 2
    )


# --- ST10-006 Blueberry Pie Cookie ------------------------------------------
@effect("ST10-006", Trigger.TRASHED)
def blueberry_pie_trashed(ctx: Ctx) -> None:
    """When this Cookie is placed from your battle area into your trash, select
    up to 1 of your opponent's Cookies. That Cookie receives 1 damage."""
    target = ctx.select_enemy()
    if target is not None:
        ctx.deal_damage(target, 1)


# --- ST10-009 Space Doughnut ------------------------------------------------
@effect("ST10-009", Trigger.TRASHED)
def space_doughnut_trashed(ctx: Ctx) -> None:
    """When this Cookie is placed from your battle area into your trash, draw
    up to 1 card from your deck."""
    ctx.draw(1)


# --- ST3-020 Divine Light Crystal (TRAP) ------------------------------------
@effect("ST3-020", Trigger.ITEM)
@playable_if(lambda ctx: bool(ctx.own_cookies())
             and ctx.can_pay(Cost.parse("{G}{G}")))
def divine_light_crystal(ctx: Ctx) -> None:
    """<{G}{G}> Select up to 1 of your Cookies. That Cookie's HP cannot reach 0
    during this battle.

    The Cookie keeps taking the damage — every card the hit would turn is still
    turned, FLIPs and all — it just cannot be the last one: `deal_damage` pulls
    a replacement off the deck whenever the pile would empty.
    """
    target = ctx.select_own(prompt="Divine Light Crystal: protect one of your Cookies")
    if target is None or not ctx.pay(Cost.parse("{G}{G}")):
        return
    target.hp_cannot_reach_zero = True
    ctx.note(f"{target.name(ctx.db)}'s HP cannot reach 0 during this battle")
