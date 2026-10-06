"""Job-Handler einzeln und der komplette Offline-Durchlauf über alle Beispieldateien."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from lxml import etree

from tests.conftest import sample
from zv_klaerfall import handlers
from zv_klaerfall.config import Dienste
from zv_klaerfall.handlers import JobKontext
from zv_klaerfall.iso20022 import pain002
from zv_klaerfall.models import Klassifikation, Pruefergebnis
from zv_klaerfall.pipeline.offline import Entscheidung, OfflinePipeline


def ctx(fachdatum: date) -> JobKontext:
    return JobKontext(prozess_id="test", instanz_key="t1", heute=fachdatum)


# --- Handler ------------------------------------------------------------------------------------


def test_nachricht_validieren_teilt_auf(dienste: Dienste, fachdatum: date) -> None:
    v = handlers.nachricht_validieren(
        {"nachrichtXml": sample("01_gueltig.xml").decode(), "dateiname": "01.xml"}, dienste, ctx(fachdatum)
    )
    assert v["nachrichtGueltig"] and len(v["zahlungen"]) == 3
    assert v["nachricht"]["msgId"] == "MSG-2026-1001" and v["nachricht"]["pmtInfId"] == "MSG-2026-1001-PI1"
    assert dienste.audit.lese()[-1]["ereignis"] == "NACHRICHT_ANGENOMMEN"


def test_nachricht_validieren_schemafehler_mit_lenienter_msgid(dienste: Dienste, fachdatum: date) -> None:
    xml = sample("01_gueltig.xml").decode().replace("<NbOfTxs>3</NbOfTxs>", "<NbOfTxs>drei</NbOfTxs>", 1)
    v = handlers.nachricht_validieren({"nachrichtXml": xml, "dateiname": "x.xml"}, dienste, ctx(fachdatum))
    assert not v["nachrichtGueltig"] and v["nachrichtFehlerCode"] == "FF01"
    assert v["nachricht"]["msgId"] == "MSG-2026-1001" and v["zahlungen"] == []


def test_nachricht_validieren_kopfdaten(dienste: Dienste, fachdatum: date) -> None:
    v = handlers.nachricht_validieren(
        {"nachrichtXml": sample("12_kontrollsumme.xml").decode()}, dienste, ctx(fachdatum)
    )
    assert not v["nachrichtGueltig"] and v["nachrichtFehlerCode"] == "AM10"
    xml = sample("01_gueltig.xml").decode().replace("<NbOfTxs>3</NbOfTxs>", "<NbOfTxs>2</NbOfTxs>", 1)
    v = handlers.nachricht_validieren({"nachrichtXml": xml}, dienste, ctx(fachdatum))
    assert (
        not v["nachrichtGueltig"]
        and v["nachrichtFehlerCode"] == "NARR"
        and "NbOfTxs" in v["nachrichtFehler"][0]
    )


def test_nachricht_validieren_ohne_xml(dienste: Dienste, fachdatum: date) -> None:
    with pytest.raises(ValueError, match="nachrichtXml"):
        handlers.nachricht_validieren({}, dienste, ctx(fachdatum))


def test_pruefen_liefert_dmn_eingaben(dienste: Dienste, fachdatum: date) -> None:
    zahlungen = handlers.nachricht_validieren(
        {"nachrichtXml": sample("04_betrag_limit.xml").decode()}, dienste, ctx(fachdatum)
    )["zahlungen"]
    v = handlers.pruefen({"zahlung": zahlungen[0]}, dienste, ctx(fachdatum))
    assert v["befundeVorhanden"] and v["befundCode"] == "BETRAG_LIMIT" and v["betrag"] == 75000.0
    assert (
        v["sanktionstreffer"] is False and v["nurAutoKorrigierbar"] is False and v["erneutePruefung"] is False
    )
    assert "BETRAG_LIMIT" in v["befundeText"] and "75.000,00 EUR" in v["zahlungText"]
    Pruefergebnis.model_validate(v["pruefung"])  # Variable ist wieder einlesbar


def test_pruefen_betragslimit_aus_task_header(dienste: Dienste, fachdatum: date) -> None:
    zahlungen = handlers.nachricht_validieren(
        {"nachrichtXml": sample("04_betrag_limit.xml").decode()}, dienste, ctx(fachdatum)
    )["zahlungen"]
    k = ctx(fachdatum)
    k.header = {"betragslimit": "100000.00"}
    assert not handlers.pruefen({"zahlung": zahlungen[0]}, dienste, k)["befundeVorhanden"]


def test_korrigieren_setzt_erneute_pruefung_und_dublette_entfaellt(dienste: Dienste, fachdatum: date) -> None:
    zahlungen = handlers.nachricht_validieren(
        {"nachrichtXml": sample("02_gebuehrenregelung.xml").decode()}, dienste, ctx(fachdatum)
    )["zahlungen"]
    v: dict = {"zahlung": zahlungen[0]}
    v.update(handlers.pruefen(v, dienste, ctx(fachdatum)))
    assert v["befundCode"] == "GEBUEHRENREGELUNG" and v["nurAutoKorrigierbar"]
    v.update(handlers.korrigieren(v, dienste, ctx(fachdatum)))
    assert v["erneutePruefung"] is True and v["zahlung"]["chargeBearer"] == "SLEV" and v["korrekturen"]
    v.update(handlers.pruefen(v, dienste, ctx(fachdatum)))
    assert not v["befundeVorhanden"], "zweite Prüfung darf keine Dublette melden"


def test_freigeben_und_ablehnen(dienste: Dienste, fachdatum: date) -> None:
    zahlungen = handlers.nachricht_validieren(
        {"nachrichtXml": sample("07_iban_pruefziffer.xml").decode()}, dienste, ctx(fachdatum)
    )["zahlungen"]
    v: dict = {"zahlung": zahlungen[0]}
    v.update(handlers.pruefen(v, dienste, ctx(fachdatum)))
    abgelehnt = handlers.ablehnen(
        {**v, "bearbeiter": "jk", "kommentar": "Bitte IBAN prüfen"}, dienste, ctx(fachdatum)
    )
    assert abgelehnt["ergebnis"] == {
        "endToEndId": "E2E-1007-1",
        "status": "RJCT",
        "statusgrund": "AC01",
        "kommentar": "Bitte IBAN prüfen",
        "korrekturen": [],
    }
    assert dienste.audit.lese()[-1]["akteur"] == "jk"
    freigegeben = handlers.freigeben({**v, "bearbeiter": "disp"}, dienste, ctx(fachdatum))
    assert freigegeben["ergebnis"]["status"] == "ACCP" and freigegeben["buchungsId"].startswith("BK-")
    assert dienste.buchungsjournal.read_text().count("\n") == 1


def test_statusreport_schreibt_xsd_gueltige_datei(dienste: Dienste, fachdatum: date) -> None:
    v = handlers.statusreport(
        {
            "nachricht": {"msgId": "MSG-X", "pmtInfId": "MSG-X-PI1"},
            "ergebnisse": [{"endToEndId": "A", "status": "ACCP"}],
        },
        dienste,
        ctx(fachdatum),
    )
    pfad = Path(v["statusreportPfad"])
    assert pfad.exists() and v["gruppenstatus"] == "ACCP"
    pain002._schema().assertValid(etree.parse(str(pfad)))


# --- Offline-Pipeline über alle Beispiele ---------------------------------------------------------

ERWARTET: dict[str, dict[str, str] | str] = {
    "01_gueltig.xml": {"E2E-1001-1": "ACCP", "E2E-1001-2": "ACCP", "E2E-1001-3": "ACCP"},
    "02_gebuehrenregelung.xml": {"E2E-1002-1": "ACCP"},
    "03_ausfuehrung_wochenende.xml": {"E2E-1003-1": "ACCP"},
    "04_betrag_limit.xml": {"E2E-1004-1": "ACCP"},
    "05_grossbetrag.xml": {"E2E-1005-1": "ACCP"},
    "06_sanktion.xml": {"E2E-1006-1": "RJCT"},
    "07_iban_pruefziffer.xml": {"E2E-1007-1": "RJCT"},
    "08_bic_passt_nicht.xml": {"E2E-1008-1": "RJCT"},
    "09_waehrung_usd.xml": {"E2E-1009-1": "RJCT"},
    "10_dublette.xml": {"E2E-1001-1": "RJCT"},
    "11_schemafehler.xml": "RJCT",
    "12_kontrollsumme.xml": "RJCT",
    "13_gemischt.xml": {
        "E2E-1013-1": "ACCP",
        "E2E-1013-2": "ACCP",
        "E2E-1013-3": "ACCP",
        "E2E-1013-4": "RJCT",
    },
}


def test_offline_durchlauf_aller_beispiele(
    dienste: Dienste, samples: Path, bpmn_dir: Path, fachdatum: date
) -> None:
    pipeline = OfflinePipeline(dienste, bpmn_dir / "klaerfall-klassifikation.dmn", heute=fachdatum)
    laeufe = {p.name: pipeline.verarbeite_datei(p) for p in sorted(samples.glob("*.xml"))}
    assert set(laeufe) == set(ERWARTET)
    for name, erwartung in ERWARTET.items():
        lauf = laeufe[name]
        if isinstance(erwartung, str):
            assert not lauf.nachricht_gueltig and lauf.gruppenstatus == erwartung, name
        else:
            assert lauf.nachricht_gueltig, name
            assert {z.end_to_end_id: z.status for z in lauf.zahlungen} == erwartung, name
        assert lauf.statusreport_pfad and Path(lauf.statusreport_pfad).exists()
        pain002._schema().assertValid(etree.parse(lauf.statusreport_pfad))

    gemischt = laeufe["13_gemischt.xml"]
    assert gemischt.gruppenstatus == "PART"
    korrigiert = next(z for z in gemischt.zahlungen if z.end_to_end_id == "E2E-1013-3")
    assert korrigiert.befunde == ["GEBUEHRENREGELUNG"] and korrigiert.korrekturen == ["ChrgBr: SHAR → SLEV"]
    assert [s.element for s in korrigiert.schritte] == [
        "Zahlung fachlich prüfen",
        "Klärfall klassifizieren",
        "Automatisch korrigieren",
        "Zahlung fachlich prüfen",
        "Zahlung freigeben",
    ]
    sanktion = laeufe["06_sanktion.xml"].zahlungen[0]
    assert sanktion.kategorie == "Compliance" and sanktion.ziel_team == "team-compliance"
    assert laeufe["05_grossbetrag.xml"].zahlungen[0].ziel_team == "team-disposition-leitung"
    assert dienste.audit.verifiziere().gueltig


def test_offline_eigener_entscheider_und_sla(
    dienste: Dienste, samples: Path, bpmn_dir: Path, fachdatum: date
) -> None:
    gesehen: list[Klassifikation] = []

    def entscheider(k: Klassifikation, _p: Pruefergebnis, _v: dict) -> Entscheidung:
        gesehen.append(k)
        return Entscheidung("FREIGEBEN", "tester", "manuell geprüft")

    pipeline = OfflinePipeline(
        dienste,
        bpmn_dir / "klaerfall-klassifikation.dmn",
        entscheider=entscheider,
        heute=fachdatum,
        sla_simulieren=True,
    )
    lauf = pipeline.verarbeite_datei(samples / "06_sanktion.xml")
    z = lauf.zahlungen[0]
    assert z.status == "ACCP" and z.eskaliert and gesehen[0].kategorie == "Compliance"
    ereignisse = [e["ereignis"] for e in dienste.audit.lese()]
    assert "KLAERFALL_ESKALIERT" in ereignisse and ereignisse[-1] == "STATUSREPORT_ERSTELLT"
