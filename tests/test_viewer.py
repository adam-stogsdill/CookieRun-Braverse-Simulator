"""The visual player's match thread: pacing, hidden information, answering."""

from __future__ import annotations

import time

import pytest

from braverse import default_db
from play_server import (MAX_SCENE_PAUSE, Match, MatchConfig, available_decks,
                         available_pilots, scene_seconds)


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
    """
    from braverse import Game, HeuristicAgent, SeatedAgent
    from play_server import hand_pick

    db = default_db()
    game = Game([available_decks()["st9_sea_fairy"], available_decks()["st8_wind_archer"]],
                [SeatedAgent(HeuristicAgent(db=db), 0), SeatedAgent(HeuristicAgent(db=db), 1)],
                db=db, seed=4)
    game.setup()
    me = game.state.players[0]

    assert hand_pick("Opening Cookie", me.hand, me) == {"verb": "Play Cookie"}
    assert hand_pick("Field a replacement Cookie", me.hand, me) == {"verb": "Play Cookie"}
    assert hand_pick("Discard 2 cards", me.hand, me) == {"verb": "Discard"}
    assert hand_pick("Reveal a card", me.hand, me) == {"verb": "Choose"}

    # Not your hand, not the picker: a question about the board stays a list.
    assert hand_pick("Damage which Cookie?", me.battle, me) is None
    assert hand_pick("Opening Cookie", game.state.players[1].hand, me) is None
    assert hand_pick("Anything", [], me) is None


def test_the_toss_is_asked_in_the_middle_of_the_table():
    """Tracking to the far-right panel to throw rock is a silly way to start."""
    from braverse.rps import CHOICES, THROWS
    from play_server import centre_style

    assert centre_style(list(THROWS)) == "throw"
    assert centre_style(list(CHOICES)) == "choice"
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
