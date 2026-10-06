"""Domänenmodelle der Zahlungsauftragsprüfung.

Alle Modelle sind JSON-serialisierbar (``model_dump(mode="json", by_alias=True)``), weil sie
1:1 als Prozessvariablen in Camunda abgelegt werden. Feldnamen sind bewusst deutsch gehalten,
damit Fachbereich, BPMN-Modell, DMN-Tabelle und Code dasselbe Vokabular sprechen.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class Schwere(StrEnum):
    """Schweregrad eines Befunds. Nur BLOCKIEREND führt in die Klärfallbearbeitung."""

    INFO = "INFO"
    BLOCKIEREND = "BLOCKIEREND"


class BefundCode(StrEnum):
    """Fachliche Befundcodes (Vokabular der DMN-Tabelle ``klaerfall-klassifikation``)."""

    SANKTION = "SANKTION"
    BETRAG_LIMIT = "BETRAG_LIMIT"
    DUBLETTE = "DUBLETTE"
    IBAN_PRUEFZIFFER = "IBAN_PRUEFZIFFER"
    BIC_UNGUELTIG = "BIC_UNGUELTIG"
    BIC_IBAN_MISMATCH = "BIC_IBAN_MISMATCH"
    WAEHRUNG = "WAEHRUNG"
    BETRAG_NULL = "BETRAG_NULL"
    GEBUEHRENREGELUNG = "GEBUEHRENREGELUNG"
    AUSFUEHRUNGSDATUM = "AUSFUEHRUNGSDATUM"
    VERWENDUNGSZWECK_FEHLT = "VERWENDUNGSZWECK_FEHLT"


# Reihenfolge = Priorität bei der Wahl des führenden Befunds (höchste zuerst).
BEFUND_RANG: dict[BefundCode, int] = {
    BefundCode.SANKTION: 100,
    BefundCode.BETRAG_LIMIT: 90,
    BefundCode.DUBLETTE: 80,
    BefundCode.IBAN_PRUEFZIFFER: 70,
    BefundCode.BIC_IBAN_MISMATCH: 65,
    BefundCode.BIC_UNGUELTIG: 60,
    BefundCode.WAEHRUNG: 50,
    BefundCode.BETRAG_NULL: 40,
    BefundCode.GEBUEHRENREGELUNG: 30,
    BefundCode.AUSFUEHRUNGSDATUM: 20,
    BefundCode.VERWENDUNGSZWECK_FEHLT: 10,
}

# ISO-20022-Statusgründe (ExternalStatusReason1Code) für die Rückmeldung im pain.002.
STATUSGRUND: dict[BefundCode, str] = {
    BefundCode.SANKTION: "RR04",  # Regulatory Reason
    BefundCode.BETRAG_LIMIT: "AM02",  # Not Allowed Amount
    BefundCode.DUBLETTE: "DUPL",  # Duplicate Payment
    BefundCode.IBAN_PRUEFZIFFER: "AC01",  # Incorrect Account Number
    BefundCode.BIC_IBAN_MISMATCH: "RC01",  # Bank Identifier Incorrect
    BefundCode.BIC_UNGUELTIG: "RC01",
    BefundCode.WAEHRUNG: "AM11",  # Invalid Transaction Currency
    BefundCode.BETRAG_NULL: "AM01",  # Zero Amount
    BefundCode.GEBUEHRENREGELUNG: "CH16",  # Element Content Formally Incorrect
    BefundCode.AUSFUEHRUNGSDATUM: "DT01",  # Invalid Date
    BefundCode.VERWENDUNGSZWECK_FEHLT: "NARR",  # Narrative
}


def betrag_de(wert: Decimal | float, waehrung: str | None = None) -> str:
    """Deutsche Betragsschreibweise: 75.000,00 (optional mit Währung)."""
    text = f"{Decimal(str(wert)):,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"{text} {waehrung}" if waehrung else text


class Partei(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    iban: str | None = None
    bic: str | None = None


class Zahlung(BaseModel):
    """Eine einzelne Überweisung (``CdtTrfTxInf``) inklusive Kontext aus ``PmtInf``/``GrpHdr``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    msg_id: str = Field(alias="msgId")
    pmt_inf_id: str = Field(alias="pmtInfId")
    end_to_end_id: str = Field(alias="endToEndId")
    instr_id: str | None = Field(default=None, alias="instrId")
    betrag: Decimal
    waehrung: str
    ausfuehrungsdatum: date
    service_level: str | None = Field(default=None, alias="serviceLevel")
    local_instrument: str | None = Field(default=None, alias="localInstrument")
    charge_bearer: str | None = Field(default=None, alias="chargeBearer")
    debitor: Partei
    kreditor: Partei
    verwendungszweck: str | None = None
    purpose_code: str | None = Field(default=None, alias="purposeCode")
    quelle: str | None = None

    @field_serializer("betrag")
    def _ser_betrag(self, v: Decimal) -> float:
        return float(v)

    @field_serializer("ausfuehrungsdatum")
    def _ser_datum(self, v: date) -> str:
        return v.isoformat()

    def als_variablen(self) -> dict[str, Any]:
        """Camunda-Variablenrepräsentation (camelCase, JSON-kompatibel)."""
        return self.model_dump(mode="json", by_alias=True)

    @classmethod
    def aus_variablen(cls, daten: dict[str, Any]) -> Zahlung:
        return cls.model_validate(daten)


