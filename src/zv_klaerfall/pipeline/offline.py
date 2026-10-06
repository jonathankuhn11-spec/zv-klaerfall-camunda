"""Offline-Ausführung des Prozesses ohne Camunda.

Bildet den Kontrollfluss von ``zv-nachricht-verarbeiten`` und ``zv-zahlung-pruefen`` Schritt für
Schritt nach – mit denselben Handlern und derselben DMN-Datei, die auch im Cluster laufen.
Zweck: Demo auf jedem Laptop in zehn Sekunden, End-to-End-Tests in der CI, und eine zweite,
unabhängige Lesart des Prozessmodells (wer die Pipeline liest, versteht das BPMN).

Der User Task "Klärfall bearbeiten" wird hier durch eine :class:`Entscheider`-Strategie ersetzt:
:func:`standard_entscheider` trifft nachvollziehbare Demo-Entscheidungen, ``interaktiv`` fragt
im Terminal nach.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from zv_klaerfall import handlers
from zv_klaerfall.config import Dienste
from zv_klaerfall.handlers import JobKontext
from zv_klaerfall.models import Klassifikation, Pruefergebnis
from zv_klaerfall.rules import dmn_lite

DECISION_ID = "klaerfall-klassifikation"
PROZESS_NACHRICHT = "zv-nachricht-verarbeiten"
PROZESS_ZAHLUNG = "zv-zahlung-pruefen"


@dataclass(frozen=True)
class Entscheidung:
    entscheidung: str  # "FREIGEBEN" | "ABLEHNEN"
    bearbeiter: str
    kommentar: str | None = None


Entscheider = Callable[[Klassifikation, Pruefergebnis, dict[str, Any]], Entscheidung]


def standard_entscheider(k: Klassifikation, _p: Pruefergebnis, _v: dict[str, Any]) -> Entscheidung:
    """Demo-Policy für den Klärfallbearbeiter – bewusst konservativ und erklärbar."""
    if k.kategorie == "Freigabe":
        return Entscheidung("FREIGEBEN", "disposition", "Vier-Augen-Freigabe erteilt (Demo-Entscheidung)")
    if k.kategorie == "Compliance":
        return Entscheidung("ABLEHNEN", "compliance", "Sanktionstreffer bestätigt, Zahlung gesperrt")
    if k.kategorie == "Dublette":
        return Entscheidung("ABLEHNEN", "zv-team", "Doppelte Einreichung, Original wurde bereits ausgeführt")
    return Entscheidung("ABLEHNEN", "zv-team", "Rückfrage beim Einreicher erforderlich")


@dataclass
class Schritt:
    """Ein Protokolleintrag je durchlaufenem Prozesselement (für Ausgabe und Tests)."""

    element: str
    detail: str = ""


@dataclass
class ZahlungsLauf:
    end_to_end_id: str
    status: str
    kategorie: str | None = None
    ziel_team: str | None = None
    befunde: list[str] = field(default_factory=list)
    korrekturen: list[str] = field(default_factory=list)
    schritte: list[Schritt] = field(default_factory=list)
    eskaliert: bool = False


@dataclass
class NachrichtLauf:
    dateiname: str
    nachricht_gueltig: bool
    msg_id: str | None = None
    zahlungen: list[ZahlungsLauf] = field(default_factory=list)
    statusreport_pfad: str | None = None
    gruppenstatus: str | None = None
    fehler: list[str] = field(default_factory=list)


class OfflinePipeline:
    def __init__(
        self,
        dienste: Dienste,
        dmn_pfad: Path,
        *,
        entscheider: Entscheider | None = None,
        heute: date | None = None,
        sla_simulieren: bool = False,
    ):
        self._dienste = dienste
        self._tabelle = dmn_lite.lade(dmn_pfad, DECISION_ID)
        self._entscheider = entscheider or standard_entscheider
        self._heute = heute or date.today()
        self._sla_simulieren = sla_simulieren

    # --- Prozess 1: Nachricht ------------------------------------------------------------
    def verarbeite_datei(self, pfad: Path) -> NachrichtLauf:
        return self.verarbeite_nachricht(pfad.read_text(encoding="utf-8"), pfad.name)

    def verarbeite_nachricht(self, xml: str, dateiname: str) -> NachrichtLauf:
        ctx = JobKontext(
            prozess_id=PROZESS_NACHRICHT, instanz_key=f"offline:{Path(dateiname).stem}", heute=self._heute
        )
        v: dict[str, Any] = {"nachrichtXml": xml, "dateiname": dateiname}
        v.update(handlers.nachricht_validieren(v, self._dienste, ctx))
        lauf = NachrichtLauf(
            dateiname=dateiname, nachricht_gueltig=v["nachrichtGueltig"], msg_id=v["nachricht"].get("msgId")
        )
        if not v["nachrichtGueltig"]:  # Gateway "Nachricht gültig?" → nein → Zurückweisung melden → Ende
            lauf.fehler = list(v["nachrichtFehler"])
            v.update(handlers.statusreport(v, self._dienste, ctx))
            lauf.statusreport_pfad, lauf.gruppenstatus = v["statusreportPfad"], v["gruppenstatus"]
            return lauf
        # Call Activity, Multi-Instance über "zahlungen" → je Zahlung eine Instanz von zv-zahlung-pruefen
        ergebnisse: list[dict[str, Any]] = []
        for zahlung in v["zahlungen"]:
            kind_v, zl = self.pruefe_zahlung(zahlung, v["nachricht"])
            ergebnisse.append(kind_v["ergebnis"])
            lauf.zahlungen.append(zl)
        v["ergebnisse"] = ergebnisse
        v.update(handlers.statusreport(v, self._dienste, ctx))
        lauf.statusreport_pfad = v["statusreportPfad"]
        lauf.gruppenstatus = v["gruppenstatus"]
        return lauf

    # --- Prozess 2: Zahlung --------------------------------------------------------------
    def pruefe_zahlung(
        self, zahlung: dict[str, Any], nachricht: dict[str, Any]
    ) -> tuple[dict[str, Any], ZahlungsLauf]:
        e2e = zahlung["endToEndId"]
        ctx = JobKontext(prozess_id=PROZESS_ZAHLUNG, instanz_key=f"offline:{e2e}", heute=self._heute)
        v: dict[str, Any] = {"zahlung": zahlung, "nachricht": nachricht}
        lauf = ZahlungsLauf(end_to_end_id=e2e, status="?")

        while True:
            v.update(handlers.pruefen(v, self._dienste, ctx))
            befunde = [b["code"] for b in v["pruefung"]["befunde"]]
            lauf.befunde = lauf.befunde or befunde  # Befunde des ersten Durchlaufs bleiben sichtbar
            lauf.schritte.append(Schritt("Zahlung fachlich prüfen", ", ".join(befunde) or "ohne Befund"))
            if not v["befundeVorhanden"]:  # Gateway "Befunde?" → nein
                return self._freigeben(v, ctx, lauf)

            # Business Rule Task: DMN klaerfall-klassifikation
            klass = dmn_lite.werte_aus(self._tabelle, v)
            if klass is None:
                raise RuntimeError("DMN lieferte kein Ergebnis – Catch-all-Regel fehlt?")
            v["klassifikation"] = klass
            k = Klassifikation.model_validate(klass)
            lauf.kategorie, lauf.ziel_team = k.kategorie, k.zielTeam
            lauf.schritte.append(
                Schritt("Klärfall klassifizieren", f"{k.kategorie} / {k.prioritaet} → {k.zielTeam}")
            )

            # Gateway "Automatisch korrigierbar?":
            #   klassifikation.autoKorrektur = true and erneutePruefung = false
            if k.autoKorrektur and not v.get("erneutePruefung", False):
                v.update(handlers.korrigieren(v, self._dienste, ctx))
                lauf.korrekturen = list(v["korrekturen"])
                lauf.schritte.append(Schritt("Automatisch korrigieren", "; ".join(lauf.korrekturen)))
                continue  # Schleife zurück zu "Zahlung fachlich prüfen"
            break

        # User Task "Klärfall bearbeiten" (+ nicht unterbrechender SLA-Timer)
        if self._sla_simulieren:
            v.update(handlers.eskalieren(v, self._dienste, ctx))
            lauf.eskaliert = True
            lauf.schritte.append(Schritt("SLA überschritten → Eskalieren", k.sla))
        pruefung = Pruefergebnis.model_validate(v["pruefung"])
        entscheidung = self._entscheider(k, pruefung, v)
        v.update(
            {
                "entscheidung": entscheidung.entscheidung,
                "bearbeiter": entscheidung.bearbeiter,
                "kommentar": entscheidung.kommentar,
            }
        )
        lauf.schritte.append(
            Schritt("Klärfall bearbeiten", f"{entscheidung.entscheidung} durch {entscheidung.bearbeiter}")
        )
        if entscheidung.entscheidung == "FREIGEBEN":
            return self._freigeben(v, ctx, lauf)
        v.update(handlers.ablehnen(v, self._dienste, ctx))
        lauf.status = "RJCT"
        lauf.schritte.append(Schritt("Zahlung ablehnen", v["ergebnis"].get("statusgrund") or ""))
        return v, lauf

    def _freigeben(
        self, v: dict[str, Any], ctx: JobKontext, lauf: ZahlungsLauf
    ) -> tuple[dict[str, Any], ZahlungsLauf]:
        v.update(handlers.freigeben(v, self._dienste, ctx))
        lauf.status = "ACCP"
        lauf.schritte.append(Schritt("Zahlung freigeben", v["buchungsId"]))
        return v, lauf
