#!/usr/bin/env python3
"""Erzeugt die Prozess- und Entscheidungsmodelle (BPMN, DMN) und das Formular.

Warum generiert statt geklickt? Die Modelle sind damit reproduzierbar, diff-bar und die
Layout-Koordinaten liegen an *einer* Stelle. Die Dateien lassen sich trotzdem jederzeit im
Camunda Modeler öffnen und weiterbearbeiten – sie sind normkonformes BPMN 2.0 / DMN 1.3 mit
Diagram-Interchange.

Aufruf:  python tools/generate_models.py   (schreibt nach bpmn/ und forms/)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

WURZEL = Path(__file__).resolve().parents[1]
BPMN_DIR = WURZEL / "bpmn"
FORMS_DIR = WURZEL / "forms"
PLATTFORM_VERSION = "8.9.0"
EXPORTER = 'exporter="zv-klaerfall/tools/generate_models.py" exporterVersion="0.1.0"'

# --------------------------------------------------------------------------------------------
# BPMN-Minimodell
# --------------------------------------------------------------------------------------------


@dataclass
class Knoten:
    id: str
    typ: str  # startEvent | endEvent | serviceTask | userTask | businessRuleTask | callActivity | exclusiveGateway | boundaryEvent
    name: str
    x: int
    y: int
    extra_xml: str = ""  # Kindelemente VOR incoming/outgoing (extensionElements)
    attribute: dict[str, str] = field(default_factory=dict)
    nach_xml: str = (
        ""  # Kindelemente NACH incoming/outgoing (loopCharacteristics, eventDefinition) – XSD-Reihenfolge
    )
    label_pos: str = "unten"  # unten | oben | rechts – wo das Label eines Events/Gateways liegt

    @property
    def groesse(self) -> tuple[int, int]:
        if self.typ in ("startEvent", "endEvent", "boundaryEvent"):
            return (36, 36)
        if self.typ == "exclusiveGateway":
            return (50, 50)
        return (100, 80)


@dataclass
class Fluss:
    id: str
    von: str
    nach: str
    punkte: list[tuple[int, int]]
    name: str = ""
    bedingung: str | None = None  # FEEL-Ausdruck (ohne "=")
    label_bounds: tuple[int, int, int, int] | None = None


def _task(typ: str, k: Knoten, eingehend: list[str], ausgehend: list[str]) -> str:
    attrs = " ".join(f"{a}={quoteattr(v)}" for a, v in k.attribute.items())
    xml = [f'    <bpmn:{typ} id="{k.id}" name={quoteattr(k.name)}{(" " + attrs) if attrs else ""}>']
    if k.extra_xml:
        xml.append(k.extra_xml.rstrip())
    xml += [f"      <bpmn:incoming>{f}</bpmn:incoming>" for f in eingehend]
    xml += [f"      <bpmn:outgoing>{f}</bpmn:outgoing>" for f in ausgehend]
    if k.nach_xml:
        xml.append(k.nach_xml.rstrip())
    xml.append(f"    </bpmn:{typ}>")
    return "\n".join(xml)


def bpmn_dokument(
    prozess_id: str, prozess_name: str, knoten: list[Knoten], fluesse: list[Fluss], version_tag: str = "1.0"
) -> str:
    eingehend: dict[str, list[str]] = {k.id: [] for k in knoten}
    ausgehend: dict[str, list[str]] = {k.id: [] for k in knoten}
    for f in fluesse:
        ausgehend[f.von].append(f.id)
        eingehend[f.nach].append(f.id)

    elemente = [
        "    <bpmn:extensionElements>",
        f'      <zeebe:versionTag value="{version_tag}" />',
        "    </bpmn:extensionElements>",
    ]
    for k in knoten:
        elemente.append(_task(k.typ, k, eingehend[k.id], ausgehend[k.id]))
    for f in fluesse:
        name = f" name={quoteattr(f.name)}" if f.name else ""
        if f.bedingung:
            elemente.append(
                f'    <bpmn:sequenceFlow id="{f.id}"{name} sourceRef="{f.von}" targetRef="{f.nach}">\n'
                f'      <bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">={escape(f.bedingung)}'
                "</bpmn:conditionExpression>\n"
                "    </bpmn:sequenceFlow>"
            )
        else:
            elemente.append(
                f'    <bpmn:sequenceFlow id="{f.id}"{name} sourceRef="{f.von}" targetRef="{f.nach}" />'
            )

    di = []
    for k in knoten:
        w, h = k.groesse
        label = ""
        if k.name and k.typ in ("startEvent", "endEvent", "boundaryEvent", "exclusiveGateway"):
            lw = max(60, min(120, 7 * len(k.name)))
            if k.label_pos == "oben":
                lx, ly = k.x + w // 2 - lw // 2, k.y - 32
            elif k.label_pos == "rechts":
                lx, ly = k.x + w + 6, k.y + h // 2 - 7
            else:
                lx, ly = k.x + w // 2 - lw // 2, k.y + h + 6
            label = f'\n        <bpmndi:BPMNLabel>\n          <dc:Bounds x="{lx}" y="{ly}" width="{lw}" height="27" />\n        </bpmndi:BPMNLabel>'
        marker = ' isMarkerVisible="true"' if k.typ == "exclusiveGateway" else ""
        di.append(
            f'      <bpmndi:BPMNShape id="{k.id}_di" bpmnElement="{k.id}"{marker}>\n'
            f'        <dc:Bounds x="{k.x}" y="{k.y}" width="{w}" height="{h}" />{label}\n'
            "      </bpmndi:BPMNShape>"
        )
    for f in fluesse:
        wp = "\n".join(f'        <di:waypoint x="{x}" y="{y}" />' for x, y in f.punkte)
        label = ""
        if f.name:
            if f.label_bounds:
                lx, ly, lw, lh = f.label_bounds
            else:
                (x1, y1), (x2, y2) = f.punkte[0], f.punkte[1]
                lx, ly, lw, lh = (x1 + x2) // 2 - 15, (y1 + y2) // 2 - 20, 30, 14
            label = f'\n        <bpmndi:BPMNLabel>\n          <dc:Bounds x="{lx}" y="{ly}" width="{lw}" height="{lh}" />\n        </bpmndi:BPMNLabel>'
        di.append(
            f'      <bpmndi:BPMNEdge id="{f.id}_di" bpmnElement="{f.id}">\n{wp}{label}\n      </bpmndi:BPMNEdge>'
        )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:zeebe="http://camunda.org/schema/zeebe/1.0" xmlns:modeler="http://camunda.org/schema/modeler/1.0" id="Definitions_{prozess_id}" targetNamespace="http://bpmn.io/schema/bpmn" {EXPORTER} modeler:executionPlatform="Camunda Cloud" modeler:executionPlatformVersion="{PLATTFORM_VERSION}">
  <bpmn:process id="{prozess_id}" name={quoteattr(prozess_name)} isExecutable="true">
{chr(10).join(elemente)}
  </bpmn:process>
  <bpmndi:BPMNDiagram id="BPMNDiagram_{prozess_id}">
    <bpmndi:BPMNPlane id="BPMNPlane_{prozess_id}" bpmnElement="{prozess_id}">
{chr(10).join(di)}
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>
"""


