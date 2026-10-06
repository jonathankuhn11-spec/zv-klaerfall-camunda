"""Vertrag zwischen Prozessmodell und Code: Job-Typen, Decision-ID, Formular-ID, Variablennamen.

Diese Tests verhindern den klassischen Fehler, dass jemand im Modeler einen Job-Typ umbenennt und
der Worker danach ins Leere läuft – ohne dass es jemand vor dem Deployment merkt.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from lxml import etree

from zv_klaerfall import handlers
from zv_klaerfall.pipeline.offline import DECISION_ID, PROZESS_NACHRICHT, PROZESS_ZAHLUNG

NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "zeebe": "http://camunda.org/schema/zeebe/1.0",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
}


def _bpmn(bpmn_dir: Path, name: str) -> etree._Element:
    return etree.parse(str(bpmn_dir / name)).getroot()


def test_job_typen_passen_zu_handlern(bpmn_dir: Path) -> None:
    typen: set[str] = set()
    for datei in bpmn_dir.glob("*.bpmn"):
        typen |= {t.get("type") for t in _bpmn(bpmn_dir, datei.name).findall(".//zeebe:taskDefinition", NS)}
    assert typen == set(handlers.HANDLER), "jeder Service Task braucht genau einen Handler und umgekehrt"


def test_prozess_ids_decision_und_formular(bpmn_dir: Path) -> None:
    nachricht = _bpmn(bpmn_dir, "zv-nachricht-verarbeiten.bpmn")
    zahlung = _bpmn(bpmn_dir, "zv-zahlung-pruefen.bpmn")
    assert nachricht.find("bpmn:process", NS).get("id") == PROZESS_NACHRICHT
    assert zahlung.find("bpmn:process", NS).get("id") == PROZESS_ZAHLUNG
    called = nachricht.find(".//zeebe:calledElement", NS)
    assert called.get("processId") == PROZESS_ZAHLUNG
    assert (
        called.get("propagateAllParentVariables") == "false"
        and called.get("propagateAllChildVariables") == "false"
    )
    decision = zahlung.find(".//zeebe:calledDecision", NS)
    assert decision.get("decisionId") == DECISION_ID and decision.get("resultVariable") == "klassifikation"
    dmn = etree.parse(str(bpmn_dir / "klaerfall-klassifikation.dmn")).getroot()
    assert dmn.find("{https://www.omg.org/spec/DMN/20191111/MODEL/}decision").get("id") == DECISION_ID
    form_id = zahlung.find(".//zeebe:formDefinition", NS).get("formId")
    form = json.loads((bpmn_dir.parent / "forms" / f"{form_id}.form").read_text(encoding="utf-8"))
    assert form["id"] == form_id
    assert {c.get("key") for c in form["components"] if c.get("key")} == {
        "zahlungText",
        "befundeText",
        "entscheidung",
        "bearbeiter",
        "kommentar",
    }


def test_multi_instance_und_io_mapping(bpmn_dir: Path) -> None:
    nachricht = _bpmn(bpmn_dir, "zv-nachricht-verarbeiten.bpmn")
    loop = nachricht.find(".//zeebe:loopCharacteristics", NS)
    assert loop.get("inputCollection") == "=zahlungen" and loop.get("inputElement") == "zahlung"
    assert loop.get("outputCollection") == "ergebnisse" and loop.get("outputElement") == "=ergebnis"
    inputs = {
        i.get("target"): i.get("source") for i in nachricht.findall(".//zeebe:ioMapping/zeebe:input", NS)
    }
    outputs = {
        o.get("target"): o.get("source") for o in nachricht.findall(".//zeebe:ioMapping/zeebe:output", NS)
    }
    assert inputs == {"zahlung": "=zahlung", "nachricht": "=nachricht"} and outputs == {
        "ergebnis": "=ergebnis"
    }


def test_gateway_bedingungen_nutzen_vorhandene_variablen(bpmn_dir: Path) -> None:
    zahlung = _bpmn(bpmn_dir, "zv-zahlung-pruefen.bpmn")
    bedingungen = [c.text for c in zahlung.findall(".//bpmn:conditionExpression", NS)]
    assert "=befundeVorhanden = true" in bedingungen
    assert "=klassifikation.autoKorrektur = true and erneutePruefung = false" in bedingungen
    assert '=entscheidung = "FREIGEBEN"' in bedingungen
    # Jedes Gateway mit Bedingungen hat einen Default-Flow (kein "stuck" bei unerwarteten Werten)
    for gw in zahlung.findall(".//bpmn:exclusiveGateway", NS):
        ausgehend = gw.findall("bpmn:outgoing", NS)
        if len(ausgehend) > 1:
            assert gw.get("default"), f"Gateway {gw.get('id')} ohne Default-Flow"


def test_user_task_konfiguration(bpmn_dir: Path) -> None:
    zahlung = _bpmn(bpmn_dir, "zv-zahlung-pruefen.bpmn")
    task = zahlung.find(".//bpmn:userTask", NS)
    assert task.find("bpmn:extensionElements/zeebe:userTask", NS) is not None, (
        "Camunda-User-Task (nicht Job-Worker)"
    )
    assert (
        task.find("bpmn:extensionElements/zeebe:assignmentDefinition", NS).get("candidateGroups")
        == "=[klassifikation.zielTeam]"
    )
    assert (
        task.find("bpmn:extensionElements/zeebe:taskSchedule", NS)
        .get("dueDate")
        .startswith("=now() + duration(")
    )
    timer = zahlung.find(".//bpmn:boundaryEvent", NS)
    assert timer.get("attachedToRef") == task.get("id") and timer.get("cancelActivity") == "false"
    assert timer.find(".//bpmn:timeDuration", NS).text == "=klassifikation.sla"


def test_alle_flows_referenzieren_existierende_elemente_und_haben_di(bpmn_dir: Path) -> None:
    for datei in bpmn_dir.glob("*.bpmn"):
        wurzel = _bpmn(bpmn_dir, datei.name)
        prozess = wurzel.find("bpmn:process", NS)
        ids = {el.get("id") for el in prozess.iter() if el.get("id")}
        for flow in prozess.findall("bpmn:sequenceFlow", NS):
            assert flow.get("sourceRef") in ids and flow.get("targetRef") in ids, (datei.name, flow.get("id"))
        di_ids = {el.get("bpmnElement") for el in wurzel.iter() if el.get("bpmnElement")}
        fluss_und_knoten = {el.get("id") for el in prozess if el.get("id")}
        fehlend = fluss_und_knoten - di_ids
        assert not fehlend, f"{datei.name}: Elemente ohne Diagramm-Shape/Edge: {fehlend}"


def test_feel_ausdruecke_der_formular_vorlage_referenzieren_klassifikation(bpmn_dir: Path) -> None:
    form = json.loads((bpmn_dir.parent / "forms" / "zv-klaerfall-form.form").read_text(encoding="utf-8"))
    kopf = next(c for c in form["components"] if c["type"] == "text")["text"]
    assert set(re.findall(r"{{klassifikation\.(\w+)}}", kopf)) <= {
        "kategorie",
        "prioritaet",
        "sla",
        "zielTeam",
    }
