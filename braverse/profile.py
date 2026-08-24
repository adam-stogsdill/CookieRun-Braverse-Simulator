"""A player: what they have played, how it went, and how far along they are.

A profile is one encrypted file on the machine that played the games. Nothing
here goes anywhere — there is no account, no server, and no way to sign in from
somewhere else. The file is sealed with `secretbox`, under a key that is either
derived from a passphrase the player chose or read from a keyfile beside the
profiles (see `ProfileStore`).

What is *not* sealed is the first line of the file: a small JSON header with
the profile's name and picture in it. That is deliberate, and it is the one
thing to know before trusting this with anything: the chooser has to draw the
list of profiles before it knows any passphrase, so the name and the picture
are readable by anyone holding the file. Everything that makes it a record —
the games, the decks, the win rates, the level — is inside the seal. The header
is authenticated along with the body, so it can be *read* but not changed.

Scoring, in one place because these are the numbers people argue about:

* A game played is `XP_PER_GAME`, a game won is `XP_PER_WIN` on top of it — so
  a win is worth four and a loss one.
* **Only against another person.** A bot on the other side of the table pays
  nothing, and two bots playing themselves pay nothing to anybody. Levels are a
  record of playing the game, and a bot will sit there and lose all night.
* A level costs `4 * level` — one won game to reach level 2, two more to reach
  3. `progress` is the whole curve; nothing else should be doing this
  arithmetic.

The history is the last `HISTORY_LIMIT` games, plus every game the player
marked *kept*, which are never dropped and do not count against the limit. Each
entry names the replay file it was recorded to, so a game in the list can be
watched back; dropping an entry is what deletes that file (`prune` returns the
entries it dropped, for the caller to unlink).
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from . import secretbox as SB

FORMAT = "braverse-profile"
FORMAT_VERSION = 1
SUFFIX = ".bvprofile"
KEYFILE_NAME = ".profile-key"

XP_PER_GAME = 1
XP_PER_WIN = 3
HISTORY_LIMIT = 30

MAX_NAME = 24
MAX_AVATAR = 192 * 1024        # a 96px PNG data URL is a few KB; this is slack
AVATAR_DATA = re.compile(r"data:image/(png|webp|jpeg);base64,[A-Za-z0-9+/=]+\Z")
AVATAR_CARD = re.compile(r"card:[A-Za-z0-9][A-Za-z0-9._-]{0,39}\Z")

# What the other seat was. "person" is the only one that pays.
BOT_KINDS = ("heuristic", "random", "tutorial", "rl", "bot")


class ProfileError(Exception):
    """A profile could not be read or written."""


class BadPassphrase(ProfileError):
    """The passphrase does not open this profile."""


class Locked(ProfileError):
    """This profile needs a passphrase and none was given."""


# ---------------------------------------------------------------------------
# levels
# ---------------------------------------------------------------------------
def xp_for_level(level: int) -> int:
    """What it costs to go from `level` to the next one."""
    return 4 * max(1, level)


def progress(xp: int) -> dict:
    """`{level, xp, into, need}` — where `xp` puts a player on the curve."""
    xp = max(0, int(xp))
    level, spent = 1, 0
    while xp - spent >= xp_for_level(level):
        spent += xp_for_level(level)
        level += 1
        if level > 999:          # a ceiling, so a corrupt number cannot spin
            break
    return {"level": level, "xp": xp, "into": xp - spent,
            "need": xp_for_level(level)}


def xp_for_result(*, won: bool, versus_person: bool) -> int:
    """What one finished game is worth. A bot is worth nothing."""
    if not versus_person:
        return 0
    return XP_PER_GAME + (XP_PER_WIN if won else 0)


# ---------------------------------------------------------------------------
# the record
# ---------------------------------------------------------------------------
@dataclass
class GameRecord:
    """One finished game, from the profile owner's side of the table."""

    id: str                      # unique within the profile
    when: float                  # unix seconds
    deck: str = ""               # what the owner played
    opponent_deck: str = ""
    opponent: str = ""           # pilot kind: "human", "heuristic", "rl:…"
    opponent_name: str = ""
    result: str = "draw"         # "win" | "loss" | "draw"
    turns: int = 0
    xp: int = 0
    kept: bool = False           # marked to be kept past the 30-game window
    replay: str = ""             # the file in replays/, "" once deleted

    @property
    def versus_person(self) -> bool:
        return self.opponent == "human"

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, blob: Any) -> "GameRecord":
        if not isinstance(blob, dict):
            raise ProfileError("a game record must be an object")
        known = {f for f in cls.__dataclass_fields__}
        row = {k: v for k, v in blob.items() if k in known}
        row["id"] = str(row.get("id") or "")
        row["when"] = float(row.get("when") or 0.0)
        row["turns"] = int(row.get("turns") or 0)
        row["xp"] = int(row.get("xp") or 0)
        row["kept"] = bool(row.get("kept"))
        for text in ("deck", "opponent_deck", "opponent", "opponent_name",
                     "result", "replay"):
            row[text] = str(row.get(text) or "")
        if row["result"] not in ("win", "loss", "draw"):
            row["result"] = "draw"
        return cls(**row)


