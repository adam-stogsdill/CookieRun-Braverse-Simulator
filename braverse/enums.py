"""Core enumerations for the Braverse engine."""

from enum import Enum


class Color(str, Enum):
    RED = "RED"
    BLUE = "BLUE"
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    PURPLE = "PURPLE"
    BLACK = "BLACK"
    PURE = "PURE"
    NONE = ""


# Energy symbols as they appear in rules text: {R} {B} {G} {Y} {P} {K} plus {N}
# for "any colour". A support card of colour X can pay {X} or {N}.
SYMBOL_TO_COLOR = {
    "R": Color.RED,
    "B": Color.BLUE,
    "G": Color.GREEN,
    "Y": Color.YELLOW,
    "P": Color.PURPLE,
    "K": Color.BLACK,
}
COLOR_TO_SYMBOL = {v: k for k, v in SYMBOL_TO_COLOR.items()}
ANY = "N"


class CardType(str, Enum):
    COOKIE = "COOKIE"
    FLIP = "FLIP"      # a Cookie that also carries a flip effect while used as HP
    ITEM = "ITEM"
    TRAP = "TRAP"
    STAGE = "STAGE"
    EXTRA = "EXTRA"
    NPC = "NPC"

    @property
    def is_cookie(self) -> bool:
        return self in (CardType.COOKIE, CardType.FLIP, CardType.EXTRA)


class Zone(str, Enum):
    DECK = "deck"
    HAND = "hand"
    BATTLE = "battle"
    SUPPORT = "support"
    STAGE = "stage"
    TRASH = "trash"
    BREAK = "break"
    HP = "hp"
    EXTRA_DECK = "extra_deck"


class Phase(str, Enum):
    ACTIVE = "active"
    DRAW = "draw"
    SUPPORT = "support"
    MAIN = "main"
    END = "end"


class Keyword(str, Enum):
    ARENA = "ARENA"
    ANCIENT = "ANCIENT"
    BEAST = "BEAST"
    DRAGON = "DRAGON"


# Ability markers found inside 【...】 in the rules text.
class Marker(str, Enum):
    ACTIVATE = "Activate"
    ON_PLAY = "On Play"
    ONCE_PER_TURN = "Once Per Turn"
    YOUR_TURN = "Your Turn"
    BLOCKER = "Blocker"
    AWAKEN = "Awaken"
    EXTRA = "EXTRA"
    SPECIAL_PLAY = "Special Play"
    EQUIP = "Equip"
    SKILL = "Skill"
