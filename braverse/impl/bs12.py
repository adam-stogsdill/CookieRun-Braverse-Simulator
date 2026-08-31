"""BS12 — BOOSTER PACK [Festival Arena].

The set is built around 【Arena】, and its two entrances are the ones this
module wires up: six 【EXTRA】 Cookies, each with its own gate, and five
【Special Play】 bodies that print the same sentence BS11's Dough Cookies do.

Almost every attack line in the set carries a "if you started the game going
second" rider. That is the set's compensation mechanic, and it compiles — the
condition is a plain question about `GameState.first_player` — so those riders
are not written out here. What is written here is what the compiler refuses:
the play gates, which are conditions rather than clauses, and the two
continuous abilities.
"""

from __future__ import annotations

from braverse.cost import Cost
from braverse.effects import (ACTIVE_PHASE_LOCKS, ATTACK_DAMAGE_AURAS,
                              ATTACK_EFFECT_LOCKS, DAMAGE_CAPS,
                              ITEM_SURCHARGES, STATIC_ABILITY_CARDS, Ctx,
                              Trigger, effect, extra_play, special_play)
from braverse.enums import Color, Keyword, Marker
from braverse.state import card_label

from .bs11 import register_special_play


def _is_your_turn(ctx: Ctx) -> bool:
    """【Your Turn】: the ability only works on its controller's own turn."""
    return ctx.state.turn_player == ctx.me.index


def _is_arena(defn) -> bool:
    return Keyword.ARENA in defn.keywords


def _arena_cards(ctx: Ctx, cards, color: Color | None = None) -> int:
    """How many of `cards` are 【Arena】 cards of `color`."""
    out = 0
    for card in cards:
        defn = ctx.db[card.card_id]
        if _is_arena(defn) and (color is None or defn.color is color):
            out += 1
    return out


# --- 【Special Play】 --------------------------------------------------------
# "Place 1 {K} LV.1 Cookie from your battle area into your trash." — the same
# entrance BS11's Dough Cookies print, so it is the same registration. The
# four Cake Hounds are LV.1 FLIPs; Red Velvet asks for a LV.1 Cookie that has
# Special Play, which is what `marked` means.
register_special_play("BS12-095", 1, 1)     # Blueberry Cake Hound
register_special_play("BS12-096", 1, 1)     # Crimson Danger Cake Hound
register_special_play("BS12-098", 1, 1)     # Caramel Pudding Cake Hound
register_special_play("BS12-100", 1, 1)     # Strategist Cake Hound
register_special_play("BS12-112", 1, 1, marked=True)   # Red Velvet Cookie


# --- 【EXTRA】 gates ---------------------------------------------------------
def _discard_one_arena(ctx: Ctx) -> bool:
    return bool(ctx.discard_matching(1, _is_arena))


@extra_play("BS12-018", pay=_discard_one_arena)
def shining_glitter_gate(ctx: Ctx) -> bool:
    """【EXTRA】 If your break area is LV.4 or higher, <discard 1 【Arena】 card
    from your hand.> Play this Cookie."""
    if ctx.me.break_level_total(ctx.db) < 4:
        return False
    # The discard is part of the move, so a hand that cannot pay it is not a
    # hand that can make it.
    return _arena_cards(ctx, ctx.me.hand) >= 1


@extra_play("BS12-036")
def clotted_cream_gate(ctx: Ctx) -> bool:
    """【EXTRA】 Can be played if there are 4 {Y} 【Arena】 Cookies or more in
    your break area."""
    return _arena_cards(ctx, ctx.me.break_area, Color.YELLOW) >= 4


@extra_play("BS12-056")
def apple_faerie_gate(ctx: Ctx) -> bool:
    """【EXTRA】 Can be played if there is a [Candy Apple Cookie] that has
    【Arena】 in your battle area, or if there are 7 {G} cards or more in your
    support area."""
    named = any(c.name(ctx.db) == "Candy Apple Cookie" and _is_arena(c.defn(ctx.db))
                for c in ctx.me.battle)
    green = sum(1 for c in ctx.me.support if ctx.db[c.card_id].color is Color.GREEN)
    return named or green >= 7


@extra_play("BS12-074")
def popping_candy_gate(ctx: Ctx) -> bool:
    """【EXTRA】 Can be played if, during this turn, an 【Arena】 Cookie was
    placed from your battle area on the bottom of your deck."""
    return ctx.me.arena_cookies_to_deck_bottom_this_turn > 0


def _purple_low_cookies(ctx: Ctx) -> list:
    return [c for c in ctx.me.battle
            if c.defn(ctx.db).color is Color.PURPLE and c.level(ctx.db) <= 2]


