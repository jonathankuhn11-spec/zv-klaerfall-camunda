"""pain.001 einlesen, pain.002 erzeugen – jeweils gegen das offizielle XSD."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from lxml import etree

from tests.conftest import sample
from zv_klaerfall.iso20022 import pain001, pain002
from zv_klaerfall.models import Ergebnis, TxStatus


def test_parse_gueltige_nachricht() -> None:
    n = pain001.parse(sample("01_gueltig.xml"), quelle="01_gueltig.xml")
    assert n.kopf.msg_id == "MSG-2026-1001"
    assert n.kopf.anzahl_transaktionen == 3
    assert n.kopf.kontrollsumme == Decimal("11720.50")
    assert n.kopf.einreicher == "Westfälische Maschinenbau GmbH"
    assert len(n.zahlungen) == 3
    z = n.zahlungen[0]
    assert z.end_to_end_id == "E2E-1001-1"
    assert z.betrag == Decimal("1250.00") and z.waehrung == "EUR"
    assert z.ausfuehrungsdatum == date(2026, 10, 7)
    assert z.service_level == "SEPA" and z.charge_bearer == "SLEV"
    assert z.debitor.iban == "DE06400501500001234567" and z.debitor.bic == "WELADED1MST"
    assert z.kreditor.name == "Ruhrstahl Handel GmbH" and z.kreditor.bic == "COBADEFFXXX"
    assert z.verwendungszweck == "RE 2026-4471"
    assert z.quelle == "01_gueltig.xml"


def test_chrgbr_auf_transaktionsebene_ueberschreibt_pmtinf() -> None:
    n = pain001.parse(sample("02_gebuehrenregelung.xml"))
    assert n.zahlungen[0].charge_bearer == "DEBT"


def test_variablen_roundtrip() -> None:
    z = pain001.parse(sample("01_gueltig.xml")).zahlungen[1]
    var = z.als_variablen()
    assert var["endToEndId"] == "E2E-1001-2" and isinstance(var["betrag"], float)
    assert var["ausfuehrungsdatum"] == "2026-10-07"
    zurueck = type(z).aus_variablen(var)
    assert zurueck.betrag == Decimal("480.50") and zurueck.kreditor == z.kreditor


def test_schemafehler_liefert_zeilen_und_meldungen() -> None:
    with pytest.raises(pain001.SchemaFehler) as exc:
        pain001.parse(sample("11_schemafehler.xml"))
    assert exc.value.fehler and "Zeile" in exc.value.fehler[0]


def test_nicht_wohlgeformt_und_falscher_namespace() -> None:
    with pytest.raises(pain001.SchemaFehler, match="wohlgeformt"):
        pain001.parse(b"<Document><Kaputt>")
    with pytest.raises(pain001.SchemaFehler, match="Namespace"):
        pain001.parse(b'<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"/>')


def test_xxe_wird_nicht_aufgeloest(tmp_path) -> None:  # type: ignore[no-untyped-def]
    geheim = tmp_path / "geheim.txt"
    geheim.write_text("streng geheim")
    xml = (
        f'<!DOCTYPE d [<!ENTITY x SYSTEM "file://{geheim}">]>'
        f'<Document xmlns="{pain001.NS_PAIN001}">&x;</Document>'
    )
    with pytest.raises(pain001.SchemaFehler):
        pain001.parse(xml.encode())


def _report(ergebnisse: list[Ergebnis], **kw) -> etree._Element:  # type: ignore[no-untyped-def]
    xml = pain002.erzeuge_statusreport(
        orgnl_msg_id="MSG-1",
        orgnl_pmt_inf_id="MSG-1-PI1",
        ergebnisse=ergebnisse,
        report_msg_id="RPT-MSG-1",
        erstellt_am=datetime(2026, 10, 6, 9, tzinfo=UTC),
        **kw,
    )
    return etree.fromstring(xml)


def test_statusreport_part_mit_statusgruenden() -> None:
    doc = _report(
        [
            Ergebnis(endToEndId="A", status=TxStatus.ACCP),
            Ergebnis(endToEndId="B", status=TxStatus.RJCT, statusgrund="AC01", kommentar="Prüfziffer falsch"),
        ]
    )
    ns = {"p": pain002.NS_PAIN002}
    assert doc.findtext(".//p:OrgnlGrpInfAndSts/p:GrpSts", namespaces=ns) == "PART"
    assert doc.findtext(".//p:OrgnlGrpInfAndSts/p:OrgnlNbOfTxs", namespaces=ns) == "2"
    per = {
        e.findtext("p:DtldSts", namespaces=ns): e.findtext("p:DtldNbOfTxs", namespaces=ns)
        for e in doc.findall(".//p:NbOfTxsPerSts", namespaces=ns)
    }
    assert per == {"ACCP": "1", "RJCT": "1"}
    tx_b = doc.findall(".//p:TxInfAndSts", namespaces=ns)[1]
    assert tx_b.findtext("p:TxSts", namespaces=ns) == "RJCT"
    assert tx_b.findtext("p:StsRsnInf/p:Rsn/p:Cd", namespaces=ns) == "AC01"
    assert tx_b.findtext("p:StsRsnInf/p:AddtlInf", namespaces=ns) == "Prüfziffer falsch"


def test_statusreport_gruppenzurueckweisung() -> None:
    doc = _report([], gruppen_ablehnung=("FF01", "GrpHdr/MsgId fehlt"))
    ns = {"p": pain002.NS_PAIN002}
    assert doc.findtext(".//p:GrpSts", namespaces=ns) == "RJCT"
    assert doc.findtext(".//p:OrgnlGrpInfAndSts/p:StsRsnInf/p:Rsn/p:Cd", namespaces=ns) == "FF01"
    assert doc.find(".//p:OrgnlPmtInfAndSts", namespaces=ns) is None


@pytest.mark.parametrize(
    ("stati", "erwartet"),
    [
        ([TxStatus.ACCP, TxStatus.ACCP], "ACCP"),
        ([TxStatus.RJCT], "RJCT"),
        ([], "RJCT"),
        ([TxStatus.ACCP, TxStatus.RJCT], "PART"),
        ([TxStatus.ACCP, TxStatus.PDNG], "PART"),
    ],
)
def test_gruppenstatus(stati: list[TxStatus], erwartet: str) -> None:
    assert (
        pain002.gruppenstatus([Ergebnis(endToEndId=str(i), status=s) for i, s in enumerate(stati)])
        == erwartet
    )


def test_statusreport_schneidet_zu_lange_texte_ab() -> None:
    doc = _report(
        [Ergebnis(endToEndId="A" * 60, status=TxStatus.RJCT, statusgrund="NARR", kommentar="x" * 500)]
    )
    ns = {"p": pain002.NS_PAIN002}
    assert len(doc.findtext(".//p:OrgnlEndToEndId", namespaces=ns)) == 35
    assert len(doc.findtext(".//p:AddtlInf", namespaces=ns)) == 105