@dataclass
class DeckRecord:
    """How one deck has done in this profile's hands."""

    games: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    xp: int = 0
    last: float = 0.0

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class Profile:
    """Everything sealed inside the file."""

    name: str = "Player"
    avatar: str = ""
    created: float = field(default_factory=time.time)
    xp: int = 0
    games: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    decks: dict[str, DeckRecord] = field(default_factory=dict)
    history: list[GameRecord] = field(default_factory=list)

    # -- reading ---------------------------------------------------------
    @property
    def level(self) -> int:
        return progress(self.xp)["level"]

    def find(self, game_id: str) -> Optional[GameRecord]:
        return next((g for g in self.history if g.id == str(game_id)), None)

    def summary(self) -> dict:
        """The whole profile as the browser wants it."""
        ranked = sorted(self.decks.items(),
                        key=lambda kv: (-kv[1].games, kv[0]))
        return {
            "name": self.name,
            "avatar": self.avatar,
            "created": self.created,
            "games": self.games,
            "wins": self.wins,
            "losses": self.losses,
            "draws": self.draws,
            **progress(self.xp),
            "decks": [{"name": name, **rec.to_json()} for name, rec in ranked],
            "history": [g.to_json() for g in
                        sorted(self.history, key=lambda g: -g.when)],
            "limit": HISTORY_LIMIT,
        }

    # -- writing ---------------------------------------------------------
    def record(self, *, deck: str, opponent_deck: str, opponent: str,
               opponent_name: str = "", result: str, turns: int = 0,
               replay: str = "", when: Optional[float] = None,
               versus_person: Optional[bool] = None) -> GameRecord:
        """Add a finished game, award its XP, and return the entry.

        `versus_person` overrides the guess made from the pilot kind — a room
        match is two "human" seats, but so is two people at one keyboard, and
        the caller knows which it had.
        """
        when = time.time() if when is None else float(when)
        result = result if result in ("win", "loss", "draw") else "draw"
        person = (opponent == "human") if versus_person is None \
            else bool(versus_person)
        gained = xp_for_result(won=result == "win", versus_person=person)

        entry = GameRecord(
            id=f"{int(when * 1000):013d}-{os.urandom(4).hex()}",
            when=when, deck=deck, opponent_deck=opponent_deck,
            opponent=opponent, opponent_name=opponent_name, result=result,
            turns=int(turns), xp=gained, replay=replay,
        )
        self.history.append(entry)
        self.xp += gained
        self.games += 1
        self.wins += result == "win"
        self.losses += result == "loss"
        self.draws += result == "draw"

        rec = self.decks.setdefault(deck or "unnamed deck", DeckRecord())
        rec.games += 1
        rec.wins += result == "win"
        rec.losses += result == "loss"
        rec.draws += result == "draw"
        rec.xp += gained
        rec.last = when
        return entry

    def keep(self, game_id: str, kept: bool) -> Optional[GameRecord]:
        """Mark a game to be kept past the window, or let it fall out again."""
        entry = self.find(game_id)
        if entry is None:
            return None
        entry.kept = bool(kept)
        return entry

    def forget(self, game_id: str) -> Optional[GameRecord]:
        """Drop one game from the history.

        The totals are left alone on purpose: deleting the log of a game does
        not mean it was never played, and a level that went down because
        somebody tidied up their replay folder would be a bug.
        """
        entry = self.find(game_id)
        if entry is None:
            return None
        self.history = [g for g in self.history if g.id != entry.id]
        return entry

    def prune(self, limit: int = HISTORY_LIMIT) -> list[GameRecord]:
        """Trim to the newest `limit` games, keeping every marked one.

        Returns what was dropped, so the caller can delete the replay files
        those entries owned — the entry and its log go together.
        """
        keeps = [g for g in self.history if g.kept]
        rest = sorted((g for g in self.history if not g.kept),
                      key=lambda g: g.when, reverse=True)
        dropped = rest[max(0, int(limit)):][::-1]      # oldest first
        self.history = keeps + rest[:max(0, int(limit))]
        self.history.sort(key=lambda g: g.when)
        return dropped

    # -- disk shape ------------------------------------------------------
    def to_json(self) -> dict:
        return {
            "name": self.name, "avatar": self.avatar, "created": self.created,
            "xp": self.xp, "games": self.games, "wins": self.wins,
            "losses": self.losses, "draws": self.draws,
            "decks": {name: rec.to_json() for name, rec in self.decks.items()},
            "history": [g.to_json() for g in self.history],
        }

    @classmethod
    def from_json(cls, blob: Any) -> "Profile":
        if not isinstance(blob, dict):
            raise ProfileError("a profile must be an object")
        decks: dict[str, DeckRecord] = {}
        for name, rec in (blob.get("decks") or {}).items():
            if isinstance(rec, dict):
                decks[str(name)] = DeckRecord(
                    games=int(rec.get("games") or 0),
                    wins=int(rec.get("wins") or 0),
                    losses=int(rec.get("losses") or 0),
                    draws=int(rec.get("draws") or 0),
                    xp=int(rec.get("xp") or 0),
                    last=float(rec.get("last") or 0.0))
        history = [GameRecord.from_json(g) for g in (blob.get("history") or [])
                   if isinstance(g, dict)]
        return cls(
            name=clean_name(blob.get("name")) or "Player",
            avatar=clean_avatar(blob.get("avatar")),
            created=float(blob.get("created") or time.time()),
            xp=max(0, int(blob.get("xp") or 0)),
            games=max(0, int(blob.get("games") or 0)),
            wins=max(0, int(blob.get("wins") or 0)),
            losses=max(0, int(blob.get("losses") or 0)),
            draws=max(0, int(blob.get("draws") or 0)),
            decks=decks, history=history)