def _trash_a_purple(ctx: Ctx) -> bool:
    options = _purple_low_cookies(ctx)
    if not options:
        return False
    cookie = ctx.choose("【EXTRA】: place a Cookie in your trash",
                        options, optional=False)
    if cookie is None:
        return False
    # Trashing is not fainting: no break area, and no Level for the opponent.
    ctx.game.trash_cookie(cookie)
    return True


@extra_play("BS12-092", pay=_trash_a_purple, frees=1)
def black_lemonade_gate(ctx: Ctx) -> bool:
    """【EXTRA】 If there are 3 【Arena】 Cookies that have 【Blocker】 or more in
    your break area, <place 1 {P} LV.2 or lower Cookie from your battle area
    into your trash.> Play this Cookie."""
    blockers = sum(1 for c in ctx.me.break_area
                   if _is_arena(ctx.db[c.card_id])
                   and ctx.db[c.card_id].has(Marker.BLOCKER))
    return blockers >= 3 and bool(_purple_low_cookies(ctx))


@extra_play("BS12-111")
def poison_mushroom_gate(ctx: Ctx) -> bool:
    """【EXTRA】 Can be played if there are 4 cards or more in your opponent's
    support area and there is a Cookie that has Special Play in your battle
    area."""
    return (len(ctx.opp.support) >= 4
            and any(c.defn(ctx.db).has(Marker.SPECIAL_PLAY) for c in ctx.me.battle))


# --- continuous abilities ----------------------------------------------------
def _poison_mushroom_aura(db, player, attacker) -> int:
    """"Other {K} 【Arena】 Cookies in your battle area gain +1 attack damage."

    An aura over the *other* Cookies, so it cannot be an ATTACK_START trigger
    on this card — that trigger fires for the attacker alone. "Other" is the
    whole of the exclusion: Poison Mushroom does not buff itself, so a board
    with one of him and nothing else is no stronger for it.
    """
    if not any(c.defn(db).base_id == "BS12-111" for c in player.battle):
        return 0
    if attacker.defn(db).base_id == "BS12-111":
        return 0
    defn = attacker.defn(db)
    if defn.color is Color.BLACK and _is_arena(defn):
        return 1
    return 0


ATTACK_DAMAGE_AURAS.append(_poison_mushroom_aura)
STATIC_ABILITY_CARDS.add("BS12-111")


# --- 【Equip】 ---------------------------------------------------------------
# Three of these in the set, and they are the odd ones out: every other Equip
# in the game is an ITEM that attaches itself on the way to the trash, while
# these are *Cookies* already standing in the battle area that climb onto
# another one. So the move has to take the Cookie off the field first — a
# Cookie leaving the battle area sheds its HP pile — and what it leaves behind
# is a card riding on its host, with the rider registered against the climbing
# Cookie's own id the way every Soul Jam's is.
def _equip_self_to(ctx: Ctx, holder_name: str, cost: str) -> None:
    fan = ctx.source_cookie
    if fan is None or fan not in ctx.me.battle:
        return
    holder = ctx.select_own(lambda c: c.name(ctx.db) == holder_name and c is not fan,
                            prompt=f"Equip to {holder_name}?")
    if holder is None:
        return
    if not ctx.pay(Cost.parse(cost)):
        return
    ctx.me.battle.remove(fan)
    # Leaving the battle area sheds the HP pile (and anything stacked under),
    # which is `spent_cards` — not `hp_cards`, which would strand an 【Awaken】.
    ctx.me.trash.extend(fan.spent_cards)
    ctx.me.trash.extend(fan.equipment)
    holder.equipment.append(fan.card)
    ctx.state.record(f"{fan.label(ctx.db)} is equipped to "
                     f"{holder.label(ctx.db)}")


def _can_equip(holder_name: str, cost: str):
    return lambda ctx: (
        ctx.source_cookie is not None
        and any(c.name(ctx.db) == holder_name and c is not ctx.source_cookie
                for c in ctx.me.battle)
        and ctx.can_pay(Cost.parse(cost)))


@effect("BS12-077", Trigger.ACTIVATE)
def spotlight_fan_equip(ctx: Ctx) -> None:
    """【Activate】 【Once Per Turn】 <{P}> 【Equip】 this Cookie to your
    [Rockstar Cookie]. When that Cookie attacks, during this battle, your
    opponent cannot activate 【Blocker】."""
    _equip_self_to(ctx, "Rockstar Cookie", "{P}")


spotlight_fan_equip.playable = _can_equip("Rockstar Cookie", "{P}")


