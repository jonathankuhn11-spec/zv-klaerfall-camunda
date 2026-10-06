"""TARGET2-Kalender: Bankarbeitstage für SEPA-Überweisungen.

TARGET2 (das Euro-Zahlungssystem der EZB) ist geschlossen an Wochenenden sowie an
Neujahr, Karfreitag, Ostermontag, 1. Mai, 1. und 2. Weihnachtsfeiertag. An diesen Tagen
findet kein Interbanken-Settlement statt; ein Ausführungsdatum, das auf einen solchen Tag
fällt, wird auf den nächsten Bankarbeitstag verschoben.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache


@lru_cache(maxsize=64)
def ostersonntag(jahr: int) -> date:
    """Gauß'sche Osterformel (gregorianisch)."""
    a = jahr % 19
    b, c = divmod(jahr, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    monat, tag = divmod(h + m - 7 * n + 114, 31)
    return date(jahr, monat, tag + 1)


@lru_cache(maxsize=64)
def target2_feiertage(jahr: int) -> frozenset[date]:
    ostern = ostersonntag(jahr)
    return frozenset(
        {
            date(jahr, 1, 1),
            ostern - timedelta(days=2),  # Karfreitag
            ostern + timedelta(days=1),  # Ostermontag
            date(jahr, 5, 1),
            date(jahr, 12, 25),
            date(jahr, 12, 26),
        }
    )


def ist_bankarbeitstag(tag: date) -> bool:
    return tag.weekday() < 5 and tag not in target2_feiertage(tag.year)


def naechster_bankarbeitstag(tag: date) -> date:
    """Der Tag selbst, falls Bankarbeitstag, sonst der nächste folgende."""
    while not ist_bankarbeitstag(tag):
        tag += timedelta(days=1)
    return tag
