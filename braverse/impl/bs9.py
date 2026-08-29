"""Hand-written cards from BS9.

BS9 keys off *when* your Cookies died — its recursion payoffs read the previous
turn's losses — and adds blanket damage shields plus HP-stealing.

The set also leans on the Extra Deck, 【Special Play】 and 【Awaken】, none of
which the engine models. Cards resting on those stay unimplemented rather than
half-resolving.
"""

from __future__ import annotations

from braverse.cost import Cost
from braverse.effects import (DAMAGE_REDUCERS, OPPONENT_DAMAGE_SHIELDS,
                              REFRESH_COST_MODIFIERS, STATIC_ABILITY_CARDS,
                              TAUNT_PROVIDERS, Ctx, Trigger, effect,
                              playable_if)
from braverse.enums import Color, Keyword
from braverse.state import card_label


def _faints_last_opponent_turn(ctx: Ctx, color=None, level=None) -> int:
    """How many of my Cookies fainted during the opponent's previous turn."""
    want = ctx.state.turn_counter - 1
    return sum(1 for turn, c, lv in ctx.me.faint_log
               if turn == want
               and (color is None or c is color)
               and (level is None or lv == level))


# --- BS9-002 Princess Cookie ------------------------------------------------
@effect("BS9-002", Trigger.ACTIVATE)
def princess_bs9_activate(ctx: Ctx) -> None:
    """If your {R} LV.1 Cookie fainted during your opponent's previous turn,
    this Cookie gains +1 attack damage during this turn."""
    if ctx.source_cookie and _faints_last_opponent_turn(ctx, Color.RED, 1):
        ctx.modify_attack(ctx.source_cookie, 1)


# --- BS9-006 Melted Choco Cookie --------------------------------------------
@effect("BS9-006", Trigger.ON_PLAY)
def melted_choco_on_play(ctx: Ctx) -> None:
    """During this turn, if 2 or more of your Cookies fainted, this Cookie
    takes -3 damage until the end of the turn."""
    if ctx.source_cookie and ctx.me.cookies_fainted_this_turn >= 2:
        ctx.source_cookie.effect_damage_reduction += 3
        ctx.source_cookie.incoming_damage_reduction += 3
        
        


# --- BS9-011 Devil Cookie ---------------------------------------------------
@effect("BS9-011", Trigger.ON_PLAY)
def devil_on_play(ctx: Ctx) -> None:
    """During this turn, if 2 or more of your {R} LV.1 Cookies fainted, select
    up to 1 of your opponent's Cookies. That Cookie receives 1 damage."""
    fainted = sum(1 for turn, c, lv in ctx.me.faint_log
                  if turn == ctx.state.turn_counter
                  and c is Color.RED and lv == 1)
    if fainted < 2:
        return
    target = ctx.select_enemy()
    if target is not None:
        ctx.deal_damage(target, 1)


# --- BS9-012 Knight Cookie --------------------------------------------------
@effect("BS9-012", Trigger.END_TURN)
def knight_end_turn(ctx: Ctx) -> None:
    """At the end of your opponent's turn, if there are 2 Cookies in your
    battle area, this Cookie receives 3 damage."""
    if len(ctx.me.battle) == 2 and ctx.source_cookie is not None:
        ctx.deal_damage(ctx.source_cookie, 3)


# --- BS9-017 Hollyberry Cookie ----------------------------------------------
@effect("BS9-017", Trigger.ACTIVATE)
def hollyberry_bs9_activate(ctx: Ctx) -> None:
    """<{N}> If there is another 【Ancient】 Cookie in your battle area, this
    Cookie gains +2 attack damage during this turn."""
    cookie = ctx.source_cookie
    if cookie is None or not ctx.pay(Cost.parse("{N}")):
        return
    if any(c is not cookie and Keyword.ANCIENT in c.defn(ctx.db).keywords
           for c in ctx.me.battle):
        ctx.modify_attack(cookie, 2)


