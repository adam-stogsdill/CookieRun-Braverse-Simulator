"""The visual player's match thread: pacing, hidden information, answering."""

from __future__ import annotations

import inspect
import re
import time

import pytest

from braverse import default_db
from play_server import (MAX_SCENE_PAUSE, VIEWER, Handler, Match,
                         MatchConfig, available_decks, available_pilots,
                         scene_seconds)


def wait_for(predicate, timeout: float = 20.0, interval: float = 0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


def play_the_toss(match: Match):
    """Answer the opening rock-paper-scissors and return the next question.

    A human seat is asked for its throw before the game is even dealt, so a
    test that wants a board has to get past the toss first.
    """
    for _ in range(20):
        pending = wait_for(lambda: match.view().get("pending"))
        if not pending:
            return None
        if "scissors" not in pending["prompt"] and "goes first" not in pending["prompt"]:
            return pending
        version = match.view()["version"]
        match.answer(0)
        wait_for(lambda: match.view()["version"] > version)
    return None


def bot_match(**kw) -> Match:
    config = MatchConfig(decks=["st9_sea_fairy", "st8_wind_archer"],
                         pilots=["heuristic", "heuristic"], seed=3, delay=0.0, **kw)
    return Match(config, default_db())


def test_decks_and_pilots_are_offered():
    decks = available_decks()
    assert "st9_sea_fairy" in decks and len(decks["st9_sea_fairy"]) == 60
    assert available_pilots()[:3] == ["human", "heuristic", "random"]


def test_bot_match_plays_itself_out():
    match = bot_match()
    match.start()
    assert wait_for(lambda: match.view().get("over")), "match did not finish"
    view = match.view()
    assert view["winner"] in (0, 1, -1)
    assert view["log"]
    assert view["error"] is None


def test_paused_match_only_advances_when_stepped():
    match = bot_match(paused=True)
    match.start()
    assert wait_for(lambda: match.view().get("players"))
    before = match.view()["version"]
    time.sleep(0.4)
    assert match.view()["version"] == before, "a paused match kept playing"

    with match.cond:
        match.step_once = True
        match.cond.notify_all()
    assert wait_for(lambda: match.view()["version"] > before), "step did not advance"
    match.stop()


def test_human_seat_blocks_until_answered_and_hides_the_other_hand():
    config = MatchConfig(decks=["st9_sea_fairy", "st8_wind_archer"],
                         pilots=["human", "heuristic"], seed=5, delay=0.0)
    match = Match(config, default_db())
    match.start()
    pending = play_the_toss(match)
    assert pending, "no question reached the browser"
    assert pending["seat"] == 0 and pending["options"]

    view = match.view()
    assert view["players"][0]["hand"], "the human cannot see their own hand"
    assert view["players"][1]["hand"] == [], "the opponent's hand leaked"
    for cookie in view["players"][0]["battle"]:
        assert cookie["hpPileCards"] == [], "the face-down HP pile leaked"

    version = view["version"]
    assert match.answer(0)
    assert wait_for(lambda: match.view()["version"] > version)
    match.stop()


def hands_visible(pilots, reveal):
    """How many hands the browser is allowed to see, without dealing a game."""
    config = MatchConfig(decks=["st9_sea_fairy", "st8_wind_archer"],
                         pilots=pilots, seed=5, delay=0.0, reveal=reveal)
    match = Match(config, default_db())
    snap = {"players": [{"index": 0, "hand": [{}], "battle": []},
                        {"index": 1, "hand": [{}], "battle": []}]}
    match._hide(snap)
    return [len(p["hand"]) for p in snap["players"]]


def test_reveal_is_for_spectating_and_cannot_be_used_to_peek():
    """`reveal` used to read `config.reveal or not human_seats`, which was
    backwards both ways: in a match you were playing it handed you the bot's
    hand, and while watching two bots it did nothing."""
    # Playing: your opponent's hand is hidden however the toggle is set.
    assert hands_visible(["human", "heuristic"], False) == [1, 0]
    assert hands_visible(["human", "heuristic"], True) == [1, 0]
    assert hands_visible(["heuristic", "human"], True) == [0, 1]

    # Watching: the toggle is the whole point, so it has to work.
    assert hands_visible(["heuristic", "heuristic"], False) == [0, 0]
    assert hands_visible(["heuristic", "heuristic"], True) == [1, 1]

    # Hot seat: two people at one screen already see everything.
    assert hands_visible(["human", "human"], False) == [1, 1]


def test_reveal_shows_both_hands_when_nobody_is_playing():
    config = MatchConfig(decks=["st9_sea_fairy", "st8_wind_archer"],
                         pilots=["heuristic", "heuristic"], seed=5, delay=0.0, reveal=True)
    match = Match(config, default_db())
    match.start()
    assert wait_for(lambda: match.view().get("players"))
    view = match.view()
    assert view["players"][0]["hand"] and view["players"][1]["hand"]
    for player in view["players"]:
        for cookie in player["battle"]:
            assert cookie["hpPileCards"] == [], "reveal must not turn the HP pile face up"
    match.stop()


def card_stub(uid, type_="COOKIE"):
    return {"uid": uid, "id": "ST9-003", "name": "Blue Whale Cookie", "type": type_}


def snapshot_with(pile, trash, battle=True, break_area=()):
    """Two-player snapshot skeleton; seat 0 holds one Cookie."""
    cookie = {"uid": 99, "card": card_stub(50), "hpPileCards": [card_stub(u) for u in pile]}
    return {"players": [
        {"battle": [cookie] if battle else [],
         "trash": [card_stub(u) for u in trash],
         "break": [card_stub(u) for u in break_area]},
        {"battle": [], "trash": [], "break": []},
    ]}


def test_reveals_are_reported_as_the_card_turns_not_after_the_flip():
    """The order is the point.

    A zone diff can only ever say "this card ended up in the trash", which is
    after its FLIP has already resolved — so the board played the heal, the
    draw or the bounce before showing the card that caused it. The engine now
    records the reveal at the moment the card turns, and the browser plays the
    batch in that order.
    """
    from braverse import STARTER_DECKS, Game, HeuristicAgent, SeatedAgent
    from braverse.state import CardInstance

    db = default_db()
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=5)
    game.setup()

    class SaysYes:
        def choose_action(self, state, options):
            return options[0]

        def choose(self, state, prompt, options, *, optional):
            return options[0]

    game._controllers[1] = SaysYes()
    player = game.state.players[1]
    cookie = player.battle[0]
    while cookie.hp_cards:
        player.trash.append(cookie.hp_cards.pop())
    # A healing FLIP on top of two plain cards: ST8-007 hands its host a card
    # back, so the reveal and the heal are in the same hit.
    for card_id in ("ST8-011", "ST8-011", "ST8-007"):
        card = CardInstance.make(card_id, 1)
        card.face_up = False
        cookie.hp_cards.append(card)

    game.state.events.clear()
    game.deal_damage(cookie, 1, source_player=0, kind="attack")
    kinds = [e["kind"] for e in game.state.events]
    assert kinds == ["reveal", "heal", "damage"], kinds
    reveal = game.state.events[0]
    assert reveal["card_id"] == "ST8-007" and reveal["flip"] is True