def service_task_ext(job_typ: str, retries: int = 3, header: dict[str, str] | None = None) -> str:
    h = ""
    if header:
        eintraege = "\n".join(
            f'        <zeebe:header key="{k}" value={quoteattr(v)} />' for k, v in header.items()
        )
        h = f"\n      <zeebe:taskHeaders>\n{eintraege}\n      </zeebe:taskHeaders>"
    return (
        "      <bpmn:extensionElements>\n"
        f'        <zeebe:taskDefinition type="{job_typ}" retries="{retries}" />{h}\n'
        "      </bpmn:extensionElements>"
    )


# --------------------------------------------------------------------------------------------
# Prozess 1: Nachricht verarbeiten
# --------------------------------------------------------------------------------------------


def prozess_nachricht() -> str:
    call_activity_ext = """      <bpmn:extensionElements>
        <zeebe:calledElement processId="zv-zahlung-pruefen" propagateAllChildVariables="false" propagateAllParentVariables="false" />
        <zeebe:ioMapping>
          <zeebe:input source="=zahlung" target="zahlung" />
          <zeebe:input source="=nachricht" target="nachricht" />
          <zeebe:output source="=ergebnis" target="ergebnis" />
        </zeebe:ioMapping>
      </bpmn:extensionElements>"""
    multi_instance_xml = """      <bpmn:multiInstanceLoopCharacteristics>
        <bpmn:extensionElements>
          <zeebe:loopCharacteristics inputCollection="=zahlungen" inputElement="zahlung" outputCollection="ergebnisse" outputElement="=ergebnis" />
        </bpmn:extensionElements>
      </bpmn:multiInstanceLoopCharacteristics>"""
    knoten = [
        Knoten("start_eingang", "startEvent", "pain.001 eingegangen", 152, 182),
        Knoten(
            "task_validieren",
            "serviceTask",
            "Nachricht validieren und aufteilen",
            240,
            160,
            service_task_ext("zv.nachricht-validieren"),
        ),
        Knoten(
            "gw_schema",
            "exclusiveGateway",
            "Nachricht gültig?",
            395,
            175,
            attribute={"default": "flow_schema_nein"},
            label_pos="oben",
        ),
        Knoten(
            "task_zurueckweisen",
            "serviceTask",
            "Zurückweisung melden",
            370,
            300,
            service_task_ext("zv.statusreport"),
        ),
        Knoten("end_zurueckgewiesen", "endEvent", "Nachricht zurückgewiesen", 522, 322),
        Knoten(
            "call_zahlung",
            "callActivity",
            "Zahlung prüfen und disponieren (je Transaktion)",
            500,
            160,
            call_activity_ext,
            nach_xml=multi_instance_xml,
        ),
        Knoten(
            "task_statusreport",
            "serviceTask",
            "Statusreport pain.002 erstellen",
            660,
            160,
            service_task_ext("zv.statusreport"),
        ),
        Knoten("end_verarbeitet", "endEvent", "Nachricht verarbeitet", 822, 182),
    ]
    fluesse = [
        Fluss("flow_start", "start_eingang", "task_validieren", [(188, 200), (240, 200)]),
        Fluss("flow_zu_gw_schema", "task_validieren", "gw_schema", [(340, 200), (395, 200)]),
        Fluss(
            "flow_schema_ja",
            "gw_schema",
            "call_zahlung",
            [(445, 200), (500, 200)],
            name="ja",
            bedingung="nachrichtGueltig = true",
            label_bounds=(464, 182, 14, 14),
        ),
        Fluss(
            "flow_schema_nein",
            "gw_schema",
            "task_zurueckweisen",
            [(420, 225), (420, 300)],
            name="nein",
            label_bounds=(426, 258, 24, 14),
        ),
        Fluss("flow_zurueckgewiesen", "task_zurueckweisen", "end_zurueckgewiesen", [(470, 340), (522, 340)]),
        Fluss("flow_zu_statusreport", "call_zahlung", "task_statusreport", [(600, 200), (660, 200)]),
        Fluss("flow_ende", "task_statusreport", "end_verarbeitet", [(760, 200), (822, 200)]),
    ]
    return bpmn_dokument(
        "zv-nachricht-verarbeiten", "Zahlungsauftragsdatei (pain.001) verarbeiten", knoten, fluesse
    )