class Befund(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    code: BefundCode
    schwere: Schwere
    feld: str
    meldung: str
    auto_korrigierbar: bool = Field(default=False, alias="autoKorrigierbar")
    korrekturwert: str | None = None


class Pruefergebnis(BaseModel):
    """Ergebnis der fachlichen Prüfung – wird als Variable ``pruefung`` abgelegt."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    befunde: list[Befund] = Field(default_factory=list)
    gueltig: bool = True
    fuehrender_befund: BefundCode | None = Field(default=None, alias="fuehrenderBefund")
    sanktionstreffer: bool = False
    nur_auto_korrigierbar: bool = Field(default=False, alias="nurAutoKorrigierbar")
    geprueft_am: datetime = Field(default_factory=datetime.now, alias="geprueftAm")

    @field_serializer("geprueft_am")
    def _ser_dt(self, v: datetime) -> str:
        return v.isoformat(timespec="seconds")

    @classmethod
    def aus_befunden(cls, befunde: list[Befund]) -> Pruefergebnis:
        blockierend = [b for b in befunde if b.schwere == Schwere.BLOCKIEREND]
        fuehrend = max(blockierend, key=lambda b: BEFUND_RANG[b.code], default=None)
        return cls(
            befunde=befunde,
            gueltig=not blockierend,
            fuehrenderBefund=fuehrend.code if fuehrend else None,
            sanktionstreffer=any(b.code == BefundCode.SANKTION for b in blockierend),
            nurAutoKorrigierbar=bool(blockierend) and all(b.auto_korrigierbar for b in blockierend),
        )


class Klassifikation(BaseModel):
    """Ausgabe der DMN-Entscheidung ``klaerfall-klassifikation``.

    Die Feldnamen entsprechen exakt den Ausgabespalten der DMN-Tabelle (camelCase ist dort
    Konvention), damit das Ergebnis ohne Mapping als Variable ``klassifikation`` landet.
    """

    model_config = ConfigDict(extra="forbid")

    kategorie: str
    prioritaet: str
    zielTeam: str  # noqa: N815
    autoKorrektur: bool  # noqa: N815
    sla: str


class TxStatus(StrEnum):
    """Transaktionsstatus nach ISO 20022 (ExternalPaymentTransactionStatus1Code)."""

    ACCP = "ACCP"  # AcceptedCustomerProfile – fachlich geprüft, zur Ausführung angenommen
    RJCT = "RJCT"  # Rejected
    PDNG = "PDNG"  # Pending – Klärfall offen


class Ergebnis(BaseModel):
    """Ergebnis pro Zahlung – Rückgabe des Teilprozesses ``zv-zahlung-pruefen``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    end_to_end_id: str = Field(alias="endToEndId")
    status: TxStatus
    statusgrund: str | None = None
    kommentar: str | None = None
    korrekturen: list[str] = Field(default_factory=list)