@effect("BS9-017", Trigger.ATTACK)
def hollyberry_bs9_attack(ctx: Ctx) -> None:
    """Then, until the end of your opponent's turn, any time your 【Ancient】
    Cookie would receive 3 or more damage, the damage is reduced to 2."""
    for cookie in ctx.me.battle:
        if Keyword.ANCIENT in cookie.defn(ctx.db).keywords:
            cookie.damage_cap = 2


# --- BS9-018 Hero Cookie ----------------------------------------------------
def _hero_shield(db, owner, state) -> bool:
    """"【Your Turn】 If this Cookie is in your battle area, your Cookies take no
    damage from your opponent."

    A shield while it is on the field, so it is consulted in the damage path
    rather than fired as a trigger — but 【Your Turn】 is half the card, and it
    was being dropped. Without it the shield stood on the opponent's turn too,
    which is when almost all of the damage in this game is dealt: one Cookie on
    the board and nothing your opponent did could ever hurt you.

    On your own turn it still does real work — a trap, a 【Blocker】, a FLIP and
    a "when your opponent's Cookie attacks" reaction all deal damage to you
    while it is your turn.
    """
    if state.turn_player != owner.index:
        return False
    return any(c.defn(db).base_id == "BS9-018" for c in owner.battle)


OPPONENT_DAMAGE_SHIELDS.append(_hero_shield)
STATIC_ABILITY_CARDS.add("BS9-018")


# --- BS9-020 Fateful Cookie Cutter ------------------------------------------
@effect("BS9-020", Trigger.ITEM)
def fateful_cookie_cutter(ctx: Ctx) -> None:
    """If your {R} LV.1 Cookie fainted during your opponent's previous turn,
    draw up to 2 cards from your deck and discard 1 card."""
    if not _faints_last_opponent_turn(ctx, Color.RED, 1):
        return
    if ctx.draw(2):
        ctx.discard(1, optional=True)


# --- BS9-021 Stolen Light of Truth ------------------------------------------
@effect("BS9-021", Trigger.ITEM)
def stolen_light_of_truth(ctx: Ctx) -> None:
    """Select up to 1 of your opponent's Cookies. Add 1 card from the top of
    that Cookie's HP face-up to the bottom of your Cookie's HP.

    HP theft: the card changes owner, which is what "your Cookie has an
    opponent's card as HP" elsewhere in the set keys off.
    """
    victim = ctx.select_enemy()
    mine = ctx.select_own()
    if victim is None or mine is None or not victim.hp_cards:
        return
    stolen = victim.hp_cards.pop()
    mine.hp_cards.insert(0, stolen)
    if not victim.hp_cards:
        ctx.game.faint(victim)


# --- BS9-024 Golden Cheese Cookie -------------------------------------------
@effect("BS9-024", Trigger.ACTIVATE)
def golden_cheese_bs9_activate(ctx: Ctx) -> None:
    """If this Cookie's remaining HP is 4 or less and another 【Ancient】 Cookie
    is in your battle area, this Cookie gains +1 HP."""
    cookie = ctx.source_cookie
    if cookie is None or cookie.remaining_hp > 4:
        return
    if any(c is not cookie and Keyword.ANCIENT in c.defn(ctx.db).keywords
           for c in ctx.me.battle):
        ctx.gain_hp(cookie, 1)


@effect("BS9-024", Trigger.ATTACK)
def golden_cheese_bs9_attack(ctx: Ctx) -> None:
    """Then, <return 1 card from the top of this Cookie's HP to your hand.>
    Deals 1 damage."""
    cookie = ctx.source_cookie
    if cookie is None or not cookie.hp_cards:
        return
    ctx.me.hand.append(cookie.hp_cards.pop())
    if not cookie.hp_cards:
        ctx.game.faint(cookie)
        return
    target = ctx.select_enemy()
    if target is not None:
        ctx.deal_damage(target, 1)


# --- BS9-027 Vampire Cookie -------------------------------------------------
@effect("BS9-027", Trigger.ACTIVATE)
def vampire_activate(ctx: Ctx) -> None:
    """Add up to 1 card from your hand to the top of this Cookie's HP. Then,
    this Cookie receives 1 damage."""
    cookie = ctx.source_cookie
    if cookie is None:
        return
    if ctx.me.hand:
        card = ctx.choose("Add a card from hand to this Cookie's HP",
                          list(ctx.me.hand), optional=True)
        if card is not None:
            ctx.me.hand.remove(card)
            card.face_up = False
            cookie.hp_cards.append(card)
    ctx.deal_damage(cookie, 1)


