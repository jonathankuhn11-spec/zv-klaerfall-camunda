"""Dublettenprüfung.

Eine Zahlung gilt als Dublette, wenn innerhalb des Zeitfensters bereits eine Zahlung mit
identischem Fingerprint (Debitor-IBAN, Kreditor-IBAN, Betrag, Währung, EndToEndId) gesehen
wurde. Typischer Auslöser in der Praxis: eine Datei wird vom Firmenkunden versehentlich zweimal
eingereicht. Speicher ist SQLite – lokal, ohne externen Dienst, für Demo und Tests ausreichend.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from zv_klaerfall.models import Zahlung


def fingerprint(z: Zahlung) -> str:
    rohdaten = "|".join(
        [
            (z.debitor.iban or "").replace(" ", "").upper(),
            (z.kreditor.iban or "").replace(" ", "").upper(),
            f"{z.betrag:.2f}",
            z.waehrung,
            z.end_to_end_id,
        ]
    )
    return hashlib.sha256(rohdaten.encode()).hexdigest()


class Dublettenspeicher:
    def __init__(self, pfad: str | Path = ":memory:", fenster: timedelta = timedelta(hours=24)):
        self._conn = sqlite3.connect(str(pfad), check_same_thread=False)
        self._fenster = fenster
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS gesehen (fingerprint TEXT, end_to_end_id TEXT, "
            "msg_id TEXT, gesehen_am TEXT, PRIMARY KEY (fingerprint, msg_id, gesehen_am))"
        )
        self._conn.commit()

    def pruefe_und_merke(self, z: Zahlung, jetzt: datetime | None = None) -> str | None:
        """Liefert die MsgId der früheren Einreichung, falls Dublette; merkt die Zahlung in jedem Fall.

        Bewusst *ohne* Ausschluss derselben MsgId: Wird dieselbe Datei zweimal eingereicht oder
        enthält eine Datei dieselbe Zahlung doppelt, ist das genau der Fall, der erkannt werden soll.
        """
        jetzt = jetzt or datetime.now()
        fp = fingerprint(z)
        grenze = (jetzt - self._fenster).isoformat()
        zeile = self._conn.execute(
            "SELECT msg_id FROM gesehen WHERE fingerprint = ? AND gesehen_am >= ? "
            "ORDER BY gesehen_am LIMIT 1",
            (fp, grenze),
        ).fetchone()
        self._conn.execute(
            "INSERT OR IGNORE INTO gesehen VALUES (?, ?, ?, ?)",
            (fp, z.end_to_end_id, z.msg_id, jetzt.isoformat()),
        )
        self._conn.commit()
        return zeile[0] if zeile else None

    def leeren(self) -> None:
        self._conn.execute("DELETE FROM gesehen")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
