"""Hand-written promo (P) cards.

One card so far, and it is here for a reason the compiler cannot fix: its
condition is a fact about the world, not about the board.
"""

from __future__ import annotations

from braverse.cost import Cost
from braverse.effects import Ctx, Trigger, effect, playable_if


# --- P-041 Birthday Cake Cookie ---------------------------------------------
def _is_birthday(ctx: Ctx) -> bool:
    """"If today is your birthday" — so ask; there is nowhere else to look.

    Reading the system clock would answer it, and would also break every
    existing recording: a replay re-runs the engine and would get a different
    answer on a different day. A question is a decision, and decisions are
    exactly what `replay` and `netplay` already carry, so asking is both the
    honest reading of the card and the reproducible one.
    """
    return ctx.confirm("Is today your birthday?")


@effect("P-041", Trigger.ON_PLAY)
def birthday_cake_on_play(ctx: Ctx) -> None:
    """【On Play】 If today is your birthday, receive a happy birthday
    congratulation from your opponent.

    No game effect — the card asks for something the rules cannot enforce — so
    the whole of it is the line in the log.
    """
    if _is_birthday(ctx):
        ctx.note("Happy birthday!")


@effect("P-041", Trigger.ATTACK)
def birthday_cake_attack(ctx: Ctx) -> None:
    """<{R}{R}> Happy birthday! deals 1
    Then, if today is your birthday, during this turn, this Cookie gains +1
    attack damage."""
    if ctx.source_cookie is not None and _is_birthday(ctx):
        ctx.modify_attack(ctx.source_cookie, 1)


# --- P-084 Magic Lettering Pens ---------------------------------------------
def _pens_cost(ctx: Ctx) -> Cost:
    """"if your Cookie fainted, this card's activation cost becomes {N}".

    Not cheaper — *colourless*. The same single symbol, payable from any
    support card instead of a green one.
    """
    return Cost.parse("{N}") if ctx.me.cookies_fainted_this_turn else Cost.parse("{G}")


def _pens_live(ctx: Ctx) -> bool:
    """Both halves of the price have to be payable, and there has to be
    something to shoot at."""
    return (ctx.can_pay(_pens_cost(ctx))
            and any(not c.rested for c in ctx.me.battle)
            and bool(ctx.enemy_cookies()))


@effect("P-084", Trigger.ITEM)
@playable_if(_pens_live)
def magic_lettering_pens(ctx: Ctx) -> None:
    """During this turn, if your Cookie fainted, this card's activation cost
    becomes {N}. <{G}> <Rest 1 Cookie in your battle area.> Select up to 1 of
    your opponent's Cookies. That Cookie receives 1 damage.

    The rewritten cost is this card's own `<{G}>`, not the one the engine
    charges: nothing is printed at the head of the text, so `play_cost` is
    empty and the colour is paid from inside the effect. Losing a Cookie this
    turn does not make the card cheaper, it makes it *colourless* — the same
    one symbol, payable from any support card.
    """
    if not ctx.pay(_pens_cost(ctx)):
        return
    # "<Rest 1 Cookie in your battle area.>" — a cost, so it must actually be
    # paid; an all-rested battle area cannot afford it.
    resters = [c for c in ctx.me.battle if not c.rested]
    if not resters:
        return
    rester = ctx.choose("Rest which Cookie?", resters, optional=False) or resters[0]
    rester.rested = True

    target = ctx.select_enemy()
    if target is not None:
        ctx.deal_damage(target, 1)


# --- P-082 Sugar Gnome Cake Shop --------------------------------------------
def _gnome_fodder(ctx: Ctx) -> list:
    """"1 Cookie with 1 HP that does not have FLIP" in your trash."""
    return [c for c in ctx.me.trash
            if ctx.db[c.card_id].is_cookie
            and (ctx.db[c.card_id].hp or 0) == 1
            and not ctx.db[c.card_id].is_flip]


def _gnome_live(ctx: Ctx) -> bool:
    return ((ctx.can_pay(Cost.parse("{Y}{N}")) or bool(_gnome_fodder(ctx)))
            and bool(ctx.me.battle) and bool(ctx.opp.battle))


@effect("P-082", Trigger.ITEM)
@playable_if(_gnome_live)
def sugar_gnome_cake_shop(ctx: Ctx) -> None:
    """<{Y}{N}> or <place 1 Cookie with 1 HP that does not have FLIP from your
    trash into your break area.> Select 1 Cookie from each player. Those
    Cookies gain +2 HP.

    Two prices, and the player picks — which is why the head of the text is
    not this card's `play_cost`: the engine charges that before the effect
    runs, and it would have billed the energy whichever way the choice went.
    Banking a Cookie into your own break area is the other price, and it is a
    real one — the break area is how you lose.
    """
    fodder = _gnome_fodder(ctx)
    energy = Cost.parse("{Y}{N}")
    options = []
    if ctx.can_pay(energy):
        options.append("Pay {Y}{N}")
    if fodder:
        options.append("Bank a 1 HP Cookie from your trash into your break area")
    if not options:
        return
    picked = ctx.choose("Which cost?", options, optional=False) or options[0]

    if picked.startswith("Pay"):
        if not ctx.pay(energy):
            return
    else:
        card = ctx.choose("Bank which Cookie?", fodder, optional=False) or fodder[0]
        ctx.me.trash.remove(card)
        ctx.me.break_area.append(card)
        ctx.game._check_win()
        if ctx.state.over:
            return

    for player, prompt in ((ctx.me, "Heal which of your Cookies?"),
                           (ctx.opp, "Heal which of their Cookies?")):
        if not player.battle:
            continue
        cookie = ctx.choose(prompt, list(player.battle), optional=False) or player.battle[0]
        ctx.gain_hp(cookie, 2)


# --- P-147 Licorice Cookie --------------------------------------------------
# "【Special Play】 Place 1 {K} LV.1 Cookie from your battle area into your
# trash." — the same entrance the three BS11 LV.2 Dough Cookies print, so it is
# wired by the same helper rather than copied. See `impl/bs11.py` for why a
# 【Special Play】 line is a gate and not an optional cost.
from braverse.impl.bs11 import register_special_play   # noqa: E402

register_special_play("P-147", 1, 1)