# --------------------------------------------------------------------------------------------
# Prozess 2: Zahlung prüfen
# --------------------------------------------------------------------------------------------


def prozess_zahlung() -> str:
    user_task_ext = """      <bpmn:extensionElements>
        <zeebe:userTask />
        <zeebe:formDefinition formId="zv-klaerfall-form" />
        <zeebe:assignmentDefinition candidateGroups="=[klassifikation.zielTeam]" />
        <zeebe:taskSchedule dueDate="=now() + duration(klassifikation.sla)" />
        <zeebe:priorityDefinition priority="=if klassifikation.prioritaet = &quot;KRITISCH&quot; then 90 else if klassifikation.prioritaet = &quot;HOCH&quot; then 70 else if klassifikation.prioritaet = &quot;MITTEL&quot; then 50 else 30" />
      </bpmn:extensionElements>"""
    rule_task_ext = """      <bpmn:extensionElements>
        <zeebe:calledDecision decisionId="klaerfall-klassifikation" resultVariable="klassifikation" />
      </bpmn:extensionElements>"""
    timer_ext = """      <bpmn:timerEventDefinition id="timer_sla_def">
        <bpmn:timeDuration xsi:type="bpmn:tFormalExpression">=klassifikation.sla</bpmn:timeDuration>
      </bpmn:timerEventDefinition>"""
    knoten = [
        Knoten("start_zahlung", "startEvent", "Zahlung erhalten", 152, 182),
        Knoten("gw_merge_pruefen", "exclusiveGateway", "", 222, 175),
        Knoten(
            "task_pruefen",
            "serviceTask",
            "Zahlung fachlich prüfen",
            300,
            160,
            service_task_ext("zv.pruefen", header={"betragslimit": "50000.00"}),
        ),
        Knoten(
            "gw_befunde",
            "exclusiveGateway",
            "Befunde vorhanden?",
            455,
            175,
            attribute={"default": "flow_befunde_nein"},
            label_pos="oben",
        ),
        Knoten("gw_merge_freigeben", "exclusiveGateway", "", 995, 175),
        Knoten(
            "task_freigeben",
            "serviceTask",
            "Zahlung freigeben und buchen",
            1080,
            160,
            service_task_ext("zv.freigeben"),
        ),
        Knoten("end_freigegeben", "endEvent", "Zahlung freigegeben (ACCP)", 1232, 182),
        Knoten(
            "rule_klassifizieren",
            "businessRuleTask",
            "Klärfall klassifizieren (DMN)",
            430,
            300,
            rule_task_ext,
        ),
        Knoten(
            "gw_auto",
            "exclusiveGateway",
            "Automatisch korrigierbar?",
            585,
            315,
            attribute={"default": "flow_auto_nein"},
            label_pos="oben",
        ),
        Knoten("task_klaerfall", "userTask", "Klärfall bearbeiten", 700, 300, user_task_ext),
        Knoten(
            "timer_sla",
            "boundaryEvent",
            "SLA überschritten",
            732,
            362,
            attribute={"attachedToRef": "task_klaerfall", "cancelActivity": "false"},
            label_pos="rechts",
            nach_xml=timer_ext,
        ),
        Knoten(
            "gw_entscheidung",
            "exclusiveGateway",
            "Entscheidung",
            915,
            315,
            attribute={"default": "flow_ablehnen"},
        ),
        Knoten(
            "task_ablehnen", "serviceTask", "Zahlung ablehnen", 1080, 300, service_task_ext("zv.ablehnen")
        ),
        Knoten("end_abgelehnt", "endEvent", "Zahlung abgelehnt (RJCT)", 1232, 322),
        Knoten(
            "task_korrigieren",
            "serviceTask",
            "Automatisch korrigieren",
            560,
            440,
            service_task_ext("zv.korrigieren"),
        ),
        Knoten(
            "task_eskalieren",
            "serviceTask",
            "Eskalation an Teamleitung",
            700,
            440,
            service_task_ext("zv.eskalieren"),
        ),
        Knoten("end_eskaliert", "endEvent", "Eskalation gemeldet", 852, 462),
    ]
    fluesse = [
        Fluss("flow_start", "start_zahlung", "gw_merge_pruefen", [(188, 200), (222, 200)]),
        Fluss("flow_merge_pruefen", "gw_merge_pruefen", "task_pruefen", [(272, 200), (300, 200)]),
        Fluss("flow_zu_gw_befunde", "task_pruefen", "gw_befunde", [(400, 200), (455, 200)]),
        Fluss(
            "flow_befunde_nein",
            "gw_befunde",
            "gw_merge_freigeben",
            [(505, 200), (995, 200)],
            name="nein",
            label_bounds=(738, 182, 24, 14),
        ),
        Fluss(
            "flow_befunde_ja",
            "gw_befunde",
            "rule_klassifizieren",
            [(480, 225), (480, 300)],
            name="ja",
            bedingung="befundeVorhanden = true",
            label_bounds=(486, 258, 14, 14),
        ),
        Fluss("flow_zu_gw_auto", "rule_klassifizieren", "gw_auto", [(530, 340), (585, 340)]),
        Fluss(
            "flow_auto_ja",
            "gw_auto",
            "task_korrigieren",
            [(610, 365), (610, 440)],
            name="ja",
            bedingung="klassifikation.autoKorrektur = true and erneutePruefung = false",
            label_bounds=(616, 396, 14, 14),
        ),
        Fluss(
            "flow_korrigiert", "task_korrigieren", "gw_merge_pruefen", [(560, 480), (247, 480), (247, 225)]
        ),
        Fluss(
            "flow_auto_nein",
            "gw_auto",
            "task_klaerfall",
            [(635, 340), (700, 340)],
            name="nein",
            label_bounds=(656, 322, 24, 14),
        ),
        Fluss("flow_zu_gw_entscheidung", "task_klaerfall", "gw_entscheidung", [(800, 340), (915, 340)]),
        Fluss(
            "flow_freigeben",
            "gw_entscheidung",
            "gw_merge_freigeben",
            [(940, 315), (940, 260), (1020, 260), (1020, 225)],
            name="freigeben",
            bedingung='entscheidung = "FREIGEBEN"',
            label_bounds=(956, 242, 48, 14),
        ),
        Fluss("flow_merge_freigeben", "gw_merge_freigeben", "task_freigeben", [(1045, 200), (1080, 200)]),
        Fluss(
            "flow_ablehnen",
            "gw_entscheidung",
            "task_ablehnen",
            [(965, 340), (1080, 340)],
            name="ablehnen",
            label_bounds=(1000, 322, 44, 14),
        ),
        Fluss("flow_ende_freigegeben", "task_freigeben", "end_freigegeben", [(1180, 200), (1232, 200)]),
        Fluss("flow_ende_abgelehnt", "task_ablehnen", "end_abgelehnt", [(1180, 340), (1232, 340)]),
        Fluss("flow_sla", "timer_sla", "task_eskalieren", [(750, 398), (750, 440)]),
        Fluss("flow_ende_eskaliert", "task_eskalieren", "end_eskaliert", [(800, 480), (852, 480)]),
    ]
    return bpmn_dokument("zv-zahlung-pruefen", "Zahlung prüfen und disponieren", knoten, fluesse)


