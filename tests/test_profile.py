"""The encrypted profile: the seal, the scoring, and the thirty-game window.

The scoring rules are pinned here rather than trusted to the UI, because they
are the part people will notice being wrong: a bot game that pays XP, or a
starred game that quietly disappeared, is a bug you only find out about weeks
later.
"""

import json

import pytest

from braverse import profile as PR
from braverse import secretbox as SB


# ---------------------------------------------------------------------------
# the seal
# ---------------------------------------------------------------------------
def test_seal_round_trips():
    key = SB.new_key()
    blob = SB.seal(key, b"the record", aad=b"header")
    assert b"the record" not in blob
    assert SB.unseal(key, blob, aad=b"header") == b"the record"


def test_seal_is_fresh_each_time():
    """Two seals of the same bytes must not be the same file.

    The nonce is per-save; if it were not, the XOR of two ciphertexts would be
    the XOR of two profiles.
    """
    key = SB.new_key()
    assert SB.seal(key, b"same") != SB.seal(key, b"same")


def test_empty_plaintext_still_seals():
    key = SB.new_key()
    assert SB.unseal(key, SB.seal(key, b"")) == b""


@pytest.mark.parametrize("break_it", [
    lambda b: b[:-1] + bytes([b[-1] ^ 1]),          # the tag
    lambda b: b[:30] + bytes([b[30] ^ 1]) + b[31:],  # the ciphertext
    lambda b: b[:6] + bytes([b[6] ^ 1]) + b[7:],     # the nonce
    lambda b: b[:-1],                                # truncated
])
def test_an_edited_blob_will_not_open(break_it):
    key = SB.new_key()
    blob = SB.seal(key, b"x" * 64, aad=b"header")
    with pytest.raises(SB.BadSeal):
        SB.unseal(key, break_it(blob), aad=b"header")


def test_the_associated_data_is_part_of_the_seal():
    key = SB.new_key()
    blob = SB.seal(key, b"x" * 64, aad=b"one")
    with pytest.raises(SB.BadSeal):
        SB.unseal(key, blob, aad=b"two")


def test_a_different_passphrase_is_a_different_key():
    salt = SB.new_salt()
    assert SB.derive("hunter2", salt) != SB.derive("hunter3", salt)
    assert SB.derive("hunter2", salt) != SB.derive("hunter2", SB.new_salt())
    assert SB.derive("hunter2", salt) == SB.derive("hunter2", salt)


# ---------------------------------------------------------------------------
# levels and XP
# ---------------------------------------------------------------------------
def test_a_win_is_four_and_a_loss_is_one():
    assert PR.xp_for_result(won=True, versus_person=True) == 4
    assert PR.xp_for_result(won=False, versus_person=True) == 1


def test_a_bot_pays_nothing():
    assert PR.xp_for_result(won=True, versus_person=False) == 0
    assert PR.xp_for_result(won=False, versus_person=False) == 0


def test_the_level_curve_climbs():
    assert PR.progress(0)["level"] == 1
    assert PR.progress(3)["level"] == 1
    assert PR.progress(4)["level"] == 2      # one won game
    assert PR.progress(11)["level"] == 2
    assert PR.progress(12)["level"] == 3
    curve = PR.progress(5)
    assert (curve["into"], curve["need"]) == (1, 8)


def test_the_curve_never_spins():
    assert PR.progress(10 ** 9)["level"] <= 1000
    assert PR.progress(-5)["level"] == 1


# ---------------------------------------------------------------------------
# the record
# ---------------------------------------------------------------------------
def played(profile, result="win", opponent="human", **kw):
    return profile.record(deck=kw.pop("deck", "mine"),
                          opponent_deck="theirs", opponent=opponent,
                          result=result, **kw)


def test_a_game_moves_every_counter():
    profile = PR.Profile()
    played(profile, "win")
    played(profile, "loss")
    assert (profile.games, profile.wins, profile.losses) == (2, 1, 1)
    assert profile.xp == 5                      # 4 for the win, 1 for the loss
    assert profile.decks["mine"].games == 2
    assert profile.decks["mine"].wins == 1


