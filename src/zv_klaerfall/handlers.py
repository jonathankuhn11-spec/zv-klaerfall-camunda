"""Job-Handler: die Geschäftslogik hinter jedem Service Task.

Ein Handler ist eine reine Funktion ``(variablen, dienste, kontext) -> neue variablen``. Dieselben
Funktionen laufen

* als Camunda-Job-Worker (``zv worker``) – Variablen kommen aus der Prozessinstanz, oder
* im Offline-Modus (``zv demo --offline``) – Variablen werden sequenziell durchgereicht.

Dadurch ist die Fachlogik ohne Cluster vollständig testbar, und der Prozess bleibt die einzige
Stelle, die den *Ablauf* kennt. Die Job-Typen (``zv.pruefen`` …) sind der Vertrag zwischen
BPMN-Modell und Code – siehe ``bpmn/zv-zahlung-pruefen.bpmn``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from zv_klaerfall.config import Dienste
from zv_klaerfall.iso20022 import pain001, pain002
from zv_klaerfall.models import (
    STATUSGRUND,
    Befund,
    BefundCode,
    Ergebnis,
    Pruefergebnis,
    TxStatus,
    Zahlung,
    betrag_de,
)
from zv_klaerfall.rules import validation


@dataclass
class JobKontext:
    """Metadaten eines Jobs (bei Camunda aus dem Job, offline synthetisch)."""

    prozess_id: str = "offline"
    instanz_key: str = "offline"
    header: dict[str, str] = field(default_factory=dict)
    heute: date = field(default_factory=date.today)


Handler = Callable[[dict[str, Any], Dienste, JobKontext], dict[str, Any]]

# Job-Typen – müssen exakt mit zeebe:taskDefinition type="…" im BPMN übereinstimmen.
JOB_NACHRICHT_VALIDIEREN = "zv.nachricht-validieren"
JOB_PRUEFEN = "zv.pruefen"
JOB_KORRIGIEREN = "zv.korrigieren"
JOB_FREIGEBEN = "zv.freigeben"
JOB_ABLEHNEN = "zv.ablehnen"
JOB_ESKALIEREN = "zv.eskalieren"
JOB_STATUSREPORT = "zv.statusreport"


def _befunde_text(befunde: list[Befund]) -> str:
    """Lesbare Zusammenfassung für das Tasklist-Formular."""
    if not befunde:
        return "Keine Befunde."
    zeilen = []
    for b in befunde:
        marker = "⚠" if b.schwere == "BLOCKIEREND" else "ℹ"
        auto = " (automatisch korrigierbar)" if b.auto_korrigierbar else ""
        zeilen.append(f"{marker} {b.code}: {b.meldung}{auto}")
    return "\n".join(zeilen)


def _msg_id_lenient(xml: str) -> str | None:
    """Versucht, die MsgId auch aus einer schemaverletzenden Datei zu lesen (für den RJCT-Report)."""
    m = re.search(r"<MsgId>\s*([^<]{1,35}?)\s*</MsgId>", xml)
    return m.group(1) if m else None


def _nachricht_ungueltig(
    dienste: Dienste, ctx: JobKontext, *, dateiname: str, msg_id: str | None, code: str, fehler: list[str]
) -> dict[str, Any]:
    dienste.audit.schreibe(
        "NACHRICHT_ZURUECKGEWIESEN",
        prozess=ctx.prozess_id,
        referenz=msg_id or dateiname,
        daten={"dateiname": dateiname, "statusgrund": code, "fehler": fehler[:20]},
    )
    return {
        "nachrichtGueltig": False,
        "nachrichtFehler": fehler[:50],
        "nachrichtFehlerCode": code,
        "nachrichtFehlerText": "\n".join(fehler[:20]),
        "zahlungen": [],
        "nachricht": {"dateiname": dateiname, "msgId": msg_id, "pmtInfId": None},
    }


def nachricht_validieren(variablen: dict[str, Any], dienste: Dienste, ctx: JobKontext) -> dict[str, Any]:
    """Technische Prüfung (XSD) und Kopfdaten-Plausibilität; Aufteilung der pain.001 in Zahlungen.

    Scheitert die Nachricht als Ganzes, wird sie mit ISO-Statusgrund zurückgewiesen:
    ``FF01`` (ungültiges Dateiformat), ``AM10`` (Kontrollsumme falsch), ``NARR`` (sonstige Erläuterung).
    """
    xml = variablen.get("nachrichtXml")
    dateiname = variablen.get("dateiname") or "unbekannt.xml"
    if not xml:
        raise ValueError("Variable 'nachrichtXml' fehlt oder ist leer")
    try:
        nachricht = pain001.parse(xml, quelle=dateiname)
    except pain001.SchemaFehler as exc:
        return _nachricht_ungueltig(
            dienste, ctx, dateiname=dateiname, msg_id=_msg_id_lenient(xml), code="FF01", fehler=exc.fehler
        )
    kopf = nachricht.kopf
    fehler: list[str] = []
    code = "NARR"
    if kopf.anzahl_transaktionen != len(nachricht.zahlungen):
        fehler.append(
            f"GrpHdr/NbOfTxs = {kopf.anzahl_transaktionen}, enthalten sind aber "
            f"{len(nachricht.zahlungen)} Transaktionen"
        )
    if kopf.kontrollsumme is not None:
        summe = sum((z.betrag for z in nachricht.zahlungen), Decimal("0"))
        if summe != kopf.kontrollsumme:
            fehler.append(f"GrpHdr/CtrlSum = {kopf.kontrollsumme}, Summe der Beträge ist {summe}")
            code = "AM10"
    if fehler:
        return _nachricht_ungueltig(
            dienste, ctx, dateiname=dateiname, msg_id=kopf.msg_id, code=code, fehler=fehler
        )

    nachricht_var = {
        "dateiname": dateiname,
        "msgId": kopf.msg_id,
        "erstelltAm": kopf.erstellt_am.isoformat(),
        "anzahlTransaktionen": kopf.anzahl_transaktionen,
        "kontrollsumme": float(kopf.kontrollsumme) if kopf.kontrollsumme is not None else None,
        "einreicher": kopf.einreicher,
        "pmtInfId": nachricht.zahlungen[0].pmt_inf_id if nachricht.zahlungen else None,
    }
    dienste.audit.schreibe(
        "NACHRICHT_ANGENOMMEN",
        prozess=ctx.prozess_id,
        referenz=kopf.msg_id,
        daten={"dateiname": dateiname, "transaktionen": len(nachricht.zahlungen)},
    )
    return {
        "nachrichtGueltig": True,
        "nachrichtFehler": [],
        "zahlungen": [z.als_variablen() for z in nachricht.zahlungen],
        "nachricht": nachricht_var,
    }


def pruefen(variablen: dict[str, Any], dienste: Dienste, ctx: JobKontext) -> dict[str, Any]:
    """Fachliche Prüfung einer Zahlung; liefert ``pruefung`` plus flache DMN-Eingaben."""
    zahlung = Zahlung.aus_variablen(variablen["zahlung"])
    erneute_pruefung = bool(variablen.get("erneutePruefung", False))
    limit = Decimal(ctx.header.get("betragslimit", str(dienste.einstellungen.betragslimit)))
    # Nach einer automatischen Korrektur wird dieselbe Zahlung erneut geprüft; die Dublettenprüfung
    # würde sie dann fälschlich als Wiedereinreichung erkennen und entfällt deshalb im zweiten Lauf.
    pk = validation.Pruefkontext(
        heute=ctx.heute, betragslimit=limit, dublettenspeicher=None if erneute_pruefung else dienste.dubletten
    )
    ergebnis = validation.pruefe(zahlung, pk)
    dienste.audit.schreibe(
        "ZAHLUNG_GEPRUEFT",
        prozess=ctx.prozess_id,
        referenz=zahlung.end_to_end_id,
        daten={
            "gueltig": ergebnis.gueltig,
            "befunde": [b.code for b in ergebnis.befunde],
            "fuehrenderBefund": ergebnis.fuehrender_befund,
            "erneutePruefung": erneute_pruefung,
        },
    )
    return {
        "pruefung": ergebnis.model_dump(mode="json", by_alias=True),
        "erneutePruefung": erneute_pruefung,
        "befundeVorhanden": not ergebnis.gueltig,
        "befundCode": ergebnis.fuehrender_befund,
        "betrag": float(zahlung.betrag),
        "sanktionstreffer": ergebnis.sanktionstreffer,
        "nurAutoKorrigierbar": ergebnis.nur_auto_korrigierbar,
        "befundeText": _befunde_text(ergebnis.befunde),
        "zahlungText": (
            f"{betrag_de(zahlung.betrag, zahlung.waehrung)} von {zahlung.debitor.name} "
            f"({zahlung.debitor.iban}) an {zahlung.kreditor.name} ({zahlung.kreditor.iban}), "
            f"Ausführung {zahlung.ausfuehrungsdatum.isoformat()}, EndToEndId {zahlung.end_to_end_id}"
        ),
    }


def korrigieren(variablen: dict[str, Any], dienste: Dienste, ctx: JobKontext) -> dict[str, Any]:
    """Automatische Korrektur aller korrigierbaren Befunde (DMN hat ``autoKorrektur=true`` entschieden)."""
    zahlung = Zahlung.aus_variablen(variablen["zahlung"])
    pruefung = Pruefergebnis.model_validate(variablen["pruefung"])
    korrigiert, protokoll = validation.wende_korrekturen_an(zahlung, pruefung.befunde)
    dienste.audit.schreibe(
        "ZAHLUNG_AUTOKORRIGIERT",
        prozess=ctx.prozess_id,
        referenz=zahlung.end_to_end_id,
        daten={"korrekturen": protokoll},
    )
    # erneutePruefung=true verhindert im Prozess eine zweite Korrekturschleife (Gateway-Bedingung).
    return {"zahlung": korrigiert.als_variablen(), "korrekturen": protokoll, "erneutePruefung": True}


def _buche(dienste: Dienste, zahlung: Zahlung, instanz: str) -> str:
    """Stellvertreter für das Kernbank-/Zahlungsverkehrssystem: schreibt ein Buchungsjournal."""
    buchungs_id = f"BK-{uuid4().hex[:12].upper()}"
    eintrag = {
        "buchungsId": buchungs_id,
        "gebuchtAm": datetime.now(UTC).isoformat(timespec="seconds"),
        "prozessinstanz": instanz,
        "endToEndId": zahlung.end_to_end_id,
        "betrag": float(zahlung.betrag),
        "waehrung": zahlung.waehrung,
        "debitorIban": zahlung.debitor.iban,
        "kreditorIban": zahlung.kreditor.iban,
        "ausfuehrungsdatum": zahlung.ausfuehrungsdatum.isoformat(),
    }
    with dienste.buchungsjournal.open("a", encoding="utf-8") as f:
        f.write(json.dumps(eintrag, ensure_ascii=False) + "\n")
    return buchungs_id


def freigeben(variablen: dict[str, Any], dienste: Dienste, ctx: JobKontext) -> dict[str, Any]:
    zahlung = Zahlung.aus_variablen(variablen["zahlung"])
    korrekturen = list(variablen.get("korrekturen") or [])
    kommentar = variablen.get("kommentar")
    buchungs_id = _buche(dienste, zahlung, ctx.instanz_key)
    akteur = variablen.get("bearbeiter") or "system"
    dienste.audit.schreibe(
        "ZAHLUNG_FREIGEGEBEN",
        prozess=ctx.prozess_id,
        referenz=zahlung.end_to_end_id,
        daten={"buchungsId": buchungs_id, "korrekturen": korrekturen, "kommentar": kommentar},
        akteur=akteur,
    )
    ergebnis = Ergebnis(
        endToEndId=zahlung.end_to_end_id, status=TxStatus.ACCP, kommentar=kommentar, korrekturen=korrekturen
    )
    return {"ergebnis": ergebnis.model_dump(mode="json", by_alias=True), "buchungsId": buchungs_id}


def ablehnen(variablen: dict[str, Any], dienste: Dienste, ctx: JobKontext) -> dict[str, Any]:
    zahlung = Zahlung.aus_variablen(variablen["zahlung"])
    pruefung = Pruefergebnis.model_validate(variablen["pruefung"])
    code = pruefung.fuehrender_befund or BefundCode.VERWENDUNGSZWECK_FEHLT
    statusgrund = STATUSGRUND[code]
    kommentar = variablen.get("kommentar") or next(
        (b.meldung for b in pruefung.befunde if b.code == code), None
    )
    akteur = variablen.get("bearbeiter") or "system"
    dienste.audit.schreibe(
        "ZAHLUNG_ABGELEHNT",
        prozess=ctx.prozess_id,
        referenz=zahlung.end_to_end_id,
        daten={"statusgrund": statusgrund, "befund": code, "kommentar": kommentar},
        akteur=akteur,
    )
    ergebnis = Ergebnis(
        endToEndId=zahlung.end_to_end_id, status=TxStatus.RJCT, statusgrund=statusgrund, kommentar=kommentar
    )
    return {"ergebnis": ergebnis.model_dump(mode="json", by_alias=True)}


def eskalieren(variablen: dict[str, Any], dienste: Dienste, ctx: JobKontext) -> dict[str, Any]:
    """SLA überschritten: Eskalation protokollieren (produktiv: Benachrichtigung Teamleitung)."""
    zahlung = Zahlung.aus_variablen(variablen["zahlung"])
    klassifikation = variablen.get("klassifikation") or {}
    dienste.audit.schreibe(
        "KLAERFALL_ESKALIERT",
        prozess=ctx.prozess_id,
        referenz=zahlung.end_to_end_id,
        daten={
            "zielTeam": klassifikation.get("zielTeam"),
            "sla": klassifikation.get("sla"),
            "prioritaet": klassifikation.get("prioritaet"),
        },
    )
    return {"eskaliertAm": datetime.now(UTC).isoformat(timespec="seconds")}


def statusreport(variablen: dict[str, Any], dienste: Dienste, ctx: JobKontext) -> dict[str, Any]:
    """Erzeugt den pain.002-Statusreport: je Transaktion (ACCP/RJCT) oder für die ganze Datei (RJCT)."""
    nachricht = variablen.get("nachricht") or {}
    ergebnisse = [Ergebnis.model_validate(e) for e in (variablen.get("ergebnisse") or [])]
    msg_id = nachricht.get("msgId") or "UNBEKANNT"
    report_id = f"RPT-{msg_id}"[:35]
    gruppen_ablehnung: tuple[str, str] | None = None
    if variablen.get("nachrichtGueltig") is False:
        fehler = variablen.get("nachrichtFehler") or ["Nachricht nicht verarbeitbar"]
        gruppen_ablehnung = (str(variablen.get("nachrichtFehlerCode") or "NARR"), str(fehler[0]))
    xml = pain002.erzeuge_statusreport(
        orgnl_msg_id=msg_id,
        orgnl_pmt_inf_id=nachricht.get("pmtInfId") or msg_id,
        ergebnisse=ergebnisse,
        report_msg_id=report_id,
        bank_name=dienste.einstellungen.bank_name,
        gruppen_ablehnung=gruppen_ablehnung,
    )
    sicherer_name = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{msg_id}_{ctx.instanz_key}")
    pfad = dienste.ausgabeverzeichnis / f"pain.002_{sicherer_name}.xml"
    pfad.write_bytes(xml)
    status = "RJCT" if gruppen_ablehnung else pain002.gruppenstatus(ergebnisse)
    dienste.audit.schreibe(
        "STATUSREPORT_ERSTELLT",
        prozess=ctx.prozess_id,
        referenz=msg_id,
        daten={"pfad": str(pfad), "gruppenstatus": status, "anzahl": len(ergebnisse)},
    )
    return {"statusreportPfad": str(pfad), "gruppenstatus": status}


HANDLER: dict[str, Handler] = {
    JOB_NACHRICHT_VALIDIEREN: nachricht_validieren,
    JOB_PRUEFEN: pruefen,
    JOB_KORRIGIEREN: korrigieren,
    JOB_FREIGEBEN: freigeben,
    JOB_ABLEHNEN: ablehnen,
    JOB_ESKALIEREN: eskalieren,
    JOB_STATUSREPORT: statusreport,
}
