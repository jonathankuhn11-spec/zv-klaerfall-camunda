"""Sanktionslisten-Screening (Namensabgleich).

Produktiv würde hier ein Screening-System (z. B. gegen die EU-Konsolidierte Sanktionsliste)
angebunden. Für das Projekt reicht ein lokaler, offensichtlich fiktiver Datensatz
(``data/sanktionsliste.json``) plus ein nachvollziehbarer Abgleich:

* Normalisierung (Kleinschreibung, Umlaute, Rechtsformen wie "GmbH"/"Ltd" entfernen),
* exakter Treffer *oder* Ähnlichkeit ≥ ``schwelle`` (Ratcliff/Obershelp, ``difflib``).

Ein Treffer ist nie automatisch korrigierbar und geht immer an das Compliance-Team –
regulatorisch ist eine manuelle Prüfung zwingend (Instant Payments Regulation, Art. 5d:
Screening der Kunden gegen Sanktionslisten).
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from importlib import resources

_RECHTSFORMEN = re.compile(
    r"\b(gmbh|mbh|ag|kg|ohg|ug|e\.?v\.?|ltd|limited|llc|inc|incorporated|corp|corporation|co|company|"
    r"sa|s\.?a\.?r\.?l|plc|bv|nv|oy|ab|aktiengesellschaft)\b",
    re.IGNORECASE,
)


def normalisiere(name: str) -> str:
    """Unicode-Normalisierung, Umlaute ausschreiben, Rechtsformen/Satzzeichen entfernen."""
    s = name.lower().replace("ß", "ss")
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue")):
        s = s.replace(a, b)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = _RECHTSFORMEN.sub(" ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return " ".join(s.split())


@dataclass(frozen=True)
class Treffer:
    eintrag: str
    liste: str
    aehnlichkeit: float


@lru_cache(maxsize=1)
def _liste() -> list[dict[str, str]]:
    quelle = resources.files("zv_klaerfall.data").joinpath("sanktionsliste.json")
    daten = json.loads(quelle.read_text(encoding="utf-8"))
    return [{"name": e["name"], "liste": e["liste"], "norm": normalisiere(e["name"])} for e in daten]


def pruefe_name(name: str | None, *, schwelle: float = 0.85) -> Treffer | None:
    """Liefert den besten Treffer über der Schwelle, sonst ``None``."""
    if not name:
        return None
    norm = normalisiere(name)
    if not norm:
        return None
    bester: Treffer | None = None
    for e in _liste():
        if norm == e["norm"]:
            return Treffer(e["name"], e["liste"], 1.0)
        quote = SequenceMatcher(None, norm, e["norm"]).ratio()
        if quote >= schwelle and (bester is None or quote > bester.aehnlichkeit):
            bester = Treffer(e["name"], e["liste"], round(quote, 3))
    return bester