@effect("BS12-077", Trigger.ATTACK_START)
def spotlight_fan_rider(ctx: Ctx) -> None:
    """"When that Cookie attacks, during this battle, your opponent cannot
    activate 【Blocker】."

    Scoped to the battle rather than the turn: the host can swing again later
    in the same turn off a second untap, and the fan only covers the swing it
    was there for.
    """
    ctx.game.for_this_battle(ctx.opp, "blockers_disabled", True)


@effect("BS12-007", Trigger.ACTIVATE)
def producer_mic_equip(ctx: Ctx) -> None:
    """【Activate】 【Once Per Turn】 <{R}> 【Equip】 this Cookie to your
    [Shining Glitter Cookie]. When that Cookie attacks, your opponent cannot
    activate FLIP during this battle."""
    _equip_self_to(ctx, "Shining Glitter Cookie", "{R}")


producer_mic_equip.playable = _can_equip("Shining Glitter Cookie", "{R}")


@effect("BS12-007", Trigger.ATTACK_START)
def producer_mic_rider(ctx: Ctx) -> None:
    """"When that Cookie attacks, your opponent cannot activate FLIP during
    this battle."

    Set across the opponent's whole battle area rather than on the Cookie
    being swung at, because the target is not settled until after this trigger
    has run — and only the Cookie taking the damage can turn a FLIP over
    anyway, so the two readings play identically.
    """
    for cookie in ctx.opp.battle:
        ctx.game.for_this_battle(cookie, "flip_disabled", True)


@effect("BS12-062", Trigger.ACTIVATE)
def angel_lightstick_equip(ctx: Ctx) -> None:
    """【Activate】 【Once Per Turn】 <{B}> 【Equip】 this Cookie to your
    [Popping Candy Cookie]. When that Cookie attacks, if there are 5 cards or
    less in your hand, draw up to 2 cards from your deck."""
    _equip_self_to(ctx, "Popping Candy Cookie", "{B}")


angel_lightstick_equip.playable = _can_equip("Popping Candy Cookie", "{B}")


@effect("BS12-062", Trigger.ATTACK_START)
def angel_lightstick_rider(ctx: Ctx) -> None:
    """"When that Cookie attacks, if there are 5 cards or less in your hand,
    draw up to 2 cards from your deck." """
    if len(ctx.me.hand) <= 5:
        ctx.draw(2)


@effect("BS12-092", Trigger.ALLY_FAINTED)
def black_lemonade_on_faint(ctx: Ctx) -> None:
    """When one of your Cookies faints, if there are 3 cards or more in your
    opponent's hand, your opponent places 1 card from their hand into their
    trash."""
    if len(ctx.opp.hand) >= 3:
        ctx.opponent_discards(1)


# --- BS12-063 CAKE POPs ------------------------------------------------------
def _cake_pops_cap(db, state, cookie) -> int | None:
    """"If there is a [Popping Candy Cookie] in your battle area, any time this
    Cookie would receive 2 or more damage, the damage is reduced to 1."

    Printed on the card and conditional on the board, so it is a continuous
    cap rather than `Cookie.damage_cap`, which an effect grants for a turn.
    "Any time" is any damage at all — a swing, an Item, a trap, a FLIP — and a
    ceiling of 1 is the whole sentence, since a 1 already passes through
    `min` unchanged.
    """
    if cookie.defn(db).base_id != "BS12-063":
        return None
    owner = state.players[cookie.owner]
    if not any(c.name(db) == "Popping Candy Cookie" for c in owner.battle):
        return None
    return 1


DAMAGE_CAPS.append(_cake_pops_cap)
STATIC_ABILITY_CARDS.add("BS12-063")


# --- BS12-098 Caramel Pudding Cake Hound -------------------------------------
@effect("BS12-098", Trigger.FLIP)
def caramel_pudding_flip(ctx: Ctx) -> None:
    """If there is a {K} 【Arena】 Cookie in your battle area, the LV.2 or
    higher Cookie that used this card as HP gains +1 HP.

    "The Cookie that used this card as HP" is the FLIP's host, which is what
    `ctx.source_cookie` is inside a FLIP — the card itself is `source_card`.
    The LV.2 floor is on the host, not on the Cookie being looked for.
    """
    host = ctx.source_cookie
    if host is None or host.level(ctx.db) < 2:
        return
    if not any(c.defn(ctx.db).color is Color.BLACK and _is_arena(c.defn(ctx.db))
               for c in ctx.me.battle):
        return
    ctx.gain_hp(host, 1)