# --------------------------------------------------------------------------------------------
# DMN: Klärfall-Klassifikation
# --------------------------------------------------------------------------------------------

DMN_EINGABEN = [
    (
        "befundCode",
        "Führender Befund",
        "string",
        '"SANKTION","BETRAG_LIMIT","DUBLETTE","IBAN_PRUEFZIFFER","BIC_UNGUELTIG","BIC_IBAN_MISMATCH",'
        '"WAEHRUNG","BETRAG_NULL","GEBUEHRENREGELUNG","AUSFUEHRUNGSDATUM","VERWENDUNGSZWECK_FEHLT"',
    ),
    ("betrag", "Betrag (EUR)", "number", None),
    ("sanktionstreffer", "Sanktionstreffer?", "boolean", None),
    ("nurAutoKorrigierbar", "Nur automatisch korrigierbare Befunde?", "boolean", None),
]
DMN_AUSGABEN = [
    (
        "kategorie",
        "Kategorie",
        "string",
        '"Compliance","Freigabe","Dublette","Formatkorrektur","Kontodaten","Auftragsdaten","Sonstige"',
    ),
    ("prioritaet", "Priorität", "string", '"KRITISCH","HOCH","MITTEL","NIEDRIG"'),
    (
        "zielTeam",
        "Zielteam (Kandidatengruppe)",
        "string",
        '"team-compliance","team-disposition-leitung","team-disposition","team-zahlungsverkehr"',
    ),
    ("autoKorrektur", "Automatisch korrigieren?", "boolean", None),
    ("sla", "Bearbeitungsfrist (ISO 8601)", "string", '"PT2H","PT4H","PT8H","PT24H"'),
]
# (Beschreibung, Eingaben…, Ausgaben…)
DMN_REGELN: list[tuple[str, list[str], list[str]]] = [
    (
        "Sanktionstreffer: regulatorisch zwingend manuelle Prüfung durch Compliance, nie automatisch.",
        ["-", "-", "true", "-"],
        ['"Compliance"', '"KRITISCH"', '"team-compliance"', "false", '"PT2H"'],
    ),
    (
        "Doppelte Einreichung: Zahlungsverkehr prüft gegen das Original, bevor Geld doppelt fließt.",
        ['"DUBLETTE"', "-", "-", "-"],
        ['"Dublette"', '"HOCH"', '"team-zahlungsverkehr"', "false", '"PT4H"'],
    ),
    (
        "Großbetrag ab 500.000 EUR: Freigabe durch Leitung Disposition (erweitertes Vier-Augen-Prinzip).",
        ['"BETRAG_LIMIT"', ">= 500000", "-", "-"],
        ['"Freigabe"', '"HOCH"', '"team-disposition-leitung"', "false", '"PT4H"'],
    ),
    (
        "Betrag ab Limit: Vier-Augen-Freigabe durch die Disposition.",
        ['"BETRAG_LIMIT"', "-", "-", "-"],
        ['"Freigabe"', '"MITTEL"', '"team-disposition"', "false", '"PT8H"'],
    ),
    (
        "Ausschließlich formale Befunde (Gebührenregelung, Ausführungsdatum): automatische Korrektur erlaubt.",
        ["-", "-", "-", "true"],
        ['"Formatkorrektur"', '"NIEDRIG"', '"team-zahlungsverkehr"', "true", '"PT24H"'],
    ),
    (
        "Fehlerhafte Kontodaten: Rückfrage beim Einreicher durch den Zahlungsverkehr.",
        ['"IBAN_PRUEFZIFFER","BIC_UNGUELTIG","BIC_IBAN_MISMATCH"', "-", "-", "-"],
        ['"Kontodaten"', '"MITTEL"', '"team-zahlungsverkehr"', "false", '"PT8H"'],
    ),
    (
        "Fehlerhafte Auftragsdaten (Währung, Betrag, Datum außerhalb des Vorlaufs, Gebührenregelung).",
        ['"WAEHRUNG","BETRAG_NULL","AUSFUEHRUNGSDATUM","GEBUEHRENREGELUNG"', "-", "-", "-"],
        ['"Auftragsdaten"', '"MITTEL"', '"team-zahlungsverkehr"', "false", '"PT8H"'],
    ),
    (
        "Auffangregel: alles Übrige landet beim Zahlungsverkehr.",
        ["-", "-", "-", "-"],
        ['"Sonstige"', '"NIEDRIG"', '"team-zahlungsverkehr"', "false", '"PT24H"'],
    ),
]


