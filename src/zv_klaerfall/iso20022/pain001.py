"""Einlesen von ``pain.001.001.09`` (Customer Credit Transfer Initiation).

Zwei Stufen, bewusst getrennt:

1. :func:`validiere_schema` – technische Prüfung gegen das offizielle ISO-20022-XSD.
   Scheitert sie, ist die Datei als Ganzes nicht verarbeitbar (Rückgabe an den Einreicher).
2. :func:`parse` – Mapping auf :class:`~zv_klaerfall.models.Zahlung`. Fachliche Prüfungen
   (IBAN-Prüfziffer, Limits, Sanktionen …) passieren *nicht* hier, sondern in
   :mod:`zv_klaerfall.rules.validation`, damit Technik und Fachlichkeit getrennt testbar bleiben.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from importlib import resources
from pathlib import Path

from lxml import etree

from zv_klaerfall.models import Partei, Zahlung

NS_PAIN001 = "urn:iso:std:iso:20022:tech:xsd:pain.001.001.09"
_NS = {"p": NS_PAIN001}


class SchemaFehler(Exception):
    """Die Nachricht verletzt das XSD; ``fehler`` enthält die Einzelmeldungen (Zeile + Text)."""

    def __init__(self, fehler: list[str]):
        super().__init__("; ".join(fehler))
        self.fehler = fehler


@dataclass(frozen=True)
class Nachrichtenkopf:
    msg_id: str
    erstellt_am: datetime
    anzahl_transaktionen: int
    kontrollsumme: Decimal | None
    einreicher: str | None


@dataclass(frozen=True)
class Nachricht:
    kopf: Nachrichtenkopf
    zahlungen: list[Zahlung]


@lru_cache(maxsize=1)
def _schema() -> etree.XMLSchema:
    xsd = resources.files("zv_klaerfall.iso20022.schemas").joinpath("pain.001.001.09.xsd")
    with resources.as_file(xsd) as pfad:
        return etree.XMLSchema(etree.parse(str(pfad)))


def _parse_xml(xml: str | bytes) -> etree._Element:
    """Parst defensiv: keine Entity-Auflösung, kein Netzwerk, keine DTD (XXE-/Billion-Laughs-Schutz)."""
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, remove_blank_text=True, huge_tree=False
    )
    daten = xml.encode("utf-8") if isinstance(xml, str) else xml
    try:
        wurzel = etree.fromstring(daten, parser)
    except etree.XMLSyntaxError as exc:  # nicht einmal wohlgeformt
        raise SchemaFehler([f"XML nicht wohlgeformt: {exc}"]) from exc
    if wurzel.getroottree().docinfo.doctype:
        raise SchemaFehler(["DOCTYPE-Deklarationen sind in ISO-20022-Nachrichten nicht zulässig"])
    return wurzel


def validiere_schema(xml: str | bytes) -> etree._Element:
    """Prüft Wohlgeformtheit, Namespace und XSD-Konformität. Liefert den geparsten Baum."""
    wurzel = _parse_xml(xml)
    if etree.QName(wurzel).namespace != NS_PAIN001:
        raise SchemaFehler(
            [f"Unerwarteter Namespace '{etree.QName(wurzel).namespace}', erwartet {NS_PAIN001}"]
        )
    schema = _schema()
    try:
        gueltig = schema.validate(wurzel)
    except etree.XMLSchemaError as exc:
        raise SchemaFehler([f"XSD-Validierung nicht möglich: {exc}"]) from exc
    if not gueltig:
        raise SchemaFehler([f"Zeile {e.line}: {e.message}" for e in schema.error_log])
    return wurzel


def _text(el: etree._Element | None, pfad: str) -> str | None:
    if el is None:
        return None
    gefunden = el.find(pfad, _NS)
    return gefunden.text.strip() if gefunden is not None and gefunden.text else None


def _partei(el: etree._Element, basis: str) -> Partei:
    """Mappt ``Dbtr``/``Cdtr`` (Name), ``…Acct`` (IBAN) und ``…Agt`` (BIC) auf eine Partei."""
    return Partei(
        name=_text(el, f"p:{basis}/p:Nm"),
        iban=_text(el, f"p:{basis}Acct/p:Id/p:IBAN"),
        bic=_text(el, f"p:{basis}Agt/p:FinInstnId/p:BICFI"),
    )


def _ausfuehrungsdatum(pmt_inf: etree._Element) -> date:
    dt = _text(pmt_inf, "p:ReqdExctnDt/p:Dt")
    if dt:
        return date.fromisoformat(dt)
    dttm = _text(pmt_inf, "p:ReqdExctnDt/p:DtTm")
    if dttm:
        return datetime.fromisoformat(dttm.replace("Z", "+00:00")).date()
    raise SchemaFehler(["ReqdExctnDt ohne Dt/DtTm"])  # durch XSD eigentlich ausgeschlossen


def parse(xml: str | bytes, quelle: str | None = None, *, schema_pruefen: bool = True) -> Nachricht:
    """Liest eine pain.001-Nachricht und liefert Kopf + eine :class:`Zahlung` je ``CdtTrfTxInf``."""
    wurzel = validiere_schema(xml) if schema_pruefen else _parse_xml(xml)
    cct = wurzel.find("p:CstmrCdtTrfInitn", _NS)
    if cct is None:
        raise SchemaFehler(["CstmrCdtTrfInitn fehlt"])
    hdr = cct.find("p:GrpHdr", _NS)
    kopf = Nachrichtenkopf(
        msg_id=_text(hdr, "p:MsgId") or "",
        erstellt_am=datetime.fromisoformat((_text(hdr, "p:CreDtTm") or "").replace("Z", "+00:00")),
        anzahl_transaktionen=int(_text(hdr, "p:NbOfTxs") or 0),
        kontrollsumme=Decimal(ks) if (ks := _text(hdr, "p:CtrlSum")) else None,
        einreicher=_text(hdr, "p:InitgPty/p:Nm"),
    )

    zahlungen: list[Zahlung] = []
    for pmt_inf in cct.findall("p:PmtInf", _NS):
        pmt_inf_id = _text(pmt_inf, "p:PmtInfId") or ""
        debitor = _partei(pmt_inf, "Dbtr")
        service_level = _text(pmt_inf, "p:PmtTpInf/p:SvcLvl/p:Cd")
        local_instrument = _text(pmt_inf, "p:PmtTpInf/p:LclInstrm/p:Cd")
        charge_bearer = _text(pmt_inf, "p:ChrgBr")
        datum = _ausfuehrungsdatum(pmt_inf)
        for tx in pmt_inf.findall("p:CdtTrfTxInf", _NS):
            amt = tx.find("p:Amt/p:InstdAmt", _NS)
            if amt is None or amt.text is None:
                raise SchemaFehler(["CdtTrfTxInf ohne InstdAmt (EquivalentAmount wird nicht unterstützt)"])
            zahlungen.append(
                Zahlung(
                    msgId=kopf.msg_id,
                    pmtInfId=pmt_inf_id,
                    endToEndId=_text(tx, "p:PmtId/p:EndToEndId") or "",
                    instrId=_text(tx, "p:PmtId/p:InstrId"),
                    betrag=Decimal(amt.text.strip()),
                    waehrung=amt.get("Ccy", ""),
                    ausfuehrungsdatum=datum,
                    serviceLevel=_text(tx, "p:PmtTpInf/p:SvcLvl/p:Cd") or service_level,
                    localInstrument=_text(tx, "p:PmtTpInf/p:LclInstrm/p:Cd") or local_instrument,
                    chargeBearer=_text(tx, "p:ChrgBr") or charge_bearer,
                    debitor=debitor,
                    kreditor=_partei(tx, "Cdtr"),
                    verwendungszweck=_text(tx, "p:RmtInf/p:Ustrd"),
                    purposeCode=_text(tx, "p:Purp/p:Cd"),
                    quelle=quelle,
                )
            )
    return Nachricht(kopf=kopf, zahlungen=zahlungen)


def parse_datei(pfad: str | Path, *, schema_pruefen: bool = True) -> Nachricht:
    p = Path(pfad)
    return parse(p.read_bytes(), quelle=p.name, schema_pruefen=schema_pruefen)
