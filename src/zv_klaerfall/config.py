"""Konfiguration (Umgebungsvariablen, Prefix ``ZV_``/``CAMUNDA_``) und Laufzeit-Dienste."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from zv_klaerfall.audit.trail import AuditTrail
from zv_klaerfall.rules.dubletten import Dublettenspeicher


@dataclass(frozen=True)
class Einstellungen:
    camunda_url: str = field(default_factory=lambda: os.getenv("CAMUNDA_URL", "http://localhost:8080"))
    camunda_user: str | None = field(default_factory=lambda: os.getenv("CAMUNDA_USER"))
    camunda_password: str | None = field(default_factory=lambda: os.getenv("CAMUNDA_PASSWORD"))
    datenverzeichnis: Path = field(default_factory=lambda: Path(os.getenv("ZV_DATA_DIR", ".data")))
    betragslimit: Decimal = field(default_factory=lambda: Decimal(os.getenv("ZV_BETRAGSLIMIT", "50000.00")))
    bank_name: str = field(default_factory=lambda: os.getenv("ZV_BANK_NAME", "Demo Bank AG"))
    worker_name: str = field(default_factory=lambda: os.getenv("ZV_WORKER_NAME", "zv-klaerfall-worker"))

    @property
    def projektwurzel(self) -> Path:
        """Verzeichnis mit ``bpmn/``, ``forms/`` und ``samples/`` (``ZV_PROJEKT_DIR`` oder Repo-Wurzel)."""
        env = os.getenv("ZV_PROJEKT_DIR")
        return Path(env).resolve() if env else Path(__file__).resolve().parents[2]

    @property
    def bpmn_verzeichnis(self) -> Path:
        return self.projektwurzel / "bpmn"

    @property
    def forms_verzeichnis(self) -> Path:
        return self.projektwurzel / "forms"


class Dienste:
    """Bündelt zustandsbehaftete Infrastruktur, die alle Handler gemeinsam nutzen."""

    def __init__(self, einstellungen: Einstellungen | None = None):
        self.einstellungen = einstellungen or Einstellungen()
        d = self.einstellungen.datenverzeichnis
        d.mkdir(parents=True, exist_ok=True)
        self.audit = AuditTrail(d / "audit-trail.jsonl")
        self.dubletten = Dublettenspeicher(d / "dubletten.sqlite")
        self.ausgabeverzeichnis = d / "statusreports"
        self.ausgabeverzeichnis.mkdir(parents=True, exist_ok=True)
        self.buchungsjournal = d / "buchungen.jsonl"

    def zuruecksetzen(self) -> None:
        """Löscht Laufzeitdaten (Demo-Neustart). Der Audit-Trail wird bewusst rotiert, nicht gelöscht."""
        self.dubletten.leeren()
        if self.buchungsjournal.exists():
            self.buchungsjournal.unlink()
        for f in self.ausgabeverzeichnis.glob("*.xml"):
            f.unlink()
        if self.audit.pfad.exists():
            archiv = self.audit.pfad.with_name(f"audit-trail.{self.audit.pfad.stat().st_mtime_ns}.jsonl")
            self.audit.pfad.rename(archiv)