# ---------------------------------------------------------------------------
# names and pictures out of a request
# ---------------------------------------------------------------------------
def clean_name(raw: Any) -> str:
    name = " ".join(str(raw or "").split())[:MAX_NAME]
    return "".join(ch for ch in name if ch.isprintable())


def clean_avatar(raw: Any) -> str:
    """A card id or a small inline image, or "" for anything else.

    Refused rather than repaired: an avatar is written into a file this program
    reads back and hands to a browser, and "nearly a data URL" is not something
    to guess at.
    """
    value = str(raw or "").strip()
    if not value:
        return ""
    if len(value) > MAX_AVATAR:
        return ""
    if AVATAR_CARD.fullmatch(value) or AVATAR_DATA.fullmatch(value):
        return value
    return ""


def slugify(name: str) -> str:
    """A file stem from a display name. Never a path, never empty."""
    flat = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", flat.lower()).strip("-")[:32]
    return slug or "player"


# ---------------------------------------------------------------------------
# the store
# ---------------------------------------------------------------------------
@dataclass
class Session:
    """An opened profile: the record, and the key that reseals it."""

    store: "ProfileStore"
    slug: str
    key: bytes
    profile: Profile
    locked: bool = False        # was a passphrase needed to get in here

    def save(self) -> None:
        self.store.write(self.slug, self.profile, self.key, locked=self.locked)