def test_a_bot_game_is_recorded_but_pays_nothing():
    profile = PR.Profile()
    played(profile, "win", opponent="heuristic")
    assert profile.games == 1 and profile.wins == 1
    assert profile.xp == 0
    assert profile.level == 1


def test_two_humans_at_one_keyboard_still_pays():
    profile = PR.Profile()
    profile.record(deck="mine", opponent_deck="theirs", opponent="human",
                   result="win", versus_person=True)
    assert profile.xp == 4


def test_decks_are_counted_apart():
    profile = PR.Profile()
    played(profile, "win", deck="aggro")
    played(profile, "loss", deck="control")
    assert profile.decks["aggro"].wins == 1
    assert profile.decks["control"].losses == 1
    assert set(profile.decks) == {"aggro", "control"}


def test_game_ids_are_distinct():
    profile = PR.Profile()
    ids = {played(profile, "win").id for _ in range(20)}
    assert len(ids) == 20


# ---------------------------------------------------------------------------
# the thirty-game window
# ---------------------------------------------------------------------------
def test_only_the_last_thirty_are_kept():
    profile = PR.Profile()
    for i in range(35):
        played(profile, "win", when=1000 + i, replay=f"{i}.json")
    dropped = profile.prune()
    assert len(profile.history) == PR.HISTORY_LIMIT
    assert [g.replay for g in dropped] == [f"{i}.json" for i in range(5)]
    # The record of *playing* them is not what was trimmed.
    assert profile.games == 35


def test_a_starred_game_is_never_dropped_and_costs_no_room():
    profile = PR.Profile()
    old = played(profile, "win", when=1, replay="old.json")
    profile.keep(old.id, True)
    for i in range(40):
        played(profile, "win", when=1000 + i, replay=f"{i}.json")
    profile.prune()
    assert profile.find(old.id) is not None
    # Starred games sit above the window rather than inside it.
    assert len(profile.history) == PR.HISTORY_LIMIT + 1


def test_unstarring_lets_an_old_game_fall_out():
    profile = PR.Profile()
    old = played(profile, "win", when=1, replay="old.json")
    profile.keep(old.id, True)
    for i in range(40):
        played(profile, "win", when=1000 + i)
    profile.prune()
    profile.keep(old.id, False)
    dropped = profile.prune()
    assert [g.replay for g in dropped] == ["old.json"]
    assert profile.find(old.id) is None


def test_deleting_a_game_leaves_the_totals_alone():
    profile = PR.Profile()
    entry = played(profile, "win", replay="one.json")
    gone = profile.forget(entry.id)
    assert gone.replay == "one.json"
    assert profile.find(entry.id) is None
    assert (profile.games, profile.wins, profile.xp) == (1, 1, 4)


def test_forgetting_something_that_is_not_there():
    assert PR.Profile().forget("nope") is None
    assert PR.Profile().keep("nope", True) is None


# ---------------------------------------------------------------------------
# the store
# ---------------------------------------------------------------------------
def test_a_profile_round_trips_through_a_passphrase(tmp_path):
    store = PR.ProfileStore(tmp_path)
    session = store.create("Ada L", passphrase="hunter2", avatar="card:ST9-001")
    played(session.profile, "win", replay="a.json")
    session.save()

    back = store.open("ada-l", "hunter2").profile
    assert back.name == "Ada L"
    assert back.avatar == "card:ST9-001"
    assert back.xp == 4
    assert [g.replay for g in back.history] == ["a.json"]


def test_the_file_does_not_have_the_games_in_it(tmp_path):
    store = PR.ProfileStore(tmp_path)
    session = store.create("Ada L", passphrase="hunter2")
    played(session.profile, "win", deck="a-very-distinctive-deck-name")
    session.save()
    raw = store.path("ada-l").read_bytes()
    assert b"a-very-distinctive-deck-name" not in raw
    # The name and the picture are the deliberate exception: the chooser draws
    # the list before it knows any passphrase.
    assert b"Ada L" in raw