# --- BS12-027 Designers' Yapping ---------------------------------------------
def _yapping_is_free(ctx: Ctx) -> bool:
    """"If there are 4 {Y} 【Arena】 Cookies or more in your break area, the cost
    to activate this card is reduced by 1 {Y}."

    The whole printed cost is one {Y}, so "reduced by 1 {Y}" means free. Written
    by hand for the same reason BS11-106 is: the discount is one sentence and
    the cost it discounts is the next one, and the compiler pulls a `<...>` out
    of the clause it sits in without being able to see back to a sentence that
    changes it.
    """
    return _arena_cards(ctx, ctx.me.break_area, Color.YELLOW) >= 4


@effect("BS12-027", Trigger.ITEM)
def designers_yapping(ctx: Ctx) -> None:
    """<{Y}> Select up to 1 of your opponent's Cookies. During this turn, that
    Cookie deals -1 attack damage."""
    if not _yapping_is_free(ctx) and not ctx.pay(Cost.parse("{Y}")):
        return
    target = ctx.select_enemy()
    if target is not None:
        ctx.modify_attack(target, -1)


designers_yapping.playable = lambda ctx: (
    bool(ctx.enemy_cookies())
    and (_yapping_is_free(ctx) or ctx.can_pay(Cost.parse("{Y}"))))


# --- BS12-032 / BS12-033: paid off for being thrown away ---------------------
# "【Your Turn】 When this Cookie is placed in your break area by an 【Arena】
# card effect, ..." — the reward for the set's own discard costs, which is why
# they are LV.1 Cookies you are happy to spend. Written by hand rather than
# compiled because the trigger is about *how* the card got where it is, and
# `Trigger.ARENA_BREAK_BY_EFFECT` already enforces the 【Arena】 half of it.
@effect("BS12-032", Trigger.ARENA_BREAK_BY_EFFECT)
def caramel_choux_broken(ctx: Ctx) -> None:
    """【Your Turn】 When this Cookie is placed in your break area by an
    【Arena】 card effect, select up to 1 of your Cookies. That Cookie gains
    +1 HP."""
    if not _is_your_turn(ctx):
        return
    target = ctx.select_own()
    if target is not None:
        ctx.gain_hp(target, 1)


@effect("BS12-033", Trigger.ARENA_BREAK_BY_EFFECT)
def espresso_broken(ctx: Ctx) -> None:
    """【Your Turn】 When this Cookie is placed in your break area by an
    【Arena】 card effect, draw up to 1 card from your deck."""
    if not _is_your_turn(ctx):
        return
    ctx.draw(1)


# --- BS12-014 Strawberry Crepe Cookie ----------------------------------------
def _crepe_stays_rested(db, state, cookie) -> bool:
    """"During the Active Phase, if there is no other 【Arena】 Cookie in your
    battle area, this Cookie is not set as active."

    A *conditional* lock, which is why it is not `skip_next_active`: that flag
    is armed once by an effect and disarmed by the next phase, while this is
    printed on the card and re-read every Active Phase against whatever else
    is on the board. "Other" is the whole of the exclusion — Strawberry Crepe
    does not keep herself awake.
    """
    if cookie.defn(db).base_id != "BS12-014":
        return False
    owner = state.players[cookie.owner]
    return not any(c is not cookie and Keyword.ARENA in c.defn(db).keywords
                   for c in owner.battle)


ACTIVE_PHASE_LOCKS.append(_crepe_stays_rested)
STATIC_ABILITY_CARDS.add("BS12-014")


# --- BS12-082 DJ Cookie ------------------------------------------------------
def _dj_cookie_tax(db, state, player) -> int:
    """"If this Cookie is in your battle area, your opponent cannot activate
    Items unless they discard 1 card."

    The tax is on the *other* seat, so it is charged where an Item is played
    rather than written on DJ Cookie — and it is a gate as much as a price:
    an opponent who cannot discard cannot play the Item at all, which is what
    "cannot ... unless" says.
    """
    opponent = state.players[1 - player.index]
    return 1 if any(c.defn(db).base_id == "BS12-082"
                    for c in opponent.battle) else 0


ITEM_SURCHARGES.append(_dj_cookie_tax)
STATIC_ABILITY_CARDS.add("BS12-082")


# --- BS12-089 Werewolf Cookie ------------------------------------------------
def _werewolf_silence(db, state, attacker, target) -> bool:
    """"When this Cookie battles, your opponent cannot activate attack effects
    of LV.3 Cookies during this battle."

    "Battles" is either seat of the battle, so Werewolf silences a LV.3 swing
    it blocks and one it makes. The rider on the attack line is what "attack
    effect" means — the swing itself still lands.
    """
    if (attacker.level(db) or 0) != 3:
        return False
    return any(c is not None and c.defn(db).base_id == "BS12-089"
               for c in (attacker, target))


