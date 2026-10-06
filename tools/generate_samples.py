#!/usr/bin/env python3
"""Erzeugt die Beispiel-pain.001-Dateien unter ``samples/pain.001/``.

Alle Daten sind fiktiv. IBANs werden aus echten deutschen Bankleitzahlen generiert (gültige
Prüfziffern, konsistente BICs aus dem Bankleitzahlregister via ``schwifty``), Firmennamen sind
erfunden. Das **Fachdatum** (Buchungstag) ist fest ``2026-10-06``, damit Ausführungsdaten und
Erwartungswerte deterministisch bleiben – die Demo setzt dieses Datum über ``--fachdatum``.

Aufruf:  python tools/generate_samples.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from lxml import etree
from schwifty import IBAN

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from zv_klaerfall.iso20022 import pain001

WURZEL = Path(__file__).resolve().parents[1]
ZIEL = WURZEL / "samples" / "pain.001"
NS = pain001.NS_PAIN001
FACHDATUM = "2026-10-06"  # Dienstag
AUSFUEHRUNG = "2026-10-07"  # Mittwoch, Bankarbeitstag


def iban(bank_code: str, konto: str) -> IBAN:
    return IBAN.generate("DE", bank_code=bank_code, account_code=konto)


# Fiktive Parteien mit echten Bankleitzahlen (→ gültige IBAN-Prüfziffern, BIC aus dem Register)
DEBITOR = {
    "name": "Westfälische Maschinenbau GmbH",
    "iban": iban("40050150", "1234567"),
    "bic": "WELADED1MST",
}
KREDITOREN = {
    "stahl": {"name": "Ruhrstahl Handel GmbH", "iban": iban("37040044", "532013000"), "bic": "COBADEFFXXX"},
    "logistik": {"name": "Emsland Logistik KG", "iban": iban("12030000", "202051"), "bic": "BYLADEM1001"},
    "it": {"name": "Hafen IT Services GmbH", "iban": iban("40060560", "7654321"), "bic": "GENODEF1S08"},
    "energie": {"name": "Münsterland Energie AG", "iban": iban("50010517", "9876543"), "bic": "INGDDEFFXXX"},
}


@dataclass
class Tx:
    e2e: str
    betrag: str
    kreditor: dict
    zweck: str | None = "Rechnung"
    ccy: str = "EUR"
    iban_override: str | None = None
    bic_override: str | None = None
    name_override: str | None = None
    chrg_br: str | None = None


def _el(parent: etree._Element, tag: str, text: str | None = None, **attrs: str) -> etree._Element:
    el = etree.SubElement(parent, f"{{{NS}}}{tag}", **attrs)
    if text is not None:
        el.text = text
    return el


def baue(
    msg_id: str,
    txs: list[Tx],
    *,
    datum: str = AUSFUEHRUNG,
    nb_of_txs: int | None = None,
    ctrl_sum: str | None = None,
    mit_msg_id: bool = True,
    debitor_iban: str | None = None,
) -> bytes:
    doc = etree.Element(f"{{{NS}}}Document", nsmap={None: NS})
    cct = _el(doc, "CstmrCdtTrfInitn")
    hdr = _el(cct, "GrpHdr")
    if mit_msg_id:
        _el(hdr, "MsgId", msg_id)
    _el(hdr, "CreDtTm", f"{FACHDATUM}T08:15:00")
    _el(hdr, "NbOfTxs", str(nb_of_txs if nb_of_txs is not None else len(txs)))
    summe = sum(Decimal(t.betrag) for t in txs)
    _el(hdr, "CtrlSum", ctrl_sum if ctrl_sum is not None else f"{summe:.2f}")
    _el(_el(hdr, "InitgPty"), "Nm", DEBITOR["name"])

    pmt = _el(cct, "PmtInf")
    _el(pmt, "PmtInfId", f"{msg_id}-PI1")
    _el(pmt, "PmtMtd", "TRF")
    _el(pmt, "BtchBookg", "false")
    _el(pmt, "NbOfTxs", str(len(txs)))
    _el(pmt, "CtrlSum", f"{summe:.2f}")
    _el(_el(_el(pmt, "PmtTpInf"), "SvcLvl"), "Cd", "SEPA")
    _el(_el(pmt, "ReqdExctnDt"), "Dt", datum)
    _el(_el(pmt, "Dbtr"), "Nm", DEBITOR["name"])
    _el(_el(_el(pmt, "DbtrAcct"), "Id"), "IBAN", debitor_iban or str(DEBITOR["iban"]))
    _el(_el(_el(pmt, "DbtrAgt"), "FinInstnId"), "BICFI", DEBITOR["bic"])
    _el(pmt, "ChrgBr", "SLEV")
    for t in txs:
        tx = _el(pmt, "CdtTrfTxInf")
        _el(_el(tx, "PmtId"), "EndToEndId", t.e2e)
        _el(_el(tx, "Amt"), "InstdAmt", t.betrag, Ccy=t.ccy)
        if t.chrg_br:
            _el(tx, "ChrgBr", t.chrg_br)
        _el(_el(_el(tx, "CdtrAgt"), "FinInstnId"), "BICFI", t.bic_override or t.kreditor["bic"])
        _el(_el(tx, "Cdtr"), "Nm", t.name_override or t.kreditor["name"])
        _el(_el(_el(tx, "CdtrAcct"), "Id"), "IBAN", t.iban_override or str(t.kreditor["iban"]))
        if t.zweck:
            _el(_el(tx, "RmtInf"), "Ustrd", t.zweck)
    return etree.tostring(doc, xml_declaration=True, encoding="UTF-8", pretty_print=True)


K = KREDITOREN
SZENARIEN: list[tuple[str, bytes, str]] = [
    (
        "01_gueltig.xml",
        baue(
            "MSG-2026-1001",
            [
                Tx("E2E-1001-1", "1250.00", K["stahl"], "RE 2026-4471"),
                Tx("E2E-1001-2", "480.50", K["logistik"], "Fracht KW40"),
                Tx("E2E-1001-3", "9990.00", K["it"], "Wartung Q4"),
            ],
        ),
        "Drei fehlerfreie Zahlungen → alle ACCP, Gruppenstatus ACCP",
    ),
    (
        "02_gebuehrenregelung.xml",
        baue("MSG-2026-1002", [Tx("E2E-1002-1", "310.00", K["stahl"], "RE 2026-4480", chrg_br="DEBT")]),
        "ChrgBr = DEBT bei SEPA (Rulebook: SLEV) → GEBUEHRENREGELUNG → automatische Korrektur → ACCP",
    ),
    (
        "03_ausfuehrung_wochenende.xml",
        baue(
            "MSG-2026-1003",
            [Tx("E2E-1003-1", "2200.00", K["energie"], "Abschlag Oktober")],
            datum="2026-10-10",
        ),
        "Ausführungsdatum Samstag → AUSFUEHRUNGSDATUM → automatisch auf Montag 2026-10-12 verschoben → ACCP",
    ),
    (
        "04_betrag_limit.xml",
        baue("MSG-2026-1004", [Tx("E2E-1004-1", "75000.00", K["stahl"], "Anzahlung Presse")]),
        "75.000 EUR ≥ Limit → BETRAG_LIMIT → Vier-Augen-Freigabe durch Disposition (User Task) → ACCP",
    ),
    (
        "05_grossbetrag.xml",
        baue("MSG-2026-1005", [Tx("E2E-1005-1", "1250000.00", K["logistik"], "Kauf Lagerhalle Hafen")]),
        "1,25 Mio EUR → BETRAG_LIMIT, Priorität HOCH → Leitung Disposition (User Task) → ACCP",
    ),
    (
        "06_sanktion.xml",
        baue(
            "MSG-2026-1006",
            [Tx("E2E-1006-1", "18400.00", K["it"], "Consulting", name_override="Nordfall Trading Limited")],
        ),
        "Kreditorname ähnelt Sanktionsliste → SANKTION → Compliance (User Task) → RJCT RR04",
    ),
    (
        "07_iban_pruefziffer.xml",
        baue(
            "MSG-2026-1007",
            [
                Tx(
                    "E2E-1007-1",
                    "640.00",
                    K["stahl"],
                    "RE 2026-4490",
                    iban_override=str(K["stahl"]["iban"])[:-1]
                    + ("1" if str(K["stahl"]["iban"])[-1] != "1" else "2"),
                )
            ],
        ),
        "IBAN-Prüfziffer falsch → IBAN_PRUEFZIFFER → Rückfrage (User Task) → RJCT AC01",
    ),
    (
        "08_bic_passt_nicht.xml",
        baue(
            "MSG-2026-1008",
            [Tx("E2E-1008-1", "999.99", K["energie"], "Abschlag", bic_override="COBADEFFXXX")],
        ),
        "BIC der Commerzbank zu ING-IBAN → BIC_IBAN_MISMATCH → Rückfrage (User Task) → RJCT RC01",
    ),
    (
        "09_waehrung_usd.xml",
        baue("MSG-2026-1009", [Tx("E2E-1009-1", "5000.00", K["logistik"], "Seefracht", ccy="USD")]),
        "USD mit Service Level SEPA → WAEHRUNG → Rückfrage (User Task) → RJCT AM11",
    ),
    (
        "10_dublette.xml",
        baue("MSG-2026-1010", [Tx("E2E-1001-1", "1250.00", K["stahl"], "RE 2026-4471")]),
        "Identisch zu Zahlung 1 aus 01_gueltig.xml → DUBLETTE → Zahlungsverkehr (User Task) → RJCT DUPL",
    ),
    (
        "11_schemafehler.xml",
        baue("MSG-2026-1011", [Tx("E2E-1011-1", "100.00", K["stahl"])], mit_msg_id=False),
        "GrpHdr/MsgId fehlt → XSD-Verletzung → Datei zurückgewiesen, pain.002 GrpSts RJCT FF01",
    ),
    (
        "12_kontrollsumme.xml",
        baue(
            "MSG-2026-1012",
            [Tx("E2E-1012-1", "100.00", K["stahl"]), Tx("E2E-1012-2", "200.00", K["it"])],
            ctrl_sum="999.00",
        ),
        "CtrlSum ≠ Summe der Beträge → Datei zurückgewiesen, pain.002 GrpSts RJCT AM10",
    ),
    (
        "13_gemischt.xml",
        baue(
            "MSG-2026-1013",
            [
                Tx("E2E-1013-1", "150.00", K["stahl"], "RE 2026-4501"),
                Tx("E2E-1013-2", "80000.00", K["logistik"], "Fuhrpark"),
                Tx("E2E-1013-3", "275.00", K["it"], "Lizenzen", chrg_br="SHAR"),
                Tx(
                    "E2E-1013-4",
                    "410.00",
                    K["energie"],
                    "Strom",
                    iban_override=str(K["energie"]["iban"])[:-2] + "00",
                ),
            ],
        ),
        "Vier Zahlungen: gültig, Limit (Freigabe), Formatkorrektur, falsche Prüfziffer → Gruppenstatus PART",
    ),
]


def main() -> None:
    ZIEL.mkdir(parents=True, exist_ok=True)
    for alt in ZIEL.glob("*.xml"):
        alt.unlink()
    zeilen = [
        "# Beispiel-Nachrichten (pain.001.001.09)",
        "",
        f"Fachdatum (Buchungstag) für die Demo: **{FACHDATUM}**. Alle Daten sind fiktiv; IBANs sind mit",
        "echten deutschen Bankleitzahlen generiert, damit Prüfziffern und BIC-Abgleich realistisch sind.",
        "",
        "| Datei | Szenario und erwartetes Ergebnis |",
        "|---|---|",
    ]
    for name, xml, beschreibung in SZENARIEN:
        (ZIEL / name).write_bytes(xml)
        try:
            pain001.validiere_schema(xml)
            status = "XSD ✓"
        except pain001.SchemaFehler:
            status = "XSD ✗ (gewollt)"
        zeilen.append(f"| `{name}` | {beschreibung} ({status}) |")
        print(f"{name:32s} {status}")
    (ZIEL / "fachdatum.txt").write_text(FACHDATUM + "\n", encoding="utf-8")
    (ZIEL / "README.md").write_text("\n".join(zeilen) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