def dmn_dokument() -> str:
    inputs = []
    for i, (expr, label, typ, werte) in enumerate(DMN_EINGABEN, start=1):
        iv = (
            f'\n        <inputValues id="iv_{i}">\n          <text>{escape(werte)}</text>\n        </inputValues>'
            if werte
            else ""
        )
        inputs.append(
            f'      <input id="in_{i}" label={quoteattr(label)}>\n'
            f'        <inputExpression id="inexpr_{i}" typeRef="{typ}">\n          <text>{expr}</text>\n        </inputExpression>{iv}\n'
            "      </input>"
        )
    outputs = []
    for i, (name, label, typ, werte) in enumerate(DMN_AUSGABEN, start=1):
        ov = (
            f'\n        <outputValues id="ov_{i}">\n          <text>{escape(werte)}</text>\n        </outputValues>'
            if werte
            else ""
        )
        close = f">{ov}\n      </output>" if ov else " />"
        outputs.append(
            f'      <output id="out_{i}" label={quoteattr(label)} name="{name}" typeRef="{typ}"{close}'
        )
    rules = []
    for r, (beschreibung, ein, aus) in enumerate(DMN_REGELN, start=1):
        ie = "\n".join(
            f'        <inputEntry id="ie_{r}_{i}">\n          <text>{escape(e)}</text>\n        </inputEntry>'
            for i, e in enumerate(ein, start=1)
        )
        oe = "\n".join(
            f'        <outputEntry id="oe_{r}_{i}">\n          <text>{escape(e)}</text>\n        </outputEntry>'
            for i, e in enumerate(aus, start=1)
        )
        rules.append(
            f'      <rule id="rule_{r}">\n        <description>{escape(beschreibung)}</description>\n{ie}\n{oe}\n      </rule>'
        )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/" xmlns:dmndi="https://www.omg.org/spec/DMN/20191111/DMNDI/" xmlns:dc="http://www.omg.org/spec/DMN/20180521/DC/" xmlns:modeler="http://camunda.org/schema/modeler/1.0" xmlns:zeebe="http://camunda.org/schema/zeebe/1.0" id="zv-klaerfall-entscheidungen" name="Klärfall-Entscheidungen Zahlungsverkehr" namespace="http://camunda.org/schema/1.0/dmn" {EXPORTER} modeler:executionPlatform="Camunda Cloud" modeler:executionPlatformVersion="{PLATTFORM_VERSION}">
  <decision id="klaerfall-klassifikation" name="Klärfall klassifizieren">
    <extensionElements>
      <zeebe:versionTag value="1.0" />
    </extensionElements>
    <decisionTable id="dt_klaerfall" hitPolicy="FIRST">
{chr(10).join(inputs)}
{chr(10).join(outputs)}
{chr(10).join(rules)}
    </decisionTable>
  </decision>
  <dmndi:DMNDI>
    <dmndi:DMNDiagram id="DMNDiagram_klaerfall">
      <dmndi:DMNShape id="DMNShape_klaerfall" dmnElementRef="klaerfall-klassifikation">
        <dc:Bounds height="80" width="180" x="160" y="100" />
      </dmndi:DMNShape>
    </dmndi:DMNDiagram>
  </dmndi:DMNDI>
