# ADR-0006: Offline-Pipeline als zweite Ausführung des Prozessmodells

**Status:** akzeptiert · **Datum:** 2026-10-06

## Kontext

Der komplette Durchlauf soll auf jedem Laptop in Sekunden demonstrierbar und in der CI testbar sein,
ohne einen Camunda-Cluster zu starten.

## Entscheidung

`pipeline/offline.py` bildet den Kontrollfluss der beiden BPMN-Prozesse in Python nach und ruft
dieselben Handler und dieselbe DMN-Datei auf. Der User Task wird durch eine austauschbare
Entscheider-Strategie ersetzt (Demo-Policy oder interaktiv im Terminal).

## Konsequenzen

* Zwei Stellen beschreiben den Ablauf (BPMN und Pipeline). Die Konsistenztests (`test_modelle_konsistenz.py`)
  sichern den Variablen- und Job-Typ-Vertrag; der Integrationstest sichert den echten Ablauf.
* Vorteil überwiegt: End-to-End-Tests laufen in der CI in Sekunden, und die Pipeline ist eine lesbare
  Prosa-Version des Modells für Reviews.
* Bei Änderung des BPMN muss die Pipeline mitgezogen werden; das ist dokumentiert und im Code mit
  Verweisen auf die Gateways kommentiert.
