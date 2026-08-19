"""Hand-written cards from BS4.

The set's signature is a Blocker body that also shrugs off small attackers, plus
a modal "choose one" effect and several deck-ordering tricks.
"""

from __future__ import annotations

from braverse.cost import Cost
from braverse.effects import (STATIC_ABILITY_CARDS, TAUNT_PROVIDERS,
                              Ctx, Trigger, effect)
from braverse.enums import Color

# Blocker Cookies that also reduce damage taken from LV.1 attackers. The
# reduction is conditional on the *attacker's* level, so it is applied in the
# when-attacked window where both Cookies are known.
_LV1_RESISTERS = ("BS4-014", "BS4-027", "BS4-047", "BS4-080", "BS4-100")


def _resist_lv1(ctx: Ctx) -> None:
    """This Cookie receives -1 attack damage from LV.1 Cookies.

    Conditional on the *attacker's* level, so it reads ``ctx.attacker`` rather
    than guessing which Cookie declared the attack.
    """
    cookie = ctx.source_cookie
    if cookie is None or ctx.attacker is None:
        return
    if ctx.attacker.level(ctx.db) == 1:
        cookie.incoming_damage_reduction += 1


for _card_id in _LV1_RESISTERS:
    effect(_card_id, Trigger.WHEN_ATTACKED)(_resist_lv1)


# --- BS4-011 Chili Pepper Cookie --------------------------------------------
@effect("BS4-011", Trigger.ATTACK)
def chili_pepper_attack(ctx: Ctx) -> None:
    """If your opponent's Cookie faints from this Cookie's attack, draw 1 card
    from your deck and discard 1 card."""
    if getattr(ctx.game, "_attack_killed", False):
        if ctx.draw(1):
            ctx.discard(1, optional=True)


# --- BS4-012 Capsaicin Cookie -----------------------------------------------
@effect("BS4-012", Trigger.ATTACK_START)
def capsaicin_static(ctx: Ctx) -> None:
    """If this Cookie's remaining HP is 1, this Cookie gains +2 attack damage."""
    cookie = ctx.source_cookie
    if cookie is not None and cookie.remaining_hp == 1:
        ctx.modify_attack(cookie, 2)


# --- BS4-024 Kumiho Cookie --------------------------------------------------
def _kumiho_taunt(db, defender):
    """"If there is a {Y} LV.3 Cookie in your battle area, your opponent's
    Cookies can only attack this Cookie."

    A targeting restriction, so it is enforced where attacks are enumerated
    rather than as a trigger.
    """
    kumiho = next((c for c in defender.battle
                   if c.defn(db).base_id == "BS4-024"), None)
    if kumiho is None:
        return None
    has_lv3_yellow = any(c.defn(db).color is Color.YELLOW
                         and c.level(db) == 3 for c in defender.battle)
    return kumiho if has_lv3_yellow else None


TAUNT_PROVIDERS.append(_kumiho_taunt)
STATIC_ABILITY_CARDS.add("BS4-024")


# --- BS4-030 Peach Blossom Cookie -------------------------------------------
@effect("BS4-030", Trigger.ON_PLAY)
def peach_blossom_activate(ctx: Ctx) -> None:
    """<{Y}> Select up to 1 of your other {Y} Cookies in your battle area.
    Return 1 card from the top of that Cookie's HP to your hand. Then, place up
    to 1 card from your hand to the top of that Cookie's HP."""
    if not ctx.pay(Cost.parse("{Y}")):
        return
    target = ctx.select_own(
        lambda c: c is not ctx.source_cookie
        and c.defn(ctx.db).color is Color.YELLOW)
    if target is None:
        return
    if target.hp_cards:
        ctx.me.hand.append(target.hp_cards.pop())
        if not target.hp_cards:
            ctx.game.faint(target)
            return
    if not ctx.me.hand:
        return
    card = ctx.choose("Place a card on top of that Cookie's HP",
                      list(ctx.me.hand), optional=True)
    if card is not None:
        ctx.me.hand.remove(card)
        card.face_up = False
        target.hp_cards.append(card)


