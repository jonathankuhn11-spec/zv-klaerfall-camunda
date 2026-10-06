"""DMN-Tabelle (gegen die echte Datei) und revisionssicherer Audit-Trail."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zv_klaerfall.audit.trail import AuditTrail
from zv_klaerfall.rules import dmn_lite


@pytest.fixture
def tabelle(bpmn_dir: Path) -> dmn_lite.Entscheidungstabelle:
    return dmn_lite.lade(bpmn_dir / "klaerfall-klassifikation.dmn", "klaerfall-klassifikation")


def test_dmn_struktur(tabelle: dmn_lite.Entscheidungstabelle) -> None:
    assert tabelle.hit_policy == "FIRST"
    assert tabelle.eingabe_variablen == ("befundCode", "betrag", "sanktionstreffer", "nurAutoKorrigierbar")
    assert len(tabelle.regeln) == 8
    assert all(r.beschreibung for r in tabelle.regeln), "jede Regel braucht eine fachliche Begründung"
    assert set(tabelle.regeln[0].ausgaben) == {"kategorie", "prioritaet", "zielTeam", "autoKorrektur", "sla"}


@pytest.mark.parametrize(
    ("variablen", "kategorie", "team", "auto", "sla"),
    [
        (
            {
                "befundCode": "BETRAG_LIMIT",
                "betrag": 75000,
                "sanktionstreffer": True,
                "nurAutoKorrigierbar": False,
            },
            "Compliance",
            "team-compliance",
            False,
            "PT2H",
        ),  # Sanktion schlägt alles
        (
            {"befundCode": "DUBLETTE", "betrag": 10, "sanktionstreffer": False, "nurAutoKorrigierbar": False},
            "Dublette",
            "team-zahlungsverkehr",
            False,
            "PT4H",
        ),
        (
            {
                "befundCode": "BETRAG_LIMIT",
                "betrag": 1250000,
                "sanktionstreffer": False,
                "nurAutoKorrigierbar": False,
            },
            "Freigabe",
            "team-disposition-leitung",
            False,
            "PT4H",
        ),
        (
            {
                "befundCode": "BETRAG_LIMIT",
                "betrag": 499999.99,
                "sanktionstreffer": False,
                "nurAutoKorrigierbar": False,
            },
            "Freigabe",
            "team-disposition",
            False,
            "PT8H",
        ),
        (
            {
                "befundCode": "GEBUEHRENREGELUNG",
                "betrag": 10,
                "sanktionstreffer": False,
                "nurAutoKorrigierbar": True,
            },
            "Formatkorrektur",
            "team-zahlungsverkehr",
            True,
            "PT24H",
        ),
        (
            {
                "befundCode": "AUSFUEHRUNGSDATUM",
                "betrag": 10,
                "sanktionstreffer": False,
                "nurAutoKorrigierbar": True,
            },
            "Formatkorrektur",
            "team-zahlungsverkehr",
            True,
            "PT24H",
        ),
        (
            {
                "befundCode": "AUSFUEHRUNGSDATUM",
                "betrag": 10,
                "sanktionstreffer": False,
                "nurAutoKorrigierbar": False,
            },
            "Auftragsdaten",
            "team-zahlungsverkehr",
            False,
            "PT8H",
        ),  # zu weit in der Zukunft: nicht korrigierbar
        (
            {
                "befundCode": "BIC_IBAN_MISMATCH",
                "betrag": 10,
                "sanktionstreffer": False,
                "nurAutoKorrigierbar": False,
            },
            "Kontodaten",
            "team-zahlungsverkehr",
            False,
            "PT8H",
        ),
        (
            {"befundCode": "WAEHRUNG", "betrag": 10, "sanktionstreffer": False, "nurAutoKorrigierbar": False},
            "Auftragsdaten",
            "team-zahlungsverkehr",
            False,
            "PT8H",
        ),
        (
            {
                "befundCode": "VERWENDUNGSZWECK_FEHLT",
                "betrag": 10,
                "sanktionstreffer": False,
                "nurAutoKorrigierbar": False,
            },
            "Sonstige",
            "team-zahlungsverkehr",
            False,
            "PT24H",
        ),
        (
            {"befundCode": None, "betrag": None, "sanktionstreffer": False, "nurAutoKorrigierbar": False},
            "Sonstige",
            "team-zahlungsverkehr",
            False,
            "PT24H",
        ),  # Auffangregel auch bei fehlenden Werten
    ],
)
def test_dmn_klassifikation(
    tabelle: dmn_lite.Entscheidungstabelle, variablen: dict, kategorie: str, team: str, auto: bool, sla: str
) -> None:
    erg = dmn_lite.werte_aus(tabelle, variablen)
    assert erg is not None
    assert (erg["kategorie"], erg["zielTeam"], erg["autoKorrektur"], erg["sla"]) == (
        kategorie,
        team,
        auto,
        sla,
    )


def test_dmn_lite_feel_teilmenge() -> None:
    assert dmn_lite._passt("-", "x") and dmn_lite._passt("", None)
    assert dmn_lite._passt('"A","B"', "B") and not dmn_lite._passt('"A","B"', "C")
    assert dmn_lite._passt('not("A")', "B") and not dmn_lite._passt('not("A")', "A")
    assert dmn_lite._passt("true", True) and not dmn_lite._passt("true", "true")
    assert dmn_lite._passt(">= 500000", 500000) and not dmn_lite._passt("< 5", 5)
    assert dmn_lite._passt("[1000..5000]", 5000) and not dmn_lite._passt("]1000..5000[", 5000)
    assert not dmn_lite._passt(">= 1", None)
    with pytest.raises(ValueError, match="Nicht unterstützt"):
        dmn_lite._passt("date(2026)", "x")


# --- Audit-Trail ---------------------------------------------------------------------------------


def test_audit_kette_und_manipulationserkennung(tmp_path: Path) -> None:
    trail = AuditTrail(tmp_path / "audit.jsonl")
    assert trail.verifiziere().eintraege == 0 and trail.verifiziere().gueltig
    for i in range(5):
        trail.schreibe("TEST", prozess="p", referenz=f"r{i}", daten={"i": i}, akteur="pytest")
    bericht = trail.verifiziere()
    assert bericht.gueltig and bericht.eintraege == 5
    eintraege = trail.lese()
    assert eintraege[0]["prev"] == "0" * 64
    assert all(eintraege[i]["prev"] == eintraege[i - 1]["hash"] for i in range(1, 5))

    # Manipulation: Inhalt der dritten Zeile ändern
    zeilen = trail.pfad.read_text(encoding="utf-8").splitlines()
    z = json.loads(zeilen[2])
    z["daten"]["i"] = 99
    zeilen[2] = json.dumps(z, ensure_ascii=False, sort_keys=True)
    trail.pfad.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    bericht = trail.verifiziere()
    assert not bericht.gueltig and any("Eintrag 3" in f and "verändert" in f for f in bericht.fehler)

    # Manipulation: Zeile löschen
    del zeilen[1]
    trail.pfad.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    bericht = trail.verifiziere()
    assert not bericht.gueltig and any("Kette unterbrochen" in f for f in bericht.fehler)


def test_audit_anhaengen_an_bestehende_datei(tmp_path: Path) -> None:
    pfad = tmp_path / "audit.jsonl"
    AuditTrail(pfad).schreibe("A", prozess="p", referenz="1")
    zweiter = AuditTrail(pfad)  # neue Instanz (z. B. nach Neustart) muss den letzten Hash finden
    zweiter.schreibe("B", prozess="p", referenz="2")
    assert zweiter.verifiziere().gueltig and zweiter.verifiziere().eintraege == 2