def test_a_reveal_without_a_flip_is_not_flagged_as_one():
    """"Place N cards from the top of that Cookie's HP into the trash" turns
    cards face up but fires no FLIP, so it is not the big beat."""
    from braverse import STARTER_DECKS, Game, HeuristicAgent, SeatedAgent
    from braverse.effects import Ctx

    db = default_db()
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=5)
    game.setup()
    ctx = Ctx(game=game, state=game.state, db=db, me=game.state.players[0],
              opp=game.state.players[1])
    victim = game.state.players[1].battle[0]

    game.state.events.clear()
    ctx.trash_hp(victim, 2)
    assert [e["kind"] for e in game.state.events] == ["reveal", "reveal"]
    assert all(e["flip"] is False for e in game.state.events)


def test_a_faint_waits_for_a_revealed_card_to_clear():
    """The board must not change under a card someone is still reading."""
    reveal_then_faint = scene_seconds([{"type": "reveal"}, {"type": "faint"}])
    assert reveal_then_faint > scene_seconds([{"type": "reveal"}])
    assert reveal_then_faint > scene_seconds([{"type": "faint"}])


def test_a_sprung_trap_is_its_own_event_not_a_skill_pop():
    """A trap is the one card that fires on someone else's turn, in the middle
    of their attack, so the board plays it big and in the middle. That needs an
    event type of its own — as a `skill` it was a small pop over on its owner's
    half of the board, which is where it is *least* worth looking."""
    import types

    config = MatchConfig(decks=["st9_sea_fairy", "st8_wind_archer"],
                         pilots=["heuristic", "heuristic"], seed=5, delay=0.0)
    match = Match(config, default_db())
    match.game = types.SimpleNamespace(state=types.SimpleNamespace(log=[
        "T4 P0 Sea Fairy Cookie attacks Leek Cookie for 3",
        "T4 P1 springs trap Divine Light Crystal",
    ]))

    events = match._trap_events({})
    assert len(events) == 1
    assert events[0]["type"] == "trap"
    assert events[0]["owner"] == 1
    assert events[0]["name"] == "Divine Light Crystal"
    assert events[0]["card"]["id"] == "ST3-020"

    # And the scene is long enough that the card is read before the bot moves.
    assert scene_seconds(events) > scene_seconds([{"type": "skill"}])
    # Read once: the same line does not fire the animation on every poll.
    assert match._trap_events({}) == []


def test_an_item_gets_the_spotlight_and_a_stage_does_not():
    """An ITEM is played from hand, does its thing and goes to the trash — the
    same shape as a trap, and the same reason for playing it big in the middle
    rather than as a small pop over an empty patch of board. A STAGE is still
    sitting there afterwards, so it keeps the pop."""
    from braverse import (STARTER_DECKS, Game, HeuristicAgent, SeatedAgent,
                          actions as A)
    from braverse.state import CardInstance

    db = default_db()
    config = MatchConfig(decks=["st9_sea_fairy", "st8_wind_archer"],
                         pilots=["heuristic", "heuristic"], seed=1, delay=0.0)
    match = Match(config, db)
    game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0),
                 SeatedAgent(HeuristicAgent(db=db), 1)], db=db, seed=1)
    game.setup()
    match.game = game

    hand = game.state.players[0].hand
    item = CardInstance.make("ST8-014", 0)      # Cape of the Vanquisher, ITEM
    stage = CardInstance.make("ST8-020", 0)     # Dark Enchantress Laboratory
    hand += [item, stage]

    match._note_skill(A.PlaySupportCard(item.uid))
    match._note_skill(A.PlaySupportCard(stage.uid))

    kinds = [e["type"] for e in match._queued]
    assert kinds == ["item", "skill"], kinds
    assert match._queued[0]["name"] == "Cape of the Vanquisher"
    # And it is long enough on screen that a bot does not move under it.
    assert scene_seconds([match._queued[0]]) > scene_seconds([match._queued[1]])


def test_a_cookie_arriving_is_its_own_event():
    """Every zone a Cookie can arrive from means the same thing on the board,
    so the arrival is read off a diff the way a faint is. An 【Awaken】 keeps
    the host's uid, so restacking one is correctly not an arrival."""
    was = {"players": [{"battle": [{"uid": 1, "card": card_stub(1)}]},
                       {"battle": []}]}
    now = {"players": [{"battle": [{"uid": 1, "card": card_stub(1)},
                                   {"uid": 2, "card": card_stub(2)}]},
                       {"battle": []}]}
    events = Match._summon_events(was, now)
    assert len(events) == 1
    assert events[0]["type"] == "summon" and events[0]["cookie"] == 2
    assert events[0]["owner"] == 0
    # The dust takes the Cookie's colour, so the event has to carry it.
    assert "color" in events[0]
    assert Match._summon_events(was, was) == []
    assert Match._summon_events(None, now) == []


