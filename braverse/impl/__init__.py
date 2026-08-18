"""Hand-implemented card effects.

Importing this package registers every implemented card with
:mod:`braverse.effects`. Add a new set by dropping a module here and importing
it below.
"""

from . import bs1, bs10, bs11, bs2, bs9, bs8, bs3, bs4, bs5, bs6, bs7, st8_green, st9_blue, st_misc  # noqa: F401

__all__ = ["bs1", "bs10", "bs11", "bs9", "bs2", "bs3", "bs8", "bs4", "bs5", "bs6", "bs7", "st8_green", "st9_blue", "st_misc"]