def test_the_wrong_passphrase_says_so(tmp_path):
    store = PR.ProfileStore(tmp_path)
    store.create("Ada L", passphrase="hunter2")
    with pytest.raises(PR.BadPassphrase):
        store.open("ada-l", "hunter3")
    with pytest.raises(PR.Locked):
        store.open("ada-l")


def test_an_unlocked_profile_opens_with_no_passphrase(tmp_path):
    store = PR.ProfileStore(tmp_path)
    store.create("Bob")
    assert store.open("bob").profile.name == "Bob"
    # It is still encrypted, under the keyfile beside it.
    assert not store.path("bob").read_bytes().endswith(b"}")
    assert (tmp_path / PR.KEYFILE_NAME).is_file()


def test_the_keyfile_is_not_readable_by_anyone_else(tmp_path):
    import os
    import sys
    store = PR.ProfileStore(tmp_path)
    store.create("Bob")
    mode = (tmp_path / PR.KEYFILE_NAME).stat().st_mode & 0o777
    if sys.platform.startswith("win"):
        return              # POSIX modes are not what protects a file there
    assert mode == 0o600


def test_two_profiles_are_not_sealed_under_one_key(tmp_path):
    store = PR.ProfileStore(tmp_path)
    one = store.create("One")
    two = store.create("Two")
    assert one.key != two.key


def test_editing_the_header_breaks_the_seal(tmp_path):
    """The name on the outside cannot be swapped for another."""
    store = PR.ProfileStore(tmp_path)
    store.create("Bob")
    path = store.path("bob")
    head, _, body = path.read_bytes().partition(b"\n")
    header = json.loads(head)
    header["name"] = "Not Bob"
    path.write_bytes(json.dumps(header, sort_keys=True).encode() + b"\n" + body)
    with pytest.raises(PR.ProfileError):
        store.open("bob")


def test_the_list_shows_what_the_chooser_needs(tmp_path):
    store = PR.ProfileStore(tmp_path)
    store.create("Ada L", passphrase="hunter2", avatar="card:ST9-001")
    store.create("Bob")
    rows = {row["name"]: row for row in store.list()}
    assert rows["Ada L"]["locked"] is True
    assert rows["Ada L"]["avatar"] == "card:ST9-001"
    assert rows["Bob"]["locked"] is False


def test_a_stray_file_in_the_folder_is_skipped(tmp_path):
    store = PR.ProfileStore(tmp_path)
    store.create("Bob")
    (tmp_path / f"junk{PR.SUFFIX}").write_bytes(b"not ours\n\x00\x01")
    assert [row["name"] for row in store.list()] == ["Bob"]


def test_one_name_one_profile(tmp_path):
    store = PR.ProfileStore(tmp_path)
    store.create("Bob")
    with pytest.raises(PR.ProfileError):
        store.create("Bob")


def test_deleting_needs_the_passphrase(tmp_path):
    store = PR.ProfileStore(tmp_path)
    session = store.create("Ada L", passphrase="hunter2")
    played(session.profile, "win", replay="a.json")
    session.save()
    with pytest.raises(PR.BadPassphrase):
        store.delete("ada-l", "hunter3")
    assert store.path("ada-l").is_file()
    assert PR.replays_of(store.delete("ada-l", "hunter2")) == ["a.json"]
    assert not store.path("ada-l").is_file()


def test_an_avatar_is_a_card_or_a_small_picture():
    assert PR.clean_avatar("card:ST9-001") == "card:ST9-001"
    assert PR.clean_avatar("data:image/png;base64,AAAA") == "data:image/png;base64,AAAA"
    assert PR.clean_avatar("javascript:alert(1)") == ""
    assert PR.clean_avatar("/etc/passwd") == ""
    assert PR.clean_avatar("data:text/html;base64,AAAA") == ""
    assert PR.clean_avatar("data:image/png;base64," + "A" * PR.MAX_AVATAR) == ""


