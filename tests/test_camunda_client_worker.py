"""REST-Client (gemockt mit respx) und Worker-Verarbeitung (Erfolg, BPMN-Error, technischer Fehler)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from zv_klaerfall.camunda.client import CamundaClient, CamundaFehler
from zv_klaerfall.camunda.worker import FachlicherFehler, JobWorker
from zv_klaerfall.config import Dienste

BASIS = "http://camunda.test:8080"


@pytest.fixture
def client() -> CamundaClient:
    return CamundaClient(BASIS)


@respx.mock
def test_topologie_und_erreichbarkeit(client: CamundaClient) -> None:
    route = respx.get(f"{BASIS}/v2/topology").mock(
        return_value=httpx.Response(200, json={"gatewayVersion": "8.9.22"})
    )
    assert client.topologie()["gatewayVersion"] == "8.9.22" and client.erreichbar()
    route.mock(side_effect=httpx.ConnectError("down"))
    assert not client.erreichbar()


@respx.mock
def test_deploy_sendet_multipart(client: CamundaClient, tmp_path: Path) -> None:
    datei = tmp_path / "x.bpmn"
    datei.write_text("<bpmn/>")
    route = respx.post(f"{BASIS}/v2/deployments").mock(
        return_value=httpx.Response(
            200, json={"deploymentKey": "1", "tenantId": "<default>", "deployments": []}
        )
    )
    assert client.deploye([datei])["deploymentKey"] == "1"
    anfrage = route.calls.last.request
    assert anfrage.headers["content-type"].startswith("multipart/form-data")
    assert b'name="resources"; filename="x.bpmn"' in anfrage.content


@respx.mock
def test_prozess_starten_und_jobs(client: CamundaClient) -> None:
    respx.post(f"{BASIS}/v2/process-instances").mock(
        return_value=httpx.Response(200, json={"processInstanceKey": "42", "processDefinitionId": "p"})
    )
    assert client.starte_prozess("p", {"a": 1})["processInstanceKey"] == "42"
    aktivierung = respx.post(f"{BASIS}/v2/jobs/activation").mock(
        return_value=httpx.Response(
            200, json={"jobs": [{"jobKey": "7", "type": "zv.pruefen", "variables": {}}]}
        )
    )
    jobs = client.aktiviere_jobs("zv.pruefen", worker="w")
    assert jobs[0]["jobKey"] == "7"
    body = json.loads(aktivierung.calls.last.request.content)
    assert body == {
        "type": "zv.pruefen",
        "worker": "w",
        "timeout": 60000,
        "maxJobsToActivate": 8,
        "requestTimeout": 10000,
    }
    abschluss = respx.post(f"{BASIS}/v2/jobs/7/completion").mock(return_value=httpx.Response(204))
    client.job_abschliessen("7", {"x": 1})
    assert json.loads(abschluss.calls.last.request.content) == {"variables": {"x": 1}}
    fehler = respx.post(f"{BASIS}/v2/jobs/7/failure").mock(return_value=httpx.Response(204))
    client.job_fehlschlagen("7", retries=2, fehlermeldung="kaputt")
    assert json.loads(fehler.calls.last.request.content) == {
        "retries": 2,
        "errorMessage": "kaputt",
        "retryBackOff": 5000,
    }


@respx.mock
def test_user_tasks(client: CamundaClient) -> None:
    respx.post(f"{BASIS}/v2/user-tasks/search").mock(
        return_value=httpx.Response(
            200, json={"items": [{"userTaskKey": "9", "candidateGroups": ["team-compliance"]}]}
        )
    )
    assert client.suche_user_tasks(kandidatengruppe="team-compliance")[0]["userTaskKey"] == "9"
    respx.post(f"{BASIS}/v2/user-tasks/9/variables/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"name": "betrag", "value": "75000.0"},
                    {"name": "zahlungText", "value": '"75,000.00 EUR"'},
                    {"name": "klassifikation", "value": '{"kategorie":"Freigabe"}'},
                ]
            },
        )
    )
    v = client.user_task_variablen("9")
    assert v == {
        "betrag": 75000.0,
        "zahlungText": "75,000.00 EUR",
        "klassifikation": {"kategorie": "Freigabe"},
    }
    zuweisung = respx.post(f"{BASIS}/v2/user-tasks/9/assignment").mock(return_value=httpx.Response(204))
    abschluss = respx.post(f"{BASIS}/v2/user-tasks/9/completion").mock(return_value=httpx.Response(204))
    client.user_task_zuweisen("9", "jk")
    client.user_task_abschliessen("9", {"entscheidung": "FREIGEBEN"})
    assert json.loads(zuweisung.calls.last.request.content) == {"assignee": "jk", "allowOverride": True}
    assert json.loads(abschluss.calls.last.request.content) == {"variables": {"entscheidung": "FREIGEBEN"}}


@respx.mock
def test_fehlerantwort_wird_zu_camundafehler(client: CamundaClient) -> None:
    respx.post(f"{BASIS}/v2/process-instances").mock(
        return_value=httpx.Response(404, json={"title": "NOT_FOUND", "detail": "Prozess unbekannt"})
    )
    with pytest.raises(CamundaFehler) as exc:
        client.starte_prozess("gibt-es-nicht")
    assert exc.value.status == 404 and "Prozess unbekannt" in str(exc.value)


# --- Worker --------------------------------------------------------------------------------------


class FakeClient:
    def __init__(self) -> None:
        self.abgeschlossen: list[tuple[str, dict]] = []
        self.fehlgeschlagen: list[tuple[str, int, str]] = []
        self.bpmn_fehler: list[tuple[str, str]] = []

    def job_abschliessen(self, key: str, variablen: dict) -> None:
        self.abgeschlossen.append((key, variablen))

    def job_fehlschlagen(self, key: str, *, retries: int, fehlermeldung: str, backoff_ms: int = 0) -> None:
        self.fehlgeschlagen.append((key, retries, fehlermeldung))

    def job_fehler_werfen(self, key: str, *, error_code: str, nachricht: str = "") -> None:
        self.bpmn_fehler.append((key, error_code))


def _job(typ: str, **variablen) -> dict:  # type: ignore[no-untyped-def]
    return {
        "jobKey": "1",
        "type": typ,
        "processDefinitionId": "zv-zahlung-pruefen",
        "processInstanceKey": "100",
        "retries": 3,
        "customHeaders": {"betragslimit": "50000.00"},
        "variables": variablen,
    }


def test_worker_verarbeitet_job_und_setzt_kontext(dienste: Dienste, fachdatum: date) -> None:
    gesehen = {}

    def handler(v, d, ctx):  # type: ignore[no-untyped-def]
        gesehen.update({"ctx": ctx, "v": v})
        return {"ok": True}

    fake = FakeClient()
    worker = JobWorker(fake, dienste, handler={"zv.test": handler}, heute=fachdatum)  # type: ignore[arg-type]
    worker.verarbeite(_job("zv.test", a=1))
    assert fake.abgeschlossen == [("1", {"ok": True})]
    assert gesehen["ctx"].instanz_key == "100" and gesehen["ctx"].header == {"betragslimit": "50000.00"}
    assert gesehen["ctx"].heute == fachdatum and gesehen["v"] == {"a": 1}
    assert worker.statistik.verarbeitet == 1 and worker.statistik.je_typ == {"zv.test": 1}


def test_worker_meldet_technischen_fehler_mit_retries(dienste: Dienste) -> None:
    def handler(v, d, ctx):  # type: ignore[no-untyped-def]
        raise KeyError("zahlung")

    fake = FakeClient()
    worker = JobWorker(fake, dienste, handler={"zv.test": handler})  # type: ignore[arg-type]
    worker.verarbeite(_job("zv.test"))
    assert fake.fehlgeschlagen == [("1", 2, "KeyError: 'zahlung'")] and worker.statistik.fehlgeschlagen == 1


def test_worker_wirft_bpmn_error(dienste: Dienste) -> None:
    def handler(v, d, ctx):  # type: ignore[no-untyped-def]
        raise FachlicherFehler("KONTO_GESPERRT", "Konto gesperrt")

    fake = FakeClient()
    JobWorker(fake, dienste, handler={"zv.test": handler}).verarbeite(_job("zv.test"))  # type: ignore[arg-type]
    assert fake.bpmn_fehler == [("1", "KONTO_GESPERRT")] and not fake.abgeschlossen
