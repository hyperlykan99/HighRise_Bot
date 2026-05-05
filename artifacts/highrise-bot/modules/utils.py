"""
modules/utils.py
----------------
Shared helper utilities used across game modules.

Kept in one place so every game uses the same matching logic —
fixing a bug here fixes it everywhere.
"""

import re


def check_answer(player_input: str, accepted_answers: list[str]) -> bool:
    """
    Flexible, forgiving answer comparison.

    What it handles:
      - Case-insensitive:  "Paris" == "paris"  ✓
      - Extra whitespace:  "  paris " == "paris"  ✓
      - Edge punctuation:  "paris." == "paris"  ✓
      - Leading articles:  "a clock" == "clock", "the future" == "future"  ✓
      - Multiple accepted: ["paris", "france"] — any match wins  ✓

    Parameters
    ----------
    player_input     : the raw text the player typed after /answer
    accepted_answers : list of strings that are considered correct

    Returns
    -------
    True if the player's answer matches any accepted answer, False otherwise.
    """
    player_norm = _normalize(player_input)

    for answer in accepted_answers:
        if player_norm == _normalize(answer):
            return True

    return False


def _normalize(text: str) -> str:
    """
    Internal helper: reduce a string to its simplest comparable form.

    Steps:
      1. Strip surrounding whitespace
      2. Lowercase everything
      3. Remove punctuation from the start and end  (e.g. trailing period, comma)
      4. Strip a leading article ("a", "an", "the") so "a clock" == "clock"
      5. Strip any whitespace left over after the above
    """
    # Step 1 & 2: trim and lowercase
    text = text.strip().lower()

    # Step 3: remove leading/trailing punctuation characters
    # This handles answers like "Paris." or "...echo"
    text = re.sub(r"^[^\w]+|[^\w]+$", "", text)

    # Step 4: remove a single leading article if present
    # e.g. "a clock" → "clock",  "an echo" → "echo",  "the future" → "future"
    text = re.sub(r"^(a|an|the)\s+", "", text)

    # Step 5: clean up any leftover whitespace
    text = text.strip()

    return text
