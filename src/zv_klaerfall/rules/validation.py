"""Fachliche Prüfung einer Zahlung (Disposition vor Ausführung).

Jede Regel ist eine kleine Funktion ``(Zahlung, Kontext) -> list[Befund]``; :func:`pruefe` führt
alle Regeln aus und fasst sie zu einem :class:`~zv_klaerfall.models.Pruefergebnis` zusammen.
Die Regeln sind bewusst *erklärbar*: jeder Befund trägt Feld, Meldung und – falls möglich – einen
Korrekturvorschlag. Genau diese Nachvollziehbarkeit braucht ein Klärfallbearbeiter.

Welche Befunde automatisch korrigiert werden dürfen, entscheidet nicht dieser Code, sondern die
DMN-Tabelle ``klaerfall-klassifikation`` (fachliche Hoheit beim Fachbereich). Der Code liefert
nur das *Angebot* (``auto_korrigierbar`` + ``korrekturwert``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from schwifty import BIC, IBAN
from schwifty import exceptions as schwifty_ex

from zv_klaerfall.models import Befund, BefundCode, Pruefergebnis, Schwere, Zahlung, betrag_de
from zv_klaerfall.rules import sanktionen
from zv_klaerfall.rules.dubletten import Dublettenspeicher
from zv_klaerfall.rules.kalender import ist_bankarbeitstag, naechster_bankarbeitstag


@dataclass
class Pruefkontext:
    """Parameter, die der Fachbereich steuert – im Prozess als Variablen/Header übergebbar."""

    heute: date = field(default_factory=date.today)
    betragslimit: Decimal = Decimal("50000.00")  # ab hier Vier-Augen-Freigabe (Disposition)
    max_vorlauf_tage: int = 90
    dublettenspeicher: Dublettenspeicher | None = None
    sanktionsschwelle: float = 0.85


Regel = Callable[[Zahlung, Pruefkontext], list[Befund]]


def _pruefe_iban(roh: str | None, feld: str) -> tuple[Befund | None, IBAN | None]:
    """Prüfziffer (ISO 7064 MOD 97-10) und Länderformat. Schreibweise prüft bereits das XSD."""
    if not roh:
        return Befund(
            code=BefundCode.IBAN_PRUEFZIFFER, schwere=Schwere.BLOCKIEREND, feld=feld, meldung="IBAN fehlt"
        ), None
    try:
        return None, IBAN(roh)
    except schwifty_ex.InvalidChecksumDigits:
        return Befund(
            code=BefundCode.IBAN_PRUEFZIFFER,
            schwere=Schwere.BLOCKIEREND,
            feld=feld,
            meldung=f"IBAN-Prüfziffer ungültig: {roh}",
        ), None
    except schwifty_ex.SchwiftyException as exc:
        return Befund(
            code=BefundCode.IBAN_PRUEFZIFFER,
            schwere=Schwere.BLOCKIEREND,
            feld=feld,
            meldung=f"IBAN ungültig ({exc}): {roh}",
        ), None


def regel_konten(z: Zahlung, _: Pruefkontext) -> list[Befund]:
    """IBAN-Prüfziffern sowie BIC-Format und BIC/IBAN-Konsistenz (Bankleitzahlregister)."""
    befunde: list[Befund] = []
    for partei, feld in ((z.debitor, "DbtrAcct/IBAN"), (z.kreditor, "CdtrAcct/IBAN")):
        iban_befund, iban = _pruefe_iban(partei.iban, feld)
        if iban_befund:
            befunde.append(iban_befund)
        if partei.bic:
            bic_feld = feld.replace("Acct/IBAN", "Agt/BICFI")
            try:
                bic = BIC(partei.bic)
            except schwifty_ex.SchwiftyException as exc:
                befunde.append(
                    Befund(
                        code=BefundCode.BIC_UNGUELTIG,
                        schwere=Schwere.BLOCKIEREND,
                        feld=bic_feld,
                        meldung=f"BIC ungültig ({exc}): {partei.bic}",
                    )
                )
                continue
            if iban is not None and iban.bic is not None and iban.bic.bank_code != bic.bank_code:
                befunde.append(
                    Befund(
                        code=BefundCode.BIC_IBAN_MISMATCH,
                        schwere=Schwere.BLOCKIEREND,
                        feld=bic_feld,
                        meldung=f"BIC {bic} passt nicht zur IBAN (Register: {iban.bic}, {iban.bank_name})",
                        autoKorrigierbar=False,
                        korrekturwert=str(iban.bic),
                    )
                )
    return befunde


def regel_betrag(z: Zahlung, ctx: Pruefkontext) -> list[Befund]:
    befunde: list[Befund] = []
    if z.betrag <= 0:
        befunde.append(
            Befund(
                code=BefundCode.BETRAG_NULL,
                schwere=Schwere.BLOCKIEREND,
                feld="Amt/InstdAmt",
                meldung=f"Betrag muss größer 0 sein: {z.betrag}",
            )
        )
    elif z.betrag >= ctx.betragslimit:
        befunde.append(
            Befund(
                code=BefundCode.BETRAG_LIMIT,
                schwere=Schwere.BLOCKIEREND,
                feld="Amt/InstdAmt",
                meldung=f"Betrag {betrag_de(z.betrag, z.waehrung)} erreicht das Freigabelimit "
                f"von {betrag_de(ctx.betragslimit)} (Vier-Augen-Prinzip)",
            )
        )
    return befunde


def regel_waehrung(z: Zahlung, _: Pruefkontext) -> list[Befund]:
    if (z.service_level or "SEPA") == "SEPA" and z.waehrung != "EUR":
        return [
            Befund(
                code=BefundCode.WAEHRUNG,
                schwere=Schwere.BLOCKIEREND,
                feld="Amt/InstdAmt/@Ccy",
                meldung=f"SEPA-Überweisung nur in EUR möglich, angegeben: {z.waehrung}",
            )
        ]
    return []


def regel_gebuehren(z: Zahlung, _: Pruefkontext) -> list[Befund]:
    """SEPA-Überweisungen tragen laut EPC-Rulebook zwingend ``ChrgBr = SLEV`` – formal korrigierbar."""
    if (z.service_level or "SEPA") == "SEPA" and z.charge_bearer not in (None, "SLEV"):
        return [
            Befund(
                code=BefundCode.GEBUEHRENREGELUNG,
                schwere=Schwere.BLOCKIEREND,
                feld="ChrgBr",
                meldung=f"Gebührenregelung '{z.charge_bearer}' bei SEPA unzulässig, Rulebook verlangt SLEV",
                autoKorrigierbar=True,
                korrekturwert="SLEV",
            )
        ]
    return []


def regel_ausfuehrungsdatum(z: Zahlung, ctx: Pruefkontext) -> list[Befund]:
    d = z.ausfuehrungsdatum
    if d < ctx.heute or not ist_bankarbeitstag(d):
        neu = naechster_bankarbeitstag(max(d, ctx.heute))
        grund = "liegt in der Vergangenheit" if d < ctx.heute else "ist kein TARGET2-Bankarbeitstag"
        return [
            Befund(
                code=BefundCode.AUSFUEHRUNGSDATUM,
                schwere=Schwere.BLOCKIEREND,
                feld="ReqdExctnDt",
                meldung=f"Ausführungsdatum {d.isoformat()} {grund}; Vorschlag: {neu.isoformat()}",
                autoKorrigierbar=True,
                korrekturwert=neu.isoformat(),
            )
        ]
    if (d - ctx.heute).days > ctx.max_vorlauf_tage:
        return [
            Befund(
                code=BefundCode.AUSFUEHRUNGSDATUM,
                schwere=Schwere.BLOCKIEREND,
                feld="ReqdExctnDt",
                meldung=f"Ausführungsdatum {d.isoformat()} liegt mehr als {ctx.max_vorlauf_tage} "
                "Tage in der Zukunft",
            )
        ]
    return []


def regel_verwendungszweck(z: Zahlung, _: Pruefkontext) -> list[Befund]:
    if not z.verwendungszweck and not z.purpose_code:
        return [
            Befund(
                code=BefundCode.VERWENDUNGSZWECK_FEHLT,
                schwere=Schwere.INFO,
                feld="RmtInf",
                meldung="Kein Verwendungszweck angegeben (Hinweis, nicht blockierend)",
            )
        ]
    return []


def regel_dublette(z: Zahlung, ctx: Pruefkontext) -> list[Befund]:
    if ctx.dublettenspeicher is None:
        return []
    frueher = ctx.dublettenspeicher.pruefe_und_merke(z)
    if frueher is None:
        return []
    return [
        Befund(
            code=BefundCode.DUBLETTE,
            schwere=Schwere.BLOCKIEREND,
            feld="PmtId/EndToEndId",
            meldung=f"Identische Zahlung bereits eingereicht (MsgId {frueher}, EndToEndId {z.end_to_end_id})",
        )
    ]


def regel_sanktionen(z: Zahlung, ctx: Pruefkontext) -> list[Befund]:
    befunde: list[Befund] = []
    for partei, feld in ((z.kreditor, "Cdtr/Nm"), (z.debitor, "Dbtr/Nm")):
        treffer = sanktionen.pruefe_name(partei.name, schwelle=ctx.sanktionsschwelle)
        if treffer:
            befunde.append(
                Befund(
                    code=BefundCode.SANKTION,
                    schwere=Schwere.BLOCKIEREND,
                    feld=feld,
                    meldung=f"Treffer Sanktionsliste {treffer.liste}: '{partei.name}' ≈ "
                    f"'{treffer.eintrag}' (Ähnlichkeit {treffer.aehnlichkeit:.0%})",
                )
            )
    return befunde


REGELN: tuple[Regel, ...] = (
    regel_konten,
    regel_betrag,
    regel_waehrung,
    regel_gebuehren,
    regel_ausfuehrungsdatum,
    regel_verwendungszweck,
    regel_dublette,
    regel_sanktionen,
)


def pruefe(z: Zahlung, ctx: Pruefkontext | None = None) -> Pruefergebnis:
    ctx = ctx or Pruefkontext()
    befunde: list[Befund] = []
    for regel in REGELN:
        befunde.extend(regel(z, ctx))
    return Pruefergebnis.aus_befunden(befunde)


def wende_korrekturen_an(z: Zahlung, befunde: list[Befund]) -> tuple[Zahlung, list[str]]:
    """Wendet alle auto-korrigierbaren Befunde an und liefert die geänderte Zahlung + Protokoll."""
    daten = z.model_dump(by_alias=True)
    protokoll: list[str] = []
    for b in befunde:
        if not (b.auto_korrigierbar and b.korrekturwert):
            continue
        if b.code == BefundCode.GEBUEHRENREGELUNG:
            protokoll.append(f"ChrgBr: {daten['chargeBearer']} → {b.korrekturwert}")
            daten["chargeBearer"] = b.korrekturwert
        elif b.code == BefundCode.AUSFUEHRUNGSDATUM:
            protokoll.append(f"ReqdExctnDt: {daten['ausfuehrungsdatum']} → {b.korrekturwert}")
            daten["ausfuehrungsdatum"] = b.korrekturwert
    return Zahlung.model_validate(daten), protokoll