</definitions>
"""


# --------------------------------------------------------------------------------------------
# Formular (Camunda Forms / form-js)
# --------------------------------------------------------------------------------------------


def formular() -> dict:
    return {
        "id": "zv-klaerfall-form",
        "type": "default",
        "schemaVersion": 18,
        "executionPlatform": "Camunda Cloud",
        "executionPlatformVersion": PLATTFORM_VERSION,
        "exporter": {"name": "zv-klaerfall/tools/generate_models.py", "version": "0.1.0"},
        "components": [
            {
                "id": "txt_kopf",
                "type": "text",
                "text": "## Klärfall Zahlungsverkehr\n\n**Kategorie:** {{klassifikation.kategorie}} · "
                "**Priorität:** {{klassifikation.prioritaet}} · **Frist:** {{klassifikation.sla}} · "
                "**Zielteam:** {{klassifikation.zielTeam}}",
            },
            {
                "id": "fld_zahlung",
                "type": "textarea",
                "key": "zahlungText",
                "label": "Zahlung",
                "readonly": True,
            },
            {
                "id": "fld_befunde",
                "type": "textarea",
                "key": "befundeText",
                "label": "Befunde der fachlichen Prüfung",
                "readonly": True,
            },
            {
                "id": "fld_entscheidung",
                "type": "radio",
                "key": "entscheidung",
                "label": "Entscheidung",
                "validate": {"required": True},
                "values": [
                    {"label": "Freigeben – Zahlung wird ausgeführt (pain.002: ACCP)", "value": "FREIGEBEN"},
                    {
                        "label": "Ablehnen – Rückmeldung mit ISO-Statusgrund (pain.002: RJCT)",
                        "value": "ABLEHNEN",
                    },
                ],
            },
            {
                "id": "fld_bearbeiter",
                "type": "textfield",
                "key": "bearbeiter",
                "label": "Bearbeiter (Kürzel)",
                "validate": {"required": True, "minLength": 2},
            },
            {
                "id": "fld_kommentar",
                "type": "textarea",
                "key": "kommentar",
                "label": "Begründung",
                "description": "Pflichtfeld (Vier-Augen-Prinzip). Wird im Audit-Trail gespeichert; "
                "bei Ablehnung erhält der Einreicher den Text im Statusreport.",
                "validate": {"required": True, "minLength": 5},
            },
        ],
    }


def main() -> None:
    BPMN_DIR.mkdir(exist_ok=True)
    FORMS_DIR.mkdir(exist_ok=True)
    (BPMN_DIR / "zv-nachricht-verarbeiten.bpmn").write_text(prozess_nachricht(), encoding="utf-8")
    (BPMN_DIR / "zv-zahlung-pruefen.bpmn").write_text(prozess_zahlung(), encoding="utf-8")
    (BPMN_DIR / "klaerfall-klassifikation.dmn").write_text(dmn_dokument(), encoding="utf-8")
    (FORMS_DIR / "zv-klaerfall-form.form").write_text(
        json.dumps(formular(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("Modelle geschrieben:", *(p.name for p in sorted(BPMN_DIR.iterdir())), "zv-klaerfall-form.form")


if __name__ == "__main__":
    main()