# --- BS9-031 Alchemist Cookie (FLIP) ----------------------------------------
@effect("BS9-031", Trigger.FLIP)
def alchemist_flip(ctx: Ctx) -> None:
    """One of your LV.3 Cookies gains +1 HP."""
    target = ctx.select_own(lambda c: c.level(ctx.db) == 3)
    if target is not None:
        ctx.gain_hp(target, 1)


# --- BS9-034 Fortune Teller Cookie ------------------------------------------
@effect("BS9-034", Trigger.ON_PLAY)
def fortune_teller_on_play(ctx: Ctx) -> None:
    """<{Y}> Select up to 1 of your opponent's Cookies. View all of that
    Cookie's HP cards and rearrange them in any order."""
    if not ctx.pay(Cost.parse("{Y}")):
        return
    target = ctx.select_enemy()
    if target is not None:
        # Bury their FLIPs so damage does not turn them up.
        target.hp_cards.sort(key=lambda c: not ctx.db[c.card_id].is_flip)


# --- BS9-035 Truthless Recluse ----------------------------------------------
@effect("BS9-035", Trigger.ACTIVATE)
def truthless_recluse_activate(ctx: Ctx) -> None:
    """<Discard 1 card.> During this turn, your opponent cannot add HP to
    Cookies via card effects."""
    if ctx.discard(1, optional=True):
        ctx.opp.hp_gain_locked = True


# --- BS9-036 Bookseller -----------------------------------------------------
@effect("BS9-036", Trigger.END_TURN)
def bookseller_end_turn(ctx: Ctx) -> None:
    """When your turn ends, discard 1 Cookie that has FLIP from your hand or
    place 1 card from the top of this Cookie's HP into your trash."""
    flips = [c for c in ctx.me.hand if ctx.db[c.card_id].is_flip]
    cookie = ctx.source_cookie
    if flips:
        card = ctx.choose("Discard a FLIP Cookie instead of paying HP?", flips,
                          optional=True)
        if card is not None:
            ctx.me.hand.remove(card)
            ctx.me.trash.append(card)
            return
    if cookie is not None:
        ctx.trash_hp(cookie, 1)


# --- BS9-038 Chess Choco Cookie ---------------------------------------------
@effect("BS9-038", Trigger.ON_PLAY)
def chess_choco_on_play(ctx: Ctx) -> None:
    """If there is another [Chess Choco Cookie] in your battle area, all your
    Cookies gain +1 HP."""
    others = [c for c in ctx.me.battle
              if c is not ctx.source_cookie
              and c.name(ctx.db) == "Chess Choco Cookie"]
    if not others:
        return
    for cookie in list(ctx.me.battle):
        ctx.gain_hp(cookie, 1)


# --- BS9-052 Ring Candy Cookie ----------------------------------------------
@effect("BS9-052", Trigger.ATTACK_START)
def ring_candy_static(ctx: Ctx) -> None:
    """If there are 7 cards or more in your support area, this Cookie gains +1
    attack damage."""
    if ctx.source_cookie and ctx.support_count() >= 7:
        ctx.modify_attack(ctx.source_cookie, 1)


# --- BS9-043 Heart Stained With Lies ----------------------------------------
@effect("BS9-043", Trigger.ITEM)
def heart_stained_with_lies(ctx: Ctx) -> None:
    """<{Y}> If your break area is LV.4 or higher, select up to 1 of your
    opponent's Equipped [Soul Jam]. Place that card on top of the Equipped
    Cookie's HP."""
    if ctx.me.break_level_total(ctx.db) < 4:
        return
    # The leading `<{Y}>` is this card's play cost, already paid to get here.
    holders = [c for c in ctx.opp.battle if c.equipment]
    if not holders:
        return
    holder = ctx.choose("Strip which Cookie's equipment?", holders, optional=True)
    if holder is None:
        return
    card = holder.equipment.pop()
    card.face_up = False
    holder.hp_cards.append(card)