ATTACK_EFFECT_LOCKS.append(_werewolf_silence)
STATIC_ABILITY_CARDS.add("BS12-089")


# --- BS12-044 Herb Teapot ----------------------------------------------------
@effect("BS12-044", Trigger.ACTIVATE)
def herb_teapot(ctx: Ctx) -> None:
    """【Activate】 【Once Per Turn】 Play up to 1 【Arena】 Cookie from your
    support area. If [Herb Cookie] was played by this effect, select up to 1
    card in your support area. Set that card as active.

    "By this effect" is the sentence before, not the board: a Herb Cookie that
    was already standing there does not pay this out. So the payoff reads the
    card this effect actually played, which is why it is written by hand —
    the compiler's "if you did" only records *whether* the sentence succeeded,
    not what it produced.
    """
    if len(ctx.me.battle) >= ctx.game.rules.max_battle_cookies:
        return
    options = [c for c in ctx.me.support
               if ctx.db[c.card_id].is_cookie and _is_arena(ctx.db[c.card_id])]
    if not options:
        return
    card = ctx.choose("Play an 【Arena】 Cookie from your support area",
                      options, optional=True)
    if card is None:
        return
    ctx.me.support.remove(card)
    ctx.game._deploy_cookie(ctx.me, card, from_zone="support")
    if ctx.db[card.card_id].name == "Herb Cookie":
        ctx.set_support_active(1)


herb_teapot.playable = lambda ctx: (
    len(ctx.me.battle) < ctx.game.rules.max_battle_cookies
    and any(ctx.db[c.card_id].is_cookie and _is_arena(ctx.db[c.card_id])
            for c in ctx.me.support))


# --- BS12-053 Kumiho Cookie --------------------------------------------------
@effect("BS12-053", Trigger.OPPONENT_ATTACKS)
def kumiho_response(ctx: Ctx) -> None:
    """【Once Per Turn】 When one of your opponent's Cookies attacks, <place 1
    card from your support area into your trash.> Select up to 1 of your
    opponent's Cookies. During this turn, that Cookie deals -2 attack damage.

    Fired for every swing the opponent makes, not only the ones aimed at
    Kumiho, so the 【Once Per Turn】 has to be enforced here — the engine only
    books that for 【Activate】 skills its controller chose.
    """
    cookie = ctx.source_cookie
    if cookie is None or Trigger.OPPONENT_ATTACKS.value in cookie.used_markers:
        return
    if not ctx.me.support:
        return
    # A cost that merely happens to you asks before it is paid.
    if not ctx.wants_to_pay("Place 1 card from your support area into your trash?"):
        return
    card = ctx.choose("Place a support card into your trash",
                      list(ctx.me.support), optional=False)
    if card is None:
        return
    ctx.me.support.remove(card)
    ctx.me.trash.append(card)
    cookie.used_markers.add(Trigger.OPPONENT_ATTACKS.value)
    target = ctx.select_enemy()
    if target is not None:
        ctx.modify_attack(target, -2)


# --- BS12-109 Licorice Cookie ------------------------------------------------
@effect("BS12-109", Trigger.ATTACK)
def licorice_attack(ctx: Ctx) -> None:
    """Then, if there are 3 cards or more in your opponent's support area,
    select up to 1 Cookie in your battle area. Place 1 Cookie that has Special
    Play from your hand face-up on the top of that Cookie's HP.

    Face up and on *top*: the top of an HP pile is the end of the list, since
    damage pops off the end — so this is the next card that Cookie turns over,
    not the last.
    """
    if len(ctx.opp.support) < 3:
        return
    pool = [c for c in ctx.me.hand
            if ctx.db[c.card_id].has(Marker.SPECIAL_PLAY)]
    if not pool:
        return
    host = ctx.select_own()
    if host is None:
        return
    card = ctx.choose("Place a 【Special Play】 Cookie on that Cookie's HP",
                      pool, optional=False)
    if card is None:
        return
    ctx.me.hand.remove(card)
    card.face_up = True
    host.hp_cards.append(card)
    ctx.state.record(f"{card_label(ctx.db[card.card_id])} is placed on "
                     f"{host.label(ctx.db)}'s HP")


licorice_attack.playable = lambda ctx: (
    len(ctx.opp.support) >= 3
    and any(ctx.db[c.card_id].has(Marker.SPECIAL_PLAY) for c in ctx.me.hand)
    and bool(ctx.me.battle))