def test_bots_wait_for_the_whole_scene_not_a_fixed_beat():
    """The pause a bot takes scales with what the browser has to animate."""
    attack = scene_seconds([{"type": "attack"}])
    with_damage = scene_seconds([{"type": "attack"}] + [{"type": "reveal"}] * 3)
    with_faint = scene_seconds([{"type": "attack"}] + [{"type": "reveal"}] * 3
                               + [{"type": "faint"}])
    assert 0 < attack < with_damage < with_faint

    # The browser only animates the first six reveals, so the wait stops growing.
    assert scene_seconds([{"type": "reveal"}] * 20) == scene_seconds([{"type": "reveal"}] * 6)
    # And a pathological batch can never stall the match.
    assert scene_seconds([{"type": "attack"}] * 50) == MAX_SCENE_PAUSE
    assert scene_seconds([]) == 0.0


def test_the_wait_covers_a_reveal_leaving_the_screen_not_just_arriving():
    """A bot must not move again while a revealed card is still being read."""
    one = scene_seconds([{"type": "reveal"}])
    assert one >= 2.0, "no time to read a single revealed card"
    # A second card adds only the gap between them; both are still waited out.
    assert scene_seconds([{"type": "reveal"}] * 2) == pytest.approx(one + 0.7)
    # Breaking a Cookie is likewise waited out to the end of its animation.
    assert scene_seconds([{"type": "faint"}]) >= 1.5


def test_a_cookie_leaving_the_battle_area_is_a_faint_event():
    before = snapshot_with(pile=[1], trash=[])

    # Fainted: the Cookie card itself lands in the break area.
    broke = Match._faint_events(before, snapshot_with(pile=[], trash=[1],
                                                     battle=False, break_area=[50]))
    assert len(broke) == 1
    assert broke[0]["type"] == "faint" and broke[0]["owner"] == 0
    assert broke[0]["cookie"] == 99 and broke[0]["broke"] is True

    # Trashed or bounced instead: gone from the board, but nothing was banked.
    quiet = Match._faint_events(before, snapshot_with(pile=[], trash=[1], battle=False))
    assert quiet[0]["broke"] is False, "only the break area banks Level"

    # Still standing.
    assert Match._faint_events(before, snapshot_with(pile=[1], trash=[])) == []


def test_stopping_a_match_releases_a_blocked_human_seat():
    config = MatchConfig(decks=["st9_sea_fairy", "st8_wind_archer"],
                         pilots=["human", "heuristic"], seed=5, delay=0.0)
    match = Match(config, default_db())
    match.start()
    assert wait_for(lambda: match.view().get("pending"))
    match.stop()
    match.thread.join(timeout=5)
    assert not match.thread.is_alive(), "the match thread outlived its match"


def test_actions_are_named_the_way_the_card_names_them():
    """Clicking a card lists what it can do, by skill name where there is one.

    980 of the Cookies print a name for their attack; the older
    `<{P}{P}> Deals 2 damage.` printing on 220 of them does not, and no
    【Activate】 skill in the whole pool is named. Those fall back to the
    marker itself.
    """
    from braverse import Game, HeuristicAgent, SeatedAgent
    from braverse import actions as A
    from play_server import skill_label

    db = default_db()
    game = Game([available_decks()["st8_wind_archer"], available_decks()["st9_sea_fairy"]],
                [SeatedAgent(HeuristicAgent(db=db), 0), SeatedAgent(HeuristicAgent(db=db), 1)],
                db=db, seed=6)
    game.setup()
    state = game.state

    named = next(c for c in state.players[0].battle if c.defn(db).attack)
    label = skill_label(db, state, A.Attack(named.uid, state.players[1].battle[0].uid))
    assert label == named.defn(db).attack.name, "the printed attack name was dropped"
    assert label != "Attack"

    # Unnamed printing: the marker stands in for the missing name.
    unnamed = db["ST5-015"]                       # `<{P}{P}{P}> Deals 3 damage.`
    assert unnamed.attack and not unnamed.attack.name
    bare = state.players[0].battle[0]
    bare.card.card_id = unnamed.id
    assert skill_label(db, state, A.Attack(bare.uid, state.players[1].battle[0].uid)) == "Attack"

    # No skill in the pool is named, so 【Activate】 is always just that.
    assert skill_label(db, state, A.ActivateSkill(bare.uid)) == "Activate"

    # Every other action still gets something sensible to click.
    card = state.players[0].hand[0]
    assert skill_label(db, state, A.PlaceSupport(card.uid)) == "Place as support"
    assert skill_label(db, state, A.PlayCookie(card.uid)) == "Play"
    assert skill_label(db, state, A.PlayTrap(card.uid)) == "Spring trap"
    assert skill_label(db, state, A.EndTurn()) == "End turn"


def test_activating_a_skill_confirms_itself():
    """Most skills change nothing you can see, so the action itself is the event.

    The engine also had to start logging activations at all: `_do_activate`
    recorded nothing, so a skill that drew a card left no trace anywhere.
    """
    from braverse import actions as A

    config = MatchConfig(decks=["st9_sea_fairy", "st8_wind_archer"],
                         pilots=["heuristic", "heuristic"], seed=4, delay=0.0)
    match = Match(config, default_db())
    match.game.setup()

    cookie = match.game.state.players[0].battle[0]
    match._note_action(A.ActivateSkill(cookie.uid))
    assert len(match._queued) == 1
    event = match._queued[0]
    assert event["type"] == "skill" and event["name"] == "Activate"
    assert event["owner"] == 0 and event["uid"] == cookie.uid
    assert event["card"]["name"] == cookie.name(match.db)

    # And the wait for it is long enough to actually read.
    assert scene_seconds([event]) >= 1.5