# --- BS9-010 Shadow Milk Cookie ({R}) ----------------------------------------
@effect("BS9-010", Trigger.ON_PLAY)
def shadow_milk_red_on_play(ctx: Ctx) -> None:
    """【On Play】 Add up to 1 card from your opponent's hand face-up to the
    bottom of this Cookie's HP.

    Random, like every other card in the pool that reaches into a hand — the
    six others all print "1 random card from your opponent's hand", and this
    one gives you no way to look first, so choosing would mean revealing their
    whole hand to make the choice. Face up, because a card taken off someone
    else is public where your own HP pile is not.
    """
    cookie = ctx.source_cookie
    if cookie is None or not ctx.opp.hand:
        return
    stolen = ctx.state.rng.choice(ctx.opp.hand)
    ctx.steal_to_hp(cookie, stolen, ctx.opp.hand)
    ctx.note(f"takes a card from the opponent's hand as HP: "
             f"{card_label(ctx.db[stolen.card_id])}")


@effect("BS9-010", Trigger.ATTACK)
def shadow_milk_red_attack(ctx: Ctx) -> None:
    """Then, <can be used as {N}.> Select up to 1 of your opponent's Cookies.
    Add 1 card from the top of that Cookie's HP face-up to the bottom of this
    Cookie's HP."""
    cookie = ctx.source_cookie
    if cookie is None:
        return
    if not ctx.wants_to_pay("{N}") or not ctx.pay(Cost.parse("{N}")):
        return
    victim = ctx.select_enemy(lambda c: bool(c.hp_cards))
    if victim is None or not victim.hp_cards:
        return
    ctx.steal_to_hp(cookie, victim.hp_cards[-1], victim.hp_cards)
    if not victim.hp_cards:
        ctx.faint(victim)


# --- BS9-030 Shadow Milk Cookie ({Y}) ----------------------------------------
@effect("BS9-030", Trigger.ON_PLAY)
def shadow_milk_yellow_on_play(ctx: Ctx) -> None:
    """【On Play】 Place up to 1 LV.1 Cookie from your break area into your
    trash.

    Taking a card *out* of your own break area lowers the Level banked against
    you, which is the whole point — this is the yellow archetype buying back
    room on the clock.
    """
    options = [c for c in ctx.me.break_area
               if ctx.db[c.card_id].is_cookie and (ctx.db[c.card_id].level or 0) == 1]
    if not options:
        return
    card = ctx.choose("Trash a LV.1 Cookie from your break area", options,
                      optional=True)
    if card is None:
        return
    ctx.me.break_area.remove(card)
    ctx.me.trash.append(card)


@effect("BS9-030", Trigger.ATTACK)
def shadow_milk_yellow_attack(ctx: Ctx) -> None:
    """Then, <discard 1 Cookie that has FLIP from your hand.> Activate the
    discarded card's FLIP effect.

    The only card in the pool that fires a FLIP from anywhere but an HP pile.
    Its host is this Cookie, so a FLIP that heals "this Cookie" heals the one
    that swung.
    """
    from braverse.enums import CardType
    is_flip = lambda d: d.type is CardType.FLIP
    if not any(is_flip(ctx.db[c.card_id]) for c in ctx.me.hand):
        return
    if not ctx.wants_to_pay("Discard 1 Cookie that has FLIP from your hand."):
        return
    discarded = ctx.discard_matching(1, is_flip)
    for card in discarded:
        ctx.run_flip(card)


# --- BS9-055 Shadow Milk Cookie ({G}) ----------------------------------------
@playable_if(lambda ctx: len(ctx.me.support) <= 5 and bool(ctx.me.deck))
@effect("BS9-055", Trigger.ACTIVATE)
def shadow_milk_green_activate(ctx: Ctx) -> None:
    """【Activate】 【Once Per Turn】 If there are 5 cards or less in your
    support area, place 1 card from the top of your deck into your support area
    as rested."""
    if len(ctx.me.support) > 5:
        return
    ctx.mill_to_support(1)


