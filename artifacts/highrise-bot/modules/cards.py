"""
modules/cards.py
----------------
Deck and hand utilities for the blackjack module.
"""

import random

SUITS = ("♠", "♥", "♦", "♣")
RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")


def make_deck() -> list[tuple[str, str]]:
    """Return a freshly shuffled single 52-card deck."""
    deck = [(r, s) for s in SUITS for r in RANKS]
    random.shuffle(deck)
    return deck


def make_shoe(decks: int = 6) -> list[tuple[str, str]]:
    """Return a shuffled multi-deck shoe (default 6 decks = 312 cards)."""
    shoe = [(r, s) for _ in range(decks) for s in SUITS for r in RANKS]
    random.shuffle(shoe)
    return shoe


def card_str(card: tuple[str, str]) -> str:
    return f"{card[0]}{card[1]}"


def hand_str(hand: list[tuple[str, str]]) -> str:
    return " ".join(card_str(c) for c in hand)


def hand_value(hand: list[tuple[str, str]]) -> int:
    """Return the best total for a BJ hand (aces as 11 or 1)."""
    total = 0
    aces  = 0
    for rank, _ in hand:
        if rank in ("J", "Q", "K"):
            total += 10
        elif rank == "A":
            aces  += 1
            total += 11
        else:
            total += int(rank)
    while total > 21 and aces:
        total -= 10
        aces  -= 1
    return total


def is_blackjack(hand: list[tuple[str, str]]) -> bool:
    """True only for a natural 21 on exactly 2 cards."""
    return len(hand) == 2 and hand_value(hand) == 21
