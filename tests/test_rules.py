"""Fachregeln: jede Regel einzeln, Priorisierung des führenden Befunds, Korrekturen, Hilfsmodule."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from zv_klaerfall.models import BefundCode, Partei, Schwere, Zahlung
from zv_klaerfall.rules import kalender, sanktionen, validation
from zv_klaerfall.rules.dubletten import Dublettenspeicher, fingerprint

HEUTE = date(2026, 10, 6)


def zahlung(**ueberschreibungen) -> Zahlung:  # type: ignore[no-untyped-def]
    daten = {
        "msgId": "MSG-T",
        "pmtInfId": "MSG-T-PI1",
        "endToEndId": "E2E-T-1",
        "betrag": Decimal("100.00"),
        "waehrung": "EUR",
        "ausfuehrungsdatum": date(2026, 10, 7),
        "serviceLevel": "SEPA",
        "chargeBearer": "SLEV",
        "debitor": Partei(
            name="Westfälische Maschinenbau GmbH", iban="DE06400501500001234567", bic="WELADED1MST"
        ),
        "kreditor": Partei(name="Ruhrstahl Handel GmbH", iban="DE89370400440532013000", bic="COBADEFFXXX"),
        "verwendungszweck": "RE 1",
    }
    daten.update(ueberschreibungen)
    return Zahlung.model_validate(daten)


def codes(z: Zahlung, **ctx) -> list[BefundCode]:  # type: ignore[no-untyped-def]
    return [b.code for b in validation.pruefe(z, validation.Pruefkontext(heute=HEUTE, **ctx)).befunde]


# --- Einzelregeln -------------------------------------------------------------------------------


def test_fehlerfreie_zahlung_hat_keine_befunde() -> None:
    erg = validation.pruefe(zahlung(), validation.Pruefkontext(heute=HEUTE))
    assert erg.gueltig and erg.befunde == [] and erg.fuehrender_befund is None


def test_iban_pruefziffer() -> None:
    assert codes(zahlung(kreditor=Partei(name="X", iban="DE89370400440532013001"))) == [
        BefundCode.IBAN_PRUEFZIFFER
    ]
    assert codes(zahlung(kreditor=Partei(name="X", iban=None))) == [BefundCode.IBAN_PRUEFZIFFER]
    assert BefundCode.IBAN_PRUEFZIFFER in codes(zahlung(kreditor=Partei(name="X", iban="DE1234")))


def test_bic_ungueltig_und_bic_iban_mismatch() -> None:
    assert codes(zahlung(kreditor=Partei(name="X", iban="DE89370400440532013000", bic="XX"))) == [
        BefundCode.BIC_UNGUELTIG
    ]
    befunde = validation.pruefe(
        zahlung(kreditor=Partei(name="X", iban="DE89370400440532013000", bic="BYLADEM1001")),
        validation.Pruefkontext(heute=HEUTE),
    ).befunde
    assert [b.code for b in befunde] == [BefundCode.BIC_IBAN_MISMATCH]
    assert befunde[0].korrekturwert == "COBADEFFXXX" and not befunde[0].auto_korrigierbar


def test_betrag_null_und_limit() -> None:
    assert codes(zahlung(betrag=Decimal("0"))) == [BefundCode.BETRAG_NULL]
    assert codes(zahlung(betrag=Decimal("49999.99"))) == []
    assert codes(zahlung(betrag=Decimal("50000.00"))) == [BefundCode.BETRAG_LIMIT]
    assert codes(zahlung(betrag=Decimal("50000.00")), betragslimit=Decimal("100000")) == []


def test_waehrung_nur_eur_bei_sepa() -> None:
    assert codes(zahlung(waehrung="USD")) == [BefundCode.WAEHRUNG]
    assert codes(zahlung(waehrung="USD", serviceLevel="URGP")) == []


def test_gebuehrenregelung_auto_korrigierbar() -> None:
    befunde = validation.pruefe(zahlung(chargeBearer="DEBT"), validation.Pruefkontext(heute=HEUTE)).befunde
    assert [b.code for b in befunde] == [BefundCode.GEBUEHRENREGELUNG]
    assert befunde[0].auto_korrigierbar and befunde[0].korrekturwert == "SLEV"
    assert codes(zahlung(chargeBearer=None)) == []


@pytest.mark.parametrize(
    ("datum", "erwartet_code", "erwartet_wert"),
    [
        (date(2026, 10, 10), BefundCode.AUSFUEHRUNGSDATUM, "2026-10-12"),  # Samstag → Montag
        (date(2026, 10, 2), BefundCode.AUSFUEHRUNGSDATUM, "2026-10-06"),  # Vergangenheit → heute
        (date(2026, 12, 25), BefundCode.AUSFUEHRUNGSDATUM, "2026-12-28"),  # Weihnachten (Fr) → Montag
        (date(2027, 3, 26), BefundCode.AUSFUEHRUNGSDATUM, "2027-03-30"),  # Karfreitag 2027 → Dienstag
    ],
)
def test_ausfuehrungsdatum_wird_verschoben(
    datum: date, erwartet_code: BefundCode, erwartet_wert: str
) -> None:
    befunde = validation.pruefe(
        zahlung(ausfuehrungsdatum=datum), validation.Pruefkontext(heute=HEUTE)
    ).befunde
    assert [b.code for b in befunde] == [erwartet_code]
    assert befunde[0].auto_korrigierbar and befunde[0].korrekturwert == erwartet_wert


def test_ausfuehrungsdatum_zu_weit_in_zukunft_nicht_korrigierbar() -> None:
    befunde = validation.pruefe(
        zahlung(ausfuehrungsdatum=HEUTE + timedelta(days=120)), validation.Pruefkontext(heute=HEUTE)
    ).befunde
    assert [b.code for b in befunde] == [BefundCode.AUSFUEHRUNGSDATUM] and not befunde[0].auto_korrigierbar


def test_verwendungszweck_nur_hinweis() -> None:
    erg = validation.pruefe(zahlung(verwendungszweck=None), validation.Pruefkontext(heute=HEUTE))
    assert erg.gueltig and erg.befunde[0].schwere == Schwere.INFO


def test_sanktion_fuzzy_und_normalisierung() -> None:
    assert sanktionen.normalisiere("Nordfall Trading Ltd.") == "nordfall trading"
    assert sanktionen.normalisiere("Vöstök Metallurgie GmbH") == "voestoek metallurgie"
    treffer = sanktionen.pruefe_name("NORDFALL TRADING LIMITED")
    assert treffer is not None and treffer.eintrag == "Nordfall Trading Ltd" and treffer.aehnlichkeit == 1.0
    unscharf = sanktionen.pruefe_name("Nordfal Tradng GmbH")  # Tippfehler
    assert (
        unscharf is not None
        and unscharf.eintrag == "Nordfall Trading Ltd"
        and 0.85 <= unscharf.aehnlichkeit < 1.0
    )
    assert sanktionen.pruefe_name("Ruhrstahl Handel GmbH") is None
    assert sanktionen.pruefe_name(None) is None
    assert codes(
        zahlung(kreditor=Partei(name="Nordfall Trading Limited", iban="DE89370400440532013000"))
    ) == [BefundCode.SANKTION]


def test_dublette_erkennt_wiedereinreichung_und_merkt_sich() -> None:
    speicher = Dublettenspeicher()
    z = zahlung()
    assert codes(z, dublettenspeicher=speicher) == []
    assert codes(z, dublettenspeicher=speicher) == [BefundCode.DUBLETTE]
    assert codes(zahlung(endToEndId="E2E-ANDERE"), dublettenspeicher=speicher) == []
    assert fingerprint(z) == fingerprint(
        zahlung(verwendungszweck="anderer Text")
    )  # Zweck ist nicht Teil des Fingerprints
    assert fingerprint(z) != fingerprint(zahlung(betrag=Decimal("100.01")))


def test_dublette_ausserhalb_des_zeitfensters_ist_keine() -> None:
    speicher = Dublettenspeicher(fenster=timedelta(hours=1))
    z = zahlung()
    assert speicher.pruefe_und_merke(z, jetzt=datetime(2026, 10, 6, 8)) is None
    assert speicher.pruefe_und_merke(z, jetzt=datetime(2026, 10, 6, 10)) is None
    assert speicher.pruefe_und_merke(z, jetzt=datetime(2026, 10, 6, 10, 30)) == "MSG-T"


# --- Aggregation und Korrektur ------------------------------------------------------------------


def test_fuehrender_befund_folgt_dem_rang() -> None:
    z = zahlung(
        betrag=Decimal("60000"),
        chargeBearer="DEBT",
        kreditor=Partei(name="Nordfall Trading Ltd", iban="DE89370400440532013001"),
    )
    erg = validation.pruefe(z, validation.Pruefkontext(heute=HEUTE))
    assert {b.code for b in erg.befunde} >= {
        BefundCode.SANKTION,
        BefundCode.BETRAG_LIMIT,
        BefundCode.IBAN_PRUEFZIFFER,
        BefundCode.GEBUEHRENREGELUNG,
    }
    assert (
        erg.fuehrender_befund == BefundCode.SANKTION
        and erg.sanktionstreffer
        and not erg.nur_auto_korrigierbar
    )


def test_nur_auto_korrigierbar_und_korrektur() -> None:
    z = zahlung(chargeBearer="SHAR", ausfuehrungsdatum=date(2026, 10, 11))
    erg = validation.pruefe(z, validation.Pruefkontext(heute=HEUTE))
    assert erg.nur_auto_korrigierbar and erg.fuehrender_befund == BefundCode.GEBUEHRENREGELUNG
    korrigiert, protokoll = validation.wende_korrekturen_an(z, erg.befunde)
    assert korrigiert.charge_bearer == "SLEV" and korrigiert.ausfuehrungsdatum == date(2026, 10, 12)
    assert len(protokoll) == 2
    assert validation.pruefe(korrigiert, validation.Pruefkontext(heute=HEUTE)).gueltig


# --- Kalender -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("jahr", "ostern"),
    [
        (2024, date(2024, 3, 31)),
        (2025, date(2025, 4, 20)),
        (2026, date(2026, 4, 5)),
        (2027, date(2027, 3, 28)),
    ],
)
def test_ostersonntag(jahr: int, ostern: date) -> None:
    assert kalender.ostersonntag(jahr) == ostern


def test_target2_bankarbeitstage() -> None:
    assert kalender.ist_bankarbeitstag(date(2026, 10, 6))
    assert not kalender.ist_bankarbeitstag(date(2026, 10, 10))  # Samstag
    assert not kalender.ist_bankarbeitstag(date(2026, 4, 3))  # Karfreitag 2026
    assert not kalender.ist_bankarbeitstag(date(2026, 4, 6))  # Ostermontag 2026
    assert not kalender.ist_bankarbeitstag(date(2026, 5, 1))
    assert kalender.ist_bankarbeitstag(
        date(2028, 10, 3)
    )  # Tag der Deutschen Einheit ist kein TARGET2-Feiertag
    assert kalender.naechster_bankarbeitstag(date(2026, 12, 25)) == date(2026, 12, 28)