@effect("BS9-055", Trigger.ATTACK)
def shadow_milk_green_attack(ctx: Ctx) -> None:
    """Then, <return 1 card from your support area to your hand.> Select up to
    1 of your opponent's Cookies. That Cookie receives 1 damage."""
    if not ctx.me.support:
        return
    if not ctx.wants_to_pay("Return 1 card from your support area to your hand."):
        return
    if not ctx.return_support_to_hand():
        return
    target = ctx.select_enemy()
    if target is not None:
        ctx.deal_damage(target, 1)


# --- BS9-088 Pure Vanilla Cookie ---------------------------------------------
@effect("BS9-088", Trigger.ON_PLAY)
def pure_vanilla_extra_on_play(ctx: Ctx) -> None:
    """【On Play】 Reveal 1 card from the top of your deck. If that card is a
    {B} LV.2 Cookie, this Cookie gains +2 HP, and draw up to 2 cards from your
    deck.

    A reveal, not a draw: the card is shown and stays on top, so the two cards
    drawn afterwards start with the one everybody just looked at.
    """
    seen = ctx.reveal_top(1)
    if not seen:
        return
    defn = ctx.db[seen[0].card_id]
    if not (defn.color is Color.BLUE and defn.is_cookie and (defn.level or 0) == 2):
        return
    if ctx.source_cookie is not None:
        ctx.gain_hp(ctx.source_cookie, 2)
    ctx.draw(2)


@effect("BS9-088", Trigger.ATTACK)
def pure_vanilla_extra_attack(ctx: Ctx) -> None:
    """Then, <can be used as {B}.> Select up to 1 of your Cookies. That Cookie
    gains +1 HP."""
    if not ctx.wants_to_pay("{B}") or not ctx.pay(Cost.parse("{B}")):
        return
    target = ctx.select_own()
    if target is not None:
        ctx.gain_hp(target, 1)


# --- BS9-102 Shadow Milk Cookie ({P}) ----------------------------------------
@playable_if(lambda ctx: len(ctx.opp.hand) >= 4 and any(
    ctx.db[c.card_id].color is Color.PURPLE for c in ctx.me.hand))
@effect("BS9-102", Trigger.ACTIVATE)
def shadow_milk_purple_activate(ctx: Ctx) -> None:
    """【Activate】 【Once Per Turn】 <Discard 1 {P} card from your hand.> If
    there are 4 cards or more in your opponent's hand, your opponent places 1
    card from their hand into their trash."""
    if not ctx.wants_to_pay("Discard 1 {P} card from your hand."):
        return
    if not ctx.discard_colored(1, Color.PURPLE):
        return
    if len(ctx.opp.hand) >= 4:
        ctx.opponent_discards(1)


@effect("BS9-102", Trigger.ATTACK)
def shadow_milk_purple_attack(ctx: Ctx) -> None:
    """Then, if there are 20 cards or more in your opponent's trash, deals 1
    damage.

    A bare "deals 1 damage" on an attack line means the Cookie this attack is
    aimed at — the swing landed, and this is a second helping on the same
    target.
    """
    if len(ctx.opp.trash) < 20:
        return
    target = ctx.attack_target
    if target is not None:
        ctx.deal_damage(target, 1)


# --- BS9-101 Blueberry Pie Cookie --------------------------------------------
@playable_if(lambda ctx: bool(ctx.me.deck) and ctx.can_pay(Cost.parse("{P}")))
@effect("BS9-101", Trigger.ACTIVATE)
def blueberry_pie_activate(ctx: Ctx) -> None:
    """【Activate】 <{P}> <Place this Cookie in your trash.> View 3 cards from
    the top of your deck, reveal up to 1 {P} card from the viewed cards, and
    add it to your hand. Then, place the remaining cards in your trash.

    Two costs, both paid before anything is looked at: an 【Activate】 the
    controller chose, so `wants_to_pay` does not stop to ask a second time, but
    the energy still has to actually be there.
    """
    cookie = ctx.source_cookie
    if cookie is None:
        return
    if not ctx.can_pay(Cost.parse("{P}")):
        return
    if not ctx.wants_to_pay("Place this Cookie in your trash."):
        return
    if not ctx.pay(Cost.parse("{P}")):
        return
    ctx.trash_cookie(cookie)
    ctx.view_top(3, pick=lambda d: d.color is Color.PURPLE,
                 criterion="{P}", reveal=True, rest="trash")
    

