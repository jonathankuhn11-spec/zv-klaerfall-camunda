"""Job-Worker: verbindet die Handler aus :mod:`zv_klaerfall.handlers` mit Camunda.

Pro Job-Typ läuft ein Thread mit Long-Polling gegen ``POST /v2/jobs/activation``. Für jeden Job:

* Handler ausführen → ``POST /jobs/{key}/completion`` mit den Rückgabevariablen,
* :class:`FachlicherFehler` → ``POST /jobs/{key}/error`` (BPMN-Error, vom Prozess abfangbar),
* jede andere Exception → ``POST /jobs/{key}/failure`` mit ``retries - 1`` und Backoff; bei 0
  Retries entsteht in Camunda ein Incident, den man in Operate sieht und auflösen kann.

Idempotenz: Camunda liefert einen Job nach Timeout erneut aus. Die Handler sind so gebaut, dass
ein doppelter Lauf fachlich unkritisch ist (Audit-Trail hat dann zwei Einträge, Buchung erzeugt
eine neue ID – in einem echten System würde die EndToEndId als Idempotenzschlüssel dienen).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx

from zv_klaerfall.camunda.client import CamundaClient, CamundaFehler
from zv_klaerfall.config import Dienste
from zv_klaerfall.handlers import HANDLER, Handler, JobKontext

log = logging.getLogger("zv.worker")


class FachlicherFehler(Exception):
    """Wird als BPMN-Error an den Prozess gemeldet (``errorCode``), nicht als technischer Fehler."""

    def __init__(self, code: str, nachricht: str = ""):
        super().__init__(nachricht or code)
        self.code = code


@dataclass
class WorkerStatistik:
    verarbeitet: int = 0
    fehlgeschlagen: int = 0
    je_typ: dict[str, int] = field(default_factory=dict)


class JobWorker:
    def __init__(
        self,
        client: CamundaClient,
        dienste: Dienste,
        *,
        worker_name: str = "zv-klaerfall-worker",
        handler: dict[str, Handler] | None = None,
        heute: date | None = None,
    ):
        self._client = client
        self._dienste = dienste
        self._name = worker_name
        self._handler = handler or HANDLER
        self._heute = heute
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.statistik = WorkerStatistik()
        self._lock = threading.Lock()

    # --- Lebenszyklus ---------------------------------------------------------------------
    def start(self) -> None:
        for typ in self._handler:
            t = threading.Thread(target=self._schleife, args=(typ,), name=f"worker-{typ}", daemon=True)
            t.start()
            self._threads.append(t)
        log.info("Worker '%s' gestartet für %d Job-Typen", self._name, len(self._threads))

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=15)

    def laufe(self, dauer_s: float | None = None) -> None:
        """Blockiert bis Strg+C oder bis ``dauer_s`` abgelaufen ist."""
        self.start()
        try:
            ende = time.monotonic() + dauer_s if dauer_s else None
            while not self._stop.is_set():
                if ende and time.monotonic() >= ende:
                    break
                time.sleep(0.25)
        except KeyboardInterrupt:
            log.info("Abbruch durch Benutzer")
        finally:
            self.stop()

    # --- Verarbeitung ---------------------------------------------------------------------
    def _schleife(self, typ: str) -> None:
        while not self._stop.is_set():
            try:
                jobs = self._client.aktiviere_jobs(typ, worker=self._name, request_timeout_ms=5_000)
            except (httpx.HTTPError, CamundaFehler) as exc:
                log.warning("[%s] Aktivierung fehlgeschlagen: %s – neuer Versuch in 3s", typ, exc)
                self._stop.wait(3)
                continue
            for job in jobs:
                self.verarbeite(job)

    def verarbeite(self, job: dict[str, Any]) -> None:
        typ = job["type"]
        key = job["jobKey"]
        handler = self._handler[typ]
        ctx = JobKontext(
            prozess_id=str(job.get("processDefinitionId", "")),
            instanz_key=str(job.get("processInstanceKey", "")),
            header={k: str(v) for k, v in (job.get("customHeaders") or {}).items()},
            heute=self._heute or date.today(),
        )
        try:
            ergebnis = handler(job.get("variables") or {}, self._dienste, ctx)
            self._client.job_abschliessen(key, ergebnis)
            with self._lock:
                self.statistik.verarbeitet += 1
                self.statistik.je_typ[typ] = self.statistik.je_typ.get(typ, 0) + 1
            log.info("[%s] Job %s abgeschlossen (Instanz %s)", typ, key, ctx.instanz_key)
        except FachlicherFehler as exc:
            log.info("[%s] Job %s → BPMN-Error %s", typ, key, exc.code)
            self._client.job_fehler_werfen(key, error_code=exc.code, nachricht=str(exc))
        except Exception as exc:
            retries = max(int(job.get("retries", 1)) - 1, 0)
            log.exception("[%s] Job %s fehlgeschlagen (%d Retries übrig)", typ, key, retries)
            with self._lock:
                self.statistik.fehlgeschlagen += 1
            try:
                self._client.job_fehlschlagen(
                    key, retries=retries, fehlermeldung=f"{type(exc).__name__}: {exc}"
                )
            except (httpx.HTTPError, CamundaFehler) as melde_exc:
                log.error("Fehlermeldung an Camunda gescheitert: %s", melde_exc)
