"""Hand-written cards from BS5.

Mostly continuous stat buffs and defensive reactions — the shapes with no event
for the compiler to hang an effect on.
"""

from __future__ import annotations

from braverse.effects import DAMAGE_REDUCERS, Ctx, Trigger, effect
from braverse.enums import Color, Keyword


# --- BS5-001 Dark Choco Cookie ----------------------------------------------
@effect("BS5-001", Trigger.ATTACK_START)
def dark_choco_static(ctx: Ctx) -> None:
    """If this Cookie's remaining HP is 1, this Cookie gains +1 attack damage."""
    cookie = ctx.source_cookie
    if cookie is not None and cookie.remaining_hp == 1:
        ctx.modify_attack(cookie, 1)


# --- BS5-016 Tiramisu Cookie ------------------------------------------------
@effect("BS5-016", Trigger.ACTIVATE)
def tiramisu_activate(ctx: Ctx) -> None:
    """<Place 1 card from the top of this Cookie's HP into the trash.> If that
    card is a non-Cookie card, select up to 1 of your opponent's Cookies. That
    Cookie receives 1 damage.

    The condition is on the card that was just paid, so the cost has to be
    resolved by hand to see what it was.
    """
    cookie = ctx.source_cookie
    if cookie is None or not cookie.hp_cards:
        return
    paid = cookie.hp_cards.pop()
    paid.face_up = True
    ctx.me.trash.append(paid)
    if not cookie.hp_cards:
        ctx.game.faint(cookie)
    if ctx.state.over or ctx.db[paid.card_id].is_cookie:
        return
    target = ctx.select_enemy()
    if target is not None:
        ctx.deal_damage(target, 1)


# --- BS5-020 Crimson Dragon Mask --------------------------------------------
@effect("BS5-020", Trigger.ITEM)
def crimson_dragon_mask(ctx: Ctx) -> None:
    """<{R}{R}> If there are 2 Cookies whose remaining HP is 1 in your battle
    area, deals 2 damage to all of your opponent's Cookies.

    Hand-written for the condition: "Cookies whose remaining HP is 1" is a
    board state, and the compiler's card filters read printed cards, so it
    refuses the line rather than counting every Cookie you have.

    This was written here before, on `Trigger.ATTACK` and against a dump that
    had lost the damage number — and an ITEM's body runs on `Trigger.ITEM`, so
    for as long as it sat on the wrong trigger the card did nothing at all
    while `is_implemented` said it was finished.
    """
    if sum(1 for c in ctx.me.battle if c.remaining_hp == 1) < 2:
        return
    for cookie in list(ctx.enemy_cookies()):
        ctx.deal_damage(cookie, 2)


# --- BS5-038 Cherry Cookie --------------------------------------------------
@effect("BS5-038", Trigger.ON_PLAY)
def cherry_on_play(ctx: Ctx) -> None:
    """Draw up to 1 card from your deck."""
    ctx.draw(1)


# --- BS5-046 Goblin Cookie --------------------------------------------------
@effect("BS5-046", Trigger.FLIP)
def goblin_flip(ctx: Ctx) -> None:
    """<Discard 1 card.> The Cookie with this card attached for HP gains +1 HP.

    Printed in the description rather than the flip field, so the compiler
    routes it to the wrong trigger.
    """
    if ctx.source_cookie and ctx.discard(1, optional=True):
        ctx.gain_hp(ctx.source_cookie, 1)


# --- BS5-054 Snake Fruit Cookie ---------------------------------------------
@effect("BS5-054", Trigger.WHEN_ATTACKED)
def snake_fruit_when_attacked(ctx: Ctx) -> None:
    """When your opponent's Cookie attacks, <place 1 card from your support
    area into the trash.> Select up to 1 of your opponent's Cookies. During
    this turn, that Cookie deals -1 attack damage."""
    if not ctx.me.support:
        return
    paid = ctx.choose("Trash a support card", list(ctx.me.support), optional=True)
    if paid is None:
        return
    ctx.me.support.remove(paid)
    ctx.me.trash.append(paid)
    target = ctx.select_enemy(prompt="Debuff which attacker?")
    if target is not None:
        ctx.modify_attack(target, -1)


# --- BS5-067 Snow Sugar Cookie ----------------------------------------------
@effect("BS5-067", Trigger.ATTACK)
def snow_sugar_attack(ctx: Ctx) -> None:
    """Then, view 3 cards from the top of your deck and place them on the top
    of your deck in any order.

    Ordering is the whole effect: FLIP cards are pushed to the front so they
    land in the next HP pile that gets built.
    """
    viewed = ctx.me.deck[:3]
    if len(viewed) < 2:
        return
    del ctx.me.deck[:len(viewed)]
    viewed.sort(key=lambda c: not ctx.db[c.card_id].is_flip)
    ctx.me.deck[:0] = viewed


# --- BS5-069 Pond Dino Cookie -----------------------------------------------
@effect("BS5-069", Trigger.ATTACK_START)
def pond_dino_static(ctx: Ctx) -> None:
    """If there are 3 cards or less in your hand, this Cookie gains +1 attack
    damage."""
    if ctx.source_cookie and ctx.hand_size <= 3:
        ctx.modify_attack(ctx.source_cookie, 1)


