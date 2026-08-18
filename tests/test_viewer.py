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


def test_reveal_shows_both_hands():
    config = MatchConfig(decks=["st9_sea_fairy", "st8_wind_archer"],
                         pilots=["human", "heuristic"], seed=5, delay=0.0, reveal=True)
    match = Match(config, default_db())
    match.start()
    assert play_the_toss(match), "no question reached the browser"
    view = match.view()
    assert view["players"][1]["hand"], "reveal did not show the opponent's hand"
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


def test_reveal_events_are_a_zone_diff():
    """The flip animation is driven by a diff, not an engine callback: an HP
    card that reaches the trash was revealed, one that goes anywhere else was
    not."""
    before = snapshot_with(pile=[1, 2, 3], trash=[7])

    revealed = Match._reveal_events(before, snapshot_with(pile=[1, 2], trash=[7, 3]))
    assert len(revealed) == 1
    assert revealed[0]["cookie"] == 99 and revealed[0]["card"]["uid"] == 3
    assert revealed[0]["owner"] == 0 and revealed[0]["flip"] is False

    # Bounced back to hand rather than trashed: never turned face up.
    assert Match._reveal_events(before, snapshot_with(pile=[1, 2], trash=[7])) == []
    # A card that was already in the trash is not re-reported.
    assert Match._reveal_events(before, snapshot_with(pile=[1, 2, 3], trash=[7])) == []
    # Nothing to diff against on the very first snapshot.
    assert Match._reveal_events(None, before) == []


def test_flip_cards_are_flagged_for_the_animation():
    before = snapshot_with(pile=[1], trash=[])
    after = {"players": [
        {"battle": [{"uid": 99, "card": card_stub(50), "hpPileCards": []}],
         "trash": [card_stub(1, "FLIP")], "break": []},
        {"battle": [], "trash": [], "break": []},
    ]}
    revealed = Match._reveal_events(before, after)[0]
    assert revealed["flip"] is True and revealed["type"] == "reveal"


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