class ProfileStore:
    """A directory of profile files, and the keyfile that opens the open ones.

    A profile with no passphrase is still encrypted — under a random key kept
    in `.profile-key` next to the profiles, readable only by its owner. That
    stops a profile being read by anything that merely *has* the file: a backup
    on a shared disk, a synced folder, a support bundle. It does not stop
    someone sitting at this account, and it is not meant to; that is what the
    passphrase is for, and `create` takes one.
    """

    def __init__(self, directory: Path):
        self.dir = Path(directory)

    # -- files -----------------------------------------------------------
    def path(self, slug: str) -> Path:
        safe = slugify(str(slug or ""))
        return self.dir / f"{safe}{SUFFIX}"

    def _keyfile(self) -> bytes:
        """The machine key, made on first use.

        Written 0600 *before* anything is in it — creating it world-readable
        and fixing the mode afterwards would leave a window where it was not.
        """
        path = self.dir / KEYFILE_NAME
        try:
            return bytes.fromhex(path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pass
        self.dir.mkdir(parents=True, exist_ok=True)
        key = SB.new_key()
        tmp = path.with_suffix(".tmp")
        handle = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as out:
            out.write(key.hex())
        tmp.replace(path)
        return key

    def _machine_key(self, slug: str) -> bytes:
        # One keyfile, a different key per profile, so two profiles on one
        # machine are not sealed under the same bytes.
        return SB.subkey(self._keyfile(), f"profile:{slug}".encode("utf-8"))

    # -- reading ---------------------------------------------------------
    @staticmethod
    def _split(blob: bytes) -> tuple[bytes, dict, bytes]:
        """`(header bytes, header, sealed body)` of a profile file."""
        line, sep, body = blob.partition(b"\n")
        if not sep:
            raise ProfileError("not a profile file")
        try:
            header = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProfileError(f"unreadable profile header: {exc}") from None
        if not isinstance(header, dict) or header.get("format") != FORMAT:
            raise ProfileError("not a profile file")
        if int(header.get("v") or 0) > FORMAT_VERSION:
            raise ProfileError("this profile was written by a newer build")
        return line, header, body

    def header(self, path: Path) -> dict:
        """What the chooser can show without a passphrase."""
        try:
            _, header, _ = self._split(path.read_bytes())
        except OSError as exc:
            raise ProfileError(str(exc)) from None
        return {
            "slug": str(header.get("slug") or path.stem),
            "name": clean_name(header.get("name")) or path.stem,
            "avatar": clean_avatar(header.get("avatar")),
            "locked": bool(header.get("locked")),
            "saved": float(header.get("saved") or 0.0),
        }

    def list(self) -> list[dict]:
        """Every profile on this machine, most recently played first."""
        try:
            found = sorted(self.dir.glob(f"*{SUFFIX}"))
        except OSError:
            return []
        rows = []
        for path in found:
            try:
                rows.append(self.header(path))
            except ProfileError:
                continue        # somebody else's file in our folder
        rows.sort(key=lambda r: -r["saved"])
        return rows

    def open(self, slug: str, passphrase: str = "") -> Session:
        path = self.path(slug)
        try:
            blob = path.read_bytes()
        except OSError:
            raise ProfileError("no profile by that name") from None
        head, header, body = self._split(blob)
        slug = str(header.get("slug") or path.stem)
        if header.get("locked"):
            if not passphrase:
                raise Locked("this profile needs its passphrase")
            salt = bytes.fromhex(str(header.get("salt") or ""))
            if len(salt) != SB.SALT_BYTES:
                raise ProfileError("this profile is missing its salt")
            key = SB.derive(passphrase, salt)
        else:
            key = self._machine_key(slug)
        try:
            plain = SB.unseal(key, body, aad=head)
        except SB.BadSeal as exc:
            if header.get("locked"):
                raise BadPassphrase(str(exc)) from None
            raise ProfileError(str(exc)) from None
        try:
            profile = Profile.from_json(json.loads(plain.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProfileError(f"unreadable profile: {exc}") from None
        return Session(store=self, slug=slug, key=key, profile=profile,
                       locked=bool(header.get("locked")))

    # -- writing ---------------------------------------------------------
    def write(self, slug: str, profile: Profile, key: bytes, *,
              locked: bool, salt: Optional[bytes] = None) -> Path:
        """Seal `profile` into its file. Raises OSError if it cannot.

        The header is rebuilt from the profile every time, so renaming or
        re-picturing a profile is just a save — and because the header is the
        seal's associated data, an old body can never be paired with a new
        header.
        """
        path = self.path(slug)
        self.dir.mkdir(parents=True, exist_ok=True)
        if locked and salt is None:
            _, header, _ = self._split(path.read_bytes())
            salt = bytes.fromhex(str(header.get("salt") or ""))
        head = json.dumps({
            "format": FORMAT, "v": FORMAT_VERSION, "slug": slug,
            "name": profile.name, "avatar": profile.avatar,
            "locked": bool(locked), "saved": time.time(),
            **({"salt": salt.hex()} if locked and salt else {}),
        }, sort_keys=True).encode("utf-8")
        body = json.dumps(profile.to_json(), sort_keys=True).encode("utf-8")
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(head + b"\n" + SB.seal(key, body, aad=head))
        tmp.replace(path)       # never leave a half-written profile behind
        return path

    def create(self, name: str, *, passphrase: str = "",
               avatar: str = "") -> Session:
        """A new profile. Raises ProfileError if that name is taken."""
        display = clean_name(name) or "Player"
        slug = slugify(display)
        if self.path(slug).exists():
            raise ProfileError(f"there is already a profile called {display!r}")
        profile = Profile(name=display, avatar=clean_avatar(avatar))
        locked = bool(passphrase)
        salt = SB.new_salt() if locked else None
        key = SB.derive(passphrase, salt) if locked else self._machine_key(slug)
        self.write(slug, profile, key, locked=locked, salt=salt)
        return Session(store=self, slug=slug, key=key, profile=profile,
                       locked=locked)

    def delete(self, slug: str, passphrase: str = "") -> list[GameRecord]:
        """Remove a profile, after proving it can be opened.

        Returns its history, so the caller can take the replay files with it.
        A locked profile cannot be deleted without its passphrase: "delete"
        must not be the way around a profile you cannot read.
        """
        session = self.open(slug, passphrase)
        try:
            self.path(session.slug).unlink()
        except OSError as exc:
            raise ProfileError(str(exc)) from None
        return list(session.profile.history)


def replays_of(records: Iterable[GameRecord]) -> list[str]:
    """The replay file names those entries own."""
    return [r.replay for r in records if r.replay]
