"""The display name, cleaned the same way wherever it comes in.

The name ends up inside a greeting and on screen, so it is treated as text and nothing else:
control and formatting characters (which include the bidirectional overrides that make text
read backwards) are removed, runs of whitespace become one space, and the ends are trimmed.
A name that is too long is refused and never cut short, so the person sees what is kept.
"""

from __future__ import annotations

import unicodedata

from makan.accounts.errors import InvalidProfile

MAX_DISPLAY_NAME_CHARS = 40
MAX_RAW_NAME_CHARS = 200  # what an API accepts before cleaning, so a huge body is refused early

_REMOVED = ("Cc", "Cf", "Cs", "Co", "Cn")


def clean_display_name(raw: str) -> str:
    """The name as it will be stored, or `InvalidProfile` when nothing usable is left."""
    spaced = "".join(" " if ch.isspace() else ch for ch in unicodedata.normalize("NFC", raw))
    kept = "".join(ch for ch in spaced if unicodedata.category(ch) not in _REMOVED)
    name = " ".join(kept.split())
    if not name:
        raise InvalidProfile("Enter the name Makan should call you.")
    if len(name) > MAX_DISPLAY_NAME_CHARS:
        raise InvalidProfile(f"Use {MAX_DISPLAY_NAME_CHARS} characters or fewer for your name.")
    return name
