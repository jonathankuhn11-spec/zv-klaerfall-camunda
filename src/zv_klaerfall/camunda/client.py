"""Client für die Camunda 8 Orchestration Cluster REST API (``/v2``).

Bewusst kein SDK: Die wenigen benötigten Endpunkte sind in ~100 Zeilen abbildbar, bleiben
transparent und lassen sich in Tests mit ``respx`` mocken. Endpunkte und Feldnamen entsprechen
der OpenAPI-Spezifikation von Camunda 8.9 (``zeebe/gateway-protocol/src/main/proto/v2``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx


class CamundaFehler(RuntimeError):
    def __init__(self, status: int, detail: Any):
        super().__init__(f"Camunda antwortete mit HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


class CamundaClient:
    def __init__(
        self,
        basis_url: str = "http://localhost:8080",
        *,
        user: str | None = None,
        password: str | None = None,
        timeout: float = 30.0,
    ):
        auth = (user, password) if user and password else None
        self._http = httpx.Client(base_url=basis_url.rstrip("/") + "/v2", auth=auth, timeout=timeout)

    # --- Infrastruktur -------------------------------------------------------------------
    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> CamundaClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _pruefe(self, antwort: httpx.Response) -> Any:
        if antwort.status_code >= 400:
            try:
                detail = antwort.json()
            except ValueError:
                detail = antwort.text
            raise CamundaFehler(antwort.status_code, detail)
        if antwort.status_code == 204 or not antwort.content:
            return None
        return antwort.json()

    # --- Cluster --------------------------------------------------------------------------
    def topologie(self) -> dict[str, Any]:
        return dict(self._pruefe(self._http.get("/topology")))

    def erreichbar(self) -> bool:
        try:
            self.topologie()
            return True
        except (httpx.HTTPError, CamundaFehler):
            return False

    # --- Deployment -----------------------------------------------------------------------
    def deploye(self, dateien: list[Path]) -> dict[str, Any]:
        """Deployt BPMN/DMN/Form-Dateien in einem Deployment (multipart ``resources``)."""
        files = [("resources", (p.name, p.read_bytes(), "application/octet-stream")) for p in dateien]
        return dict(self._pruefe(self._http.post("/deployments", files=files)))

    # --- Prozessinstanzen -----------------------------------------------------------------
    def starte_prozess(self, prozess_id: str, variablen: dict[str, Any] | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"processDefinitionId": prozess_id, "variables": variablen or {}}
        return dict(self._pruefe(self._http.post("/process-instances", json=body)))

    def suche_prozessinstanzen(
        self, prozess_id: str | None = None, aktiv: bool | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        filt: dict[str, Any] = {}
        if prozess_id:
            filt["processDefinitionId"] = prozess_id
        if aktiv is not None:
            filt["state"] = "ACTIVE" if aktiv else "COMPLETED"
        body = {"filter": filt, "page": {"limit": limit}}
        antwort = self._pruefe(self._http.post("/process-instances/search", json=body))
        return list(antwort.get("items", []))

    # --- Jobs (Worker) --------------------------------------------------------------------
    def aktiviere_jobs(
        self,
        typ: str,
        *,
        worker: str,
        timeout_ms: int = 60_000,
        max_jobs: int = 8,
        request_timeout_ms: int = 10_000,
    ) -> list[dict[str, Any]]:
        body = {
            "type": typ,
            "worker": worker,
            "timeout": timeout_ms,
            "maxJobsToActivate": max_jobs,
            "requestTimeout": request_timeout_ms,
        }
        antwort = self._pruefe(
            self._http.post("/jobs/activation", json=body, timeout=request_timeout_ms / 1000 + 15)
        )
        return list((antwort or {}).get("jobs", []))

    def job_abschliessen(self, job_key: str | int, variablen: dict[str, Any] | None = None) -> None:
        self._pruefe(self._http.post(f"/jobs/{job_key}/completion", json={"variables": variablen or {}}))

    def job_fehlschlagen(
        self, job_key: str | int, *, retries: int, fehlermeldung: str, backoff_ms: int = 5_000
    ) -> None:
        self._pruefe(
            self._http.post(
                f"/jobs/{job_key}/failure",
                json={"retries": retries, "errorMessage": fehlermeldung[:2000], "retryBackOff": backoff_ms},
            )
        )

    def job_fehler_werfen(self, job_key: str | int, *, error_code: str, nachricht: str = "") -> None:
        self._pruefe(
            self._http.post(
                f"/jobs/{job_key}/error", json={"errorCode": error_code, "errorMessage": nachricht[:2000]}
            )
        )

    # --- Entscheidungen -------------------------------------------------------------------
    def werte_entscheidung_aus(self, decision_id: str, variablen: dict[str, Any]) -> dict[str, Any]:
        body = {"decisionDefinitionId": decision_id, "variables": variablen}
        return dict(self._pruefe(self._http.post("/decision-definitions/evaluation", json=body)))

    # --- User Tasks -----------------------------------------------------------------------
    def suche_user_tasks(
        self, *, status: str = "CREATED", kandidatengruppe: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        filt: dict[str, Any] = {"state": status}
        if kandidatengruppe:
            filt["candidateGroup"] = kandidatengruppe
        body = {"filter": filt, "page": {"limit": limit}}
        antwort = self._pruefe(self._http.post("/user-tasks/search", json=body))
        return list(antwort.get("items", []))

    def user_task_variablen(self, user_task_key: str | int) -> dict[str, Any]:
        """Variablen im Scope des User Tasks. Werte kommen als JSON-String und werden dekodiert."""
        antwort = self._pruefe(
            self._http.post(f"/user-tasks/{user_task_key}/variables/search", json={"page": {"limit": 200}})
        )
        ergebnis: dict[str, Any] = {}
        for v in antwort.get("items", []):
            roh = v.get("value")
            try:
                ergebnis[v["name"]] = json.loads(roh) if isinstance(roh, str) else roh
            except (TypeError, ValueError):
                ergebnis[v["name"]] = roh
        return ergebnis

    def user_task_zuweisen(self, user_task_key: str | int, bearbeiter: str) -> None:
        self._pruefe(
            self._http.post(
                f"/user-tasks/{user_task_key}/assignment",
                json={"assignee": bearbeiter, "allowOverride": True},
            )
        )

    def user_task_abschliessen(self, user_task_key: str | int, variablen: dict[str, Any]) -> None:
        self._pruefe(
            self._http.post(f"/user-tasks/{user_task_key}/completion", json={"variables": variablen})
        )
