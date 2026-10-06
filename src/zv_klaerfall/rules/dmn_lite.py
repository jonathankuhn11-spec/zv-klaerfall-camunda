"""Minimaler DMN-Entscheidungstabellen-Evaluator (FEEL-Teilmenge).

Produktiv wertet **Camunda** die DMN-Tabelle aus (Business Rule Task). Dieser Evaluator existiert
aus zwei Gründen:

1. *Offline-Demo* ohne laufenden Cluster – gleiche Tabelle, gleiche Ergebnisse.
2. *Tests der Fachlogik*: Die DMN-Datei wird gegen Erwartungswerte getestet, damit eine Änderung
   der Tabelle durch den Fachbereich nicht unbemerkt die Routing-Logik bricht.

Unterstützt werden genau die Ausdrucksformen, die in ``klaerfall-klassifikation.dmn`` vorkommen:
``-`` (beliebig), ``"A"``, ``"A","B"``, ``not("A")``, ``true``/``false``, ``< 5``, ``>= 50000``,
``[1000..5000]``, ``]1000..5000[``. Hit-Policy ``FIRST`` und ``UNIQUE``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lxml import etree

NS_DMN = "https://www.omg.org/spec/DMN/20191111/MODEL/"
_NS = {"dmn": NS_DMN}
_INTERVAL = re.compile(r"^([\[\]])\s*(-?[\d.]+)\s*\.\.\s*(-?[\d.]+)\s*([\[\]])$")
_COMPARE = re.compile(r"^(<=|>=|<|>)\s*(-?[\d.]+)$")


@dataclass(frozen=True)
class Regel:
    eingaben: tuple[str, ...]
    ausgaben: dict[str, Any]
    beschreibung: str | None


@dataclass(frozen=True)
class Entscheidungstabelle:
    id: str
    name: str
    hit_policy: str
    eingabe_variablen: tuple[str, ...]
    regeln: tuple[Regel, ...]


def _literal(text: str) -> Any:
    t = text.strip()
    if t in ("true", "false"):
        return t == "true"
    if t.startswith('"') and t.endswith('"'):
        return t[1:-1]
    if t == "null":
        return None
    try:
        return int(t) if re.fullmatch(r"-?\d+", t) else float(t)
    except ValueError as exc:
        raise ValueError(f"Nicht unterstützter DMN-Ausgabeausdruck: {text!r}") from exc


def _passt(ausdruck: str, wert: Any) -> bool:
    a = ausdruck.strip()
    if a in ("", "-"):
        return True
    if a.startswith("not(") and a.endswith(")"):
        return not _passt(a[4:-1], wert)
    if a.startswith('"'):
        werte = [s.strip()[1:-1] for s in re.split(r',(?=\s*")', a)]
        return wert in werte
    if a in ("true", "false"):
        return wert is (a == "true")
    if wert is None:
        return False
    if m := _INTERVAL.match(a):
        lo_offen, lo, hi, hi_offen = (
            m.group(1) == "]",
            float(m.group(2)),
            float(m.group(3)),
            m.group(4) == "[",
        )
        w = float(wert)
        return (w > lo if lo_offen else w >= lo) and (w < hi if hi_offen else w <= hi)
    if m := _COMPARE.match(a):
        op, zahl = m.group(1), float(m.group(2))
        w = float(wert)
        return {"<": w < zahl, "<=": w <= zahl, ">": w > zahl, ">=": w >= zahl}[op]
    try:
        return bool(wert == _literal(a))
    except ValueError as exc:
        raise ValueError(f"Nicht unterstützter DMN-Eingabeausdruck: {ausdruck!r}") from exc


def lade(pfad: str | Path, decision_id: str | None = None) -> Entscheidungstabelle:
    baum = etree.parse(str(pfad))
    decisions = baum.getroot().findall("dmn:decision", _NS)
    if decision_id:
        decisions = [d for d in decisions if d.get("id") == decision_id]
    if not decisions:
        raise ValueError(f"Keine Decision {decision_id!r} in {pfad}")
    dec = decisions[0]
    tabelle = dec.find("dmn:decisionTable", _NS)
    if tabelle is None:
        raise ValueError("Decision enthält keine Entscheidungstabelle")
    eingaben = tuple(
        (i.findtext("dmn:inputExpression/dmn:text", default="", namespaces=_NS) or "").strip()
        for i in tabelle.findall("dmn:input", _NS)
    )
    ausgabe_namen = [o.get("name", "") for o in tabelle.findall("dmn:output", _NS)]
    regeln = []
    for r in tabelle.findall("dmn:rule", _NS):
        eingabe_texte = tuple(
            (e.findtext("dmn:text", default="", namespaces=_NS) or "")
            for e in r.findall("dmn:inputEntry", _NS)
        )
        ausgabe_texte = [
            (e.findtext("dmn:text", default="", namespaces=_NS) or "")
            for e in r.findall("dmn:outputEntry", _NS)
        ]
        regeln.append(
            Regel(
                eingaben=eingabe_texte,
                ausgaben={n: _literal(t) for n, t in zip(ausgabe_namen, ausgabe_texte, strict=True)},
                beschreibung=(r.findtext("dmn:description", default=None, namespaces=_NS) or None),
            )
        )
    return Entscheidungstabelle(
        id=dec.get("id", ""),
        name=dec.get("name", ""),
        hit_policy=tabelle.get("hitPolicy", "UNIQUE"),
        eingabe_variablen=eingaben,
        regeln=tuple(regeln),
    )


def werte_aus(tabelle: Entscheidungstabelle, variablen: dict[str, Any]) -> dict[str, Any] | None:
    """Liefert die Ausgaben der ersten passenden Regel (FIRST/UNIQUE) oder ``None`` ohne Treffer."""
    if tabelle.hit_policy not in ("FIRST", "UNIQUE"):
        raise ValueError(f"Hit-Policy {tabelle.hit_policy} wird nicht unterstützt")
    werte = [variablen.get(v) for v in tabelle.eingabe_variablen]
    treffer = [r for r in tabelle.regeln if all(_passt(a, w) for a, w in zip(r.eingaben, werte, strict=True))]
    if not treffer:
        return None
    if tabelle.hit_policy == "UNIQUE" and len(treffer) > 1:
        raise ValueError("Hit-Policy UNIQUE verletzt: mehrere passende Regeln")
    return dict(treffer[0].ausgaben)
