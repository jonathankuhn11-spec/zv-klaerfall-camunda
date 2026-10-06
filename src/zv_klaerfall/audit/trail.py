"""Revisionssicherer Audit-Trail.

Jede fachliche Entscheidung (Prüfergebnis, Klassifikation, Korrektur, Freigabe, Ablehnung,
Eskalation) wird als Zeile in eine append-only JSONL-Datei geschrieben. Jede Zeile enthält den
SHA-256-Hash der Vorgängerzeile; damit ist nachträgliches Ändern oder Löschen einzelner Einträge
ohne Bruch der Kette unmöglich (:func:`AuditTrail.verifiziere`). Das ist das gleiche Prinzip, das
Prüfer bei "revisionssicherer Dokumentation" erwarten: vollständig, unveränderbar, nachvollziehbar.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


def _hash(zeile: dict[str, Any]) -> str:
    kanonisch = json.dumps(
        {k: v for k, v in zeile.items() if k != "hash"},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(kanonisch.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Pruefbericht:
    eintraege: int
    gueltig: bool
    fehler: list[str]


class AuditTrail:
    def __init__(self, pfad: str | Path):
        self.pfad = Path(pfad)
        self.pfad.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _letzter_hash(self) -> str:
        if not self.pfad.exists() or self.pfad.stat().st_size == 0:
            return GENESIS
        with self.pfad.open("rb") as f:
            f.seek(0, 2)
            pos = f.tell()
            puffer = b""
            while pos > 0:
                schritt = min(4096, pos)
                pos -= schritt
                f.seek(pos)
                puffer = f.read(schritt) + puffer
                if puffer.count(b"\n") >= 2:
                    break
        letzte = [z for z in puffer.split(b"\n") if z.strip()][-1]
        return str(json.loads(letzte)["hash"])

    def schreibe(
        self,
        ereignis: str,
        *,
        prozess: str,
        referenz: str,
        daten: dict[str, Any] | None = None,
        akteur: str = "system",
    ) -> dict[str, Any]:
        with self._lock:
            zeile: dict[str, Any] = {
                "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "ereignis": ereignis,
                "prozess": prozess,
                "referenz": referenz,
                "akteur": akteur,
                "daten": daten or {},
                "prev": self._letzter_hash(),
            }
            zeile["hash"] = _hash(zeile)
            with self.pfad.open("a", encoding="utf-8") as f:
                f.write(json.dumps(zeile, ensure_ascii=False, sort_keys=True) + "\n")
            return zeile

    def lese(self) -> list[dict[str, Any]]:
        if not self.pfad.exists():
            return []
        with self.pfad.open(encoding="utf-8") as f:
            return [json.loads(z) for z in f if z.strip()]

    def verifiziere(self) -> Pruefbericht:
        fehler: list[str] = []
        prev = GENESIS
        eintraege = self.lese()
        for i, z in enumerate(eintraege, start=1):
            if z.get("prev") != prev:
                fehler.append(f"Eintrag {i}: Vorgänger-Hash passt nicht (Kette unterbrochen)")
            if _hash(z) != z.get("hash"):
                fehler.append(f"Eintrag {i}: Inhalt wurde verändert (Hash stimmt nicht)")
            prev = str(z.get("hash"))
        return Pruefbericht(eintraege=len(eintraege), gueltig=not fehler, fehler=fehler)
