
"""
Simple, regex-based intent matching — no ML needed for fixed commands.

Add new intents by extending INTENTS below. Use \\w* on verb stems to catch
different grammatical forms Vosk may output (e.g. "вимкни" vs "вимкнути" vs
"вимкнули") — see docs/JOURNEY.md for why this matters.
"""

import re

INTENTS = [
    (re.compile(r"яка (зараз )?погода", re.I), "weather"),
    (re.compile(r"увімкн\w* (світло|лампу)", re.I), "light_on"),
    (re.compile(r"вимкн\w* (світло|лампу)", re.I), "light_off"),
    (re.compile(r"котра (зараз )?година", re.I), "time"),
    (re.compile(r"привіт", re.I), "greeting"),
]


def match_intent(text: str):
    """Return the first matching intent name, or None if nothing matches."""
    for pattern, name in INTENTS:
        if pattern.search(text):
            return name
    return None