# --- BS5-072 Gumball Cookie -------------------------------------------------
@effect("BS5-072", Trigger.FAINT)
def gumball_faint(ctx: Ctx) -> None:
    """When this Cookie faints and your break area is LV.6 or higher, draw up
    to 2 cards from your deck."""
    if ctx.me.break_level_total(ctx.db) >= 6:
        ctx.draw(2)


# --- BS5-081 Squid Ink Cookie -----------------------------------------------
@effect("BS5-081", Trigger.WHEN_ATTACKED)
def squid_ink_when_attacked(ctx: Ctx) -> None:
    """When your opponent's Cookie attacks, <discard 4 cards.> During this
    battle, this Cookie's HP cannot reach 0."""
    cookie = ctx.source_cookie
    if cookie is not None and ctx.discard(4, optional=True):
        cookie.hp_cannot_reach_zero = True


# --- BS5-092 Rambutan Cookie ------------------------------------------------
@effect("BS5-092", Trigger.WHEN_ATTACKED)
def rambutan_when_attacked(ctx: Ctx) -> None:
    """When your opponent's Cookie attacks, <return 3 non-Cookie cards from
    your trash to your deck and shuffle it.> Select up to 1 of your opponent's
    Cookies. During this turn, that Cookie deals -1 attack damage."""
    pool = [c for c in ctx.me.trash if not ctx.db[c.card_id].is_cookie]
    if len(pool) < 3:
        return
    for _ in range(3):
        card = ctx.choose("Shuffle a non-Cookie card back into your deck",
                          pool, optional=False) or pool[0]
        pool.remove(card)
        ctx.me.trash.remove(card)
        ctx.me.deck.append(card)
    ctx.state.rng.shuffle(ctx.me.deck)
    target = ctx.select_enemy(prompt="Debuff which attacker?")
    if target is not None:
        ctx.modify_attack(target, -1)


# --- BS5-100 Yogurt Cream Cookie --------------------------------------------
@effect("BS5-100", Trigger.TRASHED)
def yogurt_cream_trashed(ctx: Ctx) -> None:
    """When this Cookie is placed from the battle area into the trash, view 3
    cards from the top of your deck, reveal up to 1 {P} card from the viewed
    cards, and add it to your hand. Then, place the remaining cards in the
    trash."""
    viewed = ctx.me.deck[:3]
    if not viewed:
        return
    del ctx.me.deck[:len(viewed)]
    purple = [c for c in viewed if ctx.db[c.card_id].color is Color.PURPLE]
    if purple:
        picked = ctx.choose("Add a {P} card to your hand", purple, optional=True)
        if picked is not None:
            viewed.remove(picked)
            ctx.me.hand.append(picked)
    ctx.me.trash.extend(viewed)


# --- BS5-103 Scorpion Cookie ------------------------------------------------
@effect("BS5-103", Trigger.ATTACK_START)
def scorpion_static(ctx: Ctx) -> None:
    """For every 15 cards in your trash, this Cookie gains +1 attack damage."""
    bonus = len(ctx.me.trash) // 15
    if ctx.source_cookie and bonus:
        ctx.modify_attack(ctx.source_cookie, bonus)


# --- BS5-111 Wrath of the Dragons -------------------------------------------
def _dragon_wrath_reduction(db, state, cookie) -> int:
    """"receives -1 attack damage" while the jam is on and its host is hurt.

    Read off the equipment every time damage lands, not banked when the card
    was attached: the condition is the host's *current* HP, and a Cookie that
    was healed back above 3 is no longer the Cookie the card describes.
    """
    if not any(c.card_id.split("@")[0] == "BS5-111" for c in cookie.equipment):
        return 0
    return 1 if cookie.remaining_hp <= 3 else 0


DAMAGE_REDUCERS.append(_dragon_wrath_reduction)


@effect("BS5-111", Trigger.ATTACK_START)
def dragon_wrath_aura(ctx: Ctx) -> None:
    """"that Cookie gains +1 attack damage" — the other half of the same rider.

    Registered against the jam's card id and reached through
    `Game._run_equipment_effects`, with the host as `source_cookie`.
    """
    cookie = ctx.source_cookie
    if cookie is not None and cookie.remaining_hp <= 3:
        ctx.modify_attack(cookie, 1)


@effect("BS5-111", Trigger.ITEM)
def dragon_wrath(ctx: Ctx) -> None:
    """<{N}> 【Equip】 this card to one of your 【Dragon】 Cookies. If that
    Cookie's remaining HP is 3 or less, that Cookie gains +1 attack damage and
    receives -1 attack damage.

    【Equip】 here is not optional — the card has no other effect, so a copy
    with nowhere to attach is simply not a legal play (`playable_if`).
    """
    card = ctx.source_card
    holder = ctx.select_own(lambda c: Keyword.DRAGON in c.defn(ctx.db).keywords,
                            prompt="Equip to which 【Dragon】 Cookie?")
    if holder is None or card is None:
        return
    if card in ctx.me.trash:
        ctx.me.trash.remove(card)
    holder.equipment.append(card)


dragon_wrath.playable = lambda ctx: any(
    Keyword.DRAGON in c.defn(ctx.db).keywords for c in ctx.me.battle)