# --- BS4-072 Mystic Opal Cookie (FLIP) --------------------------------------
@effect("BS4-072", Trigger.FLIP)
def mystic_opal_flip(ctx: Ctx) -> None:
    """View 3 cards from the top of your deck; return them to the top of your
    deck in any order.

    FLIPs are ordered to the front so they land in the next HP pile built.
    """
    viewed = ctx.me.deck[:3]
    if len(viewed) < 2:
        return
    del ctx.me.deck[:len(viewed)]
    viewed.sort(key=lambda c: not ctx.db[c.card_id].is_flip)
    ctx.me.deck[:0] = viewed


# --- BS4-075 Black Pearl Cookie ---------------------------------------------
@effect("BS4-075", Trigger.ACTIVATE)
def black_pearl_activate(ctx: Ctx) -> None:
    """<{B}> <Select 1 LV.1 Cookie from your opponent's battle area or 1 stage
    from either player's stage area and place it on the bottom of the owner's
    deck.> During this turn, this Cookie gains +1 attack damage."""
    if not ctx.pay(Cost.parse("{B}")):
        return
    cookies = [c for c in ctx.opp.battle if c.level(ctx.db) == 1]
    stages = list(ctx.me.stage) + list(ctx.opp.stage)
    options = cookies + stages
    if not options:
        return
    picked = ctx.choose("Deck a LV.1 Cookie or a stage card", options,
                        optional=True)
    if picked is None:
        return
    if picked in cookies:
        ctx.opp.battle.remove(picked)
        ctx.opp.deck.append(picked.card)
        ctx.opp.trash.extend(picked.spent_cards)
        ctx.game._check_battle_area(ctx.opp)
    else:
        owner = ctx.me if picked in ctx.me.stage else ctx.opp
        owner.stage.remove(picked)
        owner.deck.append(picked)
    if ctx.source_cookie is not None:
        ctx.modify_attack(ctx.source_cookie, 1)


# --- BS4-081 Crimson Coral Cookie -------------------------------------------
@effect("BS4-081", Trigger.ON_PLAY)
def crimson_coral_activate(ctx: Ctx) -> None:
    """<Discard 1 card.> Select 1 of the following.
    - Select up to 1 LV.1 Cookie in your opponent's battle area. Place that
      Cookie on the bottom of your opponent's deck.
    - Draw up to 2 cards from your deck.

    A modal effect: the mode is itself a decision, so it is offered to the
    controller before either branch runs.
    """
    if not ctx.discard(1, optional=True):
        return
    mode = ctx.choose("Choose one", ["deck a LV.1 Cookie", "draw 2"],
                      optional=False) or "draw 2"
    if mode == "draw 2":
        ctx.draw(2)
        return
    target = ctx.select_enemy(lambda c: c.level(ctx.db) == 1)
    if target is not None:
        ctx.opp.battle.remove(target)
        ctx.opp.deck.append(target.card)
        ctx.opp.trash.extend(target.spent_cards)
        ctx.game._check_battle_area(ctx.opp)


# --- BS4-089 Moonlight Cookie -----------------------------------------------
@effect("BS4-089", Trigger.ON_PLAY)
def moonlight_blue_activate(ctx: Ctx) -> None:
    """Place 5 cards from the top of your opponent's deck in the trash. Then,
    if your opponent has 2 Cookies in their battle area, select up to 1 of your
    opponent's Cookies. Place that Cookie in the trash."""
    for _ in range(5):
        if not ctx.opp.deck:
            break
        ctx.opp.trash.append(ctx.opp.deck.pop(0))
    if len(ctx.opp.battle) < 2:
        return
    target = ctx.select_enemy()
    if target is not None:
        ctx.trash_cookie(target)
