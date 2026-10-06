"""Erzeugung des Statusreports ``pain.002.001.12`` (Customer Payment Status Report).

Die Bank meldet dem Einreicher je Transaktion den Status zurück: ``ACCP`` (angenommen),
``RJCT`` (abgelehnt, mit ISO-Statusgrund) oder ``PDNG`` (Klärfall offen). Der Report wird vor
der Rückgabe gegen das offizielle XSD validiert – eine fehlerhafte Rückmeldung darf das System
nicht verlassen.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from importlib import resources

from lxml import etree

from zv_klaerfall.models import Ergebnis, TxStatus

NS_PAIN002 = "urn:iso:std:iso:20022:tech:xsd:pain.002.001.12"


@lru_cache(maxsize=1)
def _schema() -> etree.XMLSchema:
    xsd = resources.files("zv_klaerfall.iso20022.schemas").joinpath("pain.002.001.12.xsd")
    with resources.as_file(xsd) as pfad:
        return etree.XMLSchema(etree.parse(str(pfad)))


def _sub(parent: etree._Element, tag: str, text: str | None = None) -> etree._Element:
    el = etree.SubElement(parent, f"{{{NS_PAIN002}}}{tag}")
    if text is not None:
        el.text = text
    return el


def gruppenstatus(ergebnisse: list[Ergebnis]) -> str:
    """``ACCP`` wenn alles angenommen, ``RJCT`` wenn alles abgelehnt (oder nichts da), sonst ``PART``."""
    stati = {e.status for e in ergebnisse}
    if stati == {TxStatus.ACCP}:
        return "ACCP"
    if not stati or stati == {TxStatus.RJCT}:
        return "RJCT"
    return "PART"


def erzeuge_statusreport(
    *,
    orgnl_msg_id: str,
    orgnl_pmt_inf_id: str,
    ergebnisse: list[Ergebnis],
    report_msg_id: str,
    bank_name: str = "Demo Bank AG",
    erstellt_am: datetime | None = None,
    gruppen_ablehnung: tuple[str, str] | None = None,
) -> bytes:
    """Baut den Report und validiert ihn gegen das XSD. Liefert UTF-8-kodiertes XML.

    ``gruppen_ablehnung=(code, text)`` erzeugt eine Zurückweisung der *gesamten* Datei (``GrpSts``
    = ``RJCT`` mit Statusgrund), z. B. bei XSD-Verletzung (``FF01``) oder falscher Kontrollsumme
    (``AM10``). In diesem Fall gibt es keine Einzeltransaktionen im Report.
    """
    jetzt = erstellt_am or datetime.now(UTC)
    doc = etree.Element(f"{{{NS_PAIN002}}}Document", nsmap={None: NS_PAIN002})
    rpt = _sub(doc, "CstmrPmtStsRpt")

    hdr = _sub(rpt, "GrpHdr")
    _sub(hdr, "MsgId", report_msg_id[:35])
    _sub(hdr, "CreDtTm", jetzt.isoformat(timespec="seconds"))
    _sub(_sub(hdr, "InitgPty"), "Nm", bank_name[:140])

    grp = _sub(rpt, "OrgnlGrpInfAndSts")
    _sub(grp, "OrgnlMsgId", orgnl_msg_id[:35])
    _sub(grp, "OrgnlMsgNmId", "pain.001.001.09")
    if gruppen_ablehnung is not None:
        code, text = gruppen_ablehnung
        _sub(grp, "GrpSts", "RJCT")
        rsn = _sub(grp, "StsRsnInf")
        _sub(_sub(rsn, "Rsn"), "Cd", code[:4])
        _sub(rsn, "AddtlInf", text[:105])
        return _validiere_und_serialisiere(doc)
    _sub(grp, "OrgnlNbOfTxs", str(len(ergebnisse)))
    _sub(grp, "GrpSts", gruppenstatus(ergebnisse))

    pmt = _sub(rpt, "OrgnlPmtInfAndSts")
    _sub(pmt, "OrgnlPmtInfId", orgnl_pmt_inf_id[:35])
    for status in (TxStatus.ACCP, TxStatus.RJCT, TxStatus.PDNG):
        n = sum(1 for e in ergebnisse if e.status == status)
        if n:
            per = _sub(pmt, "NbOfTxsPerSts")
            _sub(per, "DtldNbOfTxs", str(n))
            _sub(per, "DtldSts", status.value)
    for e in ergebnisse:
        tx = _sub(pmt, "TxInfAndSts")
        _sub(tx, "OrgnlEndToEndId", e.end_to_end_id[:35])
        _sub(tx, "TxSts", e.status.value)
        if e.statusgrund or e.kommentar:
            rsn = _sub(tx, "StsRsnInf")
            if e.statusgrund:
                _sub(_sub(rsn, "Rsn"), "Cd", e.statusgrund[:4])
            if e.kommentar:
                _sub(rsn, "AddtlInf", e.kommentar[:105])

    return _validiere_und_serialisiere(doc)


def _validiere_und_serialisiere(doc: etree._Element) -> bytes:
    schema = _schema()
    if not schema.validate(doc):
        meldungen = "; ".join(f"Zeile {err.line}: {err.message}" for err in schema.error_log)
        raise ValueError(f"pain.002 verletzt das XSD: {meldungen}")
    return bytes(etree.tostring(doc, xml_declaration=True, encoding="UTF-8", pretty_print=True))