def test_the_engine_records_activations():
    from braverse import Game, HeuristicAgent, SeatedAgent
    from braverse import actions as A

    db = default_db()
    game = Game([available_decks()["st9_sea_fairy"], available_decks()["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db, seed=1), 0),
                 SeatedAgent(HeuristicAgent(db=db, seed=2), 1)], db=db, seed=4)
    game.setup()
    for _ in range(120):
        if game.state.over:
            break
        options = game.legal_actions()
        skill = next((o for o in options if isinstance(o, A.ActivateSkill)), None)
        if skill:
            before = len(game.state.log)
            game.step(skill)
            new = game.state.log[before:]
            assert any("activates" in line for line in new), new
            return
        game.step(game.controller(game.to_move()).choose_action(game.state, options))
    raise AssertionError("no skill was ever available to activate")


def test_questions_about_your_own_hand_are_answered_with_your_hand():
    """Opening Cookie, replacements and discards are all the same gesture.

    Decided structurally — every option is a card in your hand — rather than by
    matching prompt strings, so a new prompt of the same shape gets the picker
    for free. Only the verb on the confirm button reads from the prompt.

    The one carve-out is the opening Cookie: it is answered by clicking the
    Cookies the viewer stands up out of your hand, so it takes no picker.
    """
    from braverse import Game, HeuristicAgent, SeatedAgent
    from braverse.engine import OPENING_COOKIE_PROMPT
    from play_server import hand_pick

    db = default_db()
    game = Game([available_decks()["st9_sea_fairy"], available_decks()["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0), SeatedAgent(HeuristicAgent(db=db), 1)],
                db=db, seed=4)
    game.setup()
    me = game.state.players[0]

    assert hand_pick("Field a replacement Cookie", me.hand, me) == {"verb": "Play Cookie"}
    assert hand_pick("Discard 2 cards", me.hand, me) == {"verb": "Discard"}
    assert hand_pick("Reveal a card", me.hand, me) == {"verb": "Choose"}

    # Not your hand, not the picker: a question about the board stays a list.
    assert hand_pick("Damage which Cookie?", me.battle, me) is None
    assert hand_pick("Field a replacement Cookie",
                     game.state.players[1].hand, me) is None
    assert hand_pick("Anything", [], me) is None

    # The opening Cookie opts out: it is raised in the hand and clicked there.
    assert hand_pick(OPENING_COOKIE_PROMPT, me.hand, me) is None


def test_every_action_that_names_a_card_names_it_in_subject():
    """The board is the move list now, so `subject` is load-bearing.

    The viewer offers a move by lighting up the card it belongs to and putting
    it in that card's click menu, and it finds that card by `subject`. A move
    that named a card only inside its prose label would light nothing up and
    open no menu. The two kinds that legitimately name nothing — End turn and
    Pass — are the ones the tray in the middle of the table exists for, and the
    viewer routes anything else it cannot place there too, so this is a
    tidiness pin rather than the only thing standing between a move and being
    unreachable.
    """
    from braverse import Game, HeuristicAgent, SeatedAgent
    from play_server import action_json

    db = default_db()
    NAMES_NOTHING = {"EndTurn", "Pass"}
    seen = set()
    for seed in range(6):
        agents = [SeatedAgent(HeuristicAgent(db=db), 0),
                  SeatedAgent(HeuristicAgent(db=db), 1)]
        game = Game([available_decks()["st9_sea_fairy"], available_decks()["st8_wind_archer"]],
                    agents, db=db, seed=seed)
        game.setup()
        for _ in range(300):
            if game.state.over:
                break
            options = game.legal_actions()
            if not options:
                break
            for i, action in enumerate(options):
                payload = action_json(db, game.state, i, action)
                seen.add(payload["kind"])
                if payload["kind"] in NAMES_NOTHING:
                    assert "subject" not in payload, payload
                else:
                    assert payload.get("subject") is not None, payload
            chosen = agents[game.state.turn_player].choose_action(game.state, options)
            game.step(chosen or options[0])

    # The pin is worthless if the games never got past "end turn".
    assert {"Attack", "PlayCookie", "PlaceSupport"} <= seen, seen


def test_the_toss_is_asked_in_the_middle_of_the_table():
    """Tracking to the far-right panel to throw rock is a silly way to start."""
    from braverse.rps import CHOICES, THROWS
    from play_server import centre_style

    assert centre_style(list(THROWS)) == "throw"
    assert centre_style(list(CHOICES)) == "choice"
    # Both mulligan questions belong there too — the free one and the priced
    # repeat a Cookie-less hand is offered.
    from play_server import MULLIGAN_CHOICES, REDRAW_CHOICES
    assert centre_style(list(MULLIGAN_CHOICES)) == "choice"
    assert centre_style(list(REDRAW_CHOICES)) == "choice"

    # Everything else stays in the panel, including anything non-textual.
    assert centre_style(["rock", "paper"]) is None
    assert centre_style([]) is None
    assert centre_style([object(), object()]) is None


def hand_snapshot(hand_uids, deck_count):
    """Two-player snapshot skeleton for the draw diff; seat 0 is the one moving."""
    return {"players": [
        {"hand": [card_stub(u) for u in hand_uids], "deckCount": deck_count},
        {"hand": [], "deckCount": 40},
    ]}


def test_a_draw_is_a_card_that_came_off_the_deck():
    """Animated from a diff, and told apart from a card arriving any other way."""
    before = hand_snapshot([1, 2, 3], 30)

    drew = Match._draw_events(before, hand_snapshot([1, 2, 3, 4, 5], 28))
    assert drew == [{"type": "draw", "owner": 0, "count": 2}]

    # A Cookie bounced back to hand is not a draw: the deck never moved.
    assert Match._draw_events(before, hand_snapshot([1, 2, 3, 9], 30)) == []

    # Both at once — one drawn, one bounced — animates only the one that flew.
    mixed = Match._draw_events(before, hand_snapshot([1, 2, 3, 8, 9], 29))
    assert mixed[0]["count"] == 1

    # Playing a card out of hand is not a draw either.
    assert Match._draw_events(before, hand_snapshot([1, 2], 30)) == []
    assert Match._draw_events(None, before) == []


def discard_snapshot(hand_uids, trash_uids, hand_types=None):
    """Two-player snapshot skeleton for the discard diff; seat 0 is paying."""
    types = hand_types or {}
    return {"players": [
        {"hand": [card_stub(u, types.get(u, "COOKIE")) for u in hand_uids],
         "trash": [card_stub(u, types.get(u, "COOKIE")) for u in trash_uids]},
        {"hand": [], "trash": []},
    ]}


def test_a_discard_is_animated_for_both_players():
    """Paying a cost is otherwise silent, so the card flies where both can see."""
    before = discard_snapshot([1, 2, 3], [])

    paid = Match._discard_events(before, discard_snapshot([1], [2, 3]), [])
    assert [e["type"] for e in paid] == ["discard", "discard"]
    assert [e["card"]["uid"] for e in paid] == [2, 3]
    assert all(e["owner"] == 0 for e in paid)

    # Identity is sent: the trash it lands in is a zone either player may read.
    assert paid[0]["card"]["name"] == "Blue Whale Cookie"

    # A card leaving hand for anywhere but the trash is not a discard, and a
    # card reaching the trash from anywhere but a hand is not one either.
    assert Match._discard_events(before, discard_snapshot([1], [])) == []
    assert Match._discard_events(before, discard_snapshot([1, 2, 3], [9])) == []
    assert Match._discard_events(None, before) == []


def test_a_played_card_is_not_replayed_as_a_discard():
    """An Item or a Trap travels hand-to-trash too, and already owns the middle
    of the board for two seconds; sending it again would play the cost of a
    card that *was* the play."""
    before = discard_snapshot([1, 2], [], {1: "ITEM"})
    after = discard_snapshot([], [1, 2], {1: "ITEM"})

    # An Item is matched off by uid, which `_note_skill` knows.
    item = [{"type": "item", "owner": 0, "card": card_stub(1, "ITEM")}]
    assert [e["card"]["uid"] for e in Match._discard_events(before, after, item)] == [2]

    # A trap is recognised off the log and carries no uid, so its card id is
    # what matches — and only once, so a second copy still animates.
    trap = [{"type": "trap", "owner": 0, "card": {"id": "ST9-003"}}]
    assert [e["card"]["uid"] for e in Match._discard_events(before, after, trap)] == [2]


def test_a_draw_event_carries_no_card_identity():
    """A drawn card is secret; the animation only needs a count."""
    drew = Match._draw_events(hand_snapshot([1], 30), hand_snapshot([1, 2], 29))
    assert set(drew[0]) == {"type", "owner", "count"}

    # And the wait scales with how many cards actually fly.
    assert scene_seconds([{"type": "draw", "owner": 0, "count": 2}]) > \
           scene_seconds([{"type": "draw", "owner": 0, "count": 1}])


def test_the_snapshot_carries_what_the_round_track_needs():
    """The phase strip above the break area is built from these four fields.

    It cannot be driven by `phase` alone: the engine only ever *reports* `main`
    to a player — it untaps and draws inside the turn machinery, and never
    enters `support` at all, because placing a support card is a main-phase
    action capped at one per turn. So the strip also reads whose turn it is,
    who opened (they skip their first draw), and whether the support has been
    placed yet.
    """
    match = bot_match()
    match.start()
    assert wait_for(lambda: match.view().get("players"))
    view = match.view()

    assert view["phase"] == "main", "a player is only ever asked during main"
    assert view["turnPlayer"] in (0, 1)
    assert view["firstPlayer"] in (0, 1)
    for player in view["players"]:
        assert isinstance(player["supportedThisTurn"], bool)
    match.stop()


def test_the_support_flag_is_what_tells_you_a_support_is_still_owed():
    from braverse import Game, HeuristicAgent, SeatedAgent
    from braverse import actions as A
    from play_server import player_json

    db = default_db()
    game = Game([available_decks()["st9_sea_fairy"], available_decks()["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0), SeatedAgent(HeuristicAgent(db=db), 1)],
                db=db, seed=4)
    game.setup()
    me = game.state.players[0]
    assert player_json(db, me, game.state)["supportedThisTurn"] is False

    support = next(o for o in game.legal_actions() if isinstance(o, A.PlaceSupport))
    game.step(support)
    assert player_json(db, me, game.state)["supportedThisTurn"] is True

    # ...and it comes back for the next turn.
    game.end_turn()
    game.end_turn()
    assert game.state.turn_player == 0
    assert player_json(db, me, game.state)["supportedThisTurn"] is False


def test_damage_reaches_the_browser_typed_and_exactly_once():
    """The two kinds are animated differently, so the browser has to know which
    is which — and a diff cannot tell them apart, because a swing and the
    "Then, ..." rider that follows it take HP off the same Cookie in the same
    step. The engine records them structurally and the server drains that
    record, so each hit is delivered once, in order."""
    match = bot_match()
    batches = {}
    original = match.publish

    def spy(pending=None):
        original(pending)
        hits = [(e["source"], e["amount"], e["cookie"])
                for e in match.snapshot.get("events", []) if e["type"] == "damage"]
        if hits:
            batches.setdefault(match.snapshot["eventId"], hits)

    match.publish = spy
    match.start()
    assert wait_for(lambda: match.view().get("over"), timeout=30), "match did not finish"
    match.stop()

    delivered = [hit for batch in batches.values() for hit in batch]
    recorded = [(e["source"], e["amount"], e["cookie"])
                for e in match.game.state.events if e["kind"] == "damage"]
    assert delivered == recorded, "damage was dropped, doubled or reordered"
    assert recorded, "no damage in a whole match?"
    assert {source for source, _, _ in recorded} <= {"attack", "effect"}
    assert any(source == "attack" for source, _, _ in recorded)


def test_every_asset_the_page_asks_for_is_served():
    """A viewer file the page loads but `do_GET` does not list is a 404.

    The allowlist is written out by hand — it is what stops the server handing
    out arbitrary paths — so the failure mode of adding a file to the viewer is
    a script tag that silently fetches nothing. Read both and compare.
    """
    page = (VIEWER / "index.html").read_text()
    wanted = set(re.findall(r'(?:src|href)="(/[^"]+)"', page))
    wanted = {p for p in wanted if not p.startswith("/card_images/")}
    source = inspect.getsource(Handler.do_GET)
    # Any extension, not just js/css: the page also asks for its icon, and a
    # test that only knew about scripts and stylesheets would go on passing
    # while the tab showed a blank page icon.
    served = set(re.findall(r'"(/[a-z0-9_.-]+\.[a-z0-9]+)"', source))
    missing = sorted(p for p in wanted if p not in served)
    assert not missing, f"not in the do_GET allowlist: {missing}"


def test_a_size_preference_reaches_the_measurements_it_scales():
    """The size sliders write onto <html>, and `:root` declares what they feed.

    Two ways this silently does nothing, both of which look like a slider that
    moves and a board that does not. A custom property is substituted where it
    is *declared*, so `--card-w: calc(92px * var(--card-scale))` on `:root`
    reads the `--card-scale` that `:root` has — writing the preference one
    element lower, onto <body>, leaves the cards exactly where they were. And
    a slider naming a property nothing measures itself against is a control
    wired to nothing at all.
    """
    css = (VIEWER / "style.css").read_text(encoding="utf-8")
    js = (VIEWER / "sizing.js").read_text(encoding="utf-8")

    assert "documentElement.style.setProperty" in js, \
        "the sizes must be set on <html>: :root reads its own declarations"

    written = set(re.findall(r'prop: "([a-z-]+)"', js))
    assert written, "no sliders found in sizing.js"
    root = css.split("}", 1)[0]          # the opening `:root { ... }` block
    for prop in sorted(written):
        assert f"--{prop}:" in root, f"--{prop} is not declared on :root"
        # Declared *and* used: a scale nothing multiplies by is a dead control.
        assert f"var(--{prop})" in css, f"--{prop} scales nothing"


def test_a_dialog_is_not_forced_visible_after_it_closes():
    """An id rule setting `display` on a <dialog> must be scoped to `[open]`.

    A <dialog> is hidden by the user agent's own `dialog:not([open])
    { display: none }`, and *any* id selector outranks it. So a rule as
    innocuous as `#mydialog { display: flex }` — the natural way to lay out a
    dialog's contents — leaves the panel painted over the board forever after
    it closes, while `.open` cheerfully reports false and every close handler
    runs exactly as written. Nothing in JavaScript can be checked to catch it,
    which is why it is checked here.
    """
    page = (VIEWER / "index.html").read_text()
    dialogs = set(re.findall(r'<dialog[^>]*\bid="([^"]+)"', page))
    assert dialogs, "no <dialog> in the page — has the markup moved?"

    offenders = []
    for css in VIEWER.glob("*.css"):
        # Comments first: this file's own comments quote CSS containing braces,
        # and a naive rule split would take those for rules and lose the real
        # ones after them — which is exactly how the first version of this test
        # passed against the bug it was written for.
        text = re.sub(r'/\*.*?\*/', '', css.read_text(), flags=re.S)
        for selector, body in re.findall(r'([^{}]+)\{([^{}]*)\}', text):
            selector = selector.strip()
            if "display" not in body:
                continue
            for name in dialogs:
                # The rule has to target the dialog element itself; a selector
                # that reaches *inside* it is styling children and is fine.
                for part in (p.strip() for p in selector.split(",")):
                    if not re.match(rf'^#{re.escape(name)}(?![\w-])', part):
                        continue
                    rest = part[len(name) + 1:]
                    if rest.strip() and not rest.lstrip().startswith(("[", ":")):
                        continue        # a descendant, not the dialog
                    if "[open]" not in part:
                        offenders.append(f"{css.name}: {part} {{{body.strip()}}}")
    assert not offenders, (
        "these set `display` on a closed dialog and will leave it on screen:\n  "
        + "\n  ".join(offenders))


def test_the_title_screen_cannot_reclaim_another_tab():
    """`Title.sync` must consult `onPlayTab` before raising the menu.

    The routes off the title screen that do not start a game — the deck
    builder, the replay shelf — leave the server exactly as idle as it was.
    So a `sync` that decides purely from the *match* raises the title again on
    the very next poll, and `Title.show` calls `showTab("play")`, which drags
    the board back over the tab that was just opened. It presents as the deck
    builder closing the instant it is opened, one poll later, with nothing in
    the console and every click handler running correctly.

    Static, because the bug lives in the interaction between a poll and a tab
    and there is no JavaScript engine here to play it out.
    """
    src = (VIEWER / "title.js").read_text()
    sync = src[src.index("sync(snap)"):src.index("renderOver(snap)")]
    raising = [line for line in sync.splitlines() if "Title.show()" in line]
    assert raising, "Title.sync no longer raises the title — has this moved?"
    for line in raising:
        guard = sync[:sync.index(line)]
        assert "onPlayTab" in guard, (
            "Title.sync raises the title without asking which tab is on screen:\n"
            f"  {line.strip()}\n"
            "another tab would be closed by the next poll")


def test_returning_to_the_board_brings_the_menu_back_without_waiting():
    """`showTab("play")` re-syncs, so the menu is there when the tab arrives.

    Only half of the pair above: gating `sync` on the tab is what stops the
    title screen stealing the builder, and this is what stops the cure — a
    play tab that sits empty until whenever the next poll happens to land.
    """
    src = (VIEWER / "builder.js").read_text()
    show_tab = src[src.index("function showTab"):src.index("TABS.forEach((tab) => { el(tab.button)")]
    assert "Title.sync" in show_tab


def test_the_log_previews_the_card_the_id_names():
    """A hover on a log entry resolves the id, not just the name.

    The name index the viewer matches against holds one card per *name*, and
    271 of the 813 names are printed on more than one card — so a line about
    one printing previewed whichever copy the index happened to keep. The
    engine writes "Name (ST9-007)"; `logLine` has to read that id and ask
    `/api/card` for the card behind it.

    Two halves, both checked: the viewer asking, and the server answering.
    """
    src = (VIEWER / "app.js").read_text()
    line = src[src.index("function logLine"):src.index("function renderLog")]
    assert "/api/card?id=" in src, "nothing fetches a card by id"
    assert "-[0-9]+" in line or r"-\d+" in line, (
        "logLine does not read the id out of the log line")

    from play_server import PUBLIC_ROUTES, card_json

    assert '"/api/card"' in inspect.getsource(Handler.do_GET), (
        "the viewer asks for /api/card and do_GET does not serve it")
    # A joiner in a peer game reads the same log, so the route is theirs too.
    assert "/api/card" in PUBLIC_ROUTES

    db = default_db()
    # Two cards, one name: the case the id exists for. It has to come back as
    # itself and not as whichever one the name index would have found.
    by_name = {}
    for card in db.cards.values():
        by_name.setdefault(card.name, []).append(card.id)
    shared = next(ids for ids in by_name.values() if len(ids) > 1)
    for card_id in shared:
        assert card_json(db, card_id)["id"] == card_id


def test_your_own_half_of_the_table_is_never_addressed_by_seat_number():
    """A viewer zone is `.side.me`, never `#side-0`.

    `seatPerspective` seats you at the bottom by moving the `me`/`opponent`
    classes between the two sections; the ids stay bolted to their seat. So
    `#side-0` means *seat 0's* half, which for a player in seat 1 is the
    opponent's — and a drop target written that way asks them to drop a support
    card into the other player's support area to place it in their own. It
    reads as the board being mirrored and is invisible in seat 0, which is
    where everything local is played and where every test above sits.

    Static, because reproducing it needs two seats and a pointer drag.
    """
    for src in sorted(VIEWER.glob("*.js")):
        text = re.sub(r'/\*.*?\*/', '', src.read_text(), flags=re.S)
        text = re.sub(r'^\s*//.*$', '', text, flags=re.M)
        # `"#side-" + seat` is the one legitimate use: it is the function that
        # decides which section is which.
        offenders = [line.strip() for line in text.splitlines()
                     if re.search(r'#side-[01]', line)]
        assert not offenders, (
            f"{src.name} addresses a side of the table by seat number:\n  "
            + "\n  ".join(offenders)
            + "\nuse .side.me / .side.opponent — see seatPerspective")


def attack_response_pending(seed: int = 4, timeout: float = 20.0):
    """Run an online match until the human seat is asked about an attack.

    Seat 0 is a bot so the attack arrives without a script; seat 1 answers
    everything with its first option until the question it is handed is the
    response window. `online=True` is what makes the two views differ, which is
    the whole point of the test below.
    """
    config = MatchConfig(decks=["st9_sea_fairy", "st8_wind_archer"],
                         pilots=["heuristic", "human"], seed=seed, delay=0.0,
                         online=True)
    match = Match(config, default_db())
    match.start()
    deadline = time.time() + timeout
    while time.time() < deadline:
        view = match.view(1)
        pending = view.get("pending")
        if view.get("over"):
            break
        if pending and pending["seat"] == 1 and not pending.get("waiting"):
            if pending.get("responding"):
                return match, pending
            match.answer(0, seat=1)
        time.sleep(0.01)
    match.stop()
    return match, None


def test_an_attack_tells_both_seats_the_defender_may_still_answer():
    """The response window is the one wait where something of yours is in the
    air, and it used to be the least legible thing in the game: the defender
    was asked "Your move" in the middle of someone else's turn, and the
    attacker got a bare "waiting for your opponent" identical to every other
    pause. Both seats now carry the attack itself."""
    match, pending = attack_response_pending()
    assert pending, "no attack was ever answered by the human seat"
    try:
        window = pending["responding"]
        assert "trap" in pending["prompt"].lower(), pending["prompt"]

        # Named by Cookie uid, which is what `data-cookie` on the board is, so
        # either browser can point at the two Cookies involved.
        board = {c["uid"] for p in match.view(1)["players"] for c in p["battle"]}
        assert window["attacker"] in board and window["target"] in board
        assert window["attackerName"] and window["targetName"]

        # The attacker is told the same thing, without the defender's options.
        theirs = match.view(0)["pending"]
        assert theirs["waiting"] is True and theirs["options"] == []
        assert theirs["responding"] == window
    finally:
        match.stop()


def test_an_ordinary_question_carries_no_attack():
    """`responding` is the marker for one window, not a field that is always
    full — the seat bar chip and the panel line both switch on it."""
    config = MatchConfig(decks=["st9_sea_fairy", "st8_wind_archer"],
                         pilots=["human", "heuristic"], seed=5, delay=0.0)
    match = Match(config, default_db())
    match.start()
    try:
        pending = play_the_toss(match)
        assert pending and pending.get("responding") is None
    finally:
        match.stop()


def test_the_picker_explains_a_click_it_had_to_ignore():
    """At the limit, a click on an unpicked card cannot be taken.

    Silently ignoring it reads as a broken card — you pressed it and nothing
    moved — so the strip says what the limit is, says which click frees a
    slot, and marks the cards already taken so it is obvious which ones those
    are. `renderPicker` owns all three.
    """
    src = (VIEWER / "app.js").read_text()
    picker = src[src.index("function renderPicker"):src.index("function renderOptions")]

    assert "state.pickFull" in picker, "nothing records why the click did nothing"
    assert "swap it" in picker, "the notice does not say what to do about it"
    # Cleared whenever the selection changes, or the message outlives its click.
    assert picker.count('state.pickFull = ""') >= 3

    css = (VIEWER / "style.css").read_text()
    assert ".picker-row.full .picker-card.picked" in css, (
        "nothing marks the picked cards when the limit is reached")
    assert ".picker-foot .hint.over" in css, "the notice is not distinguished"


def test_a_batched_question_is_never_also_answerable_on_the_board():
    """A "pick N of these" answer is a list. One index sent to a question
    expecting a list is padded out by the engine with cards nobody chose, so
    pointing at a single card must not answer it — and the board must not
    invite the click either.
    """
    src = (VIEWER / "app.js").read_text()
    direct = src[src.index("function directOption"):src.index("function closeCardMenu")]
    assert "pending.upTo" in direct and "pending.count" in direct

    sets = src[src.index("function actionSets"):src.index("function markActionable")]
    assert "pending.upTo" in sets and "pending.pick" in sets


def test_a_docked_preview_outlives_the_pointer_leaving_the_card():
    """Hovering away from a card must not clear the panel it filled.

    The dock is a fixed slot in the right-hand panel with nothing behind it, so
    a card left standing there hides nothing — and clearing it on `mouseleave`
    meant the card vanished the moment you moved towards the text you were
    trying to read, and made the move list below it jump every time the pointer
    crossed the board. Only the next card replaces it.

    The floating preview is the opposite case and must still go: it is drawn
    over the board and follows the cursor. So every `mouseleave` goes through
    `leavePreview`, which hides only when the preview is undocked, and the
    outright `hidePreview` is left to the places the preview really is done —
    a drag starting, or the whole thing being rehomed for another view.
    """
    src = (VIEWER / "app.js").read_text()
    assert "function leavePreview" in src

    leave = src[src.index("function leavePreview"):]
    leave = leave[:leave.index("\n}")]
    assert "previewDocked()" in leave, (
        "leavePreview must ask whether the preview is docked before hiding it")

    # Every hover handler releases the card through leavePreview, never by
    # hiding it outright — one of these left as `hidePreview` is the bug back.
    for line in src.splitlines():
        if "mouseleave" in line or "onmouseleave" in line:
            assert "hidePreview" not in line, line.strip()
    for handler in re.findall(r"mouseleave\", \(\) => \{(.*?)\}\)", src, re.S):
        assert "hidePreview" not in handler, handler.strip()


def test_the_board_tilt_is_seen_through_a_perspective_on_the_mats_parent():
    """`perspective` applies to an element's *direct* children only.

    The tilt is a `rotateX` on `.mat`, and `.mat` is a child of `.side`. Put
    the perspective one level up on `#table` — the obvious place, since that is
    what looks like "the board" — and the rotation still applies, still moves
    when the slider moves, and has no depth in it whatsoever: the mat is merely
    squashed vertically. It looks like a slider that half works, which is the
    hardest kind to notice is broken.
    """
    css = (VIEWER / "style.css").read_text(encoding="utf-8")

    side = re.search(r"^\.side \{(.*?)\n\}", css, re.S | re.M)
    assert side, "no `.side` rule in style.css"
    assert "perspective:" in side.group(1), \
        "the perspective must sit on `.mat`'s own parent, `.side`"

    mat = re.search(r"^\.mat \{(.*?)\n\}", css, re.S | re.M)
    assert mat and "rotateX(calc(var(--board-tilt)" in mat.group(1), \
        "`.mat` must be what the tilt rotates"


def test_every_setting_the_viewer_keeps_goes_through_prefs():
    """A preference written straight to `localStorage` belongs to the browser.

    Which is the bug `prefs.js` exists to fix: settings follow the player who
    signed in, so a machine two people share stops handing the second one the
    first one's board. Two ways to lose that, and both leave a control that
    works perfectly right up until somebody else signs in — a module still
    calling `localStorage` itself, and a key going through `Prefs` that is
    missing from the `KEYS` list, which is what gets sent to the profile.
    """
    prefs = (VIEWER / "prefs.js").read_text(encoding="utf-8")
    listed = set(re.findall(r'^\s*"([^"]+)",', prefs, re.M))
    assert listed, "no keys found in prefs.js"

    # The seat token is not a setting: it is which chair this tab is sitting
    # in, which belongs to the tab and to nobody else. The tutorial's mark is
    # progress rather than a preference and is left alone deliberately.
    # Written as the argument reads up to its first bracket, which is as far
    # as the pattern below looks.
    allowed = {"app.js": {"Seat.key(room"}, "tutorial.js": {"KEY"}}
    used, stray = set(), []
    for js in sorted(VIEWER.glob("*.js")):
        source = js.read_text(encoding="utf-8")
        if js.name != "prefs.js":
            for call in re.findall(r'localStorage\.(?:get|set|remove)Item\(\s*([^,)]+)',
                                   source):
                if call.strip() not in allowed.get(js.name, set()):
                    stray.append(f"{js.name}: {call.strip()}")
        used |= set(re.findall(r'Prefs\.(?:get|set)\(\s*"([^"]+)"', source))
        # The keys named through a constant, which is how the bigger ones are
        # written: `Prefs.get(SIZE_KEY)` says nothing on its own.
        for const in re.findall(r'Prefs\.(?:get|set)\(\s*([A-Z_]+)\b', source):
            match = re.search(rf'{const}\s*=\s*"([^"]+)"', source)
            if match:
                used.add(match.group(1))

    assert not stray, f"settings kept outside Prefs: {stray}"
    assert not used - listed, f"used through Prefs but not in KEYS: {sorted(used - listed)}"