def test_a_name_becomes_a_file_stem_and_never_a_path():
    assert PR.slugify("Ada L") == "ada-l"
    assert PR.slugify("../../etc/passwd") == "etc-passwd"
    assert PR.slugify("🙂") == "player"
    assert PR.clean_name("  spaced   out  ") == "spaced out"
    assert len(PR.clean_name("x" * 500)) == PR.MAX_NAME


def test_the_store_survives_a_profile_from_a_newer_build(tmp_path):
    store = PR.ProfileStore(tmp_path)
    store.create("Bob")
    path = store.path("bob")
    head, _, body = path.read_bytes().partition(b"\n")
    header = json.loads(head)
    header["v"] = PR.FORMAT_VERSION + 1
    path.write_bytes(json.dumps(header).encode() + b"\n" + body)
    with pytest.raises(PR.ProfileError):
        store.open("bob")
    assert store.list() == []


def test_a_summary_is_json(tmp_path):
    profile = PR.Profile(name="Ada")
    played(profile, "win", replay="a.json")
    blob = profile.summary()
    json.dumps(blob)                    # the route sends this straight out
    assert blob["level"] == 2
    assert blob["history"][0]["result"] == "win"
    assert blob["decks"][0]["name"] == "mine"


def test_a_profile_read_back_from_junk_does_not_explode():
    with pytest.raises(PR.ProfileError):
        PR.Profile.from_json([1, 2, 3])
    # Fields that are missing or the wrong shape are defaulted, not fatal: a
    # profile written by an older build must still open.
    profile = PR.Profile.from_json({"name": "Ada", "history": [{"id": "x"}]})
    assert profile.history[0].result == "draw"
    assert profile.xp == 0


def test_settings_are_stored_as_the_browser_left_them(tmp_path):
    """Opaque to this module, and sealed with the rest of the record."""
    store = PR.ProfileStore(tmp_path)
    session = store.create("Ada")
    session.profile.remember({"sound": "0", "braverse.sizes": '{"card": 120}'})
    session.save()

    assert PR.Profile.from_json(session.profile.to_json()).settings["sound"] == "0"
    reopened = store.open("ada")
    assert reopened.profile.settings["braverse.sizes"] == '{"card": 120}'
    # Sealed, not sitting in the cleartext header beside the name.
    assert b"braverse.sizes" not in store.path("ada").read_bytes().split(b"\n")[0]


def test_a_setting_that_will_not_fit_is_dropped_rather_than_repaired():
    """The map goes into a file this program reads back and hands to a browser.

    So the shape of a value is not negotiable: numbers and booleans are written
    as themselves, anything nested is not a setting, and nothing oversized gets
    in. Dropped one key at a time — one silly value must not lose the rest.
    """
    kept = PR.clean_settings({
        "sound": "0",
        "scale": 120,
        "flip": True,
        "kit": {"sleeve": "cocoa"},                 # nested: not a setting
        "huge": "x" * (PR.MAX_SETTING_VALUE + 1),
        "line\nbreak": "x",
        "": "nameless",
    })
    assert kept == {"sound": "0", "scale": "120", "flip": "true"}
    assert PR.clean_settings("not a map") == {}
    assert len(PR.clean_settings({str(n): "1" for n in range(500)})) \
        == PR.MAX_SETTINGS


def test_settings_are_merged_and_a_null_removes_one():
    profile = PR.Profile(name="Ada")
    profile.remember({"sound": "0", "flipOpponent": "1"})
    profile.remember({"sound": "1"})
    assert profile.settings == {"sound": "1", "flipOpponent": "1"}
    profile.remember({"flipOpponent": None})
    assert profile.settings == {"sound": "1"}
    with pytest.raises(PR.ProfileError):
        profile.remember(["sound", "0"])
