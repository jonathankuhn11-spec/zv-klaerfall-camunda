"""End-to-End gegen einen echten Camunda-8-Cluster.

Wird nur ausgeführt, wenn ``CAMUNDA_URL`` gesetzt und der Cluster erreichbar ist:

    docker compose -f docker/docker-compose.yaml up -d
    CAMUNDA_URL=http://localhost:8080 pytest -m integration

Ablauf: Modelle deployen → zwei Dateien einreichen (fehlerfrei + Vier-Augen-Fall) → Worker im
Hintergrund laufen lassen → Klärfall über die REST-API entscheiden → Abschluss und pain.002 prüfen.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from zv_klaerfall.camunda.client import CamundaClient
from zv_klaerfall.camunda.worker import JobWorker
from zv_klaerfall.config import Dienste, Einstellungen

pytestmark = pytest.mark.integration

CAMUNDA_URL = os.getenv("CAMUNDA_URL")


@pytest.fixture(scope="module")
def client() -> CamundaClient:
    if not CAMUNDA_URL:
        pytest.skip("CAMUNDA_URL nicht gesetzt")
    c = CamundaClient(CAMUNDA_URL, user=os.getenv("CAMUNDA_USER"), password=os.getenv("CAMUNDA_PASSWORD"))
    if not c.erreichbar():
        pytest.skip(f"Camunda unter {CAMUNDA_URL} nicht erreichbar")
    return c


def _warte(bedingung, timeout: float = 90.0, intervall: float = 1.0):  # type: ignore[no-untyped-def]
    ende = time.monotonic() + timeout
    while time.monotonic() < ende:
        ergebnis = bedingung()
        if ergebnis:
            return ergebnis
        time.sleep(intervall)
    raise AssertionError("Zeitüberschreitung beim Warten auf Camunda")


def test_deploy_ingest_worker_und_klaerfall(
    client: CamundaClient, tmp_path: Path, samples: Path, bpmn_dir: Path, fachdatum
) -> None:  # type: ignore[no-untyped-def]
    e = Einstellungen(datenverzeichnis=tmp_path / "data")
    dateien = (
        sorted(bpmn_dir.glob("*.bpmn"))
        + sorted(bpmn_dir.glob("*.dmn"))
        + sorted(e.forms_verzeichnis.glob("*.form"))
    )
    deployment = client.deploye(dateien)
    assert deployment["deployments"], "Deployment ohne Inhalt"

    worker = JobWorker(client, Dienste(e), worker_name="pytest-worker", heute=fachdatum)
    thread = threading.Thread(target=worker.laufe, kwargs={"dauer_s": 170}, daemon=True)
    thread.start()
    try:
        gueltig = client.starte_prozess(
            "zv-nachricht-verarbeiten",
            {
                "nachrichtXml": (samples / "01_gueltig.xml").read_text(encoding="utf-8"),
                "dateiname": "01_gueltig.xml",
            },
        )
        limit = client.starte_prozess(
            "zv-nachricht-verarbeiten",
            {
                "nachrichtXml": (samples / "04_betrag_limit.xml").read_text(encoding="utf-8"),
                "dateiname": "04_betrag_limit.xml",
            },
        )

        # 1) Fehlerfreie Datei läuft ohne menschliches Zutun durch
        def fertig(key: str):  # type: ignore[no-untyped-def]
            return [
                i
                for i in client.suche_prozessinstanzen("zv-nachricht-verarbeiten", aktiv=False)
                if str(i["processInstanceKey"]) == str(key)
            ]

        _warte(lambda: fertig(gueltig["processInstanceKey"]))
        reports = list((tmp_path / "data" / "statusreports").glob("pain.002_MSG-2026-1001_*.xml"))
        assert reports and b"<GrpSts>ACCP</GrpSts>" in reports[0].read_bytes()

        # 2) Vier-Augen-Fall wartet als User Task auf die Disposition (Zuordnung über die EndToEndId)
        def offener_klaerfall():  # type: ignore[no-untyped-def]
            for t in client.suche_user_tasks(kandidatengruppe="team-disposition"):
                v = client.user_task_variablen(t["userTaskKey"])
                if (v.get("zahlung") or {}).get("endToEndId") == "E2E-1004-1":
                    return t, v
            return None

        task, variablen = _warte(offener_klaerfall)
        assert variablen.get("befundCode") == "BETRAG_LIMIT"
        assert variablen.get("klassifikation", {}).get("kategorie") == "Freigabe"
        client.user_task_zuweisen(task["userTaskKey"], "pytest")
        client.user_task_abschliessen(
            task["userTaskKey"],
            {"entscheidung": "FREIGEBEN", "bearbeiter": "pytest", "kommentar": "Integrationstest"},
        )
        _warte(lambda: fertig(limit["processInstanceKey"]))
        reports = list((tmp_path / "data" / "statusreports").glob("pain.002_MSG-2026-1004_*.xml"))
        assert reports and b"<TxSts>ACCP</TxSts>" in reports[0].read_bytes()
        assert worker.statistik.fehlgeschlagen == 0
        assert Dienste(e).audit.verifiziere().gueltig
    finally:
        worker.stop()