# --- BS9-082 Animatronic of Deceit ------------------------------------------
def _animatronic_taunt(db, defender):
    """"If [Shadow Milk Cookie] is in your battle area, your opponent's Cookies
    can only attack this Cookie."

    A targeting restriction, enforced where attacks are enumerated rather than
    as a trigger — the same shape as Kumiho Cookie (BS4-024), and for the same
    reason: there is no event here to fire on, only a rule about what is a
    legal attack while the Animatronic and its master share a battle area.

    Two of them out at once is not a contradiction the rules resolve, so the
    first one found is the one that holds — which is also what a player would
    do with two identical restrictions on the table.
    """
    animatronic = next((c for c in defender.battle
                        if c.defn(db).base_id == "BS9-082"), None)
    if animatronic is None:
        return None
    has_master = any(c.name(db) == "Shadow Milk Cookie" for c in defender.battle)
    return animatronic if has_master else None


TAUNT_PROVIDERS.append(_animatronic_taunt)
STATIC_ABILITY_CARDS.add("BS9-082")


# --- BS9-092 Soul Jam: Light of Deceit --------------------------------------
def _deceit_jam_reduction(db, state, cookie) -> int:
    """"If there are 5 cards or less in your hand, during your turn, that
    Cookie receives -3 damage."

    A rider on the jam, not on the Cookie, so it is read off the equipment and
    leaves with it. Both halves of the condition are re-read every time damage
    lands rather than banked when the jam went on: a hand that grew past five,
    or the opponent's turn coming round, turns it off again.
    """
    if not any(c.card_id.split("@")[0] == "BS9-092" for c in cookie.equipment):
        return 0
    owner = state.players[cookie.owner]
    if state.turn_player != owner.index or len(owner.hand) > 5:
        return 0
    return 3


DAMAGE_REDUCERS.append(_deceit_jam_reduction)


@effect("BS9-092", Trigger.ITEM)
def soul_jam_deceit(ctx: Ctx) -> None:
    """<{B}{N}> <Discard 2 cards.> Select up to 1 of your opponent's Cookies.
    That Cookie receives 2 damage. Then, you can 【Equip】 this card to your
    [Shadow Milk Cookie]. If there are 5 cards or less in your hand, during
    your turn, that Cookie receives -3 damage.

    The discard is a cost, so it is paid before anything happens and the card
    does nothing if it cannot be met.
    """
    if len(ctx.me.hand) < 2:
        return
    ctx.discard(2, optional=False)
    target = ctx.select_enemy()
    if target is not None:
        ctx.deal_damage(target, 2)

    holder = ctx.select_own(lambda c: c.name(ctx.db) == "Shadow Milk Cookie",
                            prompt="Equip to Shadow Milk Cookie?")
    card = ctx.source_card
    if holder is not None and card is not None:
        if card in ctx.me.trash:
            ctx.me.trash.remove(card)
        holder.equipment.append(card)


# --- BS9-096 Nosy Wizard / BS9-111 Everything Pie Cookie --------------------
def _refresh_cost_rewrite(db, player, opponent):
    """Two Cookies rewrite what a [refresh] costs, pulling opposite ways.

    Nosy Wizard waives it for its own controller; Everything Pie Cookie
    doubles it for the seat across the table. Asked about whoever is
    refreshing, so both are read from that player's point of view — and the
    waiver wins, because a cost of none cannot be raised by making it two.
    """
    if any(c.defn(db).base_id == "BS9-096" for c in player.battle):
        return 0
    if any(c.defn(db).base_id == "BS9-111" for c in opponent.battle):
        return 2
    return None


REFRESH_COST_MODIFIERS.append(_refresh_cost_rewrite)
STATIC_ABILITY_CARDS.add("BS9-096")
STATIC_ABILITY_CARDS.add("BS9-111")
